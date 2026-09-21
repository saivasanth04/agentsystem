"""
Empirical Root Cause Inspection & Fault Localization Engine.
Provides deterministic stack trace parsing, AST syntax verification, defect locus identification,
and adversarial arbitration to eliminate blind trust in LLM reviewer blame attributions.
"""
import ast
from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class TracebackFrame:
    """Represents a single parsed frame from an execution stack trace."""
    filename: str
    line_number: int
    function_name: str
    code_line: str = ""
    is_test_file: bool = False
    is_workspace_file: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "line_number": self.line_number,
            "function_name": self.function_name,
            "code_line": self.code_line,
            "is_test_file": self.is_test_file,
            "is_workspace_file": self.is_workspace_file,
        }


@dataclass
class ParsedFailure:
    """Structured empirical breakdown of an observed failure."""
    exception_class: str
    exception_message: str
    stack_frames: List[TracebackFrame] = field(default_factory=list)
    primary_offending_file: Optional[str] = None
    primary_offending_line: Optional[int] = None
    is_test_assertion: bool = False
    is_uncaught_runtime_exception: bool = False
    is_syntax_or_import_error: bool = False
    is_missing_dependency: bool = False
    missing_dependency: Optional[Any] = None
    raw_stderr: str = ""
    exit_code: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exception_class": self.exception_class,
            "exception_message": self.exception_message,
            "stack_frames": [f.to_dict() for f in self.stack_frames],
            "primary_offending_file": self.primary_offending_file,
            "primary_offending_line": self.primary_offending_line,
            "is_test_assertion": self.is_test_assertion,
            "is_uncaught_runtime_exception": self.is_uncaught_runtime_exception,
            "is_syntax_or_import_error": self.is_syntax_or_import_error,
            "is_missing_dependency": self.is_missing_dependency,
            "missing_dependency": self.missing_dependency.to_dict() if hasattr(self.missing_dependency, "to_dict") else self.missing_dependency,
            "exit_code": self.exit_code,
        }


@dataclass
class FaultLocus:
    """Localized defect attribution grounded in empirical artifacts."""
    primary_file: Optional[str]
    locus_type: str  # APPLICATION_CODE | TEST_CODE | SPEC_OR_CONFIG | SYSTEM_OR_ENVIRONMENT
    ground_truth_attribution: str  # CODER | TESTER | SPECIFICATION | SYSTEM
    confidence: float
    rationale: str
    parsed_failure: Optional[ParsedFailure] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_file": self.primary_file,
            "locus_type": self.locus_type,
            "ground_truth_attribution": self.ground_truth_attribution,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "parsed_failure": self.parsed_failure.to_dict() if self.parsed_failure else None,
        }


