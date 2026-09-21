"""
Unit and Integration Tests for Issue #26: Independent Verifier & Ground-Truth Oracle.
Verifies:
1. CoverageOracle: diff code coverage measurement, lcov/json parsing, and zero-coverage detection.
2. SpecTraceabilityOracle: acceptance criteria extraction, test-to-spec mapping, and unverified criteria detection.
3. GroundTruthVerificationMatrix: 6 independent deterministic gates, scoring, and blocking failures.
4. Reviewer Ground-Truth Veto Rule: LLM rubber-stamp PASS is deterministically overridden to FAIL on ground-truth failure.
5. TaskVerificationGate: integration of GroundTruthReport into task verification lifecycle.
6. verify_ground_truth Builtin Tool: tool availability across PLANNER, CODER, TESTER, REVIEWER and valid execution.
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.runtime.coverage_oracle import (
    CoverageOracle,
    DiffCoverageResult,
)
from agent_orchestrator.runtime.spec_oracle import (
    AcceptanceCriterion,
    SpecTraceabilityOracle,
    SpecTraceabilityReport,
)
from agent_orchestrator.runtime.verification_matrix import (
    GroundTruthGateResult,
    GroundTruthReport,
    GroundTruthVerificationMatrix,
)
from agent_orchestrator.runtime.verification import TaskVerificationGate, VerificationResult
from agent_orchestrator.runtime.task_graph import ExecutableTask
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.agents.reviewer import ReviewerAgent
from agent_orchestrator.state import OrchestratorState


class TestCoverageOracle(unittest.TestCase):
    """Tests for CoverageOracle parsing, diff coverage measurement, and AST heuristics."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_parse_coverage_json(self):
        cov_data = {
            "files": {
                "src/math.py": {
                    "executed_lines": [1, 2, 3, 5],
                    "missing_lines": [6, 7],
                }
            }
        }
        cov_file = Path(self.temp_dir) / "coverage.json"
        cov_file.write_text(json.dumps(cov_data), encoding="utf-8")

        oracle = CoverageOracle(workspace=self.workspace)
        parsed = oracle.parse_coverage_json(cov_file)
        self.assertIn("src/math.py", parsed)
        self.assertEqual(parsed["src/math.py"]["executed"], {1, 2, 3, 5})
        self.assertEqual(parsed["src/math.py"]["missing"], {6, 7})

    def test_parse_lcov(self):
        lcov_content = (
            "SF:src/service.js\n"
            "DA:1,1\n"
            "DA:2,1\n"
            "DA:3,0\n"
            "DA:4,0\n"
            "end_of_record\n"
        )
        lcov_file = Path(self.temp_dir) / "lcov.info"
        lcov_file.write_text(lcov_content, encoding="utf-8")

        oracle = CoverageOracle(workspace=self.workspace)
        parsed = oracle.parse_lcov(lcov_file)
        self.assertIn("src/service.js", parsed)
        self.assertEqual(parsed["src/service.js"]["executed"], {1, 2})
        self.assertEqual(parsed["src/service.js"]["missing"], {3, 4})

    def test_measure_diff_coverage_sufficient(self):
        oracle = CoverageOracle(workspace=self.workspace)
        cov_map = {
            "src/calc.py": {
                "executed": {10, 11, 12, 13},
                "missing": {14},
            }
        }
        modified_lines = {"src/calc.py": [10, 11, 12, 13, 14]}
        result = oracle.measure_diff_coverage(modified_lines, cov_map)

        self.assertEqual(result.total_modified_executable_lines, 5)
        self.assertEqual(result.covered_modified_lines, 4)
        self.assertEqual(result.diff_coverage_percent, 80.0)
        self.assertTrue(result.passed)

    def test_measure_diff_coverage_zero_or_insufficient(self):
        oracle = CoverageOracle(workspace=self.workspace, min_diff_coverage=70.0)
        cov_map = {
            "src/calc.py": {
                "executed": {1},
                "missing": {10, 11, 12, 13, 14},
            }
        }
        modified_lines = {"src/calc.py": [10, 11, 12, 13, 14]}
        result = oracle.measure_diff_coverage(modified_lines, cov_map)

        self.assertEqual(result.diff_coverage_percent, 0.0)
        self.assertFalse(result.passed)
        self.assertIn("src/calc.py", result.uncovered_files)


