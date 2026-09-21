"""
Unit and Integration Tests for Concurrent DAG Wave Execution, Diamond Dependencies,
Barrier Synchronization, and Scoped Parent Artifact Routing.
"""
import unittest
from pathlib import Path
import tempfile
import shutil
from unittest.mock import MagicMock

from agent_orchestrator.runtime.task_graph import (
    TaskDAG,
    ExecutableTask,
    TaskState,
    TaskPermissions,
)
from agent_orchestrator.runtime.dag_scheduler import ConcurrentDAGScheduler
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.config import OrchestratorConfig
from agent_orchestrator.state import OrchestratorState


class TestDiamondDAGAndBarrierSync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=Path(self.temp_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_diamond_dag_wave_resolution(self):
        """
        Topology:
                   ┌── T-02 (Backend)  ──┐
                   │                     ↓
        T-01 (Req) ┼── T-03 (Frontend) ──┼──► T-05 (Integration) ──► T-06 (Verification)
                   │                     ↑
                   └── T-04 (DB) ────────┘
        """
        t1 = ExecutableTask(task_id="T-01", objective="Formulate Requirements", dependencies=[])
        t2 = ExecutableTask(task_id="T-02", objective="Build Backend API", dependencies=["T-01"])
        t3 = ExecutableTask(task_id="T-03", objective="Build Frontend UI", dependencies=["T-01"])
        t4 = ExecutableTask(task_id="T-04", objective="Build DB Schema", dependencies=["T-01"])
        t5 = ExecutableTask(task_id="T-05", objective="Integration & Seam Check", dependencies=["T-02", "T-03", "T-04"])
        t6 = ExecutableTask(task_id="T-06", objective="Run E2E Suite", dependencies=["T-05"])

        dag = TaskDAG([t1, t2, t3, t4, t5, t6])

        # Wave 1: Only T-01 is ready
        wave_1 = dag.get_ready_tasks()
        self.assertEqual([t.task_id for t in wave_1], ["T-01"])

        # Mark T-01 complete with outputs
        dag.mark_task_completed("T-01", result_data={"spec": "User Registration"})

        # Wave 2: T-02, T-03, T-04 become ready concurrently
        wave_2 = dag.get_ready_tasks()
        self.assertEqual(set(t.task_id for t in wave_2), {"T-02", "T-03", "T-04"})

        # Verify parent artifact routing for parallel nodes
        for task in wave_2:
            parent_arts = dag.get_parent_artifacts(task.task_id)
            self.assertIn("T-01", parent_arts)
            self.assertEqual(parent_arts["T-01"]["result_data"]["spec"], "User Registration")

        # Complete only T-02 and T-03 -> T-05 MUST remain NOT READY (Barrier Synchronization)
        dag.mark_task_completed("T-02", result_data={"backend_endpoint": "/api/users"})
        dag.mark_task_completed("T-03", result_data={"frontend_component": "<UserForm/>"})

        # T-04 is still READY/PENDING, so T-05 cannot run
        ready_before_t4 = dag.get_ready_tasks()
        self.assertEqual([t.task_id for t in ready_before_t4], ["T-04"])
        self.assertNotIn("T-05", [t.task_id for t in ready_before_t4])

        # Now complete T-04 -> T-05 barrier unlocks!
        dag.mark_task_completed("T-04", result_data={"schema": "CREATE TABLE users"})
        wave_3 = dag.get_ready_tasks()
        self.assertEqual([t.task_id for t in wave_3], ["T-05"])

        # Verify fan-in aggregated artifacts for T-05
        t5_parent_arts = dag.get_parent_artifacts("T-05")
        self.assertEqual(set(t5_parent_arts.keys()), {"T-02", "T-03", "T-04"})
        self.assertEqual(t5_parent_arts["T-02"]["result_data"]["backend_endpoint"], "/api/users")
        self.assertEqual(t5_parent_arts["T-03"]["result_data"]["frontend_component"], "<UserForm/>")
        self.assertEqual(t5_parent_arts["T-04"]["result_data"]["schema"], "CREATE TABLE users")

        # Complete T-05 -> T-06 runs
        dag.mark_task_completed("T-05")
        wave_4 = dag.get_ready_tasks()
        self.assertEqual([t.task_id for t in wave_4], ["T-06"])

        dag.mark_task_completed("T-06")
        self.assertTrue(dag.is_all_completed())

    def test_dynamic_subtask_spawning(self):
        t1 = ExecutableTask(task_id="T-01", objective="Build Service", dependencies=[])
        t2 = ExecutableTask(task_id="T-02", objective="Run Tests", dependencies=["T-01"])
        dag = TaskDAG([t1, t2])

        # Mid-flight discovery: T-01 spawns a child dependency T-01-SUB1
        sub1 = ExecutableTask(task_id="T-01-SUB1", objective="Install Redis Adapter", dependencies=["T-01"])
        dag.spawn_child_subtasks("T-01", [sub1])

        self.assertIsNotNone(dag.get_task("T-01-SUB1"))
        self.assertEqual(dag.get_task("T-01-SUB1").dependencies, ["T-01"])
        self.assertEqual(len(dag.list_tasks()), 3)


class TestConcurrentDAGScheduler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=Path(self.temp_dir))
        self.cfg = OrchestratorConfig(
            max_replan_iterations=2,
            workspace_dir=Path(self.temp_dir)
        )
        self.mock_llm = MagicMock()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_concurrent_ready_wave_execution(self):
        # 2 independent parallel tasks in wave
        t1 = ExecutableTask(task_id="T-01", objective="Create file A", dependencies=[], outputs=["a.txt"])
        t2 = ExecutableTask(task_id="T-02", objective="Create file B", dependencies=[], outputs=["b.txt"])
        dag = TaskDAG([t1, t2])

        # Mock LLM returns content
        self.mock_llm.chat_json.return_value = {"summary": "Done creating files"}

        # Write the files so verification gate passes
        self.workspace.write_file("a.txt", "content a")
        self.workspace.write_file("b.txt", "content b")

        orchestrator = TaskOrchestrator(cfg=self.cfg, llm=self.mock_llm, workspace=self.workspace)
        scheduler = ConcurrentDAGScheduler(max_workers=2)

        res = scheduler.execute_ready_wave(
            task_dag=dag,
            orchestrator=orchestrator,
            state_data={"user_request": "Create files in parallel", "step_results": {}},
        )

        self.assertEqual(dag.get_task("T-01").state, TaskState.COMPLETED)
        self.assertEqual(dag.get_task("T-02").state, TaskState.COMPLETED)
        self.assertEqual(len(res["completed_subtasks"]), 2)
        self.assertIn("T-01", res["step_results"])
        self.assertIn("T-02", res["step_results"])


if __name__ == "__main__":
    unittest.main()
