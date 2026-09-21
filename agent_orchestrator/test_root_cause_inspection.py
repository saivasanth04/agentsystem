"""
Tests for Empirical Root Cause Inspection and Fault Localization (Issue #44).
Validates deterministic traceback parsing, AST syntax verification, defect locus identification,
and adversarial arbitration to eliminate blind trust in LLM reviewer target_agent_for_fix.
"""
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.runtime.fault_localization import (
    TracebackFrame,
    ParsedFailure,
    FaultLocus,
    EmpiricalFaultLocalizer,
    AdversarialAttributionArbiter,
)
from agent_orchestrator.runtime.diagnostics import FailureDiagnostician, DiagnosticReport
from agent_orchestrator.runtime.task_graph import TaskDAG, ExecutableTask, TaskState, TaskAttemptRecord
from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.state import TaskStatus


class TestRootCauseInspection(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.events = []
        self.callback = lambda stage, msg, payload=None: self.events.append((stage, msg))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_traceback_parser_extracts_stack_frames_and_exception(self):
        """Validates traceback parser extracts multi-frame stack traces, file, line, and exception class."""
        stderr_sample = """
Traceback (most recent call last):
  File "tests/test_calculator.py", line 42, in test_divide
    result = calc.divide(10, 0)
  File "src/calculator.py", line 15, in divide
    return a / b
ZeroDivisionError: division by zero
"""
        parsed = EmpiricalFaultLocalizer.parse_traceback(stderr_sample)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.exception_class, "ZeroDivisionError")
        self.assertEqual(parsed.exception_message, "division by zero")
        self.assertEqual(len(parsed.stack_frames), 2)

        # First frame is test file
        self.assertTrue(parsed.stack_frames[0].is_test_file)
        self.assertIn("test_calculator.py", parsed.stack_frames[0].filename)

        # Second frame is application file
        self.assertFalse(parsed.stack_frames[1].is_test_file)
        self.assertIn("calculator.py", parsed.stack_frames[1].filename)
        self.assertEqual(parsed.stack_frames[1].line_number, 15)

        # Offending frame is the application file
        self.assertEqual(parsed.primary_offending_file, "src/calculator.py")
        self.assertEqual(parsed.primary_offending_line, 15)
        self.assertTrue(parsed.is_uncaught_runtime_exception)
        self.assertFalse(parsed.is_test_assertion)

    def test_fault_localizer_application_uncaught_exception(self):
        """Validates uncaught runtime exception in application code is localized to CODER."""
        stderr_sample = """
Traceback (most recent call last):
  File "app/users.py", line 88, in get_user
    return user_dict[user_id]
KeyError: 'usr-123'
"""
        locus = EmpiricalFaultLocalizer.localize_fault(execution_stderr=stderr_sample)
        self.assertEqual(locus.locus_type, "APPLICATION_CODE")
        self.assertEqual(locus.ground_truth_attribution, "CODER")
        self.assertEqual(locus.primary_file, "app/users.py")
        self.assertGreaterEqual(locus.confidence, 0.90)
        self.assertIn("KeyError", locus.rationale)

    def test_fault_localizer_syntax_error(self):
        """Validates Python syntax errors are localized to CODER."""
        stderr_sample = """
  File "src/service.py", line 25
    def handle_request(
                      ^
SyntaxError: invalid syntax
"""
        locus = EmpiricalFaultLocalizer.localize_fault(execution_stderr=stderr_sample)
        self.assertEqual(locus.locus_type, "APPLICATION_CODE")
        self.assertEqual(locus.ground_truth_attribution, "CODER")
        self.assertEqual(locus.primary_file, "src/service.py")
        self.assertIn("Syntax", locus.rationale)

    def test_fault_localizer_ast_syntax_scan_in_workspace(self):
        """Validates workspace AST scan catches syntax errors directly on disk."""
        broken_file = Path(self.temp_dir) / "broken.py"
        broken_file.write_text("def unclosed_func(:\n    pass\n", encoding="utf-8")

        locus = EmpiricalFaultLocalizer.localize_fault(workspace_dir=self.temp_dir)
        self.assertEqual(locus.locus_type, "APPLICATION_CODE")
        self.assertEqual(locus.ground_truth_attribution, "CODER")
        self.assertIn("broken.py", locus.primary_file)
        self.assertGreaterEqual(locus.confidence, 0.95)

    def test_fault_localizer_tautological_test_reports(self):
        """Validates verification report with tautologies is localized to TESTER."""
        v_reports = [{"has_tautologies": True, "tautological_count": 3}]
        locus = EmpiricalFaultLocalizer.localize_fault(verification_reports=v_reports)
        self.assertEqual(locus.locus_type, "TEST_CODE")
        self.assertEqual(locus.ground_truth_attribution, "TESTER")
        self.assertIn("Tautological", locus.rationale)

    def test_arbiter_overrides_reviewer_blaming_spec_for_syntax_error(self):
        """Verifies that an LLM reviewer blaming SPECIFICATION for a syntax error is vetoed to CODER."""
        locus = FaultLocus(
            primary_file="src/models.py",
            locus_type="APPLICATION_CODE",
            ground_truth_attribution="CODER",
            confidence=0.98,
            rationale="SyntaxError in src/models.py:10",
        )

        final_agent, was_overridden, rationale = AdversarialAttributionArbiter.arbitrate(
            llm_suggested_agent="SPECIFICATION",
            empirical_locus=locus,
        )

        self.assertTrue(was_overridden)
        self.assertEqual(final_agent, "CODER")
        self.assertIn("Adversarial Veto", rationale)
        self.assertIn("SPECIFICATION", rationale)

    def test_arbiter_overrides_reviewer_blaming_coder_for_tautological_test(self):
        """Verifies that an LLM reviewer blaming CODER for a test suite defect is vetoed to TESTER."""
        locus = FaultLocus(
            primary_file="tests/test_api.py",
            locus_type="TEST_CODE",
            ground_truth_attribution="TESTER",
            confidence=0.95,
            rationale="Tautological assertions in test suite",
        )

        final_agent, was_overridden, rationale = AdversarialAttributionArbiter.arbitrate(
            llm_suggested_agent="CODER",
            empirical_locus=locus,
        )

        self.assertTrue(was_overridden)
        self.assertEqual(final_agent, "TESTER")
        self.assertIn("Adversarial Veto", rationale)

    def test_diagnostician_empirical_locus_and_override_integration(self):
        """Integration test validating FailureDiagnostician records empirical_locus and executes arbitration."""
        mock_llm = MagicMock()
        # Mock LLM hallucinating that ARCHITECTURE should fix a ZeroDivisionError
        mock_llm.chat_json.return_value = {
            "root_cause_summary": "Calculation failed with zero division",
            "failure_type": "RUNTIME_CRASH",
            "affected_files": ["src/math.py"],
            "suggested_remediation": ["Restructure calculation architecture"],
            "target_agent": "ARCHITECTURE",
            "regression_detected": False,
            "should_rollback": False,
        }

        diagnostician = FailureDiagnostician(llm=mock_llm)
        stderr = """
Traceback (most recent call last):
  File "src/math.py", line 10, in compute
    return x / y
ZeroDivisionError: division by zero
"""
        report = diagnostician.diagnose_failure(
            task_title="Math computation task",
            execution_stderr=stderr,
            execution_stdout="",
        )

        # The LLM hallucinated ARCHITECTURE, but empirical evidence forces CODER!
        self.assertEqual(report.target_agent, "CODER")
        self.assertIsNotNone(report.attribution_override)
        self.assertEqual(report.attribution_override["original_llm_target"], "ARCHITECTURE")
        self.assertEqual(report.attribution_override["overridden_to"], "CODER")
        self.assertIsNotNone(report.empirical_locus)
        self.assertEqual(report.empirical_locus["locus_type"], "APPLICATION_CODE")

    def test_orchestrator_node_replan_gathers_failed_task_empirical_logs(self):
        """Validates that TaskOrchestrator._node_replan harvests logs from failed tasks even if test_output is empty."""
        orch = TaskOrchestrator(on_event_callback=self.callback)

        failed_task = ExecutableTask(
            task_id="T1",
            objective="Build payment gateway",
            state=TaskState.FAILED,
            error_message="ZeroDivisionError in payment/stripe.py",
        )
        failed_task.record_attempt(TaskAttemptRecord(
            attempt_number=1,
            agent_name="CODER",
            started_at="2026-09-20T00:00:00",
            completed_at="2026-09-20T00:01:00",
            status="FAILED",
            errors=["ZeroDivisionError: division by zero at payment/stripe.py:34"],
            verification_result={"passed": False, "stderr": "Traceback (most recent call last):\n  File 'payment/stripe.py', line 34\nZeroDivisionError: division by zero"},
        ))

        state = {
            "user_request": "Implement payment processing",
            "iteration": 1,
            "max_iterations": 3,
            "subtasks": [failed_task.to_dict()],
            "task_decomposition": [failed_task.to_dict()],
            "test_output": {},  # test_output is completely EMPTY!
            "review_output": {
                "verdict": "FAIL",
                "summary": "Payment failed",
                "target_agent_for_fix": "SPECIFICATION",  # Reviewer hallucinated SPEC
            },
            "replan_history": [],
            "remediation_plan": [],
            "status": TaskStatus.IN_PROGRESS.value,
        }

        with patch.object(orch.diagnostician.llm, "chat_json") as mock_chat:
            mock_chat.return_value = {
                "root_cause_summary": "ZeroDivisionError in stripe.py",
                "failure_type": "RUNTIME_CRASH",
                "affected_files": ["payment/stripe.py"],
                "suggested_remediation": ["Check divisor before division"],
                "target_agent": "SPECIFICATION",
                "regression_detected": False,
                "should_rollback": False,
            }

            new_state = orch._node_replan(state)

            # Despite test_output being empty and reviewer claiming SPECIFICATION,
            # empirical fault localization harvested the error from failed_task and arbitrated to CODER!
            self.assertEqual(new_state["target_agent_for_fix"], "CODER")
            rec = new_state["replan_history"][-1]
            self.assertEqual(rec["target_agent_for_fix"], "CODER")


if __name__ == "__main__":
    unittest.main()