class TestSpecTraceabilityOracle(unittest.TestCase):
    """Tests for SpecTraceabilityOracle extracting criteria and mapping to tests."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_extract_criteria_given_when_then(self):
        spec_text = (
            "Feature: User Authentication\n"
            "Scenario 1:\n"
            "Given a registered user\n"
            "When the user enters valid credentials\n"
            "Then return a 200 OK and JWT token\n\n"
            "Scenario 2:\n"
            "Given an invalid password\n"
            "When login is attempted\n"
            "Then return 401 Unauthorized\n"
        )
        oracle = SpecTraceabilityOracle(workspace=self.workspace)
        criteria = oracle.extract_criteria_from_spec(spec_text)

        self.assertGreaterEqual(len(criteria), 2)
        descriptions = [c.description for c in criteria]
        self.assertTrue(any("200 OK" in d for d in descriptions))
        self.assertTrue(any("401 Unauthorized" in d for d in descriptions))

    def test_extract_criteria_checkboxes(self):
        spec_text = (
            "Acceptance Criteria:\n"
            "- [ ] AC-1: Must validate email format before saving\n"
            "- [ ] AC-2: Must hash password with bcrypt\n"
            "- [ ] AC-3: Must send verification email upon registration\n"
        )
        oracle = SpecTraceabilityOracle(workspace=self.workspace)
        criteria = oracle.extract_criteria_from_spec(spec_text)

        self.assertEqual(len(criteria), 3)
        self.assertEqual(criteria[0].criterion_id, "AC-1")

    def test_verify_traceability_passing(self):
        test_file = Path(self.temp_dir) / "test_auth.py"
        test_file.write_text(
            "import unittest\n\n"
            "class TestAuth(unittest.TestCase):\n"
            "    def test_validate_email_format(self):\n"
            "        '''AC-1: Validates email format before saving.'''\n"
            "        self.assertTrue(True)\n\n"
            "    def test_hash_password_bcrypt(self):\n"
            "        '''AC-2: Hashes password with bcrypt.'''\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )

        spec = (
            "- [ ] AC-1: validate email format\n"
            "- [ ] AC-2: hash password with bcrypt\n"
        )
        oracle = SpecTraceabilityOracle(workspace=self.workspace)
        report = oracle.verify_traceability(spec, test_files=[str(test_file)])

        self.assertEqual(report.total_criteria, 2)
        self.assertEqual(report.verified_criteria, 2)
        self.assertEqual(report.coverage_percent, 100.0)
        self.assertTrue(report.passed)

    def test_verify_traceability_failing_threshold(self):
        test_file = Path(self.temp_dir) / "test_empty.py"
        test_file.write_text("def test_nothing(): pass\n", encoding="utf-8")

        spec = (
            "- [ ] AC-1: validate email format\n"
            "- [ ] AC-2: hash password with bcrypt\n"
            "- [ ] AC-3: send verification email\n"
        )
        oracle = SpecTraceabilityOracle(workspace=self.workspace)
        report = oracle.verify_traceability(spec, test_files=[str(test_file)])

        self.assertEqual(report.verified_criteria, 0)
        self.assertFalse(report.passed)
        self.assertEqual(len(report.unverified_criteria), 3)


class TestGroundTruthVerificationMatrix(unittest.TestCase):
    """Tests for GroundTruthVerificationMatrix multi-gate evaluation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_all_gates_pass(self):
        matrix = GroundTruthVerificationMatrix(workspace=self.workspace)
        state = {
            "test_output": {
                "execution_success": True,
                "exit_code": 0,
                "passed": True,
                "total_tests": 5,
                "tautological_count": 0,
            },
            "specification_output": {
                "spec_content": "- [ ] AC-1: basic health check\n",
            },
            "baseline_test_info": {
                "baseline_passed": 5,
                "baseline_failed": 0,
            },
        }
        # Pre-seed a test that maps to AC-1
        test_file = Path(self.temp_dir) / "test_health.py"
        test_file.write_text("def test_basic_health_check(): assert True\n", encoding="utf-8")

        static_report = {"passed": True, "errors": []}
        build_pipeline_report = {"passed": True, "failed_stage": None}

        report = matrix.evaluate(
            state=state,
            modified_files=["test_health.py"],
            static_report=static_report,
            build_pipeline_report=build_pipeline_report,
        )

        self.assertTrue(report.passed)
        self.assertGreaterEqual(report.total_score, 80.0)
        self.assertEqual(len(report.blocking_failures), 0)

    def test_blocking_failure_on_static_analysis(self):
        matrix = GroundTruthVerificationMatrix(workspace=self.workspace)
        state = {"test_output": {"execution_success": True, "exit_code": 0}}
        static_report = {
            "passed": False,
            "errors": [{"file": "app.py", "message": "SyntaxError", "severity": "ERROR"}],
        }

        report = matrix.evaluate(
            state=state,
            static_report=static_report,
        )

        self.assertFalse(report.passed)
        self.assertTrue(any("Static Analysis Gate" in f for f in report.blocking_failures))

    def test_blocking_failure_on_tautological_tests(self):
        matrix = GroundTruthVerificationMatrix(workspace=self.workspace)
        state = {
            "test_output": {
                "execution_success": True,
                "exit_code": 0,
                "tautological_count": 3,
                "tautologies": ["assert True", "assert 1 == 1", "assert 'a' == 'a'"],
            }
        }
        static_report = {"passed": True, "errors": []}
        build_report = {"passed": True}

        report = matrix.evaluate(
            state=state,
            static_report=static_report,
            build_pipeline_report=build_report,
        )

        self.assertFalse(report.passed)
        self.assertTrue(any("Anti-Tautology Gate" in f for f in report.blocking_failures))


