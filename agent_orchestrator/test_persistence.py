"""
Unit and Integration tests for Persistent State Architecture (Issue #13).
Tests SQLiteStateStore, WorkspaceCheckpointManager snapshot & rollback,
Persistent MessageBus re-hydration, Orchestrator auto-persistence, and Session Resumption.
"""
from datetime import datetime
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.state import OrchestratorState, TaskStatus, ReviewVerdict, AgentMessage
from agent_orchestrator.runtime.task_graph import (
    TaskDAG,
    ExecutableTask,
    TaskState,
    TokenUsage,
    ObservationRecord,
    CheckpointRecord,
    TaskAttemptRecord,
)
from agent_orchestrator.runtime.messaging import MessageBus, StructuredMessage, MessageType
from agent_orchestrator.persistence.state_store import SQLiteStateStore
from agent_orchestrator.persistence.checkpoint_manager import WorkspaceCheckpointManager
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.orchestrator import TaskOrchestrator


class TestSQLiteStateStore(unittest.TestCase):
    """Unit tests for SQLiteStateStore engine."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_state.db")
        self.store = SQLiteStateStore(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_session_and_task_crud_roundtrip(self):
        session_id = "sess-test-101"
        state = OrchestratorState(user_request="Build a persistent cache")
        state.task_understanding = {"domain": "caching", "complexity": "medium"}
        state.verdict = ReviewVerdict.PASS
        state.status = TaskStatus.COMPLETED
        state.total_token_usage = TokenUsage(prompt_tokens=500, completion_tokens=250, total_tokens=750, cost_usd=0.000225)
        state.total_cost_usd = 0.000225
        state.total_duration_seconds = 12.5

        t1 = ExecutableTask(
            task_id="T-01",
            objective="Implement Cache",
            state=TaskState.COMPLETED,
            required_capabilities=["python"],
            inputs=["spec.json"],
            outputs=["cache.py"],
            token_usage=TokenUsage(prompt_tokens=300, completion_tokens=150, total_tokens=450),
            duration_seconds=6.0,
        )
        att = TaskAttemptRecord(
            attempt_number=1,
            agent_name="CODER",
            started_at="2026-09-18T10:00:00",
            completed_at="2026-09-18T10:00:06",
            status="COMPLETED",
            tools_used=["write_file"],
            skills_used=["code-simplification"],
            observations=[
                ObservationRecord(turn=1, tool_name="write_file", input_args={"filepath": "cache.py"}, output_result={"success": True})
            ],
            errors=[],
            token_usage=TokenUsage(prompt_tokens=300, completion_tokens=150, total_tokens=450),
            duration_seconds=6.0,
        )
        t1.record_attempt(att)
        t1.create_checkpoint(None, "PRE_EXECUTION")

        t2 = ExecutableTask(
            task_id="T-02",
            objective="Add Cache Tests",
            state=TaskState.PENDING,
            dependencies=["T-01"],
        )

        state.task_dag = TaskDAG([t1, t2])
        state.add_message("PLANNER", "PLANNING", "Synthesized 2 tasks")

        # Save to SQLite
        self.store.save_session(session_id, state)

        # Verify list_sessions
        sessions = self.store.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], session_id)
        self.assertEqual(sessions[0]["status"], "COMPLETED")

        # Load from SQLite
        loaded = self.store.load_session(session_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.user_request, "Build a persistent cache")
        self.assertEqual(loaded.status, TaskStatus.COMPLETED)
        self.assertEqual(loaded.verdict, ReviewVerdict.PASS)
        self.assertEqual(loaded.total_token_usage.total_tokens, 750)
        self.assertIsNotNone(loaded.task_dag)
        self.assertEqual(len(loaded.task_dag.list_tasks()), 2)

        # Inspect re-hydrated task 1
        loaded_t1 = loaded.task_dag.get_task("T-01")
        self.assertIsNotNone(loaded_t1)
        self.assertEqual(loaded_t1.state, TaskState.COMPLETED)
        self.assertEqual(len(loaded_t1.attempts), 1)
        self.assertEqual(loaded_t1.attempts[0].agent_name, "CODER")
        self.assertEqual(len(loaded_t1.observations), 1)
        self.assertEqual(loaded_t1.observations[0].tool_name, "write_file")
        self.assertEqual(len(loaded_t1.checkpoints), 1)

        # Messages roundtrip
        msgs = self.store.get_messages(session_id)
        self.assertGreaterEqual(len(msgs), 1)
        self.assertEqual(msgs[0].sender, "PLANNER")


class TestWorkspaceCheckpointManager(unittest.TestCase):
    """Unit tests for WorkspaceCheckpointManager snapshotting and rollback."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)
        self.mgr = WorkspaceCheckpointManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_snapshot_creation_and_workspace_rollback(self):
        # 1. Create baseline workspace files
        self.workspace.write_file("main.py", "def run():\n    print('baseline')\n")
        self.workspace.write_file("config.json", '{"version": "1.0"}')

        # 2. Take physical snapshot
        ckpt = self.mgr.create_snapshot("ckpt-baseline-1", self.workspace, "PRE_EXECUTION")
        self.assertIn("main.py", ckpt.file_hashes)
        self.assertIn("config.json", ckpt.file_hashes)

        # 3. Modify main.py, delete config.json, and add unwanted_file.py
        self.workspace.write_file("main.py", "def run():\n    raise RuntimeError('corrupted')\n")
        os.remove(os.path.join(self.workspace.root_dir, "config.json"))
        self.workspace.write_file("unwanted_file.py", "print('should be removed')")

        self.assertIn("corrupted", self.workspace.read_file("main.py"))
        self.assertTrue(os.path.isfile(os.path.join(self.workspace.root_dir, "unwanted_file.py")))
        self.assertFalse(os.path.isfile(os.path.join(self.workspace.root_dir, "config.json")))

        # 4. Perform Rollback
        res = self.mgr.rollback_to_checkpoint("ckpt-baseline-1", self.workspace)
        self.assertEqual(res["status"], "ROLLBACK_SUCCESS")
        self.assertIn("config.json", res["restored_files"])
        self.assertIn("main.py", res["restored_files"])
        self.assertIn("unwanted_file.py", res["deleted_files"])

        # 5. Verify restored state
        self.assertIn("baseline", self.workspace.read_file("main.py"))
        self.assertTrue(os.path.isfile(os.path.join(self.workspace.root_dir, "config.json")))
        self.assertFalse(os.path.isfile(os.path.join(self.workspace.root_dir, "unwanted_file.py")))


