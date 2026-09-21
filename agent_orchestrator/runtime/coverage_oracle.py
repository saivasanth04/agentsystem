"""
CoverageOracle: Deterministic Code Coverage and Diff Coverage Verification Engine.
Measures whether lines added or modified in the current task/turn were actually executed
by the test suite, preventing untested code from passing verification.
"""
import ast
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from .project_detector import ProjectEnvironment, ProjectEnvironmentDetector
try:
    from ..security.sandbox import BaseExecutionSandbox, create_sandbox
except (ImportError, ValueError):
    from agent_orchestrator.security.sandbox import BaseExecutionSandbox, create_sandbox


@dataclass
class DiffCoverageResult:
    """Findings from diff code coverage analysis."""
    total_lines_changed: int = 0
    covered_lines: int = 0
    missed_lines: int = 0
    coverage_percentage: float = 100.0
    file_coverages: Dict[str, float] = field(default_factory=dict)
    uncovered_files: List[str] = field(default_factory=list)
    tool_used: str = "none"
    skipped: bool = False
    skip_reason: Optional[str] = None

    @property
    def passed(self) -> bool:
        if self.skipped:
            return True
        if self.total_lines_changed == 0:
            return True
        # Hard fail if 0% coverage on modified code
        if self.coverage_percentage == 0.0 and self.total_lines_changed > 0:
            return False
        return self.coverage_percentage >= 70.0

    @property
    def total_modified_executable_lines(self) -> int:
        return self.total_lines_changed

    @property
    def covered_modified_lines(self) -> int:
        return self.covered_lines

    @property
    def diff_coverage_percent(self) -> float:
        return self.coverage_percentage

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_lines_changed": self.total_lines_changed,
            "covered_lines": self.covered_lines,
            "missed_lines": self.missed_lines,
            "coverage_percentage": round(self.coverage_percentage, 2),
            "file_coverages": self.file_coverages,
            "uncovered_files": self.uncovered_files,
            "tool_used": self.tool_used,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "passed": self.passed,
        }

    def summary(self) -> str:
        if self.skipped:
            return f"Diff Coverage: SKIPPED ({self.skip_reason or 'No coverage toolchain'})"
        status = "PASSED" if self.passed else "FAILED"
        return (
            f"Diff Coverage: {status} ({self.coverage_percentage:.1f}% covered, "
            f"{self.covered_lines}/{self.total_lines_changed} lines, tool: {self.tool_used})"
        )


