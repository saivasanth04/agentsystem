"""
DependencyRegressionDetector: Dependency-Aware Regression Detection and Blast-Radius Protection.
Identifies downstream dependent implementation and test files impacted by code edits,
executes targeted static and dynamic regression verification, and prevents cross-module breakages.
"""
import ast
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Union


@dataclass
class DependencyImpact:
    modified_files: List[str] = field(default_factory=list)
    impacted_impl_files: List[str] = field(default_factory=list)
    impacted_test_files: List[str] = field(default_factory=list)
    blast_radius_score: float = 0.0

    @property
    def total_impacted(self) -> int:
        return len(self.impacted_impl_files) + len(self.impacted_test_files)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "modified_files": self.modified_files,
            "impacted_impl_files": self.impacted_impl_files,
            "impacted_test_files": self.impacted_test_files,
            "blast_radius_score": round(self.blast_radius_score, 1),
            "total_impacted": self.total_impacted,
        }


@dataclass
class RegressionReport:
    passed: bool = True
    score: float = 100.0
    broken_dependents: List[str] = field(default_factory=list)
    failing_tests: List[str] = field(default_factory=list)
    details: str = "Zero regressions detected in downstream dependents."
    impact: DependencyImpact = field(default_factory=DependencyImpact)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 1),
            "broken_dependents": self.broken_dependents,
            "failing_tests": self.failing_tests,
            "details": self.details,
            "impact": self.impact.to_dict(),
        }

    def summary(self) -> str:
        if self.passed:
            return f"Regression Check: PASSED (Blast radius: {self.impact.blast_radius_score:.1f}%, {self.impact.total_impacted} dependent files verified clean)"
        return (
            f"Regression Check: FAILED ({len(self.broken_dependents)} broken dependent(s): {', '.join(self.broken_dependents[:3])}; "
            f"{len(self.failing_tests)} failing test(s))"
        )


