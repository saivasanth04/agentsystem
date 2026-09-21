"""
Comprehensive unit and integration tests for Configuration & Version Tracking,
Change Provenance, Ambient Execution Scopes, and Agent Blame Attribution.
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.reproducibility.provenance import (
    ChangeProvenance,
    provenance_context,
)
from agent_orchestrator.persistence.state_store import SQLiteStateStore
from agent_orchestrator.tools.change_tracker import (
    FileChangeRecord,
    compute_file_change,
    ChangeJournal,
)
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.state import OrchestratorState, TaskStatus
from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.config import OrchestratorConfig


class TestProvenanceDataModel(unittest.TestCase):
    def test_change_provenance_roundtrip(self):
        prov = ChangeProvenance(
            agent_name="CODER",
            agent_version="2.1.0",
            model_name="claude-3-7-sonnet",
            model_provider="anthropic",
            model_seed=42,
            system_fingerprint="fp_test123",
            prompt_template_id="coder_diff_edit_v2",
            prompt_hash="abc123def456",
            skill_name="tdd",
            skill_version="1.0.0",
            tool_name="replace_file_content",
            tool_version="1.0.0",
            tool_source="builtin",
            git_commit_sha="deadbeef12345",
            git_branch="feature/auth",
            snapshot_id="snap-999",
        )
        d = prov.to_dict()
        self.assertEqual(d["agent_name"], "CODER")
        self.assertEqual(d["model_name"], "claude-3-7-sonnet")
        self.assertEqual(d["prompt_hash"], "abc123def456")
        self.assertEqual(d["git_commit_sha"], "deadbeef12345")

        restored = ChangeProvenance.from_dict(d)
        self.assertEqual(restored.agent_name, "CODER")
        self.assertEqual(restored.model_seed, 42)
        self.assertEqual(restored.skill_name, "tdd")

    def test_git_trailers_formatting(self):
        prov = ChangeProvenance(
            agent_name="CODER",
            agent_version="2.0",
            model_name="gpt-4o",
            model_seed=123,
            prompt_template_id="coder_prompt",
            prompt_hash="f0e1d2c3b4a5998877",
            skill_name="refactor",
            tool_name="write_file",
            tool_source="builtin",
            git_commit_sha="a1b2c3d4",
        )
        trailers = prov.to_git_trailers()
        self.assertIn("X-Agent: CODER/2.0", trailers)
        self.assertIn("X-Model: gpt-4o (seed=123)", trailers)
        self.assertIn("X-Prompt-Hash: coder_prompt#f0e1d2c3b4a5", trailers)
        self.assertIn("X-Skill: refactor/1.0", trailers)
        self.assertIn("X-Tool: write_file (builtin)", trailers)
        self.assertIn("X-Base-Commit: a1b2c3d4", trailers)


class TestProvenanceContextScope(unittest.TestCase):
    def test_nested_scoping_and_inheritance(self):
        self.assertIsNone(provenance_context.get_current())

        with provenance_context.scope(
            agent_name="CODER",
            agent_version="1.5.0",
            model_name="gpt-4o",
            git_commit_sha="commit_001",
        ):
            cur = provenance_context.get_current()
            self.assertIsNotNone(cur)
            self.assertEqual(cur.agent_name, "CODER")
            self.assertEqual(cur.model_name, "gpt-4o")

            # Nested tool execution inherits agent and model, overrides tool_name
            with provenance_context.scope(
                tool_name="replace_file_content",
                tool_source="builtin",
            ):
                nested = provenance_context.get_current()
                self.assertIsNotNone(nested)
                self.assertEqual(nested.agent_name, "CODER")
                self.assertEqual(nested.model_name, "gpt-4o")
                self.assertEqual(nested.tool_name, "replace_file_content")
                self.assertEqual(nested.git_commit_sha, "commit_001")

            # After exiting inner scope, outer scope restored
            after_inner = provenance_context.get_current()
            self.assertIsNone(after_inner.tool_name)
            self.assertEqual(after_inner.agent_name, "CODER")

        self.assertIsNone(provenance_context.get_current())

    def test_multithreaded_isolation(self):
        results = {}

        def thread_task(name, model):
            with provenance_context.scope(agent_name=name, model_name=model):
                cur = provenance_context.get_current()
                results[name] = (cur.agent_name, cur.model_name)

        t1 = threading.Thread(target=thread_task, args=("AGENT_A", "model-a"))
        t2 = threading.Thread(target=thread_task, args=("AGENT_B", "model-b"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(results["AGENT_A"], ("AGENT_A", "model-a"))
        self.assertEqual(results["AGENT_B"], ("AGENT_B", "model-b"))


class TestChangeTrackerProvenanceIntegration(unittest.TestCase):
    def test_compute_file_change_inherits_ambient_provenance(self):
        with provenance_context.scope(
            agent_name="CODER",
            model_name="o3-mini",
            prompt_hash="prompt_hash_123",
            tool_name="write_file",
        ):
            change = compute_file_change(
                filepath="src/main.py",
                before_content="print('hello')",
                after_content="print('hello world')",
            )
            self.assertIsNotNone(change)
            self.assertEqual(change.change_type, "MODIFIED")
            self.assertIsNotNone(change.provenance)
            self.assertEqual(change.provenance.agent_name, "CODER")
            self.assertEqual(change.provenance.model_name, "o3-mini")
            self.assertEqual(change.provenance.prompt_hash, "prompt_hash_123")


class TestStateStoreProvenanceAttribution(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteStateStore(db_path=":memory:")

    def test_save_and_query_file_change_attribution(self):
        session_id = "sess-prov-1"
        task_id = "task-prov-1"

        prov1 = ChangeProvenance(
            agent_name="CODER",
            agent_version="1.0",
            model_name="gpt-4o",
            prompt_hash="p_hash_coder",
            skill_name="tdd",
            tool_source="builtin",
            git_commit_sha="c_init",
        )
        rec1 = FileChangeRecord(
            filepath="src/auth.py",
            change_type="CREATED",
            lines_added=25,
            diff="+ class Auth: pass",
            provenance=prov1,
        )
        chg_id1 = self.store.save_file_change(session_id, task_id, rec1)

        prov2 = ChangeProvenance(
            agent_name="TESTER",
            agent_version="1.0",
            model_name="claude-3-5-sonnet",
            prompt_hash="p_hash_tester",
            skill_name="polyglot_tester",
            tool_source="builtin",
            git_commit_sha="c_init",
        )
        rec2 = FileChangeRecord(
            filepath="tests/test_auth.py",
            change_type="CREATED",
            lines_added=15,
            diff="+ def test_auth(): pass",
            provenance=prov2,
        )
        chg_id2 = self.store.save_file_change(session_id, task_id, rec2)

        # 1. Query Agent Blame / Attribution for src/auth.py
        attr = self.store.get_file_attribution("src/auth.py", session_id=session_id)
        self.assertEqual(len(attr), 1)
        self.assertEqual(attr[0]["agent_name"], "CODER")
        self.assertEqual(attr[0]["model_name"], "gpt-4o")
        self.assertEqual(attr[0]["prompt_hash"], "p_hash_coder")
        self.assertEqual(attr[0]["skill_name"], "tdd")

        # 2. Query changes by model
        gpt4_changes = self.store.get_changes_by_model("gpt-4o", session_id=session_id)
        self.assertEqual(len(gpt4_changes), 1)
        self.assertEqual(gpt4_changes[0]["filepath"], "src/auth.py")

        claude_changes = self.store.get_changes_by_model("claude-3-5-sonnet", session_id=session_id)
        self.assertEqual(len(claude_changes), 1)
        self.assertEqual(claude_changes[0]["filepath"], "tests/test_auth.py")

        # 3. Query changes by prompt
        prompt_changes = self.store.get_changes_by_prompt("p_hash_coder", session_id=session_id)
        self.assertEqual(len(prompt_changes), 1)
        self.assertEqual(prompt_changes[0]["agent_name"], "CODER")

        # 4. Query changes by skill
        skill_changes = self.store.get_changes_by_skill("polyglot_tester", session_id=session_id)
        self.assertEqual(len(skill_changes), 1)
        self.assertEqual(skill_changes[0]["filepath"], "tests/test_auth.py")

        # 5. Query changes by agent
        coder_changes = self.store.get_changes_by_agent("coder", session_id=session_id)
        self.assertEqual(len(coder_changes), 1)

    def test_save_and_get_operation_with_provenance(self):
        session_id = "sess-op-prov"
        prov = ChangeProvenance(
            agent_name="CODER",
            model_name="o3-mini",
            tool_name="replace_file_content",
            tool_source="builtin",
        )

        mock_op = MagicMock()
        mock_op.operation_id = "op-prov-1"
        mock_op.tool_name = "replace_file_content"
        mock_op.filepath = "app.py"
        mock_op.arguments = {"filepath": "app.py", "find": "a", "replace": "b"}
        mock_op.before_hash = "h1"
        mock_op.after_hash = "h2"
        mock_op.result = {"success": True}
        mock_op.status = "COMMITTED"
        mock_op.no_op = False
        mock_op.provenance = prov

        self.store.save_operation(mock_op, session_id=session_id)

        op = self.store.get_operation("op-prov-1")
        self.assertIsNotNone(op)
        self.assertIsNotNone(op["provenance"])
        self.assertEqual(op["provenance"]["agent_name"], "CODER")
        self.assertEqual(op["provenance"]["model_name"], "o3-mini")


class TestOrchestratorAttributionIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)
        self.state_store = SQLiteStateStore(db_path=":memory:")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_orchestrator_blame_queries(self):
        cfg = OrchestratorConfig(workspace_dir=self.temp_dir)
        orchestrator = TaskOrchestrator(
            cfg=cfg,
            workspace=self.workspace,
            state_store=self.state_store,
        )

        session_id = "sess-orch-blame"
        prov = ChangeProvenance(
            agent_name="CODER",
            agent_version="1.0",
            model_name="gpt-4o",
            prompt_hash="p_hash_abc",
            skill_name="tdd",
            git_commit_sha="commit_999",
        )
        rec = FileChangeRecord(
            filepath="services/user.py",
            change_type="MODIFIED",
            diff="+ def get_user(): pass",
            provenance=prov,
        )
        orchestrator.state_store.save_file_change(session_id, "task-1", rec)

        # Query attribution from orchestrator API
        blame = orchestrator.get_file_attribution("services/user.py", session_id=session_id)
        self.assertEqual(len(blame), 1)
        self.assertEqual(blame[0]["agent_name"], "CODER")
        self.assertEqual(blame[0]["model_name"], "gpt-4o")
        self.assertEqual(blame[0]["git_commit_sha"], "commit_999")

        by_model = orchestrator.get_changes_by_model("gpt-4o", session_id=session_id)
        self.assertEqual(len(by_model), 1)

        by_prompt = orchestrator.get_changes_by_prompt("p_hash_abc", session_id=session_id)
        self.assertEqual(len(by_prompt), 1)

        by_agent = orchestrator.get_changes_by_agent("CODER", session_id=session_id)
        self.assertEqual(len(by_agent), 1)


if __name__ == "__main__":
    unittest.main()
