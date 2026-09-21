"""
Comprehensive Test Suite for TaskDAG, ExecutableTask Contracts, Verification Gates, and Permission Boundaries.
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
    RetryPolicy,
)
from agent_orchestrator.runtime.verification import TaskVerificationGate, VerificationResult
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.config import OrchestratorConfig
from agent_orchestrator.state import OrchestratorState


class TestTaskDAGEngine(unittest.TestCase):
    def test_topological_sorting_and_ready_queue(self):
        t1 = ExecutableTask(
            task_id="T-01",
            objective="Define models",
            dependencies=[],
            state=TaskState.PENDING,
        )
        t2 = ExecutableTask(
            task_id="T-02",
            objective="Implement service",
            dependencies=["T-01"],
            state=TaskState.PENDING,
        )
        t3 = ExecutableTask(
            task_id="T-03",
            objective="Write tests",
            dependencies=["T-02"],
            state=TaskState.PENDING,
        )

        dag = TaskDAG([t1, t2, t3])

        # Initially, only T-01 is ready
        ready = dag.get_ready_tasks()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].task_id, "T-01")

        # Topo sort order
        sorted_tasks = dag.topological_sort()
        self.assertEqual([t.task_id for t in sorted_tasks], ["T-01", "T-02", "T-03"])

        # Complete T-01 -> T-02 should become ready
        dag.mark_task_completed("T-01")
        ready = dag.get_ready_tasks()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].task_id, "T-02")

        # Complete T-02 -> T-03 should become ready
        dag.mark_task_completed("T-02")
        ready = dag.get_ready_tasks()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].task_id, "T-03")

        # Complete T-03 -> All completed
        dag.mark_task_completed("T-03")
        self.assertTrue(dag.is_all_completed())
        self.assertEqual(len(dag.get_ready_tasks()), 0)

    def test_cycle_detection(self):
        t1 = ExecutableTask(task_id="T-01", objective="Task 1", dependencies=["T-02"])
        t2 = ExecutableTask(task_id="T-02", objective="Task 2", dependencies=["T-01"])
        dag = TaskDAG([t1, t2])

        self.assertTrue(dag.detect_cycles())
        with self.assertRaises(ValueError):
            dag.topological_sort()

    def test_blocked_task_on_failure(self):
        t1 = ExecutableTask(task_id="T-01", objective="Build Core", dependencies=[])
        t2 = ExecutableTask(task_id="T-02", objective="Build Extension", dependencies=["T-01"])
        dag = TaskDAG([t1, t2])

        dag.mark_task_failed("T-01", error_message="SyntaxError in core")
        ready = dag.get_ready_tasks()
        self.assertEqual(len(ready), 0)
        self.assertEqual(dag.get_task("T-02").state, TaskState.BLOCKED)
        self.assertTrue(dag.has_failures())

    def test_dynamic_remediation_injection(self):
        t1 = ExecutableTask(task_id="T-01", objective="Build Core", dependencies=[])
        t2 = ExecutableTask(task_id="T-02", objective="Run Integration Tests", dependencies=["T-01"])
        dag = TaskDAG([t1, t2])

        dag.mark_task_failed("T-01", error_message="ImportError: module not found")
        
        rem_task = ExecutableTask(
            task_id="T-REM-01",
            objective="Fix module import",
            dependencies=[],
            state=TaskState.PENDING,
        )
        dag.inject_remediation_task(rem_task, failed_task_id="T-01")

        # Downstream T-02 should now depend on T-REM-01 instead of failed T-01
        self.assertIn("T-REM-01", dag.get_task("T-02").dependencies)
        
        # T-REM-01 is ready
        ready = dag.get_ready_tasks()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].task_id, "T-REM-01")


class TestTaskVerificationGate(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=Path(self.temp_dir))
        self.tools = BuiltinToolRegistry(self.workspace)
        self.gate = TaskVerificationGate(workspace=self.workspace, tool_dispatcher=self.tools)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_verify_existing_valid_file_and_passing_test(self):
        # Create a valid python file in workspace
        code = "def add(a, b):\n    return a + b\n"
        self.workspace.write_file("math_ops.py", code)

        task = ExecutableTask(
            task_id="T-01",
            objective="Implement addition",
            outputs=["math_ops.py"],
            acceptance_tests=["python -c \"import math_ops; assert math_ops.add(2, 3) == 5\""],
        )

        res = self.gate.verify_task(task)
        self.assertTrue(res.passed)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(len(res.failure_reasons), 0)
        self.assertIn("math_ops.py", res.verified_outputs)

    def test_verify_missing_file_failure(self):
        task = ExecutableTask(
            task_id="T-02",
            objective="Create missing module",
            outputs=["missing_module.py"],
        )

        res = self.gate.verify_task(task)
        self.assertFalse(res.passed)
        self.assertTrue(any("does not exist" in r for r in res.failure_reasons))

    def test_verify_syntax_error_failure(self):
        invalid_code = "def broken(:\n    return 42\n"
        self.workspace.write_file("broken.py", invalid_code)

        task = ExecutableTask(
            task_id="T-03",
            objective="Create broken module",
            outputs=["broken.py"],
        )

        res = self.gate.verify_task(task)
        self.assertFalse(res.passed)
        self.assertTrue(any("syntax compilation failed" in r.lower() for r in res.failure_reasons))

    def test_verify_failing_acceptance_test(self):
        code = "def multiply(a, b):\n    return a + b  # Bug!\n"
        self.workspace.write_file("mult.py", code)

        task = ExecutableTask(
            task_id="T-04",
            objective="Implement multiplication",
            outputs=["mult.py"],
            acceptance_tests=["python -c \"import mult; assert mult.multiply(2, 3) == 6\""],
        )

        res = self.gate.verify_task(task)
        self.assertFalse(res.passed)
        self.assertTrue(any("Acceptance test command failed" in r for r in res.failure_reasons))


class TestPermissionEnforcement(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=Path(self.temp_dir))
        self.tools = BuiltinToolRegistry(self.workspace)
        self.mock_llm = MagicMock()
        self.react_loop = ReActAgentLoop(self.mock_llm, self.tools)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_write_permission_path_boundary(self):
        perms = TaskPermissions(allowed_write_paths=["src/*"])

        # Allowed path
        err_allowed = self.react_loop._check_permission("write_file", {"filepath": "src/module.py"}, perms)
        self.assertIsNone(err_allowed)

        # Denied path
        err_denied = self.react_loop._check_permission("write_file", {"filepath": "config/secret.py"}, perms)
        self.assertIsNotNone(err_denied)
        self.assertIn("Permission Denied", err_denied)


class TestOrchestratorDAGIntegration(unittest.TestCase):
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

    def test_orchestrator_decompose_into_task_dag(self):
        def mock_chat_json(messages, model=None, temperature=0.2):
            content = messages[0]["content"] if messages else ""
            if "Decomposer" in content or "DAG Planner" in content:
                return [
                    {
                        "task_id": "T-01",
                        "objective": "Build queue class",
                        "dependencies": [],
                        "required_capabilities": ["code-generation"],
                        "required_tools": ["filesystem", "terminal"],
                        "inputs": ["queue.py"],
                        "outputs": ["queue.py"],
                        "acceptance_tests": [],
                    },
                    {
                        "task_id": "T-02",
                        "objective": "Add queue unit tests",
                        "dependencies": ["T-01"],
                        "required_capabilities": ["testing"],
                        "required_tools": ["filesystem", "terminal"],
                        "inputs": ["test_queue.py"],
                        "outputs": ["test_queue.py"],
                        "acceptance_tests": [],
                    }
                ]
            return {"proposed_endpoints": ["/queue"], "services": ["QueueService"]}

        self.mock_llm.chat_json.side_effect = mock_chat_json

        orchestrator = TaskOrchestrator(cfg=self.cfg, llm=self.mock_llm, workspace=self.workspace)
        state = OrchestratorState(user_request="Build dynamic queue with test suite")

        understanding = orchestrator.understand_task(state)
        self.assertIn("Build dynamic queue", understanding["core_goal"])

        decomposition = orchestrator.decompose_task(state)
        self.assertEqual(len(decomposition), 2)
        self.assertIsNotNone(state.task_dag)
        self.assertEqual(len(state.task_dag.list_tasks()), 2)
        self.assertEqual(state.task_dag.get_task("T-01").objective, "Build queue class")
        self.assertEqual(state.task_dag.get_task("T-02").dependencies, ["T-01"])


if __name__ == "__main__":
    unittest.main()
