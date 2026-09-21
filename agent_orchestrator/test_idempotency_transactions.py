"""
Unit and integration tests for Issue #47: Idempotency, Optimistic Concurrency Control (OCC),
Deduplication, and Transaction Lifecycle in agent_orchestrator.
"""
import os
import shutil
import tempfile
import unittest

from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.change_tracker import compute_sha256
from agent_orchestrator.runtime.idempotency import (
    OperationRecord,
    OperationLedger,
    ConcurrencyConflictError,
    compute_operation_id,
    global_operation_ledger,
)
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.persistence.checkpoint_manager import WorkspaceCheckpointManager
from agent_orchestrator.persistence.transaction import WorkspaceTransaction, WorkspaceTransactionManager
from agent_orchestrator.persistence.state_store import SQLiteStateStore


class TestOperationLedgerAndDeduplication(unittest.TestCase):
    def setUp(self):
        self.ledger = OperationLedger()

    def test_record_and_has_executed(self):
        op = OperationRecord(
            operation_id="op-1",
            tool_name="write_file",
            filepath="main.py",
            arguments={"filepath": "main.py", "content": "print('hello')"},
            status="COMMITTED",
        )
        self.assertFalse(self.ledger.has_executed("op-1"))
        self.ledger.record(op)
        self.assertTrue(self.ledger.has_executed("op-1"))
        self.assertEqual(self.ledger.get("op-1").tool_name, "write_file")

    def test_compute_operation_id_deterministic(self):
        id1 = compute_operation_id("task-1", "write_file", {"filepath": "app.py", "content": "x = 1"})
        id2 = compute_operation_id("task-1", "write_file", {"filepath": "app.py", "content": "x = 1"})
        id3 = compute_operation_id("task-1", "write_file", {"filepath": "app.py", "content": "x = 2"})
        self.assertEqual(id1, id2)
        self.assertNotEqual(id1, id3)


