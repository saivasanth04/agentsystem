"""
StaticVerificationEngine: Deterministic multi-tier verification engine.
Provides syntax verification, compiler/type-checker gates, linter hygiene,
and runtime import smoke checks without relying on LLM self-evaluation.
"""
import ast
from dataclasses import dataclass, field
import json
import os
from enum import Enum
from pathlib import Path
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from .project_detector import ProjectEnvironment, ProjectEnvironmentDetector
from ..security.sandbox import BaseExecutionSandbox, create_sandbox


class StaticVerificationTier(str, Enum):
    SYNTAX = "SYNTAX"
    COMPILER = "COMPILER"
    TYPE_CHECKER = "TYPE_CHECKER"
    LINTER = "LINTER"
    RUNTIME_SMOKE = "RUNTIME_SMOKE"
    SAST = "SAST"


@dataclass
class StaticVerificationReport:
    """Consolidated findings across all deterministic static verification tiers."""
    passed: bool
    syntax_errors: List[str] = field(default_factory=list)
    compiler_errors: List[str] = field(default_factory=list)
    type_errors: List[str] = field(default_factory=list)
    linter_issues: List[str] = field(default_factory=list)
    import_errors: List[str] = field(default_factory=list)
    tier_results: Dict[str, bool] = field(default_factory=dict)
    duration_seconds: float = 0.0
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    static_analysis_report: Optional[Dict[str, Any]] = None

    @property
    def all_failures(self) -> List[str]:
        return (
            self.syntax_errors
            + self.compiler_errors
            + self.type_errors
            + self.linter_issues
            + self.import_errors
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "syntax_errors": self.syntax_errors,
            "compiler_errors": self.compiler_errors,
            "type_errors": self.type_errors,
            "linter_issues": self.linter_issues,
            "import_errors": self.import_errors,
            "all_failures": self.all_failures,
            "tier_results": self.tier_results,
            "duration_seconds": self.duration_seconds,
            "diagnostics": self.diagnostics,
            "errors": self.errors,
            "warnings": self.warnings,
            "static_analysis_report": self.static_analysis_report,
        }


