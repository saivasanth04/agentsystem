"""
Unit and Integration Tests for Issue #48: Concurrency Control,
Multi-Threaded File Locking, 3-Way Merge, Sandboxed Merging,
Conflict-Free Wave Partitioning, and Implicit OCC in agent_orchestrator.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.tools.workspace import (
    WorkspaceManager,
    SandboxedWorkspace,
    MergeConflictError,
    three_way_merge_text,
)
from agent_orchestrator.tools.change_tracker import compute_sha256
from agent_orchestrator.runtime.idempotency import ConcurrencyConflictError
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.runtime.task_graph import (
    TaskDAG,
    ExecutableTask,
    TaskState,
    TaskPermissions,
)
from agent_orchestrator.runtime.dag_scheduler import ConcurrentDAGScheduler
from agent_orchestrator.runtime.react_loop import ReActLoop


class TestThreeWayMergeLogic(unittest.TestCase):
    def test_identical_edits_no_conflict(self):
        base = "line1\nline2\nline3\n"
        ours = "line1\nline2_mod\nline3\n"
        theirs = "line1\nline2_mod\nline3\n"
        can_merge, res = three_way_merge_text(base, ours, theirs)
        self.assertTrue(can_merge)
        self.assertEqual(res, "line1\nline2_mod\nline3\n")

    def test_non_overlapping_changes_merged_successfully(self):
        base = (
            "# Header\n"
            "def foo():\n"
            "    return 1\n"
            "\n"
            "def bar():\n"
            "    return 2\n"
        )
        # Ours modifies foo
        ours = (
            "# Header\n"
            "def foo():\n"
            "    return 100\n"
            "\n"
            "def bar():\n"
            "    return 2\n"
        )
        # Theirs modifies bar
        theirs = (
            "# Header\n"
            "def foo():\n"
            "    return 1\n"
            "\n"
            "def bar():\n"
            "    return 200\n"
        )
        can_merge, res = three_way_merge_text(base, ours, theirs)
        self.assertTrue(can_merge)
        self.assertIn("return 100", res)
        self.assertIn("return 200", res)
        self.assertIn("# Header", res)

    def test_overlapping_lines_raise_conflict(self):
        base = "def calculate():\n    return 42\n"
        ours = "def calculate():\n    return 100\n"
        theirs = "def calculate():\n    return 200\n"
        can_merge, res = three_way_merge_text(base, ours, theirs)
        self.assertFalse(can_merge)
        self.assertEqual(res, "")


class TestMultiThreadedFileLocking(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_concurrent_writes_thread_safety(self):
        """Verify per-file locking prevents race conditions and corrupted writes across threads."""
        filename = "counter.txt"
        self.workspace.write_file(filename, "initial")

        num_threads = 8
        writes_per_thread = 15
        errors = []

        def worker(thread_id: int):
            for i in range(writes_per_thread):
                try:
                    content = f"thread-{thread_id}-write-{i}\n"
                    self.workspace.write_file(filename, content)
                    # Verify read immediately returns non-corrupted string
                    read_back = self.workspace.read_file(filename)
                    if not read_back.startswith(f"thread-") or not read_back.endswith("\n"):
                        errors.append(f"Corrupted content observed: {read_back}")
                except Exception as e:
                    errors.append(str(e))

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, t) for t in range(num_threads)]
            for f in as_completed(futures):
                f.result()

        self.assertEqual(errors, [])
        final_content = self.workspace.read_file(filename)
        self.assertTrue(final_content.startswith("thread-"))


class TestSandboxedWorkspaceMerging(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_fast_forward_merge(self):
        self.workspace.write_file("app.py", "x = 1\n")
        sandbox = SandboxedWorkspace(self.workspace, sandbox_id="sb-ff")
        try:
            sandbox.write_file("app.py", "x = 2\n")
            sandbox.write_file("new_file.py", "y = 10\n")
            merged = sandbox.merge_into_main()
            self.assertIn("app.py", merged)
            self.assertIn("new_file.py", merged)
            self.assertEqual(self.workspace.read_file("app.py"), "x = 2\n")
            self.assertEqual(self.workspace.read_file("new_file.py"), "y = 10\n")
        finally:
            sandbox.cleanup()

    def test_three_way_merge_concurrent_sandboxes(self):
        initial_code = (
            "class Service:\n"
            "    def method_a(self):\n"
            "        return 'a'\n"
            "\n"
            "    def method_b(self):\n"
            "        return 'b'\n"
        )
        self.workspace.write_file("service.py", initial_code)

        # Sandbox 1 branches
        sb1 = SandboxedWorkspace(self.workspace, sandbox_id="sb-1")
        # Sandbox 2 branches
        sb2 = SandboxedWorkspace(self.workspace, sandbox_id="sb-2")

        try:
            # SB1 updates method_a
            sb1.replace_file_content("service.py", "return 'a'", "return 'alpha'")
            merged1 = sb1.merge_into_main()
            self.assertIn("service.py", merged1)
            self.assertIn("return 'alpha'", self.workspace.read_file("service.py"))

            # SB2 updates method_b (non-overlapping with method_a)
            sb2.replace_file_content("service.py", "return 'b'", "return 'beta'")
            merged2 = sb2.merge_into_main()
            self.assertIn("service.py", merged2)

            final_main = self.workspace.read_file("service.py")
            self.assertIn("return 'alpha'", final_main)
            self.assertIn("return 'beta'", final_main)
        finally:
            sb1.cleanup()
            sb2.cleanup()

    def test_overlapping_concurrent_sandboxes_raise_merge_conflict(self):
        initial_code = "def get_value():\n    return 0\n"
        self.workspace.write_file("config.py", initial_code)

        sb1 = SandboxedWorkspace(self.workspace, sandbox_id="sb-1")
        sb2 = SandboxedWorkspace(self.workspace, sandbox_id="sb-2")

        try:
            sb1.replace_file_content("config.py", "return 0", "return 10")
            sb1.merge_into_main()

            sb2.replace_file_content("config.py", "return 0", "return 20")
            with self.assertRaises(MergeConflictError) as ctx:
                sb2.merge_into_main()

            self.assertEqual(ctx.exception.filepath, "config.py")
            self.assertEqual(ctx.exception.sandbox_id, "sb-2")
            # Main file should remain what SB1 wrote
            self.assertEqual(self.workspace.read_file("config.py"), "def get_value():\n    return 10\n")
        finally:
            sb1.cleanup()
            sb2.cleanup()


class TestDAGSchedulerConflictPartitioning(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=self.test_dir)
        self.scheduler = ConcurrentDAGScheduler(max_workers=4)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_conflict_free_partitioning_defers_conflicting_writes(self):
        # Task 1 writes to users.py
        t1 = ExecutableTask(task_id="T-01", objective="Task 1", outputs=["users.py"])
        # Task 2 also writes to users.py (conflicting!)
        t2 = ExecutableTask(task_id="T-02", objective="Task 2", outputs=["users.py"])
        # Task 3 writes to orders.py (independent)
        t3 = ExecutableTask(task_id="T-03", objective="Task 3", outputs=["orders.py"])

        dag = TaskDAG([t1, t2, t3])
        mock_orch = MagicMock()
        mock_orch.workspace = self.workspace
        mock_orch.skill_registry.discover.return_value = []
        mock_orch.agent_registry.discover.return_value = []
        mock_orch._graph_to_state.return_value = MagicMock()

        mock_agent = MagicMock()
        mock_agent.name = "CODER"
        mock_agent.execute.return_value = {"success": True, "deliverables": {"status": "ok"}}
        mock_orch.select_agent.return_value = mock_agent

        mock_gate = MagicMock()
        mock_v_res = MagicMock()
        mock_v_res.passed = True
        mock_v_res.verified_outputs = ["users.py"]
        mock_v_res.failure_reasons = []
        mock_v_res.stdout = ""
        mock_gate.verify_task.return_value = mock_v_res
        mock_orch.verification_gate = mock_gate

        state_data = {
            "user_request": "build system",
            "step_results": {},
            "subtasks": dag.to_list(),
            "task_decomposition": dag.to_list(),
        }

        # First wave execution: T-01 and T-03 can run together, T-02 must be deferred!
        res_wave1 = self.scheduler.execute_ready_wave(dag, mock_orch, state_data)

        completed_ids = [t["task_id"] for t in res_wave1["completed_subtasks"]]
        self.assertIn("T-01", completed_ids)
        self.assertIn("T-03", completed_ids)
        self.assertNotIn("T-02", completed_ids)

        # T-02 should still be in READY state
        task2_ref = dag.get_task("T-02")
        self.assertEqual(task2_ref.state, TaskState.READY)

        # Second wave execution: Now T-02 runs without conflict
        res_wave2 = self.scheduler.execute_ready_wave(dag, mock_orch, state_data)
        completed_ids_2 = [t["task_id"] for t in res_wave2["completed_subtasks"]]
        self.assertIn("T-02", completed_ids_2)
        self.assertTrue(dag.is_all_completed())


class TestImplicitOCCInReActLoop(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=self.test_dir)
        self.registry = BuiltinToolRegistry(workspace=self.workspace)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_read_file_returns_hash_and_version(self):
        self.workspace.write_file("main.py", "print('hello world')\n")
        res = self.registry.call_tool("read_file", {"filepath": "main.py"})
        self.assertTrue(res["success"])
        self.assertIn("file_hash", res)
        self.assertIn("version", res)
        self.assertEqual(len(res["version"]), 12)
        self.assertEqual(res["file_hash"], compute_sha256("print('hello world')\n"))

    def test_react_loop_auto_injects_expected_hash_and_prevents_lost_update(self):
        self.workspace.write_file("target.py", "VAL = 1\n")
        initial_hash = compute_sha256("VAL = 1\n")

        # Mock LLM that simulates:
        # Turn 1: read_file("target.py")
        # Concurrent modification happens outside the agent!
        # Turn 2: write_file("target.py", "VAL = 2\n") without expected_hash
        mock_llm = MagicMock()

        call_idx = 0
        def chat_json_side_effect(messages, **kwargs):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return {
                    "tool_call": {
                        "name": "read_file",
                        "arguments": {"filepath": "target.py"}
                    }
                }
            elif call_idx == 2:
                # Concurrent external update right before agent writes!
                self.workspace.write_file("target.py", "VAL = 999\n")
                return {
                    "tool_call": {
                        "name": "write_file",
                        "arguments": {"filepath": "target.py", "content": "VAL = 2\n"}
                    }
                }
            else:
                return {"final_output": {"status": "done"}}

        mock_llm.chat_json.side_effect = chat_json_side_effect

        loop = ReActLoop(tool_registry=self.registry, llm=mock_llm)
        res = loop.execute(
            task_prompt="Update target.py",
            max_turns=3,
            workspace=self.workspace,
            task_id="t-occ-test",
        )

        # Turn 2 write_file should have encountered ConcurrencyConflictError due to auto-injected expected_hash!
        history = res.get("history_events") or []
        write_events = [e for e in history if e.get("tool") == "write_file"]
        self.assertTrue(len(write_events) > 0)
        write_res = write_events[0].get("result") or {}
        self.assertFalse(write_res.get("success", True))
        self.assertIn("Concurrency conflict on", str(write_res.get("error", "")))
        # Target content should still be the concurrent update, not silently overwritten
        self.assertEqual(self.workspace.read_file("target.py"), "VAL = 999\n")


if __name__ == "__main__":
    unittest.main()
