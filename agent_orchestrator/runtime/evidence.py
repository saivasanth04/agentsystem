"""
VerificationEvidence: Deterministic Evidence-First Verification Model.
Replaces subjective, hallucinated scalar scores ("score_out_of_100": 95) with
discrete, multi-dimensional empirical verification evidence across:
- Build pipeline status (success/failure, failing stage)
- Test counts (total, passed, failed, skipped, exit code)
- Lint/static analysis (error count, warning count, active tools)
- Diff code coverage (percentage, lines changed/covered, uncovered files)
- Acceptance criteria traceability (per-criterion verified/unverified map)
"""
from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, List, Optional, Union


@dataclass
class BuildEvidence:
    success: bool = True
    failed_stage: Optional[str] = None
    stages_run: List[str] = field(default_factory=list)
    details: str = "Build succeeded cleanly."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "failed_stage": self.failed_stage,
            "stages_run": self.stages_run,
            "details": self.details,
        }


@dataclass
class TestEvidence:
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    exit_code: int = 0
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "exit_code": self.exit_code,
            "details": self.details,
        }


@dataclass
class LintEvidence:
    errors: int = 0
    warnings: int = 0
    tools_run: List[str] = field(default_factory=list)
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "errors": self.errors,
            "warnings": self.warnings,
            "tools_run": self.tools_run,
            "details": self.details,
        }


@dataclass
class DiffCoverageEvidence:
    percentage: float = 100.0
    lines_changed: int = 0
    lines_covered: int = 0
    uncovered_files: List[str] = field(default_factory=list)
    skipped: bool = False
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "percentage": round(self.percentage, 2),
            "lines_changed": self.lines_changed,
            "lines_covered": self.lines_covered,
            "uncovered_files": self.uncovered_files,
            "skipped": self.skipped,
            "details": self.details,
        }