class EmpiricalFaultLocalizer:
    """
    Deterministic failure analyzer that parses stack traces, linters, AST errors,
    and test runner outputs to pinpoint the exact defect locus without LLM guesswork.
    """

    # Matches standard python traceback frames:
    #   File "path/to/file.py", line 42, in func_name
    _FRAME_REGEX = re.compile(
        r'File\s+["\'](?P<file>[^"\']+)["\'],\s+line\s+(?P<line>\d+)(?:,\s+in\s+(?P<func>[^\n]+))?'
    )

    # Matches pytest concise frames:
    #   tests/test_calc.py:25: in test_add
    #   path/to/file.py:10: in divide
    _PYTEST_FRAME_REGEX = re.compile(
        r'^\s*(?P<file>[^\s:]+\.py):(?P<line>\d+):\s+in\s+(?P<func>[^\n]+)',
        re.MULTILINE
    )

    # Matches standard exception tail:
    #   ZeroDivisionError: division by zero
    #   E   AssertionError: assert 1 == 2
    _EXCEPTION_REGEX = re.compile(
        r'(?:^|\n)(?:E\s+)?(?P<exc>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning|Interrupt|Exit)):\s*(?P<msg>[^\n]*)'
    )

    @classmethod
    def is_test_path(cls, path_str: str) -> bool:
        """Determines if a file path is a test file."""
        p_lower = path_str.replace("\\", "/").lower()
        parts = p_lower.split("/")
        filename = parts[-1] if parts else ""
        return (
            "test_" in filename
            or filename.endswith("_test.py")
            or "tests/" in p_lower
            or "/test/" in p_lower
            or "testing/" in p_lower
        )

    @classmethod
    def parse_traceback(cls, stderr: str, stdout: str = "") -> Optional[ParsedFailure]:
        """
        Extracts structured stack frames and exception details from process output.
        """
        combined = f"{stderr}\n{stdout}".strip()
        if not combined:
            return None

        frames: List[TracebackFrame] = []

        # 1. Parse standard Python frames
        for match in cls._FRAME_REGEX.finditer(combined):
            fname = match.group("file").strip()
            line_no = int(match.group("line"))
            func_name = (match.group("func") or "unknown").strip()
            is_test = cls.is_test_path(fname)
            is_ws = not any(p in fname.lower() for p in ["site-packages", "dist-packages", "lib/python", "python3."])
            frames.append(TracebackFrame(
                filename=fname,
                line_number=line_no,
                function_name=func_name,
                is_test_file=is_test,
                is_workspace_file=is_ws,
            ))

        # 2. Parse pytest-style frames if standard frames didn't match
        if not frames:
            for match in cls._PYTEST_FRAME_REGEX.finditer(combined):
                fname = match.group("file").strip()
                line_no = int(match.group("line"))
                func_name = match.group("func").strip()
                is_test = cls.is_test_path(fname)
                frames.append(TracebackFrame(
                    filename=fname,
                    line_number=line_no,
                    function_name=func_name,
                    is_test_file=is_test,
                    is_workspace_file=True,
                ))

        # 3. Parse Exception Class & Message
        exc_class = "UnknownError"
        exc_msg = ""
        exc_matches = list(cls._EXCEPTION_REGEX.finditer(combined))
        if exc_matches:
            last_match = exc_matches[-1]
            exc_class = last_match.group("exc").strip()
            exc_msg = last_match.group("msg").strip()
        elif "syntaxerror" in combined.lower():
            exc_class = "SyntaxError"
            exc_msg = "Invalid syntax in source file"
        elif "assertionerror" in combined.lower() or "assert " in combined:
            exc_class = "AssertionError"
            exc_msg = "Test assertion failed"

        if not frames and exc_class == "UnknownError":
            # Check for generic failure keywords
            if "failed" not in combined.lower() and "error" not in combined.lower():
                return None

        # Determine primary offending frame (innermost workspace frame)
        primary_file = None
        primary_line = None
        ws_frames = [f for f in frames if f.is_workspace_file]
        target_frames = ws_frames if ws_frames else frames

        if target_frames:
            primary_file = target_frames[-1].filename
            primary_line = target_frames[-1].line_number

        is_test_assert = bool(
            exc_class in ("AssertionError", "pytest.fail")
            or (primary_file and cls.is_test_path(primary_file) and "assert" in combined.lower())
        )
        is_missing_dep = bool(
            exc_class in ("ModuleNotFoundError",)
            or "no module named" in combined.lower()
            or "cannot find module" in combined.lower()
        )
        is_syntax_or_import = bool(
            not is_missing_dep
            and (
                exc_class in ("SyntaxError", "ImportError", "IndentationError", "TabError")
                or "syntaxerror" in combined.lower()
                or "cannot import name" in combined.lower()
            )
        )
        is_uncaught_runtime = bool(
            not is_test_assert
            and not is_syntax_or_import
            and not is_missing_dep
            and primary_file
            and not cls.is_test_path(primary_file)
        )

        return ParsedFailure(
            exception_class=exc_class,
            exception_message=exc_msg,
            stack_frames=frames,
            primary_offending_file=primary_file,
            primary_offending_line=primary_line,
            is_test_assertion=is_test_assert,
            is_uncaught_runtime_exception=is_uncaught_runtime,
            is_syntax_or_import_error=is_syntax_or_import,
            is_missing_dependency=is_missing_dep,
            raw_stderr=stderr,
        )

    @classmethod
    def check_workspace_syntax(cls, workspace_root: Path) -> List[Dict[str, Any]]:
        """
        Scans all Python files in the workspace for AST syntax errors.
        Returns a list of files that fail ast.parse.
        """
        syntax_errors = []
        if not workspace_root.exists():
            return []

        for py_path in workspace_root.rglob("*.py"):
            if any(part.startswith(".") for part in py_path.parts):
                continue
            try:
                content = py_path.read_text(encoding="utf-8", errors="ignore")
                ast.parse(content, filename=str(py_path))
            except SyntaxError as se:
                syntax_errors.append({
                    "filename": str(py_path.relative_to(workspace_root) if py_path.is_relative_to(workspace_root) else py_path),
                    "full_path": str(py_path),
                    "line": se.lineno or 1,
                    "offset": se.offset or 0,
                    "msg": str(se),
                    "is_test": cls.is_test_path(str(py_path)),
                })
            except Exception:
                pass
        return syntax_errors

    @classmethod
    def localize_fault(
        cls,
        execution_stderr: str = "",
        execution_stdout: str = "",
        failed_tasks: Optional[List[Any]] = None,
        verification_reports: Optional[List[Dict[str, Any]]] = None,
        workspace_dir: Optional[str] = None,
        review_info: Optional[Dict[str, Any]] = None,
    ) -> FaultLocus:
        """
        Deterministically maps empirical failure signals to the responsible defect locus and agent.
        """
        # 1. Check workspace AST syntax on disk first
        if workspace_dir:
            ws_root = Path(workspace_dir)
            syntax_errors = cls.check_workspace_syntax(ws_root)
            if syntax_errors:
                first_syn = syntax_errors[0]
                is_test = first_syn["is_test"]
                target_agent = "TESTER" if is_test else "CODER"
                locus = "TEST_CODE" if is_test else "APPLICATION_CODE"
                parsed = ParsedFailure(
                    exception_class="SyntaxError",
                    exception_message=first_syn["msg"],
                    primary_offending_file=first_syn["filename"],
                    primary_offending_line=first_syn["line"],
                    is_syntax_or_import_error=True,
                )
                return FaultLocus(
                    primary_file=first_syn["filename"],
                    locus_type=locus,
                    ground_truth_attribution=target_agent,
                    confidence=0.98,
                    rationale=f"Deterministic AST syntax error detected in {first_syn['filename']} at line {first_syn['line']}: {first_syn['msg']}",
                    parsed_failure=parsed,
                )

        # 1b. Check for missing dependency (ModuleNotFoundError / Cannot find module)
        try:
            from .dependency_healer import DependencyHealingEngine
            dep = DependencyHealingEngine.identify_missing_dependency(stderr=execution_stderr, stdout=execution_stdout)
            if not dep and failed_tasks:
                for task in failed_tasks:
                    attempts = getattr(task, "attempts", [])
                    if attempts:
                        latest = attempts[-1]
                        err_str = "\n".join(getattr(latest, "errors", []))
                        cand_dep = DependencyHealingEngine.identify_missing_dependency(stderr=err_str)
                        if cand_dep:
                            dep = cand_dep
                            break
            if dep:
                return FaultLocus(
                    primary_file=None,
                    locus_type="MISSING_DEPENDENCY",
                    ground_truth_attribution="DEPENDENCY_MANAGER",
                    confidence=0.99,
                    rationale=f"Missing dependency detected: module '{dep.module_name}' (package: '{dep.package_name}'). Requires installation via package manager rather than code refactoring.",
                    parsed_failure=ParsedFailure(
                        exception_class="ModuleNotFoundError",
                        exception_message=dep.raw_error,
                        is_missing_dependency=True,
                        missing_dependency=dep,
                        raw_stderr=execution_stderr,
                    ),
                )
        except Exception:
            pass

        # 2. Check for infrastructure or environment mismatch in process outputs
        combined_text = f"{execution_stderr}\n{execution_stdout}".lower()
        if any(term in combined_text for term in [
            "unexpected keyword argument",
            "got an unexpected keyword argument",
            "agentregistry",
            "sqlite3.operationalerror",
            "command not found",
            "is not recognized as an internal or external command",
            "permission denied",
        ]):
            return FaultLocus(
                primary_file=None,
                locus_type="SYSTEM_OR_ENVIRONMENT",
                ground_truth_attribution="SYSTEM",
                confidence=0.95,
                rationale="Infrastructure, framework, or process execution environment error detected.",
                parsed_failure=ParsedFailure(
                    exception_class="InfrastructureError",
                    exception_message="Runtime or command execution environment mismatch",
                    raw_stderr=execution_stderr,
                ),
            )

        # 3. Parse traceback from stderr/stdout
        parsed = cls.parse_traceback(execution_stderr, execution_stdout)

        # If no traceback in top-level stderr, inspect failed task attempts in DAG
        if (not parsed or parsed.exception_class == "UnknownError") and failed_tasks:
            for task in failed_tasks:
                attempts = getattr(task, "attempts", [])
                if attempts:
                    latest = attempts[-1]
                    err_str = "\n".join(getattr(latest, "errors", []))
                    candidate_parsed = cls.parse_traceback(err_str)
                    if candidate_parsed and candidate_parsed.exception_class != "UnknownError":
                        parsed = candidate_parsed
                        break

        # 4. Analyze parsed stack trace
        if parsed and parsed.primary_offending_file:
            off_file = parsed.primary_offending_file
            is_test = cls.is_test_path(off_file)

            # Case A: Syntax or Import Error in application code
            if parsed.is_syntax_or_import_error:
                target_agent = "TESTER" if is_test else "CODER"
                locus = "TEST_CODE" if is_test else "APPLICATION_CODE"
                return FaultLocus(
                    primary_file=off_file,
                    locus_type=locus,
                    ground_truth_attribution=target_agent,
                    confidence=0.95,
                    rationale=f"Syntax or import error ({parsed.exception_class}) located in {off_file}:{parsed.primary_offending_line}",
                    parsed_failure=parsed,
                )

            # Case B: Uncaught Runtime Exception in application code (e.g. ZeroDivisionError, KeyError)
            # Even if invoked via a test, the defect locus is the application file where the exception was raised
            if not is_test and parsed.is_uncaught_runtime_exception:
                return FaultLocus(
                    primary_file=off_file,
                    locus_type="APPLICATION_CODE",
                    ground_truth_attribution="CODER",
                    confidence=0.95,
                    rationale=f"Uncaught runtime exception {parsed.exception_class}: '{parsed.exception_message}' in application file {off_file}:{parsed.primary_offending_line}",
                    parsed_failure=parsed,
                )

            # Case C: Test Assertion Failure (AssertionError)
            if parsed.is_test_assertion or (is_test and parsed.exception_class == "AssertionError"):
                # Check whether application code threw an error deeper in the stack
                app_frames = [f for f in parsed.stack_frames if f.is_workspace_file and not f.is_test_file]
                if app_frames:
                    app_file = app_frames[-1].filename
                    return FaultLocus(
                        primary_file=app_file,
                        locus_type="APPLICATION_CODE",
                        ground_truth_attribution="CODER",
                        confidence=0.90,
                        rationale=f"Test assertion failed due to underlying logic in application file {app_file}:{app_frames[-1].line_number}",
                        parsed_failure=parsed,
                    )
                else:
                    # Test failed at its own assertion without application stack trace
                    return FaultLocus(
                        primary_file=off_file,
                        locus_type="TEST_CODE",
                        ground_truth_attribution="CODER",
                        confidence=0.80,
                        rationale=f"Test assertion failed in {off_file}:{parsed.primary_offending_line}. Requires code implementation to satisfy test contract.",
                        parsed_failure=parsed,
                    )

        # 5. Check GroundTruthVerificationMatrix / verification reports
        if verification_reports:
            for rep in verification_reports:
                if isinstance(rep, dict):
                    # Tautological assertions in test suite
                    has_tautologies = (
                        rep.get("has_tautologies") is True
                        or (isinstance(rep.get("tautological_warnings"), list) and len(rep["tautological_warnings"]) > 0)
                        or (isinstance(rep.get("tautologies"), list) and len(rep["tautologies"]) > 0)
                        or (isinstance(rep.get("tautological_count"), int) and rep["tautological_count"] > 0)
                        or "critical warning: tautological" in str(rep).lower()
                    )
                    if has_tautologies:
                        return FaultLocus(
                            primary_file=None,
                            locus_type="TEST_CODE",
                            ground_truth_attribution="TESTER",
                            confidence=0.92,
                            rationale="Tautological or vacuous test assertions detected in test suite. Requires test rewrite.",
                            parsed_failure=parsed,
                        )
                    # Spec traceability failure
                    if rep.get("spec_traceability_failed") or "uncovered acceptance criteria" in str(rep).lower():
                        return FaultLocus(
                            primary_file=None,
                            locus_type="TEST_CODE",
                            ground_truth_attribution="TESTER",
                            confidence=0.90,
                            rationale="Acceptance criteria unverified or missing test coverage.",
                            parsed_failure=parsed,
                        )

        # 6. Fallback default
        return FaultLocus(
            primary_file=None,
            locus_type="APPLICATION_CODE",
            ground_truth_attribution="CODER",
            confidence=0.60,
            rationale="Default defect attribution to CODER in absence of conclusive stack frames.",
            parsed_failure=parsed,
        )