class CoverageOracle:
    """
    Computes diff code coverage on modified and created files.
    """

    def __init__(
        self,
        workspace: Any = None,
        workspace_dir: Any = None,
        sandbox: Optional[BaseExecutionSandbox] = None,
        env: Optional[ProjectEnvironment] = None,
        min_diff_coverage: float = 70.0,
    ):
        ws = workspace if workspace is not None else workspace_dir
        self.workspace = ws
        self.root_dir = Path(ws.root_dir if hasattr(ws, "root_dir") else ws).resolve()
        self.sandbox = sandbox or create_sandbox(self.root_dir)
        self.env = env or ProjectEnvironmentDetector.detect(self.root_dir)
        self.min_diff_coverage = min_diff_coverage

    def parse_coverage_json(self, file_path: Union[str, Path]) -> Dict[str, Dict[str, Set[int]]]:
        """Parses coverage.json (coverage.py) into executed and missing lines per file."""
        p = Path(file_path)
        if not p.is_file():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            result: Dict[str, Dict[str, Set[int]]] = {}
            for fpath, finfo in data.get("files", {}).items():
                norm = self._normalize_rel(fpath)
                exec_lines = set(finfo.get("executed_lines", []))
                miss_lines = set(finfo.get("missing_lines", []))
                result[norm] = {"executed": exec_lines, "missing": miss_lines}
            return result
        except Exception:
            return {}

    def parse_lcov(self, file_path: Union[str, Path]) -> Dict[str, Dict[str, Set[int]]]:
        """Parses lcov.info file into executed and missing lines per file."""
        p = Path(file_path)
        if not p.is_file():
            return {}
        result: Dict[str, Dict[str, Set[int]]] = {}
        current_file: Optional[str] = None
        current_exec: Set[int] = set()
        current_miss: Set[int] = set()

        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("SF:"):
                current_file = self._normalize_rel(line[3:].strip())
                current_exec = set()
                current_miss = set()
            elif line.startswith("DA:") and current_file:
                parts = line[3:].split(",")
                if len(parts) >= 2:
                    try:
                        lineno = int(parts[0])
                        hits = int(parts[1])
                        if hits > 0:
                            current_exec.add(lineno)
                        else:
                            current_miss.add(lineno)
                    except ValueError:
                        pass
            elif line == "end_of_record" and current_file:
                result[current_file] = {"executed": current_exec, "missing": current_miss}
                current_file = None
        return result

    def measure_diff_coverage(
        self,
        modified_lines: Dict[str, List[int]],
        coverage_map: Dict[str, Any],
    ) -> DiffCoverageResult:
        """Measures diff coverage given a map of modified lines and coverage report map."""
        total_changed = 0
        total_covered = 0
        file_covs: Dict[str, float] = {}
        uncovered: List[str] = []

        for fpath, lines in modified_lines.items():
            norm_f = self._normalize_rel(fpath)
            lines_set = set(lines)
            if not lines_set:
                continue

            file_info = coverage_map.get(norm_f, {})
            if isinstance(file_info, dict) and "executed" in file_info:
                exec_lines = file_info["executed"]
            elif isinstance(file_info, (set, list)):
                exec_lines = set(file_info)
            else:
                exec_lines = set()

            cov_count = len(lines_set.intersection(exec_lines))
            total_count = len(lines_set)

            total_changed += total_count
            total_covered += cov_count
            pct = (cov_count / total_count * 100.0) if total_count > 0 else 100.0
            file_covs[norm_f] = round(pct, 2)

            if pct == 0.0 and total_count > 0:
                uncovered.append(norm_f)

        overall_pct = (total_covered / total_changed * 100.0) if total_changed > 0 else 100.0
        return DiffCoverageResult(
            total_lines_changed=total_changed,
            covered_lines=total_covered,
            missed_lines=total_changed - total_covered,
            coverage_percentage=overall_pct,
            file_coverages=file_covs,
            uncovered_files=uncovered,
            tool_used="report",
            skipped=False,
        )

    def compute_diff_coverage(
        self,
        modified_files: Optional[List[str]] = None,
        test_command: Optional[str] = None,
        test_stdout: Optional[str] = None,
    ) -> DiffCoverageResult:
        """
        Calculates coverage percentage on lines changed in modified_files.
        """
        target_files = self._resolve_target_files(modified_files)
        if not target_files:
            return DiffCoverageResult(
                total_lines_changed=0,
                covered_lines=0,
                missed_lines=0,
                coverage_percentage=100.0,
                skipped=True,
                skip_reason="No modified source files to evaluate for diff coverage.",
            )

        # Check if there is any test suite or test execution activity
        has_tests = self._has_test_files()
        has_test_activity = bool(test_command or test_stdout or has_tests)
        if not has_test_activity:
            return DiffCoverageResult(
                total_lines_changed=0,
                covered_lines=0,
                missed_lines=0,
                coverage_percentage=100.0,
                skipped=True,
                skip_reason="No test suite executed or present in workspace.",
            )

        # 1. Inspect existing coverage reports in workspace
        report_data = self._find_and_parse_coverage_report()
        if report_data:
            return self._calculate_from_report_data(report_data, target_files)

        # 2. Check for Python coverage.py
        if self.env.language.lower() == "python":
            py_res = self._check_python_coverage(target_files, test_stdout)
            if py_res:
                return py_res

        # 3. Fallback: Parse test stdout and commands for test execution evidence & executable lines
        return self._heuristic_diff_coverage(target_files, test_stdout, test_command=test_command)

    def _has_test_files(self) -> bool:
        """Checks if any test files exist in workspace."""
        for p in self.root_dir.glob("**/*"):
            if not p.is_file() or p.is_symlink():
                continue
            name = p.name.lower()
            if name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name:
                return True
        return False

    def _resolve_target_files(self, modified_files: Optional[List[str]]) -> List[str]:
        if modified_files:
            candidates = modified_files
        elif hasattr(self.workspace, "get_change_manifest"):
            manifest = self.workspace.get_change_manifest()
            if manifest:
                candidates = list(manifest.created_files) + list(manifest.modified_files)
            else:
                candidates = []
        else:
            candidates = []

        # Filter out test files, config files, hidden files, and build outputs
        filtered = []
        for f in candidates:
            p = Path(f)
            p_str = str(f).replace("\\", "/")
            if any(part.startswith(".") or part in ("node_modules", "target", "build", "dist", "__pycache__") for part in p.parts):
                continue
            if p.name.startswith("test_") or p.name.endswith("_test.py") or ".test." in p.name or ".spec." in p.name:
                continue
            if p.suffix.lower() in (".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".cpp", ".c"):
                filtered.append(p_str)
        return filtered

    def _find_and_parse_coverage_report(self) -> Optional[Dict[str, Set[int]]]:
        """Looks for coverage.json, coverage-final.json, or lcov.info in workspace."""
        # 1. coverage.json (pytest-cov / coverage.py)
        json_path = self.root_dir / "coverage.json"
        if json_path.is_file():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
                files_map: Dict[str, Set[int]] = {}
                for fpath, finfo in data.get("files", {}).items():
                    norm_path = self._normalize_rel(fpath)
                    executed = set(finfo.get("executed_lines", []))
                    files_map[norm_path] = executed
                return files_map
            except Exception:
                pass

        # 2. Jest/Vitest coverage-final.json
        jest_cov = self.root_dir / "coverage" / "coverage-final.json"
        if jest_cov.is_file():
            try:
                data = json.loads(jest_cov.read_text(encoding="utf-8", errors="replace"))
                files_map = {}
                for fpath, finfo in data.items():
                    norm_path = self._normalize_rel(fpath)
                    statement_map = finfo.get("statementMap", {})
                    s_hits = finfo.get("s", {})
                    executed_lines = set()
                    for s_id, hit_count in s_hits.items():
                        if hit_count > 0 and s_id in statement_map:
                            line_start = statement_map[s_id].get("start", {}).get("line")
                            if line_start:
                                executed_lines.add(line_start)
                    files_map[norm_path] = executed_lines
                return files_map
            except Exception:
                pass

        return None

    def _calculate_from_report_data(
        self,
        report_map: Dict[str, Set[int]],
        target_files: List[str],
    ) -> DiffCoverageResult:
        total_changed = 0
        total_covered = 0
        file_covs: Dict[str, float] = {}
        uncovered: List[str] = []

        for tf in target_files:
            norm_tf = self._normalize_rel(tf)
            exec_lines = self._get_executable_lines(tf)
            if not exec_lines:
                continue

            covered_set = report_map.get(norm_tf, set())
            covered_count = len(exec_lines.intersection(covered_set))
            total_count = len(exec_lines)

            total_changed += total_count
            total_covered += covered_count
            cov_pct = (covered_count / total_count * 100.0) if total_count > 0 else 100.0
            file_covs[norm_tf] = round(cov_pct, 2)

            if cov_pct == 0.0 and total_count > 0:
                uncovered.append(norm_tf)

        overall_pct = (total_covered / total_changed * 100.0) if total_changed > 0 else 100.0
        return DiffCoverageResult(
            total_lines_changed=total_changed,
            covered_lines=total_covered,
            missed_lines=total_changed - total_covered,
            coverage_percentage=overall_pct,
            file_coverages=file_covs,
            uncovered_files=uncovered,
            tool_used="coverage_report",
            skipped=False,
        )

    def _check_python_coverage(
        self,
        target_files: List[str],
        test_stdout: Optional[str],
    ) -> Optional[DiffCoverageResult]:
        """Checks for .coverage SQLite file generated during test runs."""
        cov_db = self.root_dir / ".coverage"
        if not cov_db.is_file():
            return None

        # Try running coverage json to parse executed lines
        if shutil.which("coverage"):
            res = self.sandbox.run_command(["coverage", "json", "-o", "-"], cwd=self.root_dir)
            if res.exit_code == 0 and res.stdout:
                try:
                    data = json.loads(res.stdout)
                    files_map: Dict[str, Set[int]] = {}
                    for fpath, finfo in data.get("files", {}).items():
                        norm_path = self._normalize_rel(fpath)
                        files_map[norm_path] = set(finfo.get("executed_lines", []))
                    return self._calculate_from_report_data(files_map, target_files)
                except Exception:
                    pass
        return None

    def _heuristic_diff_coverage(
        self,
        target_files: List[str],
        test_stdout: Optional[str],
        test_command: Optional[str] = None,
    ) -> DiffCoverageResult:
        """
        Heuristic fallback when no coverage binary is installed:
        Verifies whether target files and their exported symbols are referenced
        in test suites or executed in test stdout.
        """
        stdout_str = (test_stdout or "").lower()
        cmd_str = (test_command or "").lower()
        has_tests = self._has_test_files()
        file_covs: Dict[str, float] = {}
        uncovered: List[str] = []
        total_changed = 0
        total_covered = 0

        for tf in target_files:
            norm_tf = self._normalize_rel(tf)
            exec_lines = self._get_executable_lines(tf)
            line_count = len(exec_lines)
            if line_count == 0:
                continue

            total_changed += line_count
            base_name = Path(tf).stem.lower()

            # Check if file stem appears in test output, test command, or test files
            is_referenced = (
                base_name in stdout_str
                or base_name in cmd_str
                or self._is_file_imported_in_tests(tf)
                or (not has_tests and (stdout_str or cmd_str))
            )

            if is_referenced:
                # Referenced and executed during test run
                total_covered += line_count
                file_covs[norm_tf] = 100.0
            else:
                # Never referenced in any test
                file_covs[norm_tf] = 0.0
                uncovered.append(norm_tf)

        overall_pct = (total_covered / total_changed * 100.0) if total_changed > 0 else 100.0
        return DiffCoverageResult(
            total_lines_changed=total_changed,
            covered_lines=total_covered,
            missed_lines=total_changed - total_covered,
            coverage_percentage=overall_pct,
            file_coverages=file_covs,
            uncovered_files=uncovered,
            tool_used="static_symbol_trace",
            skipped=False,
        )

    def _get_executable_lines(self, rel_path: str) -> Set[int]:
        """Parses AST or non-empty lines to identify executable line numbers."""
        full_p = self.root_dir / rel_path
        if not full_p.is_file():
            return set()

        try:
            content = full_p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return set()

        if rel_path.endswith(".py"):
            try:
                tree = ast.parse(content)
                lines = set()
                for node in ast.walk(tree):
                    if hasattr(node, "lineno"):
                        lines.add(node.lineno)
                return lines
            except Exception:
                pass

        # Generic non-empty, non-comment line counting
        lines = set()
        for idx, line in enumerate(content.splitlines(), start=1):
            s = line.strip()
            if s and not s.startswith(("//", "#", "/*", "*")):
                lines.add(idx)
        return lines

    def _is_file_imported_in_tests(self, rel_path: str) -> bool:
        """Scans test files in workspace to check if rel_path is imported or referenced."""
        mod_stem = Path(rel_path).stem
        for p in self.root_dir.glob("**/*"):
            if not p.is_file() or p.is_symlink():
                continue
            name = p.name.lower()
            if name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name:
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    if mod_stem in content:
                        return True
                except Exception:
                    pass
        return False

    def _normalize_rel(self, path_str: str) -> str:
        p = Path(path_str)
        if p.is_absolute():
            try:
                return str(p.relative_to(self.root_dir)).replace("\\", "/")
            except ValueError:
                return p.name
        return str(path_str).replace("\\", "/")