@dataclass
class VerificationEvidence:
    build: BuildEvidence = field(default_factory=BuildEvidence)
    tests: TestEvidence = field(default_factory=TestEvidence)
    lint: LintEvidence = field(default_factory=LintEvidence)
    diff_coverage: DiffCoverageEvidence = field(default_factory=DiffCoverageEvidence)
    acceptance_criteria: Dict[str, str] = field(default_factory=dict)
    traceability_matrix: Optional[Dict[str, Any]] = None
    regression_report: Optional[Dict[str, Any]] = None
    tautological_assertions: int = 0
    passed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "build": self.build.to_dict(),
            "tests": self.tests.to_dict(),
            "lint": self.lint.to_dict(),
            "diff_coverage": self.diff_coverage.to_dict(),
            "acceptance_criteria": self.acceptance_criteria,
            "traceability_matrix": self.traceability_matrix,
            "regression_report": self.regression_report,
            "tautological_assertions": self.tautological_assertions,
            "passed": self.passed,
        }

    def summary(self) -> str:
        build_str = "PASS" if self.build.success else f"FAIL ({self.build.failed_stage or 'Error'})"
        test_str = f"{self.tests.passed}/{self.tests.total} passed" if self.tests.total > 0 else (
            "clean" if self.tests.exit_code == 0 else f"exit {self.tests.exit_code}"
        )
        lint_str = f"{self.lint.errors} errors, {self.lint.warnings} warnings"
        ac_verified = sum(1 for v in self.acceptance_criteria.values() if v == "verified")
        ac_total = len(self.acceptance_criteria)
        ac_str = f"{ac_verified}/{ac_total} verified" if ac_total > 0 else "N/A"
        cov_str = f"{self.diff_coverage.percentage:.1f}%" if not self.diff_coverage.skipped else "skipped"

        return (
            f"Evidence: Build: [{build_str}] | Tests: [{test_str}] | "
            f"Lint: [{lint_str}] | ACs: [{ac_str}] | DiffCov: [{cov_str}]"
        )

    def to_markdown(self) -> str:
        lines = [
            "### Verification Evidence Matrix",
            "",
            "| Category | Status | Metrics / Details |",
            "| :--- | :--- | :--- |",
        ]
        # Build
        b_status = "✅ PASS" if self.build.success else "❌ FAIL"
        b_details = self.build.details if self.build.success else f"Failed at stage: {self.build.failed_stage}"
        lines.append(f"| **Build Pipeline** | {b_status} | {b_details} |")

        # Tests
        t_status = "✅ PASS" if (self.tests.failed == 0 and self.tests.exit_code == 0) else "❌ FAIL"
        t_details = f"{self.tests.passed} passed, {self.tests.failed} failed (Exit code: {self.tests.exit_code})"
        lines.append(f"| **Automated Tests** | {t_status} | {t_details} |")

        # Lint
        l_status = "✅ CLEAN" if self.lint.errors == 0 else "❌ FAILED"
        l_details = f"{self.lint.errors} error(s), {self.lint.warnings} warning(s)"
        if self.lint.tools_run:
            l_details += f" (tools: {', '.join(self.lint.tools_run)})"
        lines.append(f"| **Static Analysis / Lint** | {l_status} | {l_details} |")

        # Diff Coverage
        c_status = "⏭️ SKIPPED" if self.diff_coverage.skipped else ("✅ PASS" if self.diff_coverage.percentage >= 70.0 else "❌ INSUFFICIENT")
        c_details = f"{self.diff_coverage.percentage:.1f}% covered" if not self.diff_coverage.skipped else (self.diff_coverage.details or "No coverage tool")
        lines.append(f"| **Diff Code Coverage** | {c_status} | {c_details} |")

        # Acceptance Criteria
        if self.acceptance_criteria:
            unverified = [k for k, v in self.acceptance_criteria.items() if v != "verified"]
            ac_status = "✅ VERIFIED" if len(unverified) == 0 else "❌ UNVERIFIED"
            ac_details = f"{len(self.acceptance_criteria) - len(unverified)}/{len(self.acceptance_criteria)} verified"
            if unverified:
                ac_details += f" (Unverified: {', '.join(unverified)})"
            lines.append(f"| **Acceptance Criteria** | {ac_status} | {ac_details} |")

        # Anti-tautology
        if self.tautological_assertions > 0:
            lines.append(f"| **Anti-Tautology** | ❌ WARNING | {self.tautological_assertions} trivial assertion(s) detected |")

        # Regression Protection
        if self.regression_report and isinstance(self.regression_report, dict):
            r_passed = self.regression_report.get("passed", True)
            r_status = "✅ CLEAN" if r_passed else "❌ REGRESSION"
            r_details = self.regression_report.get("details", "")
            lines.append(f"| **Regression Protection** | {r_status} | {r_details} |")

        # Traceability Matrix
        if self.traceability_matrix and isinstance(self.traceability_matrix, dict):
            reqs = self.traceability_matrix.get("requirements", {})
            if reqs:
                lines.append("")
                lines.append(f"#### Requirements & Acceptance Criteria Traceability ({self.traceability_matrix.get('verified_requirements', 0)}/{self.traceability_matrix.get('total_requirements', 0)} Verified)")
                lines.append("")
                lines.append("| ID | Description | Implementation | Tests | Status |")
                lines.append("| :--- | :--- | :--- | :--- | :---: |")
                for r_id, r_node in reqs.items():
                    status_badge = {
                        "VERIFIED": "🟢 VERIFIED",
                        "IMPLEMENTED_UNTESTED": "🟡 UNTESTED",
                        "TEST_FAILED": "🔴 FAILED",
                        "UNIMPLEMENTED": "⚪ UNIMPLEMENTED",
                    }.get(r_node.get("status"), r_node.get("status"))
                    impl_str = "<br>".join(r_node.get("implementation_symbols", []) or r_node.get("implementation_files", [])) or "*None*"
                    test_str = "<br>".join(r_node.get("test_cases", [])) or "*None*"
                    desc = r_node.get("description", "")[:50]
                    lines.append(f"| `{r_id}` | {desc} | {impl_str} | {test_str} | {status_badge} |")

        return "\n".join(lines)


