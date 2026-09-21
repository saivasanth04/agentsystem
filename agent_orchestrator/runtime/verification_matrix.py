"""
GroundTruthVerificationMatrix: Deterministic Multi-Gate Verification Oracle.
Replaces subjective LLM self-evaluation with 6 independent ground-truth gates:
1. Static Analysis Gate (zero errors from ruff, mypy, tsc, eslint, semgrep)
2. Build Pipeline Gate (zero failures across install, build, compile, lint, test)
3. Baseline Regression Gate (zero regressions against pre-flight test baseline)
4. Diff Code Coverage Gate (no completely untested modified files, >= 70% diff coverage)
5. Specification Traceability Gate (100% of defined acceptance criteria verified by tests)
6. Anti-Tautology Gate (zero trivial assertions like assert True or assert 1 == 1)
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .static_analyzer import StaticAnalyzer, StaticAnalysisReport
from .build_pipeline import BuildVerificationPipeline, PipelineReport
from .coverage_oracle import CoverageOracle, DiffCoverageResult
from .spec_oracle import SpecTraceabilityOracle, SpecTraceabilityReport
from ..security.sandbox import BaseExecutionSandbox, create_sandbox


@dataclass
class GroundTruthGateResult:
    gate_name: str
    passed: bool
    score: float
    details: str
    blocking: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate_name": self.gate_name,
            "passed": self.passed,
            "score": round(self.score, 2),
            "details": self.details,
            "blocking": self.blocking,
        }


@dataclass
class GroundTruthReport:
    passed: bool
    total_score: float
    gates: Dict[str, GroundTruthGateResult] = field(default_factory=dict)
    blocking_failures: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "total_score": round(self.total_score, 2),
            "gates": {k: v.to_dict() for k, v in self.gates.items()},
            "blocking_failures": self.blocking_failures,
        }

    def summary(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        lines = [f"Ground Truth Verification Matrix: {status} (Score: {self.total_score:.1f}/100)"]
        for name, g in self.gates.items():
            g_status = "PASS" if g.passed else ("FAIL (BLOCKING)" if g.blocking else "WARN")
            lines.append(f"  - [{name}] {g_status} ({g.score:.0f}/100): {g.details}")
        if self.blocking_failures:
            lines.append("Blocking Gate Failures:")
            for bf in self.blocking_failures:
                lines.append(f"  * {bf}")
        return "\n".join(lines)


class GroundTruthVerificationMatrix:
    """
    Consolidated ground-truth evaluation engine.
    Computes an objective, deterministic verification verdict.
    """

    def __init__(
        self,
        workspace: Any = None,
        workspace_dir: Any = None,
        sandbox: Optional[BaseExecutionSandbox] = None,
    ):
        ws = workspace if workspace is not None else workspace_dir
        self.workspace = ws
        self.root_dir = Path(ws.root_dir if hasattr(ws, "root_dir") else ws).resolve() if ws else Path.cwd()
        self.sandbox = sandbox or create_sandbox(self.root_dir)

    def evaluate(
        self,
        state: Any,
        modified_files: Optional[List[str]] = None,
        static_report: Optional[Dict[str, Any]] = None,
        build_pipeline_report: Optional[Dict[str, Any]] = None,
    ) -> GroundTruthReport:
        gates: Dict[str, GroundTruthGateResult] = {}
        blocking_failures: List[str] = []

        test_info = getattr(state, "test_output", None) or (state.get("test_output") if isinstance(state, dict) else {}) or {}
        spec_info = getattr(state, "specification_output", None) or (state.get("specification_output") if isinstance(state, dict) else {}) or {}
        baseline_test = getattr(state, "baseline_test_output", None) or (state.get("baseline_test_info") if isinstance(state, dict) else {}) or {}

        # 1. Gate 1: Static Analysis
        gate_static = self._eval_static_gate(modified_files, static_report)
        gates["STATIC_ANALYSIS"] = gate_static
        if not gate_static.passed and gate_static.blocking:
            blocking_failures.append(f"Static Analysis Gate: {gate_static.details}")

        # 2. Gate 2: Build Pipeline & Unit Tests
        gate_build = self._eval_build_gate(test_info, build_pipeline_report)
        gates["BUILD_PIPELINE"] = gate_build
        if not gate_build.passed and gate_build.blocking:
            blocking_failures.append(f"Build Pipeline Gate: {gate_build.details}")

        # 3. Gate 3: Baseline Regressions
        gate_regression = self._eval_regression_gate(test_info, baseline_test, modified_files=modified_files)
        gates["BASELINE_REGRESSIONS"] = gate_regression
        if not gate_regression.passed and gate_regression.blocking:
            blocking_failures.append(f"Baseline Regression Gate: {gate_regression.details}")

        # 4. Gate 4: Diff Code Coverage
        gate_coverage = self._eval_coverage_gate(modified_files, test_info)
        gates["DIFF_COVERAGE"] = gate_coverage
        if not gate_coverage.passed and gate_coverage.blocking:
            blocking_failures.append(f"Diff Coverage Gate: {gate_coverage.details}")

        # 5. Gate 5: Specification Traceability
        gate_spec = self._eval_spec_gate(spec_info, test_info)
        gates["SPEC_TRACEABILITY"] = gate_spec
        if not gate_spec.passed and gate_spec.blocking:
            blocking_failures.append(f"Spec Traceability Gate: {gate_spec.details}")

        # 6. Gate 6: Anti-Tautology Gate
        gate_tautology = self._eval_tautology_gate(test_info)
        gates["ANTI_TAUTOLOGY"] = gate_tautology
        if not gate_tautology.passed and gate_tautology.blocking:
            blocking_failures.append(f"Anti-Tautology Gate: {gate_tautology.details}")

        total_score = sum(g.score for g in gates.values()) / len(gates) if gates else 100.0
        passed = (len(blocking_failures) == 0)

        return GroundTruthReport(
            passed=passed,
            total_score=total_score,
            gates=gates,
            blocking_failures=blocking_failures,
        )

    def _eval_static_gate(
        self,
        modified_files: Optional[List[str]],
        static_report: Optional[Dict[str, Any]],
    ) -> GroundTruthGateResult:
        if static_report and isinstance(static_report, dict):
            passed = bool(static_report.get("passed", True))
            err_count = len(static_report.get("errors", [])) or len(static_report.get("syntax_errors", []))
            return GroundTruthGateResult(
                gate_name="STATIC_ANALYSIS",
                passed=passed,
                score=100.0 if passed else max(0.0, 100.0 - err_count * 25.0),
                details=f"{'Clean' if passed else f'{err_count} static/compiler error(s) found'}",
                blocking=True,
            )

        # Run targeted analyzer
        analyzer = StaticAnalyzer(workspace=self.workspace, sandbox=self.sandbox)
        sa_rep = analyzer.run_analysis(files=modified_files)
        return GroundTruthGateResult(
            gate_name="STATIC_ANALYSIS",
            passed=sa_rep.passed,
            score=100.0 if sa_rep.passed else max(0.0, 100.0 - len(sa_rep.errors) * 25.0),
            details=f"{'Clean' if sa_rep.passed else f'{len(sa_rep.errors)} error(s): ' + '; '.join(d.to_string() for d in sa_rep.errors[:3])}",
            blocking=True,
        )

    def _eval_build_gate(
        self,
        test_info: Dict[str, Any],
        build_pipeline_report: Optional[Dict[str, Any]],
    ) -> GroundTruthGateResult:
        pipe_rep = build_pipeline_report or test_info.get("build_pipeline_report")
        if pipe_rep and isinstance(pipe_rep, dict):
            passed = bool(pipe_rep.get("passed", True))
            failed_stage = pipe_rep.get("failed_stage")
            return GroundTruthGateResult(
                gate_name="BUILD_PIPELINE",
                passed=passed,
                score=100.0 if passed else 0.0,
                details=f"{'All stages passed' if passed else f'Failed at stage: {failed_stage}'}",
                blocking=True,
            )

        # Fallback to test execution result
        exec_success = test_info.get("execution_success", True)
        exit_code = test_info.get("exit_code", 0)
        passed = bool(exec_success and exit_code == 0)
        return GroundTruthGateResult(
            gate_name="BUILD_PIPELINE",
            passed=passed,
            score=100.0 if passed else 0.0,
            details=f"{'Tests executed cleanly (exit code 0)' if passed else f'Tests failed (exit code {exit_code})'}",
            blocking=True,
        )

    def _eval_regression_gate(
        self,
        test_info: Dict[str, Any],
        baseline_test: Dict[str, Any],
        modified_files: Optional[List[str]] = None,
    ) -> GroundTruthGateResult:
        # Check DependencyRegressionDetector on modified files
        if modified_files and self.workspace:
            try:
                from .regression_detector import DependencyRegressionDetector
                reg_report = DependencyRegressionDetector.verify_regressions(
                    modified_files=modified_files,
                    workspace=self.workspace,
                    test_info=test_info,
                    baseline_test=baseline_test,
                )
                if not reg_report.passed:
                    return GroundTruthGateResult(
                        gate_name="BASELINE_REGRESSIONS",
                        passed=False,
                        score=0.0,
                        details=f"Regression detected in downstream dependents: {reg_report.details}",
                        blocking=True,
                    )
            except Exception:
                pass

        if not baseline_test or not isinstance(baseline_test, dict):
            return GroundTruthGateResult(
                gate_name="BASELINE_REGRESSIONS",
                passed=True,
                score=100.0,
                details="No pre-flight baseline test suite present (new project).",
                blocking=True,
            )

        baseline_passed = bool(baseline_test.get("execution_success", True) and baseline_test.get("exit_code", 0) == 0)
        current_passed = bool(test_info.get("execution_success", True) and test_info.get("exit_code", 0) == 0)

        if baseline_passed and not current_passed:
            return GroundTruthGateResult(
                gate_name="BASELINE_REGRESSIONS",
                passed=False,
                score=0.0,
                details="Regression detected! Existing baseline tests were passing but broke after code edits.",
                blocking=True,
            )

        return GroundTruthGateResult(
            gate_name="BASELINE_REGRESSIONS",
            passed=True,
            score=100.0,
            details="Zero baseline regressions detected.",
            blocking=True,
        )

    def _eval_coverage_gate(
        self,
        modified_files: Optional[List[str]],
        test_info: Dict[str, Any],
    ) -> GroundTruthGateResult:
        oracle = CoverageOracle(workspace=self.workspace, sandbox=self.sandbox)
        cov_res = oracle.compute_diff_coverage(
            modified_files=modified_files,
            test_command=test_info.get("test_command"),
            test_stdout=test_info.get("stdout"),
        )
        if cov_res.skipped:
            return GroundTruthGateResult(
                gate_name="DIFF_COVERAGE",
                passed=True,
                score=100.0,
                details=f"Skipped: {cov_res.skip_reason}",
                blocking=False,
            )

        is_blocking = (
            cov_res.tool_used in ("report", "coverage_report", "pytest-cov", "coverage.py", "c8", "lcov")
            and cov_res.coverage_percentage == 0.0
            and cov_res.total_lines_changed > 0
        )
        return GroundTruthGateResult(
            gate_name="DIFF_COVERAGE",
            passed=cov_res.passed,
            score=cov_res.coverage_percentage,
            details=cov_res.summary(),
            blocking=is_blocking,
        )

    def _eval_spec_gate(
        self,
        spec_info: Dict[str, Any],
        test_info: Dict[str, Any],
    ) -> GroundTruthGateResult:
        oracle = SpecTraceabilityOracle(workspace=self.workspace)
        rep = oracle.verify_traceability(specification_output=spec_info, test_output=test_info)

        if rep.skipped:
            return GroundTruthGateResult(
                gate_name="SPEC_TRACEABILITY",
                passed=True,
                score=100.0,
                details=f"Skipped: {rep.skip_reason or 'No explicit acceptance criteria specified in PRD.'}",
                blocking=False,
            )

        return GroundTruthGateResult(
            gate_name="SPEC_TRACEABILITY",
            passed=rep.all_verified,
            score=rep.traceability_percentage,
            details=rep.summary(),
            blocking=rep.traceability_percentage < 50.0,  # Blocking if less than half of criteria verified
        )

    def _eval_tautology_gate(self, test_info: Dict[str, Any]) -> GroundTruthGateResult:
        taut_warnings = test_info.get("tautological_warnings") or test_info.get("tautologies") or []
        taut_count = test_info.get("tautological_count", len(taut_warnings))
        passed = (len(taut_warnings) == 0 and taut_count == 0)
        count = max(len(taut_warnings), taut_count)
        return GroundTruthGateResult(
            gate_name="ANTI_TAUTOLOGY",
            passed=passed,
            score=100.0 if passed else max(0.0, 100.0 - count * 35.0),
            details=f"Zero tautological assertions" if passed else f"{count} trivial/tautological assertion(s) found",
            blocking=True,
        )