class StaticVerificationEngine:
    """
    Executes independent multi-tier verification:
    1. Syntax & Delimiter Gate (Python AST, JSON parser, balanced bracket analyzer)
    2. Compiler / Type Checker Gate (tsc --noEmit, cargo check, go vet, mypy)
    3. Linter Hygiene Gate (ruff, eslint, clippy)
    4. Runtime Import Smoke Gate (importlib / smoke dry-run in isolated process)
    """

    def __init__(self, workspace: Any = None, workspace_dir: Any = None, sandbox: Optional[BaseExecutionSandbox] = None):
        ws = workspace if workspace is not None else workspace_dir
        self.workspace = ws
        self.root_dir = Path(ws.root_dir if hasattr(ws, "root_dir") else ws).resolve()
        self.sandbox = sandbox or create_sandbox(self.root_dir)

    def verify_syntax(self, file_path_rel: str) -> List[str]:
        """Validates file grammar and syntax deterministically."""
        errors: List[str] = []
        full_path = self.root_dir / file_path_rel
        if not full_path.is_file() and hasattr(self.workspace, "main_workspace"):
            main_p = self.workspace.main_workspace.root_dir / file_path_rel
            if main_p.is_file():
                full_path = main_p
        if not full_path.is_file():
            return [f"File not found: '{file_path_rel}'"]

        content = ""
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return [f"Failed to read file '{file_path_rel}': {str(e)}"]

        if file_path_rel.endswith(".py"):
            try:
                ast.parse(content, filename=file_path_rel)
            except SyntaxError as se:
                errors.append(f"Syntax compilation failed: Python SyntaxError in '{file_path_rel}' (line {se.lineno}, col {se.offset}): {se.msg}")
            except Exception as e:
                errors.append(f"Syntax compilation failed: Python AST compilation error in '{file_path_rel}': {str(e)}")

        elif file_path_rel.endswith(".json"):
            try:
                json.loads(content)
            except json.JSONDecodeError as jde:
                errors.append(f"Syntax error: JSON syntax error in '{file_path_rel}' (line {jde.lineno}, col {jde.colno}): {jde.msg}")
            except Exception as e:
                errors.append(f"Syntax error: JSON parsing error in '{file_path_rel}': {str(e)}")

        elif file_path_rel.endswith((".js", ".ts", ".jsx", ".tsx", ".java", ".rs", ".go", ".cpp", ".c", ".dart")):
            stack = []
            pairs = {')': '(', ']': '[', '}': '{'}
            in_string = None
            in_line_comment = False
            in_block_comment = False
            i = 0
            line_no = 1
            col_no = 1
            while i < len(content):
                ch = content[i]
                nxt = content[i + 1] if i + 1 < len(content) else ''
                if ch == '\n':
                    line_no += 1
                    col_no = 1
                    in_line_comment = False
                else:
                    col_no += 1

                if in_line_comment:
                    i += 1
                    continue
                elif in_block_comment:
                    if ch == '*' and nxt == '/':
                        in_block_comment = False
                        i += 1
                elif in_string:
                    if ch == '\\':
                        i += 1
                    elif ch == in_string:
                        in_string = None
                else:
                    if ch == '/' and nxt == '/':
                        in_line_comment = True
                        i += 1
                    elif ch == '/' and nxt == '*':
                        in_block_comment = True
                        i += 1
                    elif ch in ("'", '"', '`'):
                        in_string = ch
                    elif ch in ('(', '[', '{'):
                        stack.append((ch, line_no, col_no))
                    elif ch in (')', ']', '}'):
                        if not stack or stack[-1][0] != pairs[ch]:
                            expected = pairs[ch]
                            actual = stack[-1][0] if stack else "nothing"
                            errors.append(
                                f"Syntax error: Structural delimiter error in '{file_path_rel}' (line {line_no}): "
                                f"Unmatched closing '{ch}' (expected to match '{expected}', but open was '{actual}')"
                            )
                            break
                        stack.pop()
                i += 1
            if stack and not errors:
                unclosed, lno, _ = stack[-1]
                errors.append(f"Syntax error: Structural delimiter error in '{file_path_rel}': Unclosed '{unclosed}' opened at line {lno}")

        return errors

    def verify_import_smoke(self, file_path_rel: str) -> List[str]:
        """Tests that Python module can be imported without top-level exception."""
        if not file_path_rel.endswith(".py"):
            return []
        p = Path(file_path_rel)
        if p.name.startswith("test_") or p.name.endswith("_test.py"):
            return []  # Skip test files from import smoke check

        full_path = self.root_dir / file_path_rel
        target_dir = self.root_dir
        if not full_path.is_file() and hasattr(self.workspace, "main_workspace"):
            main_p = self.workspace.main_workspace.root_dir / file_path_rel
            if main_p.is_file():
                full_path = main_p
                target_dir = self.workspace.main_workspace.root_dir
        if not full_path.is_file():
            return []

        # Derive module name from path
        parts = list(p.with_suffix("").parts)
        if not parts:
            return []
        mod_name = ".".join(parts)

        code = (
            f"import sys; sys.path.insert(0, '.'); "
            f"import importlib; importlib.import_module('{mod_name}')"
        )
        res = self.sandbox.run_command(
            ["python", "-c", code],
            timeout=10,
            cwd=target_dir,
        )
        if res.exit_code != 0:
            err = res.stderr.strip() or res.stdout.strip()
            # Extract last line of traceback
            tb_lines = [line for line in err.splitlines() if line.strip()]
            last_err = tb_lines[-1] if tb_lines else err
            return [f"Import smoke check failed for '{file_path_rel}': {last_err}"]
        return []

    def run_toolchain_command(self, command_str: Optional[str]) -> Tuple[bool, List[str]]:
        """
        Executes a compiler, type checker, or linter command if the binary exists.
        Gracefully ignores missing local toolchains so clean test setups don't crash.
        """
        if not command_str:
            return True, []

        first_token = command_str.split()[0]
        # Ignore cd commands or prefixes
        if "&&" in command_str:
            tokens = [t.strip() for t in command_str.split("&&")]
            first_token = tokens[-1].split()[0] if tokens else first_token

        # Check if tool is available on PATH
        tool_name = Path(first_token).stem.lower()
        if tool_name not in ("python", "python3") and not shutil.which(first_token):
            return True, []  # Toolchain not installed in host/sandbox, gracefully skip

        res = self.sandbox.run_command(command_str, timeout=30, cwd=self.root_dir)
        if res.exit_code != 0:
            output = res.stderr.strip() or res.stdout.strip()
            if "not recognized" in output.lower() or "command not found" in output.lower():
                return True, []
            return False, [f"Toolchain check failed ('{command_str}'):\n{output}"]
        return True, []

    def verify(
        self,
        files: Optional[List[str]] = None,
        env: Optional[ProjectEnvironment] = None,
        skip_toolchains: bool = False,
    ) -> StaticVerificationReport:
        """
        Executes all 4 deterministic verification tiers:
        Tier 1: Syntax & Delimiters
        Tier 2: Compiler & Typecheck
        Tier 3: Linter
        Tier 4: Import Smoke Check
        """
        start_time = time.time()
        project_env = env or ProjectEnvironmentDetector.detect(self.root_dir)
        target_files = files or []

        # If no files specified, inspect all files in workspace
        if not target_files:
            if hasattr(self.workspace, "list_files"):
                target_files = self.workspace.list_files()
            else:
                target_files = [str(p.relative_to(self.root_dir)).replace("\\", "/") for p in self.root_dir.glob("**/*") if p.is_file()]

        # Filter out hidden or build files
        target_files = [
            f for f in target_files
            if not any(part.startswith(".") or part in ("node_modules", "target", "vendor", "__pycache__", "dist", "build") for part in Path(f).parts)
        ]

        syntax_errors: List[str] = []
        import_errors: List[str] = []
        compiler_errors: List[str] = []
        type_errors: List[str] = []
        linter_issues: List[str] = []
        tier_results: Dict[str, bool] = {}

        # 1. Tier 1: Syntax & Delimiter verification
        files_with_syntax_errors = set()
        for f in target_files:
            errs = self.verify_syntax(f)
            if errs:
                files_with_syntax_errors.add(f)
                syntax_errors.extend(errs)
        tier_results["syntax"] = len(syntax_errors) == 0

        # 2. Tier 4: Import Smoke verification (for valid python modules)
        for f in target_files:
            if f.endswith(".py") and f not in files_with_syntax_errors:
                ierrs = self.verify_import_smoke(f)
                import_errors.extend(ierrs)
        tier_results["import_smoke"] = len(import_errors) == 0

        # 3. Tier 2, 3, & SAST: Targeted Multi-Tool Static Analysis
        diagnostics_list: List[Dict[str, Any]] = []
        errors_list: List[Dict[str, Any]] = []
        warnings_list: List[Dict[str, Any]] = []
        sa_report_dict = None

        if not skip_toolchains:
            try:
                from .static_analyzer import StaticAnalyzer
                from .diagnostic import DiagnosticSeverity
                analyzer = StaticAnalyzer(workspace=self.workspace, sandbox=self.sandbox, env=project_env)
                sa_report = analyzer.run_analysis(files=target_files)
                sa_report_dict = sa_report.to_dict()
                diagnostics_list = [d.to_dict() for d in sa_report.diagnostics]
                errors_list = [d.to_dict() for d in sa_report.errors]
                warnings_list = [d.to_dict() for d in sa_report.warnings]

                for diag in sa_report.errors:
                    msg = diag.to_string()
                    if diag.source_tool in ("tsc", "compiler"):
                        compiler_errors.append(msg)
                    elif diag.source_tool in ("mypy", "pyright"):
                        type_errors.append(msg)
                    else:
                        linter_issues.append(msg)

                tier_results["sast"] = not any(d.source_tool == "semgrep" and d.severity == DiagnosticSeverity.ERROR for d in sa_report.errors)
            except Exception:
                pass

            # Compiler Check fallback / explicit command
            if project_env.compiler_command and not compiler_errors:
                ok, errs = self.run_toolchain_command(project_env.compiler_command)
                if not ok:
                    compiler_errors.extend(errs)
            tier_results["compiler"] = len(compiler_errors) == 0

            # Typecheck fallback / explicit command
            if project_env.type_checker_command and project_env.type_checker_command != project_env.compiler_command and not type_errors:
                ok, errs = self.run_toolchain_command(project_env.type_checker_command)
                if not ok:
                    type_errors.extend(errs)
            tier_results["type_checker"] = len(type_errors) == 0

            # Linter fallback / explicit command
            if project_env.linter_command and not linter_issues:
                ok, errs = self.run_toolchain_command(project_env.linter_command)
                if not ok:
                    linter_issues.extend(errs)
            tier_results["linter"] = len(linter_issues) == 0

        passed = (
            len(syntax_errors) == 0
            and len(import_errors) == 0
            and len(compiler_errors) == 0
            and len(type_errors) == 0
        )

        return StaticVerificationReport(
            passed=passed,
            syntax_errors=syntax_errors,
            compiler_errors=compiler_errors,
            type_errors=type_errors,
            linter_issues=linter_issues,
            import_errors=import_errors,
            tier_results=tier_results,
            duration_seconds=round(time.time() - start_time, 4),
            diagnostics=diagnostics_list,
            errors=errors_list,
            warnings=warnings_list,
            static_analysis_report=sa_report_dict,
        )