class TestPersistentMessageBus(unittest.TestCase):
    """Unit tests for MessageBus SQLite dual-write and re-hydration."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_bus.db")
        self.store = SQLiteStateStore(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_message_bus_persistence_and_reload(self):
        session_id = "sess-bus-1"
        bus1 = MessageBus(state_store=self.store, session_id=session_id)

        # Send direct message and publish on topic
        bus1.send_direct(StructuredMessage(
            sender="CODER",
            recipient="TESTER",
            message_type=MessageType.TASK_RESULT,
            content="Created auth module in auth.py",
            payload={"files": ["auth.py"]},
        ))
        bus1.publish(
            topic="architecture",
            message=StructuredMessage(
                sender="ARCHITECTURE",
                recipient="*",
                message_type=MessageType.FINDING,
                content="Use JWT authentication standard",
            ),
        )

        # Create fresh MessageBus instance (simulating process restart)
        bus2 = MessageBus(state_store=self.store, session_id=session_id)
        bus2.load_history_from_store(session_id)

        inbox_tester = bus2.get_inbox("TESTER")
        self.assertEqual(len(inbox_tester), 1)
        self.assertEqual(inbox_tester[0].sender, "CODER")
        self.assertEqual(inbox_tester[0].payload["files"], ["auth.py"])

        self.assertEqual(len(bus2._history), 2)


class TestOrchestratorPersistenceAndResumption(unittest.TestCase):
    """Integration tests for Orchestrator auto-persistence, crash recovery, and resume()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)
        self.db_path = os.path.join(self.temp_dir, ".orchestrator", "orchestrator_state.db")
        self.state_store = SQLiteStateStore(db_path=self.db_path)
        self.ckpt_manager = WorkspaceCheckpointManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_orchestrator_auto_persists_workflow_to_sqlite(self):
        mock_llm = MagicMock()
        def mock_chat_json(messages, **kwargs):
            sys_msg = messages[0].get("content", "") if isinstance(messages, list) and messages and isinstance(messages[0], dict) else ""
            msg_str = json.dumps(messages).lower()
            if "you are the reviewer" in sys_msg.lower() or "quality audit" in sys_msg.lower():
                return {"verdict": "PASS", "score_out_of_100": 98, "summary": "Approved"}
            if "you are the tester" in sys_msg.lower() or "automated test" in sys_msg.lower():
                return {"summary": "Implemented test_mod.py", "files": [{"filepath": "test_mod.py", "content": "import unittest\nclass TestMod(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n"}]}
            if "domain" in msg_str or "backend" in msg_str or "architect" in msg_str:
                return {"title": "Domain Analysis", "architectural_blueprint": ["mod.py"], "key_decisions": []}
            if "executable tasks" in msg_str or "decompose" in msg_str or "task_id" in msg_str:
                return [
                    {"task_id": "T-01", "objective": "Write module", "dependencies": [], "required_capabilities": ["python"], "required_tools": ["filesystem"], "inputs": [], "outputs": ["mod.py"], "acceptance_tests": []}
                ]
            return {"summary": "Implemented mod.py", "files": [{"filepath": "mod.py", "content": "print('hello')"}]}

        mock_llm.chat_json.side_effect = mock_chat_json

        orchestrator = TaskOrchestrator(
            llm=mock_llm,
            workspace=self.workspace,
            state_store=self.state_store,
            checkpoint_manager=self.ckpt_manager,
        )

        session_id = "sess-live-001"
        final_state = orchestrator.run("Create a python module", session_id=session_id)

        self.assertEqual(final_state.status, TaskStatus.COMPLETED)
        self.assertEqual(final_state.verdict, ReviewVerdict.PASS)

        # Verify state exists in SQLite
        loaded = self.state_store.load_session(session_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.status, TaskStatus.COMPLETED)
        self.assertEqual(loaded.verdict, ReviewVerdict.PASS)
        self.assertGreaterEqual(len(loaded.task_dag.list_tasks()), 1)
        self.assertEqual(loaded.task_dag.list_tasks()[0].state, TaskState.COMPLETED)

    def test_orchestrator_resumes_interrupted_dag(self):
        session_id = "sess-interrupted-002"

        # 1. Simulate a session that completed Task 1, but crashed before Task 2
        t1 = ExecutableTask(
            task_id="T-01",
            objective="Build storage engine",
            state=TaskState.COMPLETED,
            required_capabilities=["python"],
            result_data={"summary": "Storage engine built"},
        )
        t2 = ExecutableTask(
            task_id="T-02",
            objective="Build API route",
            state=TaskState.READY,
            dependencies=["T-01"],
            required_capabilities=["python"],
        )

        initial_state = OrchestratorState(user_request="Build storage and API")
        initial_state.task_dag = TaskDAG([t1, t2])
        initial_state.task_decomposition = initial_state.task_dag.to_list()
        initial_state.status = TaskStatus.IN_PROGRESS
        initial_state.verdict = ReviewVerdict.UNDECIDED

        self.state_store.save_session(session_id, initial_state)

        # 2. Re-create orchestrator and resume session
        mock_llm = MagicMock()
        mock_llm.chat_json.side_effect = [
            # T-02 Coder Execution
            {"summary": "API route implemented", "files": [{"filepath": "api.py", "content": "print('api')"}]},
            # Reviewer Audit
            {"verdict": "PASS", "score_out_of_100": 98, "summary": "All tasks verified"},
        ]

        orchestrator = TaskOrchestrator(
            llm=mock_llm,
            workspace=self.workspace,
            state_store=self.state_store,
            checkpoint_manager=self.ckpt_manager,
        )

        resumed_state = orchestrator.resume(session_id)

        # 3. Validate that T-01 remained completed and T-02 was executed to completion
        self.assertEqual(resumed_state.status, TaskStatus.COMPLETED)
        self.assertEqual(resumed_state.verdict, ReviewVerdict.PASS)
        self.assertTrue(resumed_state.task_dag.is_all_completed())
        self.assertEqual(resumed_state.task_dag.get_task("T-01").state, TaskState.COMPLETED)
        self.assertEqual(resumed_state.task_dag.get_task("T-02").state, TaskState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
