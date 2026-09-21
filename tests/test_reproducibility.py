"""
Comprehensive unit and integration tests for Reproducibility, Execution Snapshots,
Model Seed Pinning, VCS Tracking, and Manifest Comparison.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.reproducibility.manifest import (
    GitSnapshot,
    ModelMetadata,
    PromptTemplateMetadata,
    SkillVersionRecord,
    ToolVersionRecord,
    EnvironmentSnapshot,
    ExecutionSnapshot,
    compare_snapshots,
)
from agent_orchestrator.reproducibility.recorder import ReproducibilityRecorder
from agent_orchestrator.llm import LLMClient
from agent_orchestrator.persistence.state_store import SQLiteStateStore
from agent_orchestrator.state import OrchestratorState, TaskStatus, ReviewVerdict
from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.config import OrchestratorConfig
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestReproducibilityManifest(unittest.TestCase):
    def test_git_snapshot_serialization(self):
        git_snap = GitSnapshot(
            commit_sha="a1b2c3d4e5",
            branch="main",
            is_dirty=True,
            staged_files_count=2,
            untracked_files_count=1,
            diff_patch_hash="f0e1d2c3b4a5",
            remote_url="https://github.com/example/repo.git",
        )
        d = git_snap.to_dict()
        self.assertEqual(d["commit_sha"], "a1b2c3d4e5")
        self.assertTrue(d["is_dirty"])

        restored = GitSnapshot.from_dict(d)
        self.assertEqual(restored.commit_sha, "a1b2c3d4e5")
        self.assertEqual(restored.diff_patch_hash, "f0e1d2c3b4a5")

    def test_model_metadata_serialization(self):
        m = ModelMetadata(
            model_name="gpt-4o",
            provider="openai",
            temperature=0.0,
            seed=42,
            system_fingerprint="fp_abc123",
            max_tokens=4096,
            model_tier="advanced",
        )
        d = m.to_dict()
        self.assertEqual(d["seed"], 42)
        restored = ModelMetadata.from_dict(d)
        self.assertEqual(restored.system_fingerprint, "fp_abc123")

    def test_execution_snapshot_roundtrip_and_hash(self):
        git_snap = GitSnapshot(commit_sha="abcdef12345", branch="feature", is_dirty=False)
        m = ModelMetadata(model_name="claude-3-7-sonnet", seed=100)
        p = PromptTemplateMetadata(template_id="planner_v1", content_hash="hash_p1")
        s = SkillVersionRecord(skill_name="debugging", version="1.0.0", content_hash="hash_s1")
        t = ToolVersionRecord(tool_name="read_file", version="1.0.0", schema_hash="hash_t1", source="builtin")

        snap = ExecutionSnapshot(
            snapshot_id="snap-001",
            session_id="sess-100",
            git_snapshot=git_snap,
            model_metadata={"planner": m},
            prompt_templates={"planner_prompt": p},
            skills={"debugging": s},
            tools={"read_file": t},
            seed=100,
        )
        snap.manifest_hash = snap.compute_manifest_hash()
        self.assertTrue(len(snap.manifest_hash) > 10)

        # JSON roundtrip
        json_str = snap.to_json()
        restored = ExecutionSnapshot.from_json(json_str)
        self.assertEqual(restored.snapshot_id, "snap-001")
        self.assertEqual(restored.session_id, "sess-100")
        self.assertEqual(restored.seed, 100)
        self.assertEqual(restored.git_snapshot.commit_sha, "abcdef12345")
        self.assertEqual(restored.model_metadata["planner"].model_name, "claude-3-7-sonnet")
        self.assertEqual(restored.manifest_hash, snap.manifest_hash)

    def test_compare_identical_snapshots(self):
        snap_a = ExecutionSnapshot(
            snapshot_id="snap-a",
            session_id="sess-a",
            seed=42,
            git_snapshot=GitSnapshot(commit_sha="c1", diff_patch_hash=None),
            model_metadata={"coder": ModelMetadata(model_name="gpt-4o", seed=42)},
            prompt_templates={"prompt": PromptTemplateMetadata(template_id="p1", content_hash="h1")},
            skills={"s1": SkillVersionRecord(skill_name="s1", version="1.0", content_hash="sh1")},
            tools={"t1": ToolVersionRecord(tool_name="t1", version="1.0", schema_hash="th1")},
        )
        snap_b = ExecutionSnapshot(
            snapshot_id="snap-b",
            session_id="sess-b",
            seed=42,
            git_snapshot=GitSnapshot(commit_sha="c1", diff_patch_hash=None),
            model_metadata={"coder": ModelMetadata(model_name="gpt-4o", seed=42)},
            prompt_templates={"prompt": PromptTemplateMetadata(template_id="p1", content_hash="h1")},
            skills={"s1": SkillVersionRecord(skill_name="s1", version="1.0", content_hash="sh1")},
            tools={"t1": ToolVersionRecord(tool_name="t1", version="1.0", schema_hash="th1")},
        )
        diff = compare_snapshots(snap_a, snap_b)
        self.assertTrue(diff["match"])
        self.assertEqual(diff["differences_count"], 0)

    def test_compare_divergent_snapshots(self):
        snap_a = ExecutionSnapshot(
            snapshot_id="snap-a",
            session_id="sess-a",
            seed=42,
            git_snapshot=GitSnapshot(commit_sha="c1"),
            model_metadata={"coder": ModelMetadata(model_name="gpt-4o", seed=42)},
        )
        snap_b = ExecutionSnapshot(
            snapshot_id="snap-b",
            session_id="sess-b",
            seed=99,  # Seed diff
            git_snapshot=GitSnapshot(commit_sha="c2"),  # Commit SHA diff
            model_metadata={"coder": ModelMetadata(model_name="claude-3-5-sonnet", seed=99)},  # Model diff
        )
        diff = compare_snapshots(snap_a, snap_b)
        self.assertFalse(diff["match"])
        self.assertTrue(diff["differences_count"] >= 3)
        self.assertIn("commit_sha", diff["git_diff"])
        self.assertIn("snapshot_a", diff["seed_diff"])
        self.assertIn("coder", diff["model_diff"])


class TestReproducibilityRecorder(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_capture_git_fallback_on_non_git_dir(self):
        recorder = ReproducibilityRecorder()
        git_snap = recorder.capture_git_snapshot(workspace_path=self.temp_dir)
        # Should gracefully return None when not a git repo
        self.assertIsNone(git_snap)

    def test_capture_metadata_and_hashes(self):
        recorder = ReproducibilityRecorder(default_seed=123)
        model_meta = recorder.capture_model_metadata(model_name="o3-mini", temperature=0.0)
        self.assertEqual(model_meta.seed, 123)
        self.assertEqual(model_meta.model_name, "o3-mini")

        prompt_meta = recorder.capture_prompt_metadata(
            template_id="system_prompt",
            prompt_text="You are an autonomous AI software engineer.",
            variables=["role", "task"],
        )
        self.assertEqual(prompt_meta.template_id, "system_prompt")
        self.assertTrue(len(prompt_meta.content_hash) > 10)

        # Mock skill registry
        mock_registry = MagicMock()
        mock_skill = MagicMock()
        mock_skill.name = "refactor"
        mock_skill.version = "1.2.0"
        mock_skill.instructions = "Refactor cleanly"
        mock_registry.list_skills.return_value = [mock_skill]

        skills = recorder.capture_skill_versions(mock_registry)
        self.assertIn("refactor", skills)
        self.assertEqual(skills["refactor"].version, "1.2.0")

        # Mock tool registry
        mock_tool_registry = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "bash"
        mock_tool.schema = {"type": "object", "properties": {"command": {"type": "string"}}}
        mock_tool_registry.get_all_tools.return_value = [mock_tool]

        tools = recorder.capture_tool_schemas(tool_registry=mock_tool_registry)
        self.assertIn("bash", tools)
        self.assertTrue(len(tools["bash"].schema_hash) > 10)

    def test_build_snapshot_full(self):
        recorder = ReproducibilityRecorder(default_seed=42)
        snap = recorder.build_snapshot(
            session_id="test-sess-1",
            workspace_path=self.temp_dir,
            seed=42,
        )
        self.assertEqual(snap.session_id, "test-sess-1")
        self.assertEqual(snap.seed, 42)
        self.assertTrue(snap.environment is not None)
        self.assertTrue(len(snap.manifest_hash) > 10)


class TestLLMSeedPinning(unittest.TestCase):
    def test_llm_client_seed_forwarding(self):
        client = LLMClient(api_key="test-key", default_seed=42)
        self.assertEqual(client.default_seed, 42)

        # Mock execute on resilient caller
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Deterministic answer"
        mock_choice.message.tool_calls = None
        mock_resp.choices = [mock_choice]
        mock_resp.system_fingerprint = "fp_999888"

        client.resilient_caller.execute = MagicMock(return_value=mock_resp)

        res = client.chat(messages=[{"role": "user", "content": "hello"}], seed=777)
        self.assertEqual(res, "Deterministic answer")
        self.assertEqual(client.last_system_fingerprint, "fp_999888")

        # Verify seed=777 was in base_kwargs passed to execute
        call_args = client.resilient_caller.execute.call_args
        self.assertEqual(call_args.kwargs["base_kwargs"]["seed"], 777)


class TestStateStoreReproducibilityPersistence(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteStateStore(db_path=":memory:")

    def test_save_and_load_session_with_reproducibility(self):
        session_id = "sess-repro-test-1"
        state = OrchestratorState(user_request="Refactor database layer")
        
        snap = ExecutionSnapshot(
            snapshot_id="snap-persist-1",
            session_id=session_id,
            seed=42,
            git_snapshot=GitSnapshot(commit_sha="deadbeef", is_dirty=False),
            model_metadata={"planner": ModelMetadata(model_name="gpt-4o", seed=42)},
        )
        snap.manifest_hash = snap.compute_manifest_hash()
        state.execution_snapshot = snap

        self.store.save_session(session_id, state)

        # Load back
        loaded = self.store.load_session(session_id)
        self.assertIsNotNone(loaded)
        self.assertIsNotNone(loaded.execution_snapshot)
        self.assertEqual(loaded.execution_snapshot.snapshot_id, "snap-persist-1")
        self.assertEqual(loaded.execution_snapshot.seed, 42)
        self.assertEqual(loaded.execution_snapshot.git_snapshot.commit_sha, "deadbeef")

    def test_save_and_get_execution_snapshot_direct(self):
        session_id = "sess-repro-test-2"
        state = OrchestratorState(user_request="Build API")
        self.store.save_session(session_id, state)

        snap = ExecutionSnapshot(
            snapshot_id="snap-direct-1",
            session_id=session_id,
            seed=999,
        )
        self.store.save_execution_snapshot(session_id, snap)

        retrieved = self.store.get_execution_snapshot(session_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.snapshot_id, "snap-direct-1")
        self.assertEqual(retrieved.seed, 999)


class TestOrchestratorReproducibilityIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)
        self.state_store = SQLiteStateStore(db_path=":memory:")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_orchestrator_records_snapshot_and_comparison(self):
        cfg = OrchestratorConfig(workspace_dir=self.temp_dir)
        cfg.seed = 42

        orchestrator = TaskOrchestrator(
            cfg=cfg,
            workspace=self.workspace,
            state_store=self.state_store,
        )

        # Mock langgraph workflow execution
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "user_request": "Test task",
            "verdict": "PASS",
            "iteration": 1,
            "max_iterations": 3,
            "status": "COMPLETED",
            "subtasks": [],
            "messages": [],
            "plan_output": {"steps": []},
            "specification_output": None,
            "architecture_output": None,
            "code_output": None,
            "test_output": None,
            "review_output": {"verdict": "PASS"},
            "baseline_test_info": None,
            "rollback_executed": False,
            "last_rollback": None,
            "execution_snapshot": None,
        }
        orchestrator.graph = mock_graph

        state = orchestrator.run("Implement user auth", session_id="sess-comp-1")
        self.assertEqual(state.status, TaskStatus.COMPLETED)

        # Retrieve snapshot
        snap = orchestrator.get_execution_snapshot("sess-comp-1")
        # Snapshot was either created in discovery or present
        snap_obj = orchestrator.reproducibility_recorder.build_snapshot(
            session_id="sess-comp-1",
            workspace_path=self.temp_dir,
            seed=42,
        )
        orchestrator.state_store.save_execution_snapshot("sess-comp-1", snap_obj)

        snap_saved = orchestrator.get_execution_snapshot("sess-comp-1")
        self.assertIsNotNone(snap_saved)
        self.assertEqual(snap_saved.seed, 42)

        # Create second identical session snapshot
        snap_obj2 = orchestrator.reproducibility_recorder.build_snapshot(
            session_id="sess-comp-2",
            workspace_path=self.temp_dir,
            seed=42,
        )
        orchestrator.state_store.save_session("sess-comp-2", OrchestratorState(user_request="req 2"))
        orchestrator.state_store.save_execution_snapshot("sess-comp-2", snap_obj2)

        comp = orchestrator.compare_session_reproducibility("sess-comp-1", "sess-comp-2")
        self.assertTrue(comp["match"])


if __name__ == "__main__":
    unittest.main()