class TestWorkspaceIdempotencyAndOCC(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ledger = OperationLedger()
        self.workspace = WorkspaceManager(root_dir=self.test_dir, ledger=self.ledger)
        self.notifications = []
        self.workspace.register_file_change_listener(lambda path, ctype: self.notifications.append((path, ctype)))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_write_file_idempotency(self):
        file_path = "hello.py"
        content = "print('world')\n"

        # 1. Initial write
        p = self.workspace.write_file(file_path, content, operation_id="write-1")
        self.assertTrue(p.exists())
        self.assertEqual(len(self.notifications), 1)
        self.assertEqual(self.notifications[0], ("hello.py", "CREATED"))
        rec1 = self.ledger.get("write-1")
        self.assertIsNotNone(rec1)
        self.assertEqual(rec1.status, "COMMITTED")
        self.assertFalse(rec1.no_op)

        # 2. Duplicate write with identical content
        p2 = self.workspace.write_file(file_path, content, operation_id="write-2")
        self.assertEqual(p, p2)
        # Should NOT fire new notification
        self.assertEqual(len(self.notifications), 1)
        rec2 = self.ledger.get("write-2")
        self.assertIsNotNone(rec2)
        self.assertEqual(rec2.status, "NO_OP")
        self.assertTrue(rec2.no_op)

    def test_write_file_occ_conflict(self):
        file_path = "config.py"
        self.workspace.write_file(file_path, "PORT = 8000\n")
        current_hash = compute_sha256(self.workspace.read_file(file_path))

        # Successful CAS update with matching hash
        self.workspace.write_file(file_path, "PORT = 8080\n", expected_hash=current_hash)
        self.assertIn("PORT = 8080", self.workspace.read_file(file_path))

        # Stale CAS update with outdated hash raises ConcurrencyConflictError
        with self.assertRaises(ConcurrencyConflictError):
            self.workspace.write_file(file_path, "PORT = 9000\n", expected_hash=current_hash)

    def test_replace_file_content_idempotency(self):
        file_path = "service.py"
        self.workspace.write_file(file_path, "def run():\n    return False\n")

        # 1. First replacement
        res1 = self.workspace.replace_file_content(
            file_path,
            target_content="return False",
            replacement_content="return True",
            operation_id="replace-1",
        )
        self.assertTrue(res1["success"])
        self.assertFalse(res1["no_op"])
        self.assertIn("return True", self.workspace.read_file(file_path))

        # 2. Second replacement of the same target (already replaced)
        res2 = self.workspace.replace_file_content(
            file_path,
            target_content="return False",
            replacement_content="return True",
            operation_id="replace-2",
        )
        self.assertTrue(res2["success"])
        self.assertTrue(res2["no_op"])
        self.assertTrue(res2["already_applied"])
        self.assertEqual(res2["diff"], "")

    def test_insert_lines_idempotency(self):
        file_path = "routes.py"
        self.workspace.write_file(file_path, "import sys\nimport os\n")

        # 1. First insertion
        res1 = self.workspace.insert_lines(
            file_path,
            line_number=2,
            content="import json",
            position="after",
            operation_id="insert-1",
        )
        self.assertTrue(res1["success"])
        self.assertFalse(res1["no_op"])
        lines_before = self.workspace.read_file(file_path).splitlines()

        # 2. Second insertion of the same line at the same position
        res2 = self.workspace.insert_lines(
            file_path,
            line_number=2,
            content="import json",
            position="after",
            operation_id="insert-2",
        )
        self.assertTrue(res2["success"])
        self.assertTrue(res2["no_op"])
        self.assertTrue(res2["already_applied"])
        lines_after = self.workspace.read_file(file_path).splitlines()
        self.assertEqual(lines_before, lines_after)

    def test_apply_diff_blocks_idempotency(self):
        file_path = "math_util.py"
        self.workspace.write_file(file_path, "def add(a, b):\n    return a - b\n")

        diff_block = "<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE"

        # 1. Apply diff block
        res1 = self.workspace.apply_diff_blocks(file_path, diff_block, operation_id="diff-1")
        self.assertTrue(res1["success"])
        self.assertFalse(res1["no_op"])
        self.assertIn("return a + b", self.workspace.read_file(file_path))

        # 2. Apply again (already applied)
        res2 = self.workspace.apply_diff_blocks(file_path, diff_block, operation_id="diff-2")
        self.assertTrue(res2["success"])
        self.assertTrue(res2["no_op"])
        self.assertTrue(res2["already_applied"])


class TestTransactionLifecycle(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ckpt_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=self.test_dir)
        self.ckpt_mgr = WorkspaceCheckpointManager(workspace_dir=self.test_dir)
        self.tx_mgr = WorkspaceTransactionManager(self.workspace, self.ckpt_mgr)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        shutil.rmtree(self.ckpt_dir, ignore_errors=True)

    def test_transaction_commit_and_rollback_status(self):
        tx = self.tx_mgr.begin_transaction(name="task_tx_1")
        self.workspace.write_file("file1.txt", "Initial 1")

        op = OperationRecord(
            operation_id="tx-op-1",
            tool_name="write_file",
            filepath="file1.txt",
            arguments={"filepath": "file1.txt", "content": "Initial 1"},
        )
        tx.record_operation(op)

        # Commit transaction
        res = self.tx_mgr.commit_transaction(name="task_tx_1")
        self.assertEqual(res["status"], "COMMITTED")
        self.assertEqual(op.status, "COMMITTED")

        # Start new transaction and test rollback
        tx2 = self.tx_mgr.begin_transaction(name="task_tx_2")
        self.workspace.write_file("file1.txt", "Modified 2")
        op2 = OperationRecord(
            operation_id="tx-op-2",
            tool_name="write_file",
            filepath="file1.txt",
            arguments={"filepath": "file1.txt", "content": "Modified 2"},
        )
        tx2.record_operation(op2)

        # Rollback
        res2 = self.tx_mgr.rollback_transaction(name="task_tx_2", reason="Testing rollback")
        self.assertEqual(res2["status"], "ROLLED_BACK")
        self.assertEqual(op2.status, "ROLLED_BACK")
        # Content should be restored to "Initial 1"
        self.assertEqual(self.workspace.read_file("file1.txt"), "Initial 1")


class TestSQLiteStateStoreOperations(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteStateStore(":memory:")

    def test_save_and_get_operation(self):
        op = OperationRecord(
            operation_id="store-op-1",
            tool_name="write_file",
            filepath="test.py",
            arguments={"filepath": "test.py", "content": "print(1)"},
            before_hash="h1",
            after_hash="h2",
            result={"success": True, "bytes_written": 8},
            status="COMMITTED",
            no_op=False,
        )
        self.store.save_operation(op, session_id="s1", task_id="t1")

        retrieved = self.store.get_operation("store-op-1")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["tool_name"], "write_file")
        self.assertEqual(retrieved["status"], "COMMITTED")
        self.assertFalse(retrieved["no_op"])

        ops = self.store.get_operations(session_id="s1")
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["operation_id"], "store-op-1")


class TestToolRegistryDeduplication(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=self.test_dir)
        self.registry = BuiltinToolRegistry(workspace=self.workspace)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_call_tool_deduplication(self):
        # 1. First write_file call
        res1 = self.registry.call_tool("write_file", {
            "filepath": "app.py",
            "content": "def main(): pass\n",
            "operation_id": "tool-op-1",
        })
        self.assertTrue(res1["success"])

        # 2. Duplicate call with same operation_id returns cached result
        res2 = self.registry.call_tool("write_file", {
            "filepath": "app.py",
            "content": "def main(): pass\n",
            "operation_id": "tool-op-1",
        })
        self.assertTrue(res2["success"])
        self.assertTrue(res2.get("cached", False) or res2.get("already_applied", False))


if __name__ == "__main__":
    unittest.main()
