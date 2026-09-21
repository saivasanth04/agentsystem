"""
Unit and integration tests for Transitive Dependent Task Invalidation & Cascade Pruning (Issue #45).
Verifies that replanning invalidates dependent tasks, preventing the repetition of bad work,
resolves orphaned PENDING deadlocks, and supports graph-theoretic descendant reachability.
"""
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.runtime.task_graph import (
    TaskDAG,
    ExecutableTask,
    TaskState,
    TaskPermissions,
)
from agent_orchestrator.runtime.replan_engine import (
    EpistemicReplanner,
    ReplanResult,
)
from agent_orchestrator.runtime.diagnostics import DiagnosticReport
from agent_orchestrator.state import OrchestratorState


class TestDependentTaskInvalidation(unittest.TestCase):
    def setUp(self):
        self.events = []

    def callback(self, event_type: str, message: str):
        self.events.append((event_type, message))

    def test_get_descendant_task_ids_linear_chain(self):
        """Verify A -> B -> C -> D returns correct transitive descendant sets."""
        dag = TaskDAG()
        dag.add_task(ExecutableTask(task_id="T1", objective="Task 1"))
        dag.add_task(ExecutableTask(task_id="T2", objective="Task 2", dependencies=["T1"]))
        dag.add_task(ExecutableTask(task_id="T3", objective="Task 3", dependencies=["T2"]))
        dag.add_task(ExecutableTask(task_id="T4", objective="Task 4", dependencies=["T3"]))

        self.assertEqual(dag.get_descendant_task_ids("T1"), {"T2", "T3", "T4"})
        self.assertEqual(dag.get_descendant_task_ids("T2"), {"T3", "T4"})
        self.assertEqual(dag.get_descendant_task_ids("T3"), {"T4"})
        self.assertEqual(dag.get_descendant_task_ids("T4"), set())
        self.assertEqual(dag.get_descendant_task_ids("NON_EXISTENT"), set())

    def test_get_descendant_task_ids_diamond_and_fork(self):
        """Verify branching and diamond DAGs resolve all downstream paths transitively."""
        dag = TaskDAG()
        dag.add_task(ExecutableTask(task_id="A", objective="Root"))
        dag.add_task(ExecutableTask(task_id="B", objective="Branch 1", dependencies=["A"]))
        dag.add_task(ExecutableTask(task_id="C", objective="Branch 2", dependencies=["A"]))
        dag.add_task(ExecutableTask(task_id="D", objective="Join", dependencies=["B", "C"]))
        dag.add_task(ExecutableTask(task_id="E", objective="Leaf", dependencies=["D"]))
        dag.add_task(ExecutableTask(task_id="X", objective="Isolated"))

        self.assertEqual(dag.get_descendant_task_ids("A"), {"B", "C", "D", "E"})
        self.assertEqual(dag.get_descendant_task_ids("B"), {"D", "E"})
        self.assertEqual(dag.get_descendant_task_ids("C"), {"D", "E"})
        self.assertEqual(dag.get_descendant_task_ids("D"), {"E"})
        self.assertEqual(dag.get_descendant_task_ids("X"), set())

    def test_invalidate_dependent_tasks_cascades_properly(self):
        """Verify invalidating a parent marks all uncompleted descendants as SKIPPED_REDUNDANT."""
        dag = TaskDAG()
        dag.add_task(ExecutableTask(task_id="T1", objective="Fail node", state=TaskState.FAILED))
        dag.add_task(ExecutableTask(task_id="T2", objective="Child 1", dependencies=["T1"], state=TaskState.BLOCKED))
        dag.add_task(ExecutableTask(task_id="T3", objective="Child 2", dependencies=["T2"], state=TaskState.PENDING))
        dag.add_task(ExecutableTask(task_id="T4", objective="Child 3", dependencies=["T3"], state=TaskState.READY))

        pruned = dag.invalidate_dependent_tasks(["T1"], reason="Architecture pivot")
        self.assertEqual(set(pruned), {"T2", "T3", "T4"})

        # T1 must remain FAILED to preserve forensic record
        self.assertEqual(dag.get_task("T1").state, TaskState.FAILED)
        # All descendants transitioned to SKIPPED_REDUNDANT
        self.assertEqual(dag.get_task("T2").state, TaskState.SKIPPED_REDUNDANT)
        self.assertEqual(dag.get_task("T3").state, TaskState.SKIPPED_REDUNDANT)
        self.assertEqual(dag.get_task("T4").state, TaskState.SKIPPED_REDUNDANT)

    def test_prune_tasks_cascade_true_vs_false(self):
        """Verify prune_tasks respects cascade flag."""
        dag = TaskDAG()
        dag.add_task(ExecutableTask(task_id="T1", objective="Task 1", state=TaskState.PENDING))
        dag.add_task(ExecutableTask(task_id="T2", objective="Task 2", dependencies=["T1"], state=TaskState.PENDING))
        dag.add_task(ExecutableTask(task_id="T3", objective="Task 3", dependencies=["T2"], state=TaskState.PENDING))

        # Cascade = False only prunes T1
        dag.prune_tasks(["T1"], cascade=False)
        self.assertEqual(dag.get_task("T1").state, TaskState.SKIPPED_REDUNDANT)
        self.assertEqual(dag.get_task("T2").state, TaskState.PENDING)
        self.assertEqual(dag.get_task("T3").state, TaskState.PENDING)

        # Reset and test Cascade = True
        dag.get_task("T1").state = TaskState.PENDING
        dag.prune_tasks(["T1"], cascade=True)
        self.assertEqual(dag.get_task("T1").state, TaskState.SKIPPED_REDUNDANT)
        self.assertEqual(dag.get_task("T2").state, TaskState.SKIPPED_REDUNDANT)
        self.assertEqual(dag.get_task("T3").state, TaskState.SKIPPED_REDUNDANT)

    def test_get_ready_tasks_resolves_orphaned_pending_deadlock(self):
        """Verify get_ready_tasks transitions children of SKIPPED_REDUNDANT dependencies rather than deadlocking in PENDING."""
        dag = TaskDAG()
        dag.add_task(ExecutableTask(task_id="T1", objective="Pruned parent", state=TaskState.SKIPPED_REDUNDANT))
        dag.add_task(ExecutableTask(task_id="T2", objective="Child waiting on T1", dependencies=["T1"], state=TaskState.PENDING))
        dag.add_task(ExecutableTask(task_id="T3", objective="Child waiting on T2", dependencies=["T2"], state=TaskState.PENDING))

        ready = dag.get_ready_tasks()
        self.assertEqual(ready, [])

        # T2 should have transitioned from PENDING to SKIPPED_REDUNDANT
        self.assertEqual(dag.get_task("T2").state, TaskState.SKIPPED_REDUNDANT)

        # On next check or same loop, T3 also cascades
        ready = dag.get_ready_tasks()
        self.assertEqual(ready, [])
        self.assertEqual(dag.get_task("T3").state, TaskState.SKIPPED_REDUNDANT)

        # is_all_completed should now return True! No deadlock!
        self.assertTrue(dag.is_all_completed())

    def test_replan_engine_invalidates_downstream_on_architectural_failure(self):
        """Verify that when replanning an architectural failure, stale downstream tasks are pruned to avoid repeating bad work."""
        dag = TaskDAG()
        t1 = ExecutableTask(task_id="T1", objective="Design SQL models", state=TaskState.FAILED)
        t2 = ExecutableTask(task_id="T2", objective="Generate SQL queries", dependencies=["T1"], state=TaskState.PENDING)
        t3 = ExecutableTask(task_id="T3", objective="Create REST endpoints for SQL", dependencies=["T2"], state=TaskState.PENDING)
        dag.add_task(t1)
        dag.add_task(t2)
        dag.add_task(t3)

        diag = DiagnosticReport(
            root_cause_summary="SQL architecture violates no-database requirement; must use in-memory key-value store",
            failure_type="ARCHITECTURE_DRIFT",
            affected_files=["models.py"],
            suggested_remediation=["Implement InMemoryKVStore instead of SQLAlchemy"],
            target_agent="CODER",
            invalid_assumptions=["Assumed relational database was permitted by client architecture"],
            remediation_tasks=[
                {
                    "task_id": "T-REM-KV-STORE",
                    "objective": "Implement thread-safe InMemoryKVStore",
                    "dependencies": [],
                }
            ],
        )

        replanner = EpistemicReplanner(on_event=self.callback)
        result = replanner.replan(task_dag=dag, diagnostic=diag, iteration=1)

        # Stale downstream tasks T2 and T3 must be invalidated so they don't run obsolete SQL logic!
        self.assertIn("T2", result.pruned_task_ids)
        self.assertIn("T3", result.pruned_task_ids)
        self.assertEqual(dag.get_task("T2").state, TaskState.SKIPPED_REDUNDANT)
        self.assertEqual(dag.get_task("T3").state, TaskState.SKIPPED_REDUNDANT)

        # Remediation task and verify task must be injected
        self.assertIn("T-REM-KV-STORE", dag.tasks)
        self.assertIn("T-VERIFY-1", dag.tasks)

        # Remediation task must be ready to execute
        ready_tasks = dag.get_ready_tasks()
        ready_ids = [t.task_id for t in ready_tasks]
        self.assertIn("T-REM-KV-STORE", ready_ids)
        # Neither T2 nor T3 should ever be ready
        self.assertNotIn("T2", ready_ids)
        self.assertNotIn("T3", ready_ids)

    def test_replan_engine_prunes_transitive_descendants_of_invalid_tasks(self):
        """Verify that when an invalid task is pruned, its descendants are also pruned, while independent tasks remain."""
        dag = TaskDAG()
        dag.add_task(ExecutableTask(task_id="T1", objective="Failing task", state=TaskState.FAILED))
        dag.add_task(ExecutableTask(task_id="T2", objective="Invalid task", dependencies=["T1"], state=TaskState.PENDING))
        dag.add_task(ExecutableTask(task_id="T3", objective="Dependent on invalid", dependencies=["T2"], state=TaskState.PENDING))
        dag.add_task(ExecutableTask(task_id="T_INDEPENDENT", objective="Unrelated reporting", dependencies=[], state=TaskState.PENDING))

        diag = DiagnosticReport(
            root_cause_summary="T2 implementation plan is invalid",
            failure_type="LOGIC_DEFECT",
            affected_files=["auth.py"],
            suggested_remediation=["Patch logic"],
            target_agent="CODER",
            invalid_task_ids=["T2"],
        )

        replanner = EpistemicReplanner(on_event=self.callback)
        result = replanner.replan(task_dag=dag, diagnostic=diag, iteration=1)

        # Both T2 and its child T3 should be pruned
        self.assertIn("T2", result.pruned_task_ids)
        self.assertIn("T3", result.pruned_task_ids)
        self.assertEqual(dag.get_task("T2").state, TaskState.SKIPPED_REDUNDANT)
        self.assertEqual(dag.get_task("T3").state, TaskState.SKIPPED_REDUNDANT)

        # Independent task must NOT be pruned
        self.assertNotIn("T_INDEPENDENT", result.pruned_task_ids)
        self.assertEqual(dag.get_task("T_INDEPENDENT").state, TaskState.PENDING)

    def test_inject_remediation_task_with_invalidate_downstream(self):
        """Verify inject_remediation_task with invalidate_downstream=True prunes old downstream queue."""
        dag = TaskDAG()
        t1 = ExecutableTask(task_id="T1", objective="Original failing task", state=TaskState.FAILED)
        t2 = ExecutableTask(task_id="T2", objective="Dependent task", dependencies=["T1"], state=TaskState.PENDING)
        dag.add_task(t1)
        dag.add_task(t2)

        rem = ExecutableTask(task_id="T-REM", objective="Remediate with new schema", dependencies=[])
        dag.inject_remediation_task(rem, failed_task_id="T1", invalidate_downstream=True)

        self.assertIn("T-REM", dag.tasks)
        # T2 must be pruned to SKIPPED_REDUNDANT
        self.assertEqual(dag.get_task("T2").state, TaskState.SKIPPED_REDUNDANT)


if __name__ == "__main__":
    unittest.main()
