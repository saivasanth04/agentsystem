import shutil
import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.persistence.state_store import SQLiteStateStore
from agent_orchestrator.runtime.task_graph import ExecutableTask, TaskDAG, TaskState
from agent_orchestrator.state import OrchestratorState, ReviewVerdict, TaskStatus


class TestTerminalStatusResolution(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "test_sessions.db"
        self.store = SQLiteStateStore(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_undecided_never_yields_completed(self):
        """ReviewVerdict.UNDECIDED must NEVER evaluate to TaskStatus.COMPLETED under any conditions."""
        # 1. No DAG
        res1 = TaskOrchestrator.resolve_terminal_status(
            verdict=ReviewVerdict.UNDECIDED,
            task_dag=None,
            current_iteration=0,
            max_iterations=3,
        )
        self.assertNotEqual(res1, TaskStatus.COMPLETED)
        self.assertEqual(res1, TaskStatus.NEED_VERIFICATION)

        # 2. Fully completed DAG
        dag = TaskDAG()
        t1 = ExecutableTask(task_id="T1", objective="Do work", state=TaskState.COMPLETED)
        dag.add_task(t1)

        res2 = TaskOrchestrator.resolve_terminal_status(
            verdict=ReviewVerdict.UNDECIDED,
            task_dag=dag,
            current_iteration=0,
            max_iterations=3,
        )
        self.assertNotEqual(res2, TaskStatus.COMPLETED)
        self.assertEqual(res2, TaskStatus.NEED_VERIFICATION)

        # 3. String "UNDECIDED"
        res3 = TaskOrchestrator.resolve_terminal_status(
            verdict="UNDECIDED",
            task_dag=dag,
            current_iteration=1,
            max_iterations=3,
        )
        self.assertNotEqual(res3, TaskStatus.COMPLETED)
        self.assertEqual(res3, TaskStatus.NEED_VERIFICATION)

    def test_undecided_with_incomplete_dag_yields_incomplete(self):
        """If DAG has pending or running tasks and verdict is UNDECIDED, status is INCOMPLETE."""
        dag = TaskDAG()
        t1 = ExecutableTask(task_id="T1", objective="Do work 1", state=TaskState.COMPLETED)
        t2 = ExecutableTask(task_id="T2", objective="Do work 2", state=TaskState.PENDING)
        dag.add_task(t1)
        dag.add_task(t2)

        status = TaskOrchestrator.resolve_terminal_status(
            verdict=ReviewVerdict.UNDECIDED,
            task_dag=dag,
            current_iteration=0,
            max_iterations=3,
        )
        self.assertEqual(status, TaskStatus.INCOMPLETE)

    def test_pass_yields_completed(self):
        """When reviewer verdict is PASS, the workflow reaches COMPLETED."""
        status = TaskOrchestrator.resolve_terminal_status(
            verdict=ReviewVerdict.PASS,
            task_dag=None,
            current_iteration=0,
            max_iterations=3,
        )
        self.assertEqual(status, TaskStatus.COMPLETED)

    def test_pass_with_completed_dag_yields_completed(self):
        """Strict completion: ONLY when verdict is PASS and all DAG tasks are completed."""
        dag = TaskDAG()
        t1 = ExecutableTask(task_id="T1", objective="Work 1", state=TaskState.COMPLETED)
        t2 = ExecutableTask(task_id="T2", objective="Work 2", state=TaskState.SKIPPED)
        dag.add_task(t1)
        dag.add_task(t2)

        status = TaskOrchestrator.resolve_terminal_status(
            verdict=ReviewVerdict.PASS,
            task_dag=dag,
            current_iteration=1,
            max_iterations=3,
        )
        self.assertEqual(status, TaskStatus.COMPLETED)

    def test_iteration_exhaustion_yields_stopped(self):
        """When current_iteration >= max_iterations and verdict != PASS, status is STOPPED."""
        # Case 1: FAIL at max iterations
        status_fail = TaskOrchestrator.resolve_terminal_status(
            verdict=ReviewVerdict.FAIL,
            task_dag=None,
            current_iteration=3,
            max_iterations=3,
        )
        self.assertEqual(status_fail, TaskStatus.STOPPED)

        # Case 2: UNDECIDED at max iterations
        status_undecided = TaskOrchestrator.resolve_terminal_status(
            verdict=ReviewVerdict.UNDECIDED,
            task_dag=None,
            current_iteration=3,
            max_iterations=3,
        )
        self.assertEqual(status_undecided, TaskStatus.STOPPED)

    def test_fail_with_iterations_remaining_yields_failed(self):
        """When verdict is FAIL and iterations remain, status is FAILED."""
        status = TaskOrchestrator.resolve_terminal_status(
            verdict=ReviewVerdict.FAIL,
            task_dag=None,
            current_iteration=1,
            max_iterations=3,
        )
        self.assertEqual(status, TaskStatus.FAILED)

    def test_persistence_roundtrip_new_statuses(self):
        """Ensure SQLite state store faithfully round-trips all new statuses."""
        new_statuses = [
            TaskStatus.NEED_VERIFICATION,
            TaskStatus.INCOMPLETE,
            TaskStatus.STOPPED,
        ]

        for i, status in enumerate(new_statuses):
            sess_id = f"sess-status-test-{i}"
            state = OrchestratorState(user_request=f"Test request {i}")
            state.status = status
            state.verdict = ReviewVerdict.UNDECIDED

            self.store.save_session(sess_id, state)
            loaded = self.store.load_session(sess_id)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.status, status)
            self.assertEqual(loaded.verdict, ReviewVerdict.UNDECIDED)


if __name__ == "__main__":
    unittest.main()
