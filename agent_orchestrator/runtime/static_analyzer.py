"""
StaticAnalyzer: Polyglot Multi-Tool Discovery, Targeted Static Analysis, and SAST Engine.
Detects project configurations (ruff, mypy, pyright, eslint, tsc, semgrep, clippy, govet),
executes targeted checks on modified files, and aggregates structured LSP diagnostics.
"""
from dataclasses import dataclass, field
from enum import Enum
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from .diagnostic import Diagnostic, DiagnosticParser, DiagnosticSeverity
from .project_detector import ProjectEnvironment, ProjectEnvironmentDetector
try:
    from ..security.sandbox import BaseExecutionSandbox, create_sandbox
except (ImportError, ValueError):
    from agent_orchestrator.security.sandbox import BaseExecutionSandbox, create_sandbox


class StaticToolCategory(str, Enum):
    LINTER = "LINTER"
    TYPE_CHECKER = "TYPE_CHECKER"
    COMPILER = "COMPILER"
    SAST = "SAST"


@dataclass
class StaticTool:
    name: str
    category: StaticToolCategory
    language: str
    command_template: str  # e.g. "ruff check {files}" or "mypy {files}"
    whole_repo_command: str  # e.g. "ruff check ."
    supported_extensions: List[str]
    config_files: List[str] = field(default_factory=list)
    supports_json: bool = False
    json_flag: Optional[str] = None


@dataclass
class StaticAnalysisResult:
    tool_name: str
    category: StaticToolCategory
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    diagnostics: List[Diagnostic] = field(default_factory=list)
    errors: List[Diagnostic] = field(default_factory=list)
    warnings: List[Diagnostic] = field(default_factory=list)
    skipped: bool = False
    skip_reason: Optional[str] = None

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0 and not (self.exit_code != 0 and not self.skipped and not self.diagnostics)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "category": self.category.value if isinstance(self.category, StaticToolCategory) else str(self.category),
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "errors": [d.to_dict() for d in self.errors],
            "warnings": [d.to_dict() for d in self.warnings],
            "passed": self.passed,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


