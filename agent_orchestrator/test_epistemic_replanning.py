"""
Tests for Epistemic Replanning Engine (Issue #43).
Validates dynamic TaskDAG restructuring, assumption invalidation, task pruning,
multi-task remediation DAG injection, concurrency analysis, and working memory synchronization.
"""
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.runtime.diagnostics import DiagnosticReport, FailureDiagnostician
from agent_orchestrator.runtime.replan_engine import EpistemicReplanner, ReplanResult
from agent_orchestrator.runtime.task_graph import TaskDAG, ExecutableTask, TaskState, TaskPermissions
from agent_orchestrator.memory.working_memory import WorkingMemory
from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.state import TaskStatus, ReplanRecord


class TestEpistemicReplanning(unittest.TestCase):

    def setUp(self):
        self.events = []
        self.callback = lambda stage, msg, payload=None: self.events.append((stage, msg))

    def test_diagnostic_report_epistemic_fields_and_defaults(self):
        """Validates that DiagnosticReport contains epistemic fields with sensible defaults."""
        diag = DiagnosticReport(
            root_cause_summary="Off-by-one boundary error in binary search.",
            failure_type="LOGIC_DEFECT",
            affected_files=["binary_search.py"],
            suggested_remediation=["Adjust upper bound inclusive check."],
            target_agent="CODER",
        )
        self.assertEqual(diag.invalid_assumptions, [])
        self.assertEqual(diag.missing_evidence, [])
        self.assertEqual(diag.invalid_task_ids, [])
        self.assertEqual(diag.pruned_task_ids, [])
        self.assertEqual(diag.remediation_tasks, [])

        # Custom epistemic assignment
        diag.invalid_assumptions = ["Assumed array was 1-indexed"]
        diag.missing_evidence = ["Check test vector format"]
        diag.invalid_task_ids = ["T2"]
        diag.pruned_task_ids = ["T3"]
        diag.remediation_tasks = [{"task_id": "T-REM-1-1", "objective": "Fix bounds"}]

        d = diag.to_dict()
        self.assertIn("Assumed array was 1-indexed", d["invalid_assumptions"])
        self.assertIn("Check test vector format", d["missing_evidence"])
        self.assertEqual(d["invalid_task_ids"], ["T2"])
        self.assertEqual(d["pruned_task_ids"], ["T3"])
        self.assertEqual(len(d["remediation_tasks"]), 1)

    def test_diagnostician_extracts_epistemic_fields_from_llm(self):
        """Validates FailureDiagnostician parses LLM JSON containing epistemic replanning fields."""
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "failure_type": "LOGIC_DEFECT",
            "target_agent": "CODER",
            "root_cause_summary": "Assumed token format is JWT instead of opaque bearer.",
            "suggested_remediation": ["Update token verification middleware"],
            "invalid_assumptions": [
                "Assumed authorization header was JWT format"
            ],
            "missing_evidence": [
                "Auth server schema specification"
            ],
            "invalid_task_ids": ["T-VERIFY-JWT"],
            "pruned_task_ids": ["T-CACHE-JWT-CLAIMS"],
            "remediation_tasks": [
                {
                    "task_id": "T-REM-TOKEN-FIX",
                    "objective": "Rewrite token validator to support opaque tokens",
                    "dependencies": [],
                    "required_capabilities": ["code-generation", "security-hardening"],
                    "required_tools": ["filesystem", "terminal"],
                    "preferred_skills": ["security-and-hardening"]
                }
            ]
        }
        diagnostician = FailureDiagnostician(llm=mock_llm)
        report = diagnostician.diagnose_failure(
            task_title="Validate user auth token",
            execution_stderr="JWT decode error: invalid header padding",
            execution_stdout="",
            review_info={"verdict": "FAIL", "feedback": "JWT decode error: invalid header padding"},
        )

        self.assertEqual(report.failure_type, "LOGIC_DEFECT")
        self.assertIn("Assumed authorization header was JWT format", report.invalid_assumptions)
        self.assertIn("Auth server schema specification", report.missing_evidence)
        self.assertEqual(report.invalid_task_ids, ["T-VERIFY-JWT"])
        self.assertEqual(report.pruned_task_ids, ["T-CACHE-JWT-CLAIMS"])
        self.assertEqual(len(report.remediation_tasks), 1)
        self.assertEqual(report.remediation_tasks[0]["task_id"], "T-REM-TOKEN-FIX")

    def test_replan_engine_prunes_invalid_tasks(self):
        """Validates that EpistemicReplanner prunes invalidated and redundant tasks from the DAG."""
        dag = TaskDAG()
        t1 = ExecutableTask(task_id="T1", objective="Implement token decoder", state=TaskState.FAILED)
        t2 = ExecutableTask(task_id="T2", objective="Build JWT claim cache", dependencies=["T1"], state=TaskState.PENDING)
        t3 = ExecutableTask(task_id="T3", objective="Unrelated reporting task", dependencies=[], state=TaskState.PENDING)
        dag.add_task(t1)
        dag.add_task(t2)
        dag.add_task(t3)

        diag = DiagnosticReport(
            root_cause_summary="JWT assumptions invalid",
            failure_type="LOGIC_DEFECT",
            affected_files=["token.py"],
            suggested_remediation=["Switch to opaque token handler"],
            target_agent="CODER",
            invalid_task_ids=["T2"],
        )

        replanner = EpistemicReplanner(on_event=self.callback)
        result = replanner.replan(task_dag=dag, diagnostic=diag, iteration=1)

        # T2 should be marked SKIPPED_REDUNDANT
        self.assertEqual(dag.tasks["T2"].state, TaskState.SKIPPED_REDUNDANT)
        self.assertIn("T2", result.pruned_task_ids)
        # T3 should remain untouched
        self.assertEqual(dag.tasks["T3"].state, TaskState.PENDING)

    def test_replan_engine_dynamic_multi_task_dag_injection(self):
        """Validates that dynamic multi-task remediation branches are injected with proper dependencies."""
        dag = TaskDAG()
        t1 = ExecutableTask(task_id="T1", objective="Original failing task", state=TaskState.FAILED)
        t_follow = ExecutableTask(task_id="T_FOLLOW", objective="Follow-up task", dependencies=["T1"], state=TaskState.PENDING)
        dag.add_task(t1)
        dag.add_task(t_follow)

        diag = DiagnosticReport(
            root_cause_summary="Database connection pool leak",
            failure_type="LOGIC_DEFECT",
            affected_files=["database.py"],
            suggested_remediation=["Patch connection leak", "Add pool health check"],
            target_agent="CODER",
            remediation_tasks=[
                {
                    "task_id": "T-REM-POOL-LEAK",
                    "objective": "Wrap DB sessions in context managers",
                    "dependencies": [],
                    "required_capabilities": ["defect-repair"],
                },
                {
                    "task_id": "T-REM-POOL-METRICS",
                    "objective": "Add connection pool metrics and health check",
                    "dependencies": ["T-REM-POOL-LEAK"],
                    "required_capabilities": ["observability"],
                },
            ],
        )

        replanner = EpistemicReplanner(on_event=self.callback)
        result = replanner.replan(task_dag=dag, diagnostic=diag, iteration=1)

        self.assertEqual(len(result.injected_tasks), 3)  # Pool leak + Pool metrics + Auto-added verify task
        self.assertIn("T-REM-POOL-LEAK", dag.tasks)
        self.assertIn("T-REM-POOL-METRICS", dag.tasks)
        self.assertIn("T-VERIFY-1", dag.tasks)

        # Verify dependency chain
        self.assertEqual(dag.tasks["T-REM-POOL-METRICS"].dependencies, ["T-REM-POOL-LEAK"])
        self.assertIn("T-REM-POOL-METRICS", dag.tasks["T-VERIFY-1"].dependencies)
        # Downstream task T_FOLLOW should now depend on T-REM-POOL-LEAK instead of failed T1
        self.assertIn("T-REM-POOL-LEAK", dag.tasks["T_FOLLOW"].dependencies)
        self.assertNotIn("T1", dag.tasks["T_FOLLOW"].dependencies)

    def test_replan_engine_parallel_wave_computation(self):
        """Validates that concurrent remediation tasks are grouped into parallel execution waves."""
        dag = TaskDAG()
        t1 = ExecutableTask(task_id="T1", objective="Failed monolithic task", state=TaskState.FAILED)
        dag.add_task(t1)

        diag = DiagnosticReport(
            root_cause_summary="Cross-module interface misalignment",
            failure_type="LOGIC_DEFECT",
            affected_files=["module_a.py", "module_b.py"],
            suggested_remediation=["Fix module A", "Fix module B", "Run integration tests"],
            target_agent="CODER",
            remediation_tasks=[
                {"task_id": "T-FIX-A", "objective": "Fix module A", "dependencies": []},
                {"task_id": "T-FIX-B", "objective": "Fix module B", "dependencies": []},
                {"task_id": "T-INT-TEST", "objective": "Run integration tests", "dependencies": ["T-FIX-A", "T-FIX-B"]},
            ],
        )

        replanner = EpistemicReplanner(on_event=self.callback)
        result = replanner.replan(task_dag=dag, diagnostic=diag, iteration=1)

        # Wave 1 should contain independent tasks T-FIX-A and T-FIX-B
        # Wave 2 should contain T-INT-TEST
        self.assertEqual(result.parallel_groups[0], ["T-FIX-A", "T-FIX-B"])
        self.assertIn("T-INT-TEST", result.parallel_groups[1])

    def test_replan_records_assumptions_in_working_memory(self):
        """Validates that invalidated assumptions and missing evidence are recorded into WorkingMemory."""
        dag = TaskDAG()
        dag.add_task(ExecutableTask(task_id="T1", objective="Do work", state=TaskState.FAILED))

        diag = DiagnosticReport(
            root_cause_summary="API version mismatch v1 vs v2",
            failure_type="SPEC_MISMATCH",
            affected_files=["api.py"],
            suggested_remediation=["Update API endpoint to v2"],
            target_agent="SPECIFICATION",
            invalid_assumptions=["Assumed v1 endpoints are active in prod"],
            missing_evidence=["API v2 OpenAPI spec"],
        )

        wm = WorkingMemory()
        replanner = EpistemicReplanner(on_event=self.callback)
        result = replanner.replan(task_dag=dag, diagnostic=diag, working_memory=wm, iteration=1)

        self.assertIn("Invalidated assumption: Assumed v1 endpoints are active in prod", wm.discovered_pitfalls)
        self.assertIn("Missing evidence to acquire: API v2 OpenAPI spec", wm.scratchpad_notes)
        self.assertIn("Assumed v1 endpoints are active in prod", result.invalid_assumptions)
        self.assertIn("API v2 OpenAPI spec", result.missing_evidence)

    def test_replan_fallback_parity(self):
        """Validates 100% backward-compatible fallback when diagnostic has no dynamic remediation tasks."""
        dag = TaskDAG()
        t1 = ExecutableTask(task_id="T1", objective="Run baseline test", state=TaskState.FAILED)
        dag.add_task(t1)

        diag = DiagnosticReport(
            root_cause_summary="AssertionError in math helper",
            failure_type="LOGIC_DEFECT",
            affected_files=["math_helper.py"],
            suggested_remediation=["Fix math helper"],
            target_agent="CODER",
            remediation_tasks=[],  # Empty list -> trigger fallback
        )

        replanner = EpistemicReplanner(on_event=self.callback)
        result = replanner.replan(task_dag=dag, diagnostic=diag, iteration=2)

        self.assertEqual(len(result.injected_tasks), 2)
        self.assertEqual(result.injected_tasks[0].task_id, "T-REM-2")
        self.assertEqual(result.injected_tasks[1].task_id, "T-VERIFY-2")
        self.assertEqual(dag.tasks["T-VERIFY-2"].dependencies, ["T-REM-2"])

    def test_orchestrator_node_replan_epistemic_flow(self):
        """Integration test validating TaskOrchestrator._node_replan execution with EpistemicReplanner."""
        orch = TaskOrchestrator(on_event_callback=self.callback)

        initial_subtasks = [
            ExecutableTask(task_id="T1", objective="Build auth controller", state=TaskState.COMPLETED).to_dict(),
            ExecutableTask(task_id="T2", objective="Build oauth flow", state=TaskState.FAILED).to_dict(),
            ExecutableTask(task_id="T3", objective="Cache oauth tokens", dependencies=["T2"], state=TaskState.PENDING).to_dict(),
        ]

        state = {
            "user_request": "Implement OAuth2 login flow",
            "iteration": 1,
            "max_iterations": 3,
            "subtasks": initial_subtasks,
            "task_decomposition": initial_subtasks,
            "step_results": {
                "reviewer_output": {
                    "verdict": "FAIL",
                    "feedback": "OAuth redirect URI mismatch with provider config",
                    "summary": "Redirect URI config invalid",
                }
            },
            "replan_history": [],
            "remediation_plan": [],
            "status": TaskStatus.IN_PROGRESS.value,
        }

        # Mock diagnostician to return epistemic report
        with patch.object(orch.diagnostician, "diagnose_failure") as mock_diag:
            mock_diag.return_value = DiagnosticReport(
                root_cause_summary="OAuth redirect URI mismatch in config",
                failure_type="CONFIG_ERROR",
                affected_files=["oauth_client.py"],
                suggested_remediation=["Align redirect URI with provider config"],
                target_agent="CODER",
                invalid_assumptions=["Assumed redirect URI was http://localhost:8000"],
                missing_evidence=["Provider console callback configuration"],
                invalid_task_ids=["T3"],
                remediation_tasks=[
                    {
                        "task_id": "T-REM-OAUTH-CONFIG",
                        "objective": "Update callback URL in OAuth client",
                        "dependencies": [],
                        "required_capabilities": ["defect-repair"],
                    }
                ],
            )

            new_state = orch._node_replan(state)

            self.assertEqual(new_state["iteration"], 2)
            self.assertEqual(new_state["target_agent_for_fix"], "CODER")
            self.assertEqual(len(new_state["replan_history"]), 1)

            rec = new_state["replan_history"][0]
            self.assertIn("Assumed redirect URI was http://localhost:8000", rec["invalid_assumptions"])
            self.assertIn("Provider console callback configuration", rec["missing_evidence"])
            self.assertIn("T3", rec["pruned_task_ids"])
            self.assertIn("T-REM-OAUTH-CONFIG", rec["injected_task_ids"])

            # Verify DAG state in subtasks
            dag = TaskDAG.from_list(new_state["subtasks"])
            self.assertEqual(dag.tasks["T3"].state, TaskState.SKIPPED_REDUNDANT)
            self.assertIn("T-REM-OAUTH-CONFIG", dag.tasks)
            self.assertIn("T-VERIFY-2", dag.tasks)

            # Verify working memory recorded the invalidated assumption
            self.assertTrue(any("Assumed redirect URI was http://localhost:8000" in p for p in orch.working_memory.discovered_pitfalls))


if __name__ == "__main__":
    unittest.main()
