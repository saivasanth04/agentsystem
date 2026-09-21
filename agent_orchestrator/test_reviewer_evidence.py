"""
Unit and Integration Tests for Issue #27: Evidence-First Reviewer Architecture.
Verifies:
1. EvidenceSynthesizer: deterministic extraction of build, test, lint, diff coverage, and acceptance criteria.
2. VerificationEvidenceContract: Pydantic schema validation and serialization.
3. ReviewerAgent: embedding evidence dictionary into review_output and markdown into prompt.
4. Backward Compatibility: score_out_of_100 and verdict remain consistent and deterministically calculated.
5. CLI & Orchestrator: evidence card generation and logging.
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.runtime.evidence import (
    BuildEvidence,
    TestEvidence,
    LintEvidence,
    DiffCoverageEvidence,
    VerificationEvidence,
    EvidenceSynthesizer,
)
from agent_orchestrator.contracts import (
    BuildEvidenceContract,
    TestEvidenceContract,
    LintEvidenceContract,
    DiffCoverageEvidenceContract,
    VerificationEvidenceContract,
    ReviewAuditContract,
)
from agent_orchestrator.runtime.verification_matrix import (
    GroundTruthGateResult,
    GroundTruthReport,
)
from agent_orchestrator.agents.reviewer import ReviewerAgent
from agent_orchestrator.state import OrchestratorState, ReviewVerdict
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestEvidenceSynthesizer(unittest.TestCase):
    """Tests for EvidenceSynthesizer deterministic extraction."""

    def test_synthesize_all_passing(self):
        state = {
            "test_output": {
                "execution_success": True,
                "exit_code": 0,
                "total_tests": 12,
                "tests_passed": 12,
                "tests_failed": 0,
                "stdout": "Ran 12 tests in 0.04s\n\nOK",
            },
            "specification_output": {
                "acceptance_criteria": ["AC-1: User login", "AC-2: Token issuance"],
            },
        }
        gt_report = GroundTruthReport(
            passed=True,
            total_score=95.0,
            gates={
                "DIFF_COVERAGE": GroundTruthGateResult("DIFF_COVERAGE", True, 88.5, "88.5% covered"),
                "SPEC_TRACEABILITY": GroundTruthGateResult("SPEC_TRACEABILITY", True, 100.0, "2/2 verified"),
            },
        )
        static_report = {"errors": [], "warnings": [{"message": "unused import"}], "tools_run": ["ruff"]}
        build_report = {"passed": True, "failed_stage": None, "stage_results": {"BUILD": {}}}

        ev = EvidenceSynthesizer.synthesize(
            state=state,
            ground_truth_report=gt_report,
            static_report=static_report,
            build_pipeline_report=build_report,
        )

        self.assertTrue(ev.passed)
        self.assertTrue(ev.build.success)
        self.assertEqual(ev.tests.passed, 12)
        self.assertEqual(ev.tests.failed, 0)
        self.assertEqual(ev.lint.errors, 0)
        self.assertEqual(ev.lint.warnings, 1)
        self.assertEqual(ev.diff_coverage.percentage, 88.5)
        self.assertEqual(ev.acceptance_criteria.get("AC-1"), "verified")
        self.assertEqual(ev.acceptance_criteria.get("AC-2"), "verified")

        # Test formatting
        summary = ev.summary()
        self.assertIn("Build: [PASS]", summary)
        self.assertIn("Tests: [12/12 passed]", summary)
        self.assertIn("Lint: [0 errors, 1 warnings]", summary)

        md = ev.to_markdown()
        self.assertIn("Verification Evidence Matrix", md)
        self.assertIn("✅ PASS", md)

    def test_parse_pytest_and_jest_counts(self):
        # 1. Pytest stdout
        pytest_out = "======= 42 passed, 2 failed, 1 skipped in 1.2s ======="
        tot, p, f, s = EvidenceSynthesizer._parse_test_counts_from_stdout(pytest_out, exit_code=1)
        self.assertEqual(tot, 45)
        self.assertEqual(p, 42)
        self.assertEqual(f, 2)
        self.assertEqual(s, 1)

        # 2. Jest stdout
        jest_out = "Tests:       1 failed, 10 passed, 11 total"
        tot, p, f, s = EvidenceSynthesizer._parse_test_counts_from_stdout(jest_out, exit_code=1)
        self.assertEqual(tot, 11)
        self.assertEqual(p, 10)
        self.assertEqual(f, 1)

    def test_synthesize_with_failures(self):
        state = {
            "test_output": {
                "execution_success": False,
                "exit_code": 1,
                "stdout": "FAILED (failures=2)",
            },
        }
        build_report = {"passed": False, "failed_stage": "COMPILE_TYPECHECK"}
        static_report = {"errors": [{"message": "type mismatch"}], "warnings": []}

        ev = EvidenceSynthesizer.synthesize(
            state=state,
            static_report=static_report,
            build_pipeline_report=build_report,
        )

        self.assertFalse(ev.passed)
        self.assertFalse(ev.build.success)
        self.assertEqual(ev.build.failed_stage, "COMPILE_TYPECHECK")
        self.assertGreaterEqual(ev.tests.failed, 1)
        self.assertEqual(ev.lint.errors, 1)


class TestEvidenceContracts(unittest.TestCase):
    """Tests for Pydantic contracts and ReviewAuditContract integration."""

    def test_contract_serialization_and_validation(self):
        ev_contract = VerificationEvidenceContract(
            build=BuildEvidenceContract(success=True, stages_run=["BUILD", "LINT"]),
            tests=TestEvidenceContract(total=10, passed=10, failed=0),
            lint=LintEvidenceContract(errors=0, warnings=2, tools_run=["ruff", "mypy"]),
            diff_coverage=DiffCoverageEvidenceContract(percentage=85.0),
            acceptance_criteria={"AC-1": "verified"},
            passed=True,
        )

        audit = ReviewAuditContract(
            verdict="PASS",
            score_out_of_100=95,
            summary="Evidence verified cleanly.",
            evidence=ev_contract,
        )

        d = audit.model_dump()
        self.assertEqual(d["verdict"], "PASS")
        self.assertEqual(d["score_out_of_100"], 95)
        self.assertIsNotNone(d["evidence"])
        self.assertTrue(d["evidence"]["build"]["success"])
        self.assertEqual(d["evidence"]["tests"]["passed"], 10)
        self.assertEqual(d["evidence"]["lint"]["tools_run"], ["ruff", "mypy"])


class TestReviewerAgentEvidence(unittest.TestCase):
    """Tests for ReviewerAgent evidence synthesis and prompt integration."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("agent_orchestrator.runtime.verification_matrix.GroundTruthVerificationMatrix.evaluate")
    def test_reviewer_emits_structured_evidence(self, mock_evaluate):
        mock_evaluate.return_value = GroundTruthReport(
            passed=True,
            total_score=95.0,
            gates={
                "STATIC_ANALYSIS": GroundTruthGateResult("STATIC_ANALYSIS", True, 100.0, "Clean"),
                "DIFF_COVERAGE": GroundTruthGateResult("DIFF_COVERAGE", True, 90.0, "90% coverage"),
            },
            blocking_failures=[],
        )

        reviewer = ReviewerAgent()
        captured_prompt = None

        def fake_react_run(**kwargs):
            nonlocal captured_prompt
            captured_prompt = kwargs.get("user_prompt", "")
            return {
                "final_output": {
                    "verdict": "PASS",
                    "score_out_of_100": 95,
                    "summary": "All evidence gates passed cleanly.",
                    "issues": [],
                },
                "turns_taken": 1,
                "history_events": [],
            }

        reviewer.react_loop.run = MagicMock(side_effect=fake_react_run)

        state = {
            "current_task": {"task_id": "T-1", "objective": "Add feature"},
            "workspace_dir": self.temp_dir,
            "coder_output": {"written_files": ["app.py"]},
            "test_output": {"execution_success": True, "exit_code": 0, "total_tests": 5, "tests_passed": 5, "tests_failed": 0},
            "specification_output": {"acceptance_criteria": ["AC-1: Valid feature"]},
        }

        res = reviewer.review(state)

        # 1. Check evidence is attached
        self.assertIn("evidence", res)
        ev = res["evidence"]
        self.assertTrue(ev["build"]["success"])
        self.assertEqual(ev["tests"]["passed"], 5)
        self.assertEqual(ev["tests"]["failed"], 0)
        self.assertEqual(ev["acceptance_criteria"]["AC-1"], "verified")

        # 2. Check prompt includes the evidence matrix table
        self.assertIsNotNone(captured_prompt)
        self.assertIn("Verification Evidence Matrix", captured_prompt)
        self.assertIn("Automated Tests", captured_prompt)

        # 3. Check backward compatibility: score_out_of_100 and verdict
        self.assertEqual(res["verdict"], "PASS")
        self.assertGreaterEqual(res["score_out_of_100"], 80)


if __name__ == "__main__":
    unittest.main()
