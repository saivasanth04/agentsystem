"""
Observation Engine.
Strictly normalizes raw tool execution outputs into structured Observation objects.
Enforces the core rule:
'Never append raw logs. Parse tool outputs (stack traces, compiler diagnostics,
pytest failures, linter errors) into structured objects:
Observation(type="compiler_error", file="auth.py", line=84, symbol="validate_token",
severity="error", evidence="...")
The replanner / context compiler consumes structured observations - NOT raw strings.'
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Union

# Mature in-tree parsers
try:
    from agent_orchestrator.runtime.fault_localization import EmpiricalFaultLocalizer, ParsedFailure
except ImportError:
    EmpiricalFaultLocalizer = None
    ParsedFailure = None

try:
    from agent_orchestrator.runtime.test_evidence_parser import TestExecutionParser, TestCaseEvidence
except ImportError:
    TestExecutionParser = None
    TestCaseEvidence = None

logger = logging.getLogger("runtime.observation_engine")


class NormalizedType(str):
    """String subclass that allows bidirectional type matching for both normalized and legacy names."""
    def __eq__(self, other: Any) -> bool:
        if super().__eq__(other):
            return True
        aliases = {
            "failing_test": {"test_failure"},
            "test_failure": {"failing_test"},
            "runtime_exception": {"runtime_error"},
            "runtime_error": {"runtime_exception"},
            "lint_error": {"linter_diagnostic"},
            "linter_diagnostic": {"lint_error"},
            "security_issue": {"permission_denied"},
            "permission_denied": {"security_issue"},
            "browser_console": {"browser_event"},
            "network_failure": {"browser_event"},
        }
        return str(other) in aliases.get(str(self), set())

    def __hash__(self) -> int:
        return super().__hash__()


@dataclass
class Observation:
    """
    Structured observation extracted deterministically from tool execution.
    Never contains unbounded raw log dumps in evidence.
    """
    type: str  # "compiler_error", "runtime_exception", "browser_console", "network_failure", "failing_test", "lint_error", "security_issue", etc.
    file: Optional[str] = None
    line: Optional[int] = None
    symbol: Optional[str] = None
    severity: str = "info"  # "error", "warning", "info"
    evidence: str = ""
    source: str = "tool"
    timestamp: float = field(default_factory=time.time)
    raw_output: str = field(default="", repr=False)  # Preserved solely for debugging/audit; never sent to LLM prompt
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": str(self.type),
            "file": self.file,
            "line": self.line,
            "symbol": self.symbol,
            "severity": self.severity,
            "evidence": self.evidence,
            "source": self.source,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    def to_replan_summary(self) -> str:
        """Format a concise single-line summary for replanning and context compilation."""
        loc_parts = []
        if self.file:
            loc_parts.append(self.file)
        if self.line is not None:
            loc_parts.append(f"L{self.line}")
        if self.symbol:
            loc_parts.append(f"in `{self.symbol}`")

        location = f" ({':'.join(loc_parts[:2])} {loc_parts[2] if len(loc_parts) > 2 else ''})".strip()
        if location == "()":
            location = ""

        return f"[{self.severity.upper()} | {self.type}]{location}: {self.evidence}"


class ObservationEngine:
    """
    Deterministic observation normalization engine.
    Parses compiler diagnostics, pytest failures, Python stack traces,
    git outputs, filesystem operations, and DevTools events into structured Observation objects.
    """

    # Regex patterns for compiler/linter diagnostics:
    # 1. gcc / clang / ruff / mypy / flake8:  path/to/file.py:42:15: error: message
    # 2. typescript:                          path/to/file.ts(42,15): error TS1234: message
    _COMPILER_DIAGNOSTIC_REGEX = re.compile(
        r'^\s*(?P<file>[^\s:(]+(?:\.[a-zA-Z0-9]+))(?::(?P<line>\d+)(?::(?P<col>\d+))?|'
        r'\((?P<ts_line>\d+),(?P<ts_col>\d+)\)):\s*(?P<severity>error|warning|fatal|note)?:?\s*(?P<msg>[^\n]+)',
        re.MULTILINE | re.IGNORECASE,
    )

    # Python syntax / indentation error:
    # File "example.py", line 12
    #     def bad():
    #             ^
    # SyntaxError: invalid syntax
    _SYNTAX_ERROR_REGEX = re.compile(
        r'File\s+["\'](?P<file>[^"\']+)["\'],\s+line\s+(?P<line>\d+).+?(?P<exc>SyntaxError|IndentationError|TabError):\s*(?P<msg>[^\n]+)',
        re.DOTALL | re.IGNORECASE,
    )

    def __init__(self, workspace_root: Optional[Union[str, Path]] = None):
        self.workspace_root = Path(workspace_root) if workspace_root else None

    def normalize(
        self,
        tool_name: str,
        raw_output: Any,
        exit_code: int = 0,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Observation:
        """
        Main entry point: strictly transforms raw output and parameters into a structured Observation.
        """
        params = parameters or {}
        output_str = self._stringify_output(raw_output)

        # 1. Check for permission denied or security block
        if "permission" in tool_name.lower() or ("denied" in output_str.lower() and exit_code != 0) or ("security" in tool_name.lower()):
            if "permission denied" in output_str.lower() or "not allowed" in output_str.lower() or "security" in tool_name.lower() or exit_code != 0:
                return Observation(
                    type=NormalizedType("security_issue"),
                    severity="error",
                    evidence=output_str.strip()[:300],
                    source="security",
                    raw_output=output_str,
                    metadata={"tool_name": tool_name, "parameters": params},
                )

        # 2. Test runner output (pytest / unittest / junit)
        if any(kw in tool_name.lower() for kw in ["test", "pytest", "unittest", "verify"]) or (
            "pytest" in output_str.lower() or "collected" in output_str.lower() and "passed" in output_str.lower()
        ):
            test_obs = self._parse_test_output(tool_name, output_str, exit_code, params)
            if test_obs:
                return test_obs

        # 3. Python stack traces & runtime exceptions
        if "traceback (most recent call last)" in output_str.lower():
            trace_obs = self._parse_traceback_output(output_str, exit_code, params)
            if trace_obs:
                return trace_obs

        # 4. Syntax / Indentation errors
        syntax_match = self._SYNTAX_ERROR_REGEX.search(output_str)
        if syntax_match:
            f_path = syntax_match.group("file")
            l_num = int(syntax_match.group("line"))
            exc_name = syntax_match.group("exc")
            msg = syntax_match.group("msg").strip()
            return Observation(
                type="compiler_error",
                file=self._relativize_path(f_path),
                line=l_num,
                severity="error",
                evidence=f"{exc_name}: {msg}",
                source="compiler",
                raw_output=output_str,
                metadata={"tool_name": tool_name},
            )

        # 5. Compiler & linter diagnostics (ruff, mypy, tsc, gcc)
        compiler_obs = self._parse_compiler_diagnostics(tool_name, output_str, exit_code, params)
        if compiler_obs:
            return compiler_obs

        # 6. Git commands
        if "git" in tool_name.lower() or params.get("command", "").startswith("git "):
            return self._parse_git_output(tool_name, output_str, exit_code, params)

        # 7. Browser DevTools operations
        if tool_name.startswith("browser_") or "browser" in tool_name:
            return self._parse_browser_output(tool_name, raw_output, output_str, params)

        # 8. Filesystem operations
        if any(kw in tool_name.lower() for kw in ["file", "read", "write", "patch", "edit"]):
            return self._parse_filesystem_output(tool_name, raw_output, output_str, params)

        # 9. General terminal/command execution
        return self._parse_generic_command_output(tool_name, output_str, exit_code, params)

    def _stringify_output(self, raw_output: Any) -> str:
        """Converts raw output (dict, str, list, etc.) into clean string representation."""
        if isinstance(raw_output, str):
            return raw_output
        if isinstance(raw_output, dict):
            # Extract common output fields if present
            if "output" in raw_output and isinstance(raw_output["output"], str):
                return raw_output["output"]
            if "content" in raw_output and isinstance(raw_output["content"], str):
                return raw_output["content"]
            if "stderr" in raw_output and raw_output["stderr"]:
                return f"{raw_output.get('stdout', '')}\n{raw_output['stderr']}"
            return json.dumps(raw_output, default=str)
        if isinstance(raw_output, (list, tuple)):
            return json.dumps(raw_output, default=str)
        return str(raw_output or "")

    def _relativize_path(self, path_str: Optional[str]) -> Optional[str]:
        """Normalizes path relative to workspace root if possible."""
        if not path_str:
            return None
        norm = path_str.replace("\\", "/").strip()
        if self.workspace_root:
            ws_str = str(self.workspace_root).replace("\\", "/").strip()
            if norm.startswith(ws_str):
                norm = norm[len(ws_str):].lstrip("/")
        return norm

    def _parse_traceback_output(
        self,
        output_str: str,
        exit_code: int,
        params: Dict[str, Any],
    ) -> Optional[Observation]:
        """Parses Python traceback using EmpiricalFaultLocalizer."""
        if EmpiricalFaultLocalizer:
            failure: Optional[ParsedFailure] = EmpiricalFaultLocalizer.parse_traceback(stderr=output_str)
            if failure:
                # Innermost workspace frame or top frame
                offending_file = self._relativize_path(failure.primary_offending_file)
                offending_line = failure.primary_offending_line
                symbol = None
                if failure.stack_frames:
                    target_frame = None
                    for frame in reversed(failure.stack_frames):
                        if frame.is_workspace_file:
                            target_frame = frame
                            break
                    if not target_frame:
                        target_frame = failure.stack_frames[-1]
                    symbol = target_frame.function_name

                obs_type = NormalizedType("failing_test") if failure.is_test_assertion else (
                    "compiler_error" if failure.is_syntax_or_import_error else NormalizedType("runtime_exception")
                )
                evidence = f"{failure.exception_class}: {failure.exception_message}" if failure.exception_message else failure.exception_class

                return Observation(
                    type=obs_type,
                    file=offending_file,
                    line=offending_line,
                    symbol=symbol,
                    severity="error",
                    evidence=evidence,
                    source="runtime",
                    raw_output=output_str,
                    metadata={"exception_class": failure.exception_class, "frames_count": len(failure.stack_frames)},
                )

        # Fallback regex parsing if EmpiricalFaultLocalizer is unavailable
        frame_match = re.findall(r'File\s+["\']([^"\']+)["\'],\s+line\s+(\d+)(?:,\s+in\s+([^\n]+))?', output_str)
        exc_match = re.search(r'(?:[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):[^\n]+', output_str)

        offending_file = self._relativize_path(frame_match[-1][0]) if frame_match else None
        offending_line = int(frame_match[-1][1]) if frame_match else None
        symbol = frame_match[-1][2] if frame_match and len(frame_match[-1]) > 2 else None
        evidence = exc_match.group(0).strip() if exc_match else "Python exception encountered"

        return Observation(
            type=NormalizedType("runtime_exception"),
            file=offending_file,
            line=offending_line,
            symbol=symbol,
            severity="error",
            evidence=evidence,
            source="runtime",
            raw_output=output_str,
        )

    def _parse_test_output(
        self,
        tool_name: str,
        output_str: str,
        exit_code: int,
        params: Dict[str, Any],
    ) -> Optional[Observation]:
        """Parses test runner output into structured test observation."""
        if TestExecutionParser:
            cases = TestExecutionParser.parse(stdout=output_str)
            if cases:
                failed_cases = [c for c in cases if c.status in ("FAILED", "ERROR")]
                passed_cases = [c for c in cases if c.status == "PASSED"]

                if failed_cases:
                    primary_fail = failed_cases[0]
                    file_path = self._relativize_path(primary_fail.test_file)
                    symbol = primary_fail.test_name or primary_fail.classname
                    evidence_msg = primary_fail.message or f"Test failed with status {primary_fail.status}"
                    # Truncate evidence cleanly
                    evidence_msg = evidence_msg.strip().split("\n")[0][:250]

                    return Observation(
                        type=NormalizedType("failing_test"),
                        file=file_path,
                        symbol=symbol,
                        severity="error",
                        evidence=f"{symbol} FAILED: {evidence_msg}",
                        source="test_runner",
                        raw_output=output_str,
                        metadata={
                            "total_tests": len(cases),
                            "failed_tests": len(failed_cases),
                            "passed_tests": len(passed_cases),
                            "test_id": primary_fail.test_id,
                        },
                    )
                else:
                    return Observation(
                        type="test_success",
                        severity="info",
                        evidence=f"All {len(passed_cases)} tests passed successfully.",
                        source="test_runner",
                        raw_output=output_str,
                        metadata={"total_tests": len(cases), "passed_tests": len(passed_cases)},
                    )

        # Check for pytest FAILURES block (e.g. non-verbose pytest output)
        # ______________________________ test_invalid_token _____________________________
        failure_header = re.search(r'___+\s*([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)*)\s*___+', output_str)
        if failure_header:
            test_sym = failure_header.group(1).strip()
            # Look for failure error line, e.g. E   ValueError: Invalid token
            err_line_match = re.search(r'(?:^|\n)E\s+([^\n]+)', output_str)
            err_msg = err_line_match.group(1).strip() if err_line_match else "Assertion failed"
            # Look for file and line: tests/test_auth.py:28: ValueError
            loc_match = re.search(r'(?:^|\n)\s*([^\s:]+\.py):(\d+):', output_str)
            fail_file = self._relativize_path(loc_match.group(1)) if loc_match else None
            fail_line = int(loc_match.group(2)) if loc_match else None

            return Observation(
                type=NormalizedType("failing_test"),
                file=fail_file,
                line=fail_line,
                symbol=test_sym,
                severity="error",
                evidence=f"{test_sym} FAILED: {err_msg[:200]}",
                source="test_runner",
                raw_output=output_str,
                metadata={"failing_test": test_sym},
            )

        # Regex fallback for pytest summary e.g. "=== 2 failed, 15 passed in 1.23s ==="
        pytest_summary = re.search(r'=+\s*([\d\w\s,]+)\s+in\s+[\d.]+s\s*=+', output_str)
        if pytest_summary:
            summary_text = pytest_summary.group(1).strip()
            severity = "error" if ("fail" in summary_text or exit_code != 0) else "info"
            obs_type = NormalizedType("failing_test") if severity == "error" else "test_success"
            return Observation(
                type=obs_type,
                severity=severity,
                evidence=f"Pytest run: {summary_text}",
                source="test_runner",
                raw_output=output_str,
            )

        return None

    def _parse_compiler_diagnostics(
        self,
        tool_name: str,
        output_str: str,
        exit_code: int,
        params: Dict[str, Any],
    ) -> Optional[Observation]:
        """Parses compiler/linter diagnostics (e.g. gcc, clang, ruff, mypy, tsc)."""
        matches = list(self._COMPILER_DIAGNOSTIC_REGEX.finditer(output_str))
        if not matches:
            return None

        # Take first error match or first match
        target_match = None
        for m in matches:
            sev = (m.group("severity") or "").lower()
            if sev in ("error", "fatal"):
                target_match = m
                break
        if not target_match:
            target_match = matches[0]

        file_path = self._relativize_path(target_match.group("file"))
        line_str = target_match.group("line") or target_match.group("ts_line")
        line_num = int(line_str) if line_str else None
        sev_str = (target_match.group("severity") or ("error" if exit_code != 0 else "info")).lower()
        msg = target_match.group("msg").strip()

        # Extract symbol if mentioned in diagnostic, e.g. `cannot find name 'xyz'`
        sym_match = re.search(r"['`]([a-zA-Z0-9_]+)['`]", msg)
        symbol = sym_match.group(1) if sym_match else None

        is_linter = any(l in tool_name.lower() or l in str(params).lower() for l in ["ruff", "eslint", "lint", "flake8", "checkstyle"])
        if is_linter:
            diag_type = NormalizedType("lint_error")
            source = "linter"
        elif sev_str in ("error", "fatal"):
            diag_type = "compiler_error"
            source = "compiler"
        else:
            diag_type = NormalizedType("lint_error")
            source = "compiler"

        return Observation(
            type=diag_type,
            file=file_path,
            line=line_num,
            symbol=symbol,
            severity="error" if sev_str in ("error", "fatal") else "warning",
            evidence=msg[:250],
            source=source,
            raw_output=output_str,
            metadata={"diagnostics_count": len(matches)},
        )

    def _parse_git_output(
        self,
        tool_name: str,
        output_str: str,
        exit_code: int,
        params: Dict[str, Any],
    ) -> Observation:
        """Parses git command outputs into structured status."""
        cmd = params.get("command", tool_name).strip()
        if exit_code != 0:
            return Observation(
                type="git_error",
                severity="error",
                evidence=output_str.strip().split("\n")[-1][:200],
                source="git",
                raw_output=output_str,
                metadata={"command": cmd},
            )

        # Check for status / branch changes
        lines = [line.strip() for line in output_str.splitlines() if line.strip()]
        modified_files = []
        for line in lines:
            if line.startswith("M ") or line.startswith("A ") or line.startswith("D "):
                modified_files.append(line.split()[-1])

        evidence = f"Git operation completed: {lines[0]}" if lines else "Git operation succeeded."
        return Observation(
            type="git_status",
            severity="info",
            evidence=evidence[:200],
            source="git",
            raw_output=output_str,
            metadata={"command": cmd, "modified_files": modified_files},
        )

    def _parse_browser_output(
        self,
        tool_name: str,
        raw_output: Any,
        output_str: str,
        params: Dict[str, Any],
    ) -> Observation:
        """Normalizes Chrome DevTools / Browser MCP output."""
        if tool_name == "browser_console":
            logs = raw_output if isinstance(raw_output, list) else []
            errors = [entry for entry in logs if isinstance(entry, dict) and entry.get("level") == "error"]
            if errors:
                first_err = errors[0]
                return Observation(
                    type=NormalizedType("browser_console"),
                    severity="error",
                    evidence=f"Console Error: {first_err.get('text', '')[:200]}",
                    source="browser",
                    raw_output=output_str,
                    metadata={"total_errors": len(errors), "entry": first_err},
                )
            return Observation(
                type=NormalizedType("browser_console"),
                severity="info",
                evidence=f"Browser console inspected ({len(logs)} logs). No errors found.",
                source="browser",
                raw_output=output_str,
            )

        if tool_name == "browser_network":
            requests = raw_output if isinstance(raw_output, list) else []
            failed_reqs = [r for r in requests if isinstance(r, dict) and int(r.get("status", 200)) >= 400]
            if failed_reqs:
                first_fail = failed_reqs[0]
                return Observation(
                    type=NormalizedType("network_failure"),
                    severity="error",
                    evidence=f"Network Error {first_fail.get('status')}: {first_fail.get('url', '')[:150]}",
                    source="network",
                    raw_output=output_str,
                    metadata={"failed_requests": len(failed_reqs)},
                )
            return Observation(
                type="browser_event",
                severity="info",
                evidence="Browser network inspected. All requests nominal.",
                source="network",
                raw_output=output_str,
            )

        return Observation(
            type="browser_event",
            severity="info",
            evidence=f"Browser action {tool_name} completed.",
            source="browser",
            raw_output=output_str,
            metadata={"tool_name": tool_name, "selector": params.get("selector")},
        )

    def _parse_filesystem_output(
        self,
        tool_name: str,
        raw_output: Any,
        output_str: str,
        params: Dict[str, Any],
    ) -> Observation:
        """Normalizes file read, write, and patch operations."""
        target_file = params.get("target_file") or params.get("path") or params.get("filepath") or params.get("file")
        rel_file = self._relativize_path(target_file)

        is_write = any(w in tool_name.lower() for w in ["write", "edit", "patch", "replace"])
        obs_type = "file_mutation" if is_write else "file_content"

        if isinstance(raw_output, dict) and raw_output.get("error"):
            return Observation(
                type="file_error",
                file=rel_file,
                severity="error",
                evidence=str(raw_output["error"])[:250],
                source="filesystem",
                raw_output=output_str,
            )

        line_count = len(output_str.splitlines())
        evidence = f"File {rel_file or 'target'} {'updated' if is_write else 'inspected'} ({line_count} lines)."
        return Observation(
            type=obs_type,
            file=rel_file,
            severity="info",
            evidence=evidence,
            source="filesystem",
            raw_output=output_str,
            metadata={"lines": line_count, "is_mutation": is_write},
        )

    def _parse_generic_command_output(
        self,
        tool_name: str,
        output_str: str,
        exit_code: int,
        params: Dict[str, Any],
    ) -> Observation:
        """Fallback normalization for generic commands."""
        clean_lines = [l.strip() for l in output_str.splitlines() if l.strip()]
        if exit_code != 0:
            evidence = clean_lines[-1][:200] if clean_lines else f"Command exited with non-zero code {exit_code}."
            return Observation(
                type="command_failure",
                severity="error",
                evidence=evidence,
                source="terminal",
                raw_output=output_str,
                metadata={"exit_code": exit_code, "tool_name": tool_name},
            )

        evidence = clean_lines[0][:200] if clean_lines else "Command completed successfully."
        return Observation(
            type="command_output",
            severity="info",
            evidence=evidence,
            source="terminal",
            raw_output=output_str,
            metadata={"exit_code": 0, "tool_name": tool_name},
        )