class AdversarialAttributionArbiter:
    """
    Cross-examines LLM-suggested target_agent_for_fix against empirical ground truth.
    Deterministically vetoes and overrides blame hallucinations.
    """

    @classmethod
    def arbitrate(
        cls,
        llm_suggested_agent: Optional[str],
        empirical_locus: FaultLocus,
    ) -> Tuple[str, bool, str]:
        """
        Returns:
            (final_agent, was_overridden, rationale)
        """
        raw_llm = (llm_suggested_agent or "CODER").upper().strip()
        gt_agent = empirical_locus.ground_truth_attribution.upper().strip()
        confidence = empirical_locus.confidence

        # If empirical ground truth confidence is high and disagrees with LLM, veto the LLM!
        if confidence >= 0.85 and raw_llm != gt_agent:
            # Deterministic Veto Rules:
            # Rule 1: Syntax / Import / AST error in source code CANNOT be blamed on SPEC or ARCHITECTURE
            if empirical_locus.locus_type == "APPLICATION_CODE" and gt_agent == "CODER":
                if raw_llm in ("SPECIFICATION", "ARCHITECTURE", "TESTER"):
                    return (
                        "CODER",
                        True,
                        f"Adversarial Veto: Reviewer attributed fix to {raw_llm}, but empirical fault localizer proved code defect in {empirical_locus.primary_file or 'source code'} ({empirical_locus.rationale}). Overriding to CODER."
                    )

            # Rule 2: Tautological test or test AST crash CANNOT be blamed on CODER
            if empirical_locus.locus_type == "TEST_CODE" and gt_agent == "TESTER":
                if raw_llm in ("CODER", "SPECIFICATION"):
                    return (
                        "TESTER",
                        True,
                        f"Adversarial Veto: Reviewer attributed fix to {raw_llm}, but empirical ground truth isolated defect to test suite ({empirical_locus.rationale}). Overriding to TESTER."
                    )

            # Rule 3: Infrastructure / Environment error MUST be routed to SYSTEM circuit-breaker
            if empirical_locus.locus_type == "SYSTEM_OR_ENVIRONMENT" and gt_agent == "SYSTEM":
                if raw_llm != "SYSTEM":
                    return (
                        "SYSTEM",
                        True,
                        f"Adversarial Veto: Reviewer attributed fix to {raw_llm}, but empirical evidence confirms an environment/framework failure ({empirical_locus.rationale}). Overriding to SYSTEM."
                    )

        # In all other cases, accept the LLM suggestion or align with ground truth
        return (raw_llm, False, "LLM attribution validated against empirical locus.")


__all__ = [
    "TracebackFrame",
    "ParsedFailure",
    "FaultLocus",
    "EmpiricalFaultLocalizer",
    "AdversarialAttributionArbiter",
]