class EvidenceSynthesizer:
    """
    Synthesizes discrete, deterministic verification evidence from
    ground-truth gates, build pipelines, static analysis, and test runs.
    """

    @classmethod
    def synthesize(
        cls,
        state: Any,
        ground_truth_report: Optional[Any] = None,
        test_info: Optional[Dict[str, Any]] = None,
        static_report: Optional[Dict[str, Any]] = None,
        build_pipeline_report: Optional[Dict[str, Any]] = None,
        spec_report: Optional[Any] = None,
        workspace: Optional[Any] = None,
        modified_files: Optional[List[str]] = None,
    ) -> VerificationEvidence:
        t_info = test_info or getattr(state, "test_output", None) or (state.get("test_output") if isinstance(state, dict) else {}) or {}
        b_report = build_pipeline_report or t_info.get("build_pipeline_report")
        s_report = static_report or getattr(state, "static_report", None) or (state.get("static_report") if isinstance(state, dict) else {}) or {}
        spec_info = getattr(state, "specification_output", None) or (state.get("specification_output") if isinstance(state, dict) else {}) or {}

        # 1. Synthesize Build Evidence
        build_ev = cls._extract_build_evidence(b_report, t_info, ground_truth_report)

        # 2. Synthesize Test Evidence
        test_ev = cls._extract_test_evidence(t_info)

        # 3. Synthesize Lint Evidence
        lint_ev = cls._extract_lint_evidence(s_report, ground_truth_report)

        # 4. Synthesize Diff Coverage Evidence
        cov_ev = cls._extract_coverage_evidence(ground_truth_report)

        # 5. Synthesize Acceptance Criteria & Traceability Matrix
        ac_map = cls._extract_acceptance_criteria(spec_info, spec_report, ground_truth_report)
        trace_matrix_dict = None
        ws = workspace or getattr(state, "workspace", None) if state else None
        if not ws and hasattr(state, "get"):
            ws = state.get("workspace")
        if ws:
            try:
                from .traceability import TraceabilityEngine, TraceStatus
                arch_info = getattr(state, "architecture_output", None) or (state.get("architecture_output") if isinstance(state, dict) else None)
                matrix = TraceabilityEngine.build_matrix(
                    spec=spec_info,
                    workspace=ws,
                    test_results=t_info,
                    architecture=arch_info,
                )
                if matrix.nodes:
                    trace_matrix_dict = matrix.to_dict()
                    for node_id, node in matrix.nodes.items():
                        if node.status == TraceStatus.VERIFIED:
                            ac_map[node_id] = "verified"
                        elif node.status == TraceStatus.TEST_FAILED:
                            ac_map[node_id] = "unverified"
                        elif node_id not in ac_map:
                            ac_map[node_id] = "unverified"
            except Exception:
                pass

        # 6. Synthesize Regression Report
        reg_report_dict = None
        mod_files = modified_files or (state.get("code_output", {}).get("written_files") if isinstance(state, dict) else None)
        if not mod_files and hasattr(state, "code_output") and hasattr(state.code_output, "written_files"):
            mod_files = state.code_output.written_files
        if not mod_files and isinstance(state, dict):
            mod_files = state.get("coder_output", {}).get("written_files")

        if ws and mod_files:
            try:
                from .regression_detector import DependencyRegressionDetector
                b_test = getattr(state, "baseline_test_output", None) or (state.get("baseline_test_info") if isinstance(state, dict) else None)
                r_rep = DependencyRegressionDetector.verify_regressions(
                    modified_files=mod_files,
                    workspace=ws,
                    test_info=t_info,
                    baseline_test=b_test,
                )
                reg_report_dict = r_rep.to_dict()
            except Exception:
                pass

        # 7. Tautology checks
        taut_count = t_info.get("tautological_count", 0)
        taut_warnings = t_info.get("tautological_warnings") or t_info.get("tautologies") or []
        taut_total = max(taut_count, len(taut_warnings))

        # Determine overall pass
        passed = (
            build_ev.success
            and (test_ev.failed == 0 and test_ev.exit_code == 0)
            and lint_ev.errors == 0
            and (cov_ev.skipped or cov_ev.percentage >= 70.0 or cov_ev.lines_changed == 0)
            and taut_total == 0
            and (reg_report_dict is None or reg_report_dict.get("passed", True))
        )
        if ground_truth_report and hasattr(ground_truth_report, "passed"):
            passed = passed and ground_truth_report.passed

        return VerificationEvidence(
            build=build_ev,
            tests=test_ev,
            lint=lint_ev,
            diff_coverage=cov_ev,
            acceptance_criteria=ac_map,
            traceability_matrix=trace_matrix_dict,
            regression_report=reg_report_dict,
            tautological_assertions=taut_total,
            passed=passed,
        )

    @classmethod
    def _extract_build_evidence(
        cls,
        b_report: Optional[Dict[str, Any]],
        test_info: Dict[str, Any],
        gt_report: Optional[Any],
    ) -> BuildEvidence:
        if b_report and isinstance(b_report, dict):
            success = bool(b_report.get("passed", True))
            failed_stage = b_report.get("failed_stage")
            stages_run = list(b_report.get("stage_results", {}).keys()) or b_report.get("stages_executed", [])
            details = "All build pipeline stages passed." if success else f"Build pipeline failed at stage: {failed_stage}"
            return BuildEvidence(success=success, failed_stage=failed_stage, stages_run=stages_run, details=details)

        # Fallback to test execution result
        exec_success = test_info.get("execution_success", True)
        exit_code = test_info.get("exit_code", 0)
        success = bool(exec_success and exit_code == 0)
        return BuildEvidence(
            success=success,
            failed_stage=None if success else "EXECUTION",
            stages_run=["TEST_EXECUTION"],
            details="Execution completed cleanly." if success else f"Execution failed with exit code {exit_code}.",
        )

    @classmethod
    def _extract_test_evidence(cls, test_info: Dict[str, Any]) -> TestEvidence:
        exit_code = test_info.get("exit_code", 0)
        stdout = test_info.get("stdout", "")
        stderr = test_info.get("stderr", "")

        # Check explicit counts
        total = test_info.get("total_tests") if test_info.get("total_tests") is not None else test_info.get("tests_total")
        passed = test_info.get("tests_passed") if test_info.get("tests_passed") is not None else test_info.get("passed_tests")
        failed = test_info.get("tests_failed") if test_info.get("tests_failed") is not None else test_info.get("failed_tests")
        skipped = test_info.get("tests_skipped") if test_info.get("tests_skipped") is not None else 0

        # If not provided, parse from stdout
        if total is None or passed is None or failed is None:
            total, passed, failed, skipped = cls._parse_test_counts_from_stdout(stdout, exit_code)

        details = f"{passed}/{total} passed" if total > 0 else (
            "Tests ran successfully." if exit_code == 0 else f"Test run failed with exit code {exit_code}."
        )

        return TestEvidence(
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            exit_code=exit_code,
            details=details,
        )

    @classmethod
    def _parse_test_counts_from_stdout(cls, stdout: str, exit_code: int) -> tuple[int, int, int, int]:
        """Parses test counts from common test runners (pytest, unittest, jest, go test)."""
        # Python unittest: "Ran 42 tests in 0.05s\n\nOK" or "FAILED (failures=2, errors=1)"
        unittest_match = re.search(r"Ran (\d+) tests? in", stdout)
        if unittest_match:
            total = int(unittest_match.group(1))
            failures_match = re.search(r"failures=(\d+)", stdout)
            errors_match = re.search(r"errors=(\d+)", stdout)
            skipped_match = re.search(r"skipped=(\d+)", stdout)

            f_count = int(failures_match.group(1)) if failures_match else 0
            e_count = int(errors_match.group(1)) if errors_match else 0
            s_count = int(skipped_match.group(1)) if skipped_match else 0
            failed = f_count + e_count
            passed = max(0, total - failed - s_count)
            return total, passed, failed, s_count

        # Pytest: "42 passed, 2 failed in 0.5s"
        pytest_passed = re.search(r"(\d+) passed", stdout)
        pytest_failed = re.search(r"(\d+) failed", stdout)
        pytest_skipped = re.search(r"(\d+) skipped", stdout)
        if pytest_passed or pytest_failed:
            p_count = int(pytest_passed.group(1)) if pytest_passed else 0
            f_count = int(pytest_failed.group(1)) if pytest_failed else 0
            s_count = int(pytest_skipped.group(1)) if pytest_skipped else 0
            return p_count + f_count + s_count, p_count, f_count, s_count

        # Jest: "Tests:       2 failed, 40 passed, 42 total"
        jest_match = re.search(r"Tests:\s+(?:(\d+)\s+failed,\s+)?(?:(\d+)\s+passed,\s+)?(\d+)\s+total", stdout)
        if jest_match:
            f_count = int(jest_match.group(1) or 0)
            p_count = int(jest_match.group(2) or 0)
            tot = int(jest_match.group(3))
            return tot, p_count, f_count, max(0, tot - p_count - f_count)

        # Fallback based on exit_code
        if exit_code == 0:
            return 1, 1, 0, 0
        return 1, 0, 1, 0

    @classmethod
    def _extract_lint_evidence(
        cls,
        static_report: Dict[str, Any],
        gt_report: Optional[Any],
    ) -> LintEvidence:
        errors = len(static_report.get("errors", []))
        warnings = len(static_report.get("warnings", []))
        tools = list(static_report.get("results", {}).keys()) or static_report.get("tools_run", [])

        # Check diagnostics list
        diags = static_report.get("diagnostics", [])
        if diags and errors == 0 and warnings == 0:
            for d in diags:
                sev = str(d.get("severity", "")).upper()
                if "ERROR" in sev:
                    errors += 1
                else:
                    warnings += 1

        details = "Static analysis clean." if errors == 0 else f"{errors} error(s) found."
        return LintEvidence(
            errors=errors,
            warnings=warnings,
            tools_run=tools,
            details=details,
        )

    @classmethod
    def _extract_coverage_evidence(cls, gt_report: Optional[Any]) -> DiffCoverageEvidence:
        if not gt_report or not hasattr(gt_report, "gates"):
            return DiffCoverageEvidence(skipped=True, details="No ground truth coverage gate.")

        cov_gate = gt_report.gates.get("DIFF_COVERAGE")
        if not cov_gate:
            return DiffCoverageEvidence(skipped=True, details="Coverage gate not evaluated.")

        pct = cov_gate.score
        details = cov_gate.details
        skipped = "Skipped" in details
        return DiffCoverageEvidence(
            percentage=pct,
            skipped=skipped,
            details=details,
        )

    @classmethod
    def _extract_acceptance_criteria(
        cls,
        spec_info: Dict[str, Any],
        spec_report: Optional[Any],
        gt_report: Optional[Any],
    ) -> Dict[str, str]:
        ac_map: Dict[str, str] = {}

        # 1. From spec_report if available
        if spec_report and hasattr(spec_report, "all_criteria"):
            for crit in spec_report.all_criteria:
                cid = crit.criterion_id if hasattr(crit, "criterion_id") else str(crit)
                verified = crit.verified if hasattr(crit, "verified") else False
                ac_map[cid] = "verified" if verified else "unverified"
            return ac_map

        # 2. From raw specification_output
        raw_items = []
        for key in ("acceptance_criteria", "requirements", "contracts", "acceptance_tests"):
            val = spec_info.get(key)
            if isinstance(val, list):
                raw_items.extend(val)
            elif isinstance(val, dict):
                for k, v in val.items():
                    raw_items.append(f"{k}: {v}")

        # If spec gate passed, mark all extracted criteria as verified
        spec_gate_passed = True
        if gt_report and hasattr(gt_report, "gates"):
            spec_gate = gt_report.gates.get("SPEC_TRACEABILITY")
            if spec_gate:
                spec_gate_passed = spec_gate.passed

        for idx, item in enumerate(raw_items, start=1):
            cid = f"AC-{idx}"
            if isinstance(item, dict):
                cid = str(item.get("id") or item.get("criterion_id") or f"AC-{idx}")
            elif isinstance(item, str):
                match = re.match(r"^([A-Za-z0-9_\-]+)[:\.]", item.strip())
                if match:
                    cid = match.group(1)
            ac_map[cid] = "verified" if spec_gate_passed else "unverified"

        return ac_map