class DependencyRegressionDetector:
    """
    Analyzes dependency relationships to identify impacted downstream modules and verify
    that changes to an upstream module (e.g. users.py) do not break downstream modules (e.g. payments.py).
    """

    @classmethod
    def analyze_impact(
        cls,
        modified_files: List[str],
        workspace: Any,
        code_graph: Optional[Any] = None,
        max_depth: int = 3,
    ) -> DependencyImpact:
        """
        Calculates the blast radius of modified files by discovering all files that import
        or call symbols from the modified files.
        """
        norm_modified = [f.replace("\\", "/").lstrip("/") for f in modified_files if f]
        if not norm_modified:
            return DependencyImpact()

        impacted_impl: Set[str] = set()
        impacted_test: Set[str] = set()

        # 1. Use CodeGraphEngine if available and initialized
        if code_graph and hasattr(code_graph, "get_impact_radius"):
            for mf in norm_modified:
                try:
                    res = code_graph.get_impact_radius(mf, max_depth=max_depth)
                    for af in res.get("affected_files", []):
                        norm_af = af.replace("\\", "/").lstrip("/")
                        if norm_af in norm_modified:
                            continue
                        if "test" in norm_af.lower() or "spec" in norm_af.lower():
                            impacted_test.add(norm_af)
                        else:
                            impacted_impl.add(norm_af)
                except Exception:
                    pass

        # 2. Native AST Import & Reference Scanner (multi-hop transitive closure up to max_depth)
        all_files: List[str] = []
        if hasattr(workspace, "list_files"):
            try:
                res = workspace.list_files()
                all_files = res.get("files", []) if isinstance(res, dict) else (res or [])
            except Exception:
                pass
        elif hasattr(workspace, "root_dir"):
            root = Path(workspace.root_dir)
            all_files = [str(p.relative_to(root)).replace("\\", "/") for p in root.glob("**/*.py")]

        # Normalize and filter python files
        all_py_files = [f.replace("\\", "/").lstrip("/") for f in all_files if f.replace("\\", "/").lstrip("/").endswith(".py")]

        # Read content cache
        file_contents: Dict[str, str] = {}
        for rel_path in all_py_files:
            content = ""
            if hasattr(workspace, "read_file"):
                try:
                    res = workspace.read_file(rel_path)
                    content = res.get("content", "") if isinstance(res, dict) else str(res or "")
                except Exception:
                    pass
            elif hasattr(workspace, "root_dir"):
                try:
                    content = (Path(workspace.root_dir) / rel_path).read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
            file_contents[rel_path] = content

        current_targets = set(norm_modified)
        visited = set(norm_modified)

        for _ in range(max(1, max_depth)):
            new_impl_targets: Set[str] = set()
            mod_stems = {Path(f).stem.lower(): f for f in current_targets}
            mod_modules = {f.replace("/", ".").replace(".py", "").lower(): f for f in current_targets}

            for rel_path in all_py_files:
                if rel_path in visited:
                    continue

                content = file_contents.get(rel_path, "")
                if not content:
                    continue

                # Quick string filter before AST parsing
                has_potential = any(stem in content.lower() for stem in mod_stems)
                if not has_potential:
                    continue

                imports_target = False
                try:
                    tree = ast.parse(content, filename=rel_path)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                parts = alias.name.lower().split(".")
                                if any(stem in parts for stem in mod_stems) or any(mod == alias.name.lower() for mod in mod_modules):
                                    imports_target = True
                                    break
                        elif isinstance(node, ast.ImportFrom):
                            if node.module:
                                parts = node.module.lower().split(".")
                                if any(stem in parts for stem in mod_stems) or any(mod == node.module.lower() for mod in mod_modules):
                                    imports_target = True
                                    break
                        if imports_target:
                            break
                except Exception:
                    # Fallback to heuristic import lines
                    for line in content.splitlines():
                        line_strip = line.strip()
                        if line_strip.startswith("import ") or line_strip.startswith("from "):
                            if any(stem in line_strip.lower() for stem in mod_stems):
                                imports_target = True
                                break

                if imports_target:
                    visited.add(rel_path)
                    if "test" in rel_path.lower() or "spec" in rel_path.lower():
                        impacted_test.add(rel_path)
                    else:
                        impacted_impl.add(rel_path)
                        new_impl_targets.add(rel_path)

            if not new_impl_targets:
                break
            current_targets = new_impl_targets

        total_ws_files = max(1, len(all_files))
        blast_score = min(100.0, ((len(impacted_impl) + len(impacted_test)) / total_ws_files) * 100.0)

        return DependencyImpact(
            modified_files=sorted(norm_modified),
            impacted_impl_files=sorted(list(impacted_impl)),
            impacted_test_files=sorted(list(impacted_test)),
            blast_radius_score=blast_score,
        )

    @classmethod
    def verify_regressions(
        cls,
        modified_files: List[str],
        workspace: Any,
        static_engine: Optional[Any] = None,
        test_info: Optional[Dict[str, Any]] = None,
        baseline_test: Optional[Dict[str, Any]] = None,
        code_graph: Optional[Any] = None,
    ) -> RegressionReport:
        """
        Executes targeted regression verification across the blast radius:
        1. Analyzes dependency impact.
        2. Runs direct AST syntax checks on all impacted implementation files.
        3. Executes static analysis across impacted implementation files.
        4. Checks if any dependent tests failed in test_info or baseline comparison.
        """
        impact = cls.analyze_impact(
            modified_files=modified_files,
            workspace=workspace,
            code_graph=code_graph,
        )

        broken_dependents: List[str] = []
        failing_tests: List[str] = []

        # 1. Unconditional direct AST syntax verification on all impacted implementation files
        for f in impact.impacted_impl_files:
            content = ""
            if hasattr(workspace, "read_file"):
                try:
                    r = workspace.read_file(f)
                    content = r.get("content", "") if isinstance(r, dict) else str(r or "")
                except Exception:
                    pass
            elif hasattr(workspace, "root_dir"):
                try:
                    content = (Path(workspace.root_dir) / f).read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

            if content:
                try:
                    ast.parse(content, filename=f)
                except SyntaxError as se:
                    broken_dependents.append(f"Syntax error in dependent '{f}': {se.msg} (line {se.lineno})")

        # 2. Static Analysis Engine verification if available
        if impact.impacted_impl_files:
            try:
                engine = static_engine
                if not engine and hasattr(workspace, "root_dir"):
                    from .static_verifier import StaticVerificationEngine
                    engine = StaticVerificationEngine(workspace_dir=workspace.root_dir)

                if engine and hasattr(engine, "verify"):
                    report = engine.verify(files=impact.impacted_impl_files)
                    if hasattr(report, "passed") and not report.passed:
                        all_errs = getattr(report, "all_failures", [])
                        for err in all_errs:
                            err_str = str(err)
                            if err_str not in broken_dependents:
                                broken_dependents.append(err_str)
            except Exception:
                pass

        # 2. Check for failing tests in the blast radius
        if test_info and isinstance(test_info, dict):
            test_success = bool(test_info.get("execution_success", True) and test_info.get("exit_code", 0) == 0)
            if not test_success:
                stdout_text = test_info.get("stdout", "")
                stderr_text = test_info.get("stderr", "")
                # Check if any impacted test file is mentioned in failure logs
                for tf in impact.impacted_test_files:
                    tf_name = Path(tf).name
                    if tf_name in stdout_text or tf_name in stderr_text:
                        failing_tests.append(f"Regression in dependent test '{tf}': execution failed.")

        # 3. Check baseline comparison
        if baseline_test and isinstance(baseline_test, dict):
            base_success = bool(baseline_test.get("execution_success", True) and baseline_test.get("exit_code", 0) == 0)
            curr_success = bool(test_info.get("execution_success", True) and test_info.get("exit_code", 0) == 0) if test_info else True
            if base_success and not curr_success and not failing_tests:
                failing_tests.append("Baseline test suite was passing but failed after recent changes.")

        has_regressions = bool(broken_dependents or failing_tests)
        score = 0.0 if has_regressions else 100.0

        details = "Zero regressions detected in downstream dependents."
        if has_regressions:
            issues = broken_dependents + failing_tests
            details = f"Regressions detected ({len(issues)} issues): " + "; ".join(issues[:3])

        return RegressionReport(
            passed=not has_regressions,
            score=score,
            broken_dependents=broken_dependents,
            failing_tests=failing_tests,
            details=details,
            impact=impact,
        )