@dataclass
class StaticAnalysisReport:
    passed: bool
    target_files: List[str] = field(default_factory=list)
    diagnostics: List[Diagnostic] = field(default_factory=list)
    errors: List[Diagnostic] = field(default_factory=list)
    warnings: List[Diagnostic] = field(default_factory=list)
    results: Dict[str, StaticAnalysisResult] = field(default_factory=dict)
    total_duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "target_files": self.target_files,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "errors": [d.to_dict() for d in self.errors],
            "warnings": [d.to_dict() for d in self.warnings],
            "results": {k: v.to_dict() for k, v in self.results.items()},
            "total_duration_seconds": self.total_duration_seconds,
        }

    def summary(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        lines = [
            f"Static Analysis: {status} ({len(self.errors)} errors, {len(self.warnings)} warnings, {self.total_duration_seconds}s)"
        ]
        for name, res in self.results.items():
            if res.skipped:
                lines.append(f"  - [{name}] SKIPPED: {res.skip_reason}")
            else:
                st = "PASS" if res.passed else "FAIL"
                lines.append(f"  - [{name}] {st} ({len(res.errors)} errs, {len(res.warnings)} warns, {res.duration_seconds}s)")
        if self.errors:
            lines.append("Errors:")
            for err in self.errors[:10]:
                lines.append(f"  * {err.to_string()}")
            if len(self.errors) > 10:
                lines.append(f"  ... and {len(self.errors) - 10} more errors.")
        if self.warnings:
            lines.append("Warnings:")
            for warn in self.warnings[:5]:
                lines.append(f"  * {warn.to_string()}")
            if len(self.warnings) > 5:
                lines.append(f"  ... and {len(self.warnings) - 5} more warnings.")
        return "\n".join(lines)


class StaticAnalyzer:
    """
    Polyglot static analysis engine.
    Detects project configuration, selects optimal tools with graceful fallbacks,
    and runs targeted file checks.
    """

    KNOWN_TOOLS: List[StaticTool] = [
        # Python
        StaticTool(
            name="ruff",
            category=StaticToolCategory.LINTER,
            language="python",
            command_template="ruff check {files}",
            whole_repo_command="ruff check .",
            supported_extensions=[".py"],
            config_files=["ruff.toml", ".ruff.toml", "pyproject.toml"],
            supports_json=True,
            json_flag="--output-format=json",
        ),
        StaticTool(
            name="mypy",
            category=StaticToolCategory.TYPE_CHECKER,
            language="python",
            command_template="mypy {files}",
            whole_repo_command="mypy .",
            supported_extensions=[".py"],
            config_files=["mypy.ini", ".mypy.ini", "setup.cfg", "pyproject.toml"],
        ),
        StaticTool(
            name="pyright",
            category=StaticToolCategory.TYPE_CHECKER,
            language="python",
            command_template="pyright {files}",
            whole_repo_command="pyright .",
            supported_extensions=[".py"],
            config_files=["pyrightconfig.json", "pyproject.toml"],
            supports_json=True,
            json_flag="--outputjson",
        ),
        # JS / TS
        StaticTool(
            name="eslint",
            category=StaticToolCategory.LINTER,
            language="javascript",
            command_template="npx eslint {files}",
            whole_repo_command="npx eslint .",
            supported_extensions=[".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"],
            config_files=[
                ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json",
                ".eslintrc.yaml", ".eslintrc.yml", "eslint.config.js", "eslint.config.mjs"
            ],
            supports_json=True,
            json_flag="--format=json",
        ),
        StaticTool(
            name="tsc",
            category=StaticToolCategory.TYPE_CHECKER,
            language="typescript",
            command_template="npx tsc --noEmit",
            whole_repo_command="npx tsc --noEmit",
            supported_extensions=[".ts", ".tsx"],
            config_files=["tsconfig.json"],
        ),
        # Rust
        StaticTool(
            name="clippy",
            category=StaticToolCategory.LINTER,
            language="rust",
            command_template="cargo clippy -- -D warnings",
            whole_repo_command="cargo clippy -- -D warnings",
            supported_extensions=[".rs"],
            config_files=["Cargo.toml", "Clippy.toml"],
        ),
        # Go
        StaticTool(
            name="govet",
            category=StaticToolCategory.LINTER,
            language="go",
            command_template="go vet {files}",
            whole_repo_command="go vet ./...",
            supported_extensions=[".go"],
            config_files=["go.mod"],
        ),
        # SAST / Security
        StaticTool(
            name="semgrep",
            category=StaticToolCategory.SAST,
            language="polyglot",
            command_template="semgrep scan --config auto {files}",
            whole_repo_command="semgrep scan --config auto .",
            supported_extensions=[".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".java", ".c", ".cpp"],
            config_files=[".semgrep.yml", ".semgrep.yaml", "semgrep.yml", "semgrep.yaml"],
            supports_json=True,
            json_flag="--json",
        ),
    ]

    def __init__(
        self,
        workspace: Any = None,
        workspace_dir: Any = None,
        sandbox: Optional[BaseExecutionSandbox] = None,
        env: Optional[ProjectEnvironment] = None,
    ):
        ws = workspace if workspace is not None else workspace_dir
        self.workspace = ws
        self.root_dir = Path(ws.root_dir if hasattr(ws, "root_dir") else ws).resolve()
        self.sandbox = sandbox or create_sandbox(self.root_dir)
        self.env = env or ProjectEnvironmentDetector.detect(self.root_dir)

    def detect_active_tools(self) -> List[StaticTool]:
        """
        Discovers appropriate static analysis tools for the workspace based on:
        1. Explicit config files in the project
        2. Detected primary language in ProjectEnvironment
        3. Toolchain availability
        """
        active: List[StaticTool] = []
        project_lang = self.env.language.lower()

        # Check for SAST configuration
        has_semgrep_config = any((self.root_dir / cfg).is_file() for cfg in [".semgrep.yml", ".semgrep.yaml", "semgrep.yml", "semgrep.yaml"])
        if has_semgrep_config:
            active.append(next(t for t in self.KNOWN_TOOLS if t.name == "semgrep"))

        for tool in self.KNOWN_TOOLS:
            if tool.name == "semgrep" and has_semgrep_config:
                continue

            # Check if project has explicit config file for this tool
            has_explicit_config = False
            for cfg in tool.config_files:
                cfg_path = self.root_dir / cfg
                if cfg_path.is_file():
                    if cfg == "pyproject.toml":
                        try:
                            content = cfg_path.read_text(encoding="utf-8", errors="replace")
                            if f"[tool.{tool.name}]" in content:
                                has_explicit_config = True
                                break
                        except Exception:
                            pass
                    else:
                        has_explicit_config = True
                        break

            # Language match or explicit config
            lang_match = (
                tool.language == project_lang
                or (tool.language == "javascript" and project_lang in ("javascript", "typescript"))
                or (tool.language == "typescript" and project_lang == "typescript")
            )

            if has_explicit_config or lang_match:
                # Avoid duplicate type checker if both pyright and mypy match unless explicitly configured
                if tool.name == "mypy" and any(t.name == "pyright" for t in active):
                    continue
                if tool.name == "pyright" and any(t.name == "mypy" for t in active) and not has_explicit_config:
                    continue
                active.append(tool)

        return active

    def run_analysis(
        self,
        files: Optional[List[str]] = None,
        tools: Optional[List[str]] = None,
        categories: Optional[List[Union[StaticToolCategory, str]]] = None,
        timeout: int = 60,
    ) -> StaticAnalysisReport:
        """
        Executes static analysis tools.
        If files are specified, builds targeted invocations.
        """
        start_time = time.time()
        active_tools = self.detect_active_tools()

        if tools:
            tool_names = [t.lower().strip() for t in tools]
            active_tools = [t for t in active_tools if t.name.lower() in tool_names]

        if categories:
            cat_strs = [c.value if isinstance(c, StaticToolCategory) else str(c).upper() for c in categories]
            active_tools = [t for t in active_tools if t.category.value in cat_strs]

        results: Dict[str, StaticAnalysisResult] = {}
        all_diagnostics: List[Diagnostic] = []
        all_errors: List[Diagnostic] = []
        all_warnings: List[Diagnostic] = []

        target_files = files or []
        # Normalize target files relative to root_dir
        norm_files = []
        for f in target_files:
            fp = Path(f)
            if fp.is_absolute():
                try:
                    norm_files.append(str(fp.relative_to(self.root_dir)).replace("\\", "/"))
                except ValueError:
                    norm_files.append(str(f).replace("\\", "/"))
            else:
                norm_files.append(str(f).replace("\\", "/"))

        for tool in active_tools:
            tool_start = time.time()
            # Filter target files relevant to this tool
            tool_files = [f for f in norm_files if any(f.endswith(ext) for ext in tool.supported_extensions)]
            if norm_files and not tool_files and tool.category != StaticToolCategory.COMPILER:
                # Skip tool if none of the target files match its extensions
                results[tool.name] = StaticAnalysisResult(
                    tool_name=tool.name,
                    category=tool.category,
                    command="N/A",
                    exit_code=0,
                    stdout="",
                    stderr="",
                    duration_seconds=0.0,
                    skipped=True,
                    skip_reason=f"No relevant files for extensions {tool.supported_extensions}",
                )
                continue

            # Construct command
            if tool_files:
                # Quote files to handle paths with spaces
                quoted_files = " ".join(f'"{f}"' for f in tool_files)
                cmd = tool.command_template.format(files=quoted_files)
            else:
                cmd = tool.whole_repo_command

            # Check if tool binary is available
            first_token = cmd.split()[0]
            if first_token == "npx":
                first_token = "npm"
            tool_bin = Path(first_token).stem.lower()

            if tool_bin not in ("python", "python3") and not shutil.which(first_token):
                # Check sandbox if possible
                results[tool.name] = StaticAnalysisResult(
                    tool_name=tool.name,
                    category=tool.category,
                    command=cmd,
                    exit_code=0,
                    stdout="",
                    stderr="",
                    duration_seconds=0.0,
                    skipped=True,
                    skip_reason=f"Toolchain binary '{first_token}' not found on host/sandbox.",
                )
                continue

            exec_res = self.sandbox.run_command(cmd, timeout=timeout, cwd=self.root_dir)
            tool_duration = round(time.time() - tool_start, 4)

            combined_out = (exec_res.stdout or "") + "\n" + (exec_res.stderr or "")
            # Check for missing command error in sandbox output
            lower_out = combined_out.lower()
            if not exec_res.success and ("not recognized" in lower_out or "command not found" in lower_out):
                results[tool.name] = StaticAnalysisResult(
                    tool_name=tool.name,
                    category=tool.category,
                    command=cmd,
                    exit_code=0,
                    stdout=exec_res.stdout,
                    stderr=exec_res.stderr,
                    duration_seconds=tool_duration,
                    skipped=True,
                    skip_reason=f"Toolchain binary '{first_token}' not recognized by shell.",
                )
                continue

            # Parse diagnostics
            diags = DiagnosticParser.parse(combined_out, tool.name)

            # If tool exited with error but no diagnostics parsed, create a diagnostic from stderr
            if exec_res.exit_code != 0 and not diags:
                err_msg = exec_res.stderr.strip() or exec_res.stdout.strip()
                if err_msg:
                    diags.append(Diagnostic(
                        file_path=tool_files[0] if tool_files else str(self.root_dir),
                        line=1,
                        column=1,
                        severity=DiagnosticSeverity.ERROR,
                        code="TOOL_ERROR",
                        message=f"{tool.name} failed with exit code {exec_res.exit_code}: {err_msg[:300]}",
                        source_tool=tool.name,
                    ))

            errs = [d for d in diags if d.severity == DiagnosticSeverity.ERROR]
            warns = [d for d in diags if d.severity in (DiagnosticSeverity.WARNING, DiagnosticSeverity.INFO, DiagnosticSeverity.HINT)]

            res_obj = StaticAnalysisResult(
                tool_name=tool.name,
                category=tool.category,
                command=cmd,
                exit_code=exec_res.exit_code,
                stdout=exec_res.stdout,
                stderr=exec_res.stderr,
                duration_seconds=tool_duration,
                diagnostics=diags,
                errors=errs,
                warnings=warns,
                skipped=False,
            )
            results[tool.name] = res_obj
            all_diagnostics.extend(diags)
            all_errors.extend(errs)
            all_warnings.extend(warns)

        total_duration = round(time.time() - start_time, 4)
        report_passed = len(all_errors) == 0

        return StaticAnalysisReport(
            passed=report_passed,
            target_files=norm_files,
            diagnostics=all_diagnostics,
            errors=all_errors,
            warnings=all_warnings,
            results=results,
            total_duration_seconds=total_duration,
        )