class TestReviewerGroundTruthVeto(unittest.TestCase):
    """Tests that ReviewerAgent deterministically overrides LLM PASS if ground-truth fails."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("agent_orchestrator.runtime.verification_matrix.GroundTruthVerificationMatrix.evaluate")
    def test_reviewer_veto_rule_overrides_llm_pass(self, mock_evaluate):
        # Ground truth fails
        mock_evaluate.return_value = GroundTruthReport(
            passed=False,
            total_score=35.0,
            gates={
                "STATIC_ANALYSIS": GroundTruthGateResult("STATIC_ANALYSIS", False, 0.0, "SyntaxError on line 12", True),
                "DIFF_COVERAGE": GroundTruthGateResult("DIFF_COVERAGE", False, 0.0, "0% diff coverage on new code", True),
            },
            blocking_failures=[
                "Static Analysis Gate: SyntaxError on line 12",
                "Diff Coverage Gate: 0% diff coverage on new code",
            ],
        )

        reviewer = ReviewerAgent()
        reviewer.react_loop.run = MagicMock(return_value={
            "final_output": {
                "verdict": "PASS",
                "score_out_of_100": 95,
                "issues": [],
                "target_agent_for_fix": None,
            },
            "turns_taken": 1,
            "history_events": [],
        })
        state = {
            "current_task": {"task_id": "T-1", "objective": "Add auth endpoint"},
            "workspace_dir": self.temp_dir,
            "coder_output": {"modified_files": ["src/auth.py"]},
            "test_output": {"execution_success": True},
        }

        review_result = reviewer.review(state)

        # Ground-truth veto MUST force FAIL
        self.assertEqual(review_result["verdict"], "FAIL")
        self.assertLessEqual(review_result["score_out_of_100"], 45.0)
        self.assertIn("ground_truth_report", review_result)
        self.assertFalse(review_result["ground_truth_report"]["passed"])
        # Must flag the failure in issues
        components = [i.get("component") for i in review_result.get("issues", [])]
        self.assertIn("GroundTruthMatrix", components)
        # Should identify CODER for static analysis or TESTER for diff coverage
        self.assertIn(review_result["target_agent_for_fix"], ["CODER", "TESTER"])

    @patch("agent_orchestrator.runtime.verification_matrix.GroundTruthVerificationMatrix.evaluate")
    def test_reviewer_pass_when_ground_truth_passes(self, mock_evaluate):
        # Ground truth passes
        mock_evaluate.return_value = GroundTruthReport(
            passed=True,
            total_score=92.0,
            gates={
                "STATIC_ANALYSIS": GroundTruthGateResult("STATIC_ANALYSIS", True, 100.0, "Clean", True),
                "DIFF_COVERAGE": GroundTruthGateResult("DIFF_COVERAGE", True, 85.0, "85% coverage", True),
            },
            blocking_failures=[],
        )

        reviewer = ReviewerAgent()
        reviewer.react_loop.run = MagicMock(return_value={
            "final_output": {
                "verdict": "PASS",
                "score_out_of_100": 92,
                "issues": [],
                "target_agent_for_fix": None,
            },
            "turns_taken": 1,
            "history_events": [],
        })
        state = {
            "current_task": {"task_id": "T-1", "objective": "Add auth endpoint"},
            "workspace_dir": self.temp_dir,
            "coder_output": {"modified_files": ["src/auth.py"]},
            "test_output": {"execution_success": True},
        }

        review_result = reviewer.review(state)
        self.assertEqual(review_result["verdict"], "PASS")
        self.assertEqual(review_result["score_out_of_100"], 92)


class TestTaskVerificationGateGroundTruth(unittest.TestCase):
    """Tests for TaskVerificationGate integrating GroundTruthVerificationMatrix."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)
        self.task = ExecutableTask(
            task_id="T-01",
            objective="Implement user service",
            outputs=["src/user.py"],
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("agent_orchestrator.runtime.verification_matrix.GroundTruthVerificationMatrix.evaluate")
    def test_gate_fails_when_ground_truth_fails(self, mock_evaluate):
        mock_evaluate.return_value = GroundTruthReport(
            passed=False,
            total_score=40.0,
            gates={},
            blocking_failures=["Diff Coverage Gate: 0% diff coverage"],
        )

        self.workspace.write_file("src/user.py", "def get_user(): pass\n")
        gate = TaskVerificationGate(workspace=self.workspace)

        res = gate.verify_task(self.task)
        self.assertFalse(res.passed)
        self.assertIsNotNone(res.ground_truth_report)
        self.assertFalse(res.ground_truth_report["passed"])
        self.assertTrue(any("Ground-truth gate failure" in err for err in res.errors))


class TestVerifyGroundTruthBuiltinTool(unittest.TestCase):
    """Tests for verify_ground_truth tool availability and execution."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)
        self.registry = BuiltinToolRegistry(workspace=self.workspace)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tool_available_to_core_agents(self):
        for role in ["PLANNER", "CODER", "TESTER", "REVIEWER"]:
            tools = self.registry.get_tools_for_agent(role)
            tool_names = [t.name for t in tools]
            self.assertIn(
                "verify_ground_truth",
                tool_names,
                f"verify_ground_truth tool missing from {role}",
            )

    @patch("agent_orchestrator.runtime.verification_matrix.GroundTruthVerificationMatrix.evaluate")
    def test_tool_execution(self, mock_evaluate):
        mock_evaluate.return_value = GroundTruthReport(
            passed=True,
            total_score=95.0,
            gates={"STATIC_ANALYSIS": GroundTruthGateResult("STATIC_ANALYSIS", True, 100.0, "Clean")},
            blocking_failures=[],
        )

        tool = self.registry._tools["verify_ground_truth"]
        result = tool.func(modified_files=["app.py"])

        self.assertTrue(result["success"])
        self.assertEqual(result["total_score"], 95.0)
        self.assertIn("summary", result)


if __name__ == "__main__":
    unittest.main()
