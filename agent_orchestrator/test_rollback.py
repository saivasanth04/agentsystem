"""
Unit and Integration Tests for Workspace Transaction and Rollback Mechanism (Issue #20).
Validates:
1. WorkspaceTransaction (begin, commit, rollback, context manager).
2. WorkspaceTransactionManager (transaction lifecycle, tracking).
3. FailureDiagnostician (regression detection and should_rollback decisioning).
4. BuiltinToolRegistry & FilesystemMCPServer (rollback_to_checkpoint tool).
5. SQLite audit logging of rollback events (rollback_events table).
6. TaskOrchestrator automated rollback during replanning loop on regression.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.persistence.checkpoint_manager import WorkspaceCheckpointManager
from agent_orchestrator.persistence.transaction import (
    WorkspaceTransaction,
    WorkspaceTransactionManager,
    TransactionError,
)
from agent_orchestrator.persistence.state_store import SQLiteStateStore
from agent_orchestrator.runtime.diagnostics import FailureDiagnostician
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.mcp.servers.filesystem_server import FilesystemMCPServer
from agent_orchestrator.orchestrator import TaskOrchestrator, OrchestratorGraphState
from agent_orchestrator.config import OrchestratorConfig


class TestWorkspaceTransaction(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(self.temp_dir)
        self.ckpt_mgr = WorkspaceCheckpointManager(self.temp_dir)

        # Create initial files
        self.ws.write_file("module_a.py", "def add(a, b):\n    return a + b\n")
        self.ws.write_file("module_b.py", "def multiply(a, b):\n    return a * b\n")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_transaction_commit(self):
        tx = WorkspaceTransaction(
            workspace=self.ws,
            checkpoint_manager=self.ckpt_mgr,
            name="test-commit-tx",
        )
        ckpt_id = tx.begin()
        self.assertTrue(tx.is_active)
        self.assertIn(ckpt_id, self.ckpt_mgr.list_snapshots())

        # Modify existing file and create new file
        self.ws.write_file("module_a.py", "def add(a, b):\n    return a + b + 1\n")
        self.ws.write_file("module_c.py", "def subtract(a, b):\n    return a - b\n")

        res = tx.commit()
        self.assertEqual(res["status"], "COMMITTED")
        self.assertFalse(tx.is_active)
        self.assertTrue(tx.is_committed)

        # Files remain in modified state
        self.assertIn("return a + b + 1", self.ws.read_file("module_a.py"))
        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir, "module_c.py")))

    def test_transaction_rollback(self):
        tx = WorkspaceTransaction(
            workspace=self.ws,
            checkpoint_manager=self.ckpt_mgr,
            name="test-rollback-tx",
        )
        tx.begin()

        # Modify existing file, delete another, create brand new file
        self.ws.write_file("module_a.py", "CORRUPTED CONTENT")
        self.ws.delete_file("module_b.py")
        self.ws.write_file("bad_file.py", "BROKEN CODE")

        self.assertIn("CORRUPTED", self.ws.read_file("module_a.py"))
        self.assertIsNone(self.ws.read_file("module_b.py"))
        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir, "bad_file.py")))

        # Rollback
        res = tx.rollback(reason="Test failure")
        self.assertEqual(res["status"], "ROLLED_BACK")
        self.assertTrue(tx.is_rolled_back)
        self.assertIn("bad_file.py", res["deleted_files"])

        # Verify restoration: module_a restored, module_b restored, bad_file deleted
        self.assertEqual(self.ws.read_file("module_a.py"), "def add(a, b):\n    return a + b\n")
        self.assertEqual(self.ws.read_file("module_b.py"), "def multiply(a, b):\n    return a * b\n")
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "bad_file.py")))

    def test_transaction_context_manager_exception_rollback(self):
        tx = WorkspaceTransaction(
            workspace=self.ws,
            checkpoint_manager=self.ckpt_mgr,
            name="ctx-tx",
        )

        try:
            with tx:
                self.ws.write_file("module_a.py", "CORRUPTED")
                self.ws.write_file("garbage.py", "GARBAGE")
                raise RuntimeError("Something failed catastrophically!")
        except RuntimeError:
            pass

        self.assertTrue(tx.is_rolled_back)
        self.assertEqual(self.ws.read_file("module_a.py"), "def add(a, b):\n    return a + b\n")
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "garbage.py")))


class TestWorkspaceTransactionManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(self.temp_dir)
        self.ckpt_mgr = WorkspaceCheckpointManager(self.temp_dir)
        self.tx_mgr = WorkspaceTransactionManager(self.ws, self.ckpt_mgr)

        self.ws.write_file("core.py", "# Original core\n")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_manager_lifecycle(self):
        tx = self.tx_mgr.begin_transaction(name="task-1", scope="subtask")
        self.assertTrue(tx.is_active)

        self.ws.write_file("core.py", "# Corrupted core\n")
        self.ws.write_file("temp_extra.py", "# Extra\n")

        # Rollback via manager
        res = self.tx_mgr.rollback_transaction(name="task-1", scope="subtask", reason="Tests failed")
        self.assertEqual(res["status"], "ROLLED_BACK")
        self.assertEqual(self.ws.read_file("core.py"), "# Original core\n")
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "temp_extra.py")))

        # History recorded
        history = self.tx_mgr.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "ROLLED_BACK")


class TestFailureDiagnosticianRegression(unittest.TestCase):
    def setUp(self):
        self.diag = FailureDiagnostician(llm=None)

    def test_regression_detected_when_tests_degraded(self):
        baseline_test = {
            "passed": 5,
            "failed": 0,
            "stdout": "5 passed in 0.2s",
            "stderr": "",
        }
        current_test = {
            "passed": 2,
            "failed": 3,
            "stdout": "2 passed, 3 failed",
            "stderr": "FAILED test_auth.py\nFAILED test_api.py",
        }
        report = self.diag.diagnose_failure(
            task_title="Verify Changes",
            execution_stderr=current_test["stderr"],
            execution_stdout=current_test["stdout"],
            baseline_test_info=baseline_test,
            current_test_info=current_test,
            default_rollback_target="ckpt-baseline-1",
        )
        self.assertTrue(report.should_rollback)
        self.assertTrue(report.regression_detected)
        self.assertEqual(report.rollback_target, "ckpt-baseline-1")
        self.assertTrue(any("failed" in r.lower() or "regression" in r.lower() for r in report.reasons_for_rollback))

    def test_regression_detected_via_reviewer_feedback(self):
        review_info = {
            "verdict": "FAIL",
            "summary": "The coder made things worse. Multiple syntax errors and regression introduced.",
        }
        report = self.diag.diagnose_failure(
            task_title="Review Audit",
            execution_stderr="SyntaxError: invalid syntax",
            execution_stdout="",
            review_info=review_info,
            default_rollback_target="ckpt-baseline-1",
        )
        self.assertTrue(report.should_rollback)
        self.assertTrue(report.regression_detected)

    def test_no_rollback_for_initial_failure_without_regression(self):
        # When there is no baseline (first run or test defect), do not rollback
        report = self.diag.diagnose_failure(
            task_title="Initial Run",
            execution_stderr="AssertionError: expected 5 got 4",
            execution_stdout="",
        )
        self.assertFalse(report.should_rollback)
        self.assertFalse(report.regression_detected)


class TestRollbackTools(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(self.temp_dir)
        self.ckpt_mgr = WorkspaceCheckpointManager(self.temp_dir)
        self.tools = BuiltinToolRegistry(workspace=self.ws, checkpoint_manager=self.ckpt_mgr)
        self.mcp_fs = FilesystemMCPServer(root_dir=Path(self.temp_dir))

        self.ws.write_file("main.py", "print('hello')\n")
        self.ckpt_id = self.ckpt_mgr.create_snapshot("test-ckpt", self.ws).checkpoint_id

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_builtin_tool_rollback(self):
        # Modify and add files
        self.ws.write_file("main.py", "print('corrupted')\n")
        self.ws.write_file("scratch.py", "# temporary\n")

        # Rollback via BuiltinToolRegistry
        res = self.tools._rollback_to_checkpoint(checkpoint_id=self.ckpt_id, reason="Broken code")
        self.assertTrue(res["success"])
        self.assertEqual(self.ws.read_file("main.py"), "print('hello')\n")
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "scratch.py")))

    def test_mcp_fs_server_rollback(self):
        # Modify and add files
        self.ws.write_file("main.py", "print('corrupted')\n")
        self.ws.write_file("scratch2.py", "# temporary 2\n")

        # Rollback via MCP tool
        res = self.mcp_fs.call_tool("rollback_to_checkpoint", {"checkpoint_id": self.ckpt_id, "reason": "MCP test"})
        self.assertTrue(res["success"])
        self.assertEqual(self.ws.read_file("main.py"), "print('hello')\n")
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "scratch2.py")))


class TestSQLiteRollbackAuditLog(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_store = SQLiteStateStore(workspace_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_and_get_rollback_events(self):
        self.state_store.save_rollback_event(
            session_id="sess-001",
            task_id="T-01",
            checkpoint_id="ckpt-iter-0-baseline",
            trigger_reason="Regression detected in test suite",
            restored_files=["file1.py", "file2.py"],
            deleted_files=["new_bad.py"],
        )

        events = self.state_store.get_rollback_events(session_id="sess-001")
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["session_id"], "sess-001")
        self.assertEqual(ev["checkpoint_id"], "ckpt-iter-0-baseline")
        self.assertEqual(ev["restored_files"], ["file1.py", "file2.py"])
        self.assertEqual(ev["deleted_files"], ["new_bad.py"])


class TestOrchestratorRollbackOnRegression(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(self.temp_dir)
        cfg = OrchestratorConfig(workspace_dir=self.temp_dir, max_replan_iterations=2)
        from unittest.mock import MagicMock
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "root_cause_summary": "Regression detected in app.py",
            "failure_type": "CODE_DEFECT",
            "affected_files": ["app.py"],
            "suggested_remediation": ["Revert changes and re-implement"],
            "target_agent": "CODER",
        }
        self.orchestrator = TaskOrchestrator(cfg=cfg, llm=mock_llm, workspace=self.ws)

        # Baseline file
        self.ws.write_file("app.py", "def run():\n    return 'OK'\n")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_replan_triggers_rollback_when_coder_degrades_state(self):
        # 1. Baseline checkpoint
        ckpt_id = self.orchestrator.checkpoint_manager.create_snapshot(
            checkpoint_id="ckpt-iter-0-baseline",
            workspace=self.ws,
            stage="ITERATION_BASELINE",
        ).checkpoint_id

        # 2. Coder modifies app.py and creates 1 bad file
        self.ws.write_file("app.py", "def run():\n    BROKEN SYNTAX!!\n")
        self.ws.write_file("bad_util.py", "# broken utility\n")

        # 3. Simulate failure with regression in review & tests
        state: OrchestratorGraphState = {
            "user_request": "Refactor app",
            "task_understanding": {},
            "task_decomposition": [],
            "subtasks": [{"task_id": "T-01", "objective": "Refactor app", "state": "FAILED"}],
            "current_subtask_index": 0,
            "completed_subtasks": [],
            "step_results": {},
            "plan_output": None,
            "specification_output": None,
            "architecture_output": None,
            "code_output": {},
            "test_output": {
                "passed": 0,
                "failed": 1,
                "stderr": "SyntaxError: invalid syntax in app.py",
            },
            "baseline_test_info": {
                "passed": 1,
                "failed": 0,
                "stderr": "",
            },
            "review_output": {
                "verdict": "FAIL",
                "summary": "Coder broke existing tests. Regression detected, made things worse.",
            },
            "verdict": "FAIL",
            "iteration": 0,
            "max_iterations": 2,
            "remediation_plan": [],
            "replan_history": [],
            "target_agent_for_fix": "CODER",
            "status": "RUNNING",
            "messages": [],
            "rollback_executed": False,
            "last_rollback": None,
        }

        updates = self.orchestrator._node_replan(state)

        # 4. Verify rollback executed
        self.assertTrue(updates.get("rollback_executed"))
        self.assertIsNotNone(updates.get("last_rollback"))
        self.assertEqual(updates["last_rollback"]["checkpoint_id"], "ckpt-iter-0-baseline")

        # 5. Verify files physically restored to baseline state
        self.assertEqual(self.ws.read_file("app.py"), "def run():\n    return 'OK'\n")
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "bad_util.py")))

        # 6. Verify SQLite logged rollback event
        events = self.orchestrator.state_store.get_rollback_events(self.orchestrator.active_session_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["checkpoint_id"], "ckpt-iter-0-baseline")
        self.assertIn("bad_util.py", events[0]["deleted_files"])


if __name__ == "__main__":
    unittest.main()
