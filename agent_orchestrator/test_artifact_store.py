"""
Unit and Integration Tests for Issue #50: Production-Grade Artifact Store
Verifies:
  - Canonical 7-category partitioning (plans, specifications, patches, logs, test_results, reports, snapshots)
  - Content-addressed deduplication (CAS) via SHA-256
  - Text, binary, JSON, and file-backed storage & retrieval
  - Uniform URI scheme resolution (artifact://...)
  - SQLiteStateStore integration and metadata persistence
  - WorkspaceCheckpointManager snapshot registration
  - Dual-write backward compatibility with OrchestratorState
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_orchestrator.persistence.artifact_store import ArtifactStore, ArtifactCategory
from agent_orchestrator.persistence.state_store import SQLiteStateStore
from agent_orchestrator.persistence.checkpoint_manager import WorkspaceCheckpointManager
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.runtime.task_graph import ArtifactRecord
from agent_orchestrator.state import OrchestratorState


class TestArtifactStore(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.store_dir = os.path.join(self.test_dir, ".orchestrator", "artifacts")
        self.state_store = SQLiteStateStore(workspace_dir=self.test_dir)
        self.artifact_store = ArtifactStore(storage_root=self.store_dir, state_store=self.state_store)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_all_seven_categories_partitioned(self):
        """Verify storage directory contains all 7 canonical category folders."""
        for cat in ArtifactCategory:
            cat_path = Path(self.store_dir) / cat.value
            self.assertTrue(cat_path.exists(), f"Missing category directory: {cat.value}")
            self.assertTrue(cat_path.is_dir())

    def test_put_and_read_across_all_categories(self):
        """Verify content can be stored and retrieved across plans, specs, patches, logs, test_results, reports, snapshots."""
        session_id = "sess-test-001"
        task_id = "T-01"

        # 1. Plans (JSON dict)
        plan_data = {"phases": ["setup", "implementation"], "target_files": ["main.py"]}
        rec_plan = self.artifact_store.put(
            category=ArtifactCategory.PLANS,
            name="execution_plan.json",
            content=plan_data,
            session_id=session_id,
            task_id=task_id,
        )
        self.assertEqual(rec_plan.category, "plans")
        self.assertTrue(rec_plan.uri_or_path.startswith("artifact://plans/"))
        self.assertEqual(self.artifact_store.read_json(rec_plan.artifact_id), plan_data)

        # 2. Specifications (Markdown text)
        spec_text = "# Specification\nMust implement authentication middleware."
        rec_spec = self.artifact_store.put(
            category=ArtifactCategory.SPECIFICATIONS,
            name="auth_spec.md",
            content=spec_text,
            session_id=session_id,
            task_id=task_id,
        )
        self.assertEqual(rec_spec.category, "specifications")
        self.assertEqual(self.artifact_store.read_text(rec_spec.artifact_id), spec_text)

        # 3. Patches (Diff text)
        diff_text = "--- a/auth.py\n+++ b/auth.py\n@@ -1 +1 @@\n-old\n+new"
        rec_patch = self.artifact_store.put(
            category=ArtifactCategory.PATCHES,
            name="auth_fix.patch",
            content=diff_text,
            session_id=session_id,
            task_id=task_id,
        )
        self.assertEqual(rec_patch.category, "patches")
        self.assertEqual(self.artifact_store.read_text(rec_patch.artifact_id), diff_text)

        # 4. Logs (Raw terminal text)
        log_text = "[2026-09-20] Build started...\n[2026-09-20] Compiled in 0.4s"
        rec_log = self.artifact_store.put(
            category=ArtifactCategory.LOGS,
            name="build_output.log",
            content=log_text,
            session_id=session_id,
            task_id=task_id,
        )
        self.assertEqual(rec_log.category, "logs")
        self.assertEqual(self.artifact_store.read_text(rec_log.artifact_id), log_text)

        # 5. Test Results (JSON test results)
        test_data = {"passed": 12, "failed": 0, "coverage_percent": 95.5}
        rec_test = self.artifact_store.put(
            category=ArtifactCategory.TEST_RESULTS,
            name="pytest_report.json",
            content=test_data,
            session_id=session_id,
            task_id=task_id,
        )
        self.assertEqual(rec_test.category, "test_results")
        self.assertEqual(self.artifact_store.read_json(rec_test.artifact_id), test_data)

        # 6. Reports (Review audit)
        report_data = {"verdict": "PASS", "confidence": 0.98, "recommendations": []}
        rec_rep = self.artifact_store.put(
            category=ArtifactCategory.REPORTS,
            name="quality_audit.json",
            content=report_data,
            session_id=session_id,
            task_id=task_id,
        )
        self.assertEqual(rec_rep.category, "reports")
        self.assertEqual(self.artifact_store.read_json(rec_rep.artifact_id), report_data)

        # 7. Snapshots (Binary / bytes)
        snap_bytes = b"BINARY_SNAPSHOT_TARBALL_DATA"
        rec_snap = self.artifact_store.put(
            category=ArtifactCategory.SNAPSHOTS,
            name="checkpoint_01.tar.gz",
            content=snap_bytes,
            session_id=session_id,
            task_id=task_id,
        )
        self.assertEqual(rec_snap.category, "snapshots")
        self.assertEqual(self.artifact_store.read_bytes(rec_snap.artifact_id), snap_bytes)

        # Verify summary statistics
        summary = self.artifact_store.get_summary()
        self.assertEqual(summary["total_artifacts"], 7)
        for cat in ArtifactCategory:
            self.assertEqual(summary["categories"][cat.value]["count"], 1)

    def test_content_addressed_storage_cas_deduplication(self):
        """Identical content in the same category produces the exact same artifact_id."""
        content = "print('Hello world!')\n"
        rec1 = self.artifact_store.put(
            category=ArtifactCategory.PATCHES,
            name="change1.patch",
            content=content,
        )
        rec2 = self.artifact_store.put(
            category=ArtifactCategory.PATCHES,
            name="change2.patch",
            content=content,
        )
        self.assertEqual(rec1.artifact_id, rec2.artifact_id)
        self.assertEqual(rec1.content_hash, rec2.content_hash)

    def test_put_file_from_workspace(self):
        """put_file imports a physical file from the workspace and computes mime-type."""
        source_file = Path(self.test_dir) / "source_spec.md"
        source_file.write_bytes(b"# API Spec\nEndpoints: /users, /auth")

        rec = self.artifact_store.put_file(
            category=ArtifactCategory.SPECIFICATIONS,
            file_path=source_file,
            name="imported_spec.md",
        )
        self.assertEqual(rec.category, "specifications")
        self.assertEqual(self.artifact_store.read_text(rec.artifact_id), "# API Spec\nEndpoints: /users, /auth")

    def test_uri_scheme_and_resolution(self):
        """ArtifactStore correctly generates and resolves uniform URIs."""
        rec = self.artifact_store.put(
            category=ArtifactCategory.LOGS,
            name="execution.log",
            content="Execution turn 1 completed.",
        )
        expected_uri = f"artifact://logs/{rec.artifact_id}/execution.log"
        self.assertEqual(rec.uri_or_path, expected_uri)

        resolved = self.artifact_store.resolve_uri(rec.uri_or_path)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.artifact_id, rec.artifact_id)
        self.assertEqual(resolved.name, "execution.log")

    def test_listing_and_filtering(self):
        """list_artifacts allows filtering by category, session, and task."""
        self.artifact_store.put("plans", "p1.json", {"a": 1}, session_id="s1", task_id="t1")
        self.artifact_store.put("plans", "p2.json", {"b": 2}, session_id="s1", task_id="t2")
        self.artifact_store.put("reports", "r1.json", {"c": 3}, session_id="s2", task_id="t1")

        s1_artifacts = self.artifact_store.list_artifacts(session_id="s1")
        self.assertEqual(len(s1_artifacts), 2)

        plans_only = self.artifact_store.list_artifacts(category="plans")
        self.assertEqual(len(plans_only), 2)

        s2_t1_reports = self.artifact_store.list_artifacts(session_id="s2", task_id="t1", category="reports")
        self.assertEqual(len(s2_t1_reports), 1)

    def test_sqlite_persistence_sync(self):
        """ArtifactStore puts are automatically mirrored to SQLiteStateStore."""
        rec = self.artifact_store.put(
            category=ArtifactCategory.TEST_RESULTS,
            name="coverage.json",
            content={"coverage": 100},
            session_id="sess-db-001",
            task_id="T-VERIFY",
        )
        # Query directly from SQLite
        db_rec = self.state_store.get_artifact(rec.artifact_id)
        self.assertIsNotNone(db_rec)
        self.assertEqual(db_rec.artifact_id, rec.artifact_id)
        self.assertEqual(db_rec.category, "test_results")
        self.assertEqual(db_rec.content_hash, rec.content_hash)

        # List from SQLite
        db_list = self.state_store.list_artifacts(session_id="sess-db-001")
        self.assertEqual(len(db_list), 1)
        self.assertEqual(db_list[0].name, "coverage.json")

    def test_checkpoint_manager_snapshot_integration(self):
        """WorkspaceCheckpointManager automatically logs snapshots in ArtifactStore under SNAPSHOTS."""
        ws = WorkspaceManager(root_dir=self.test_dir)
        ws.write_file("main.py", "print('v1')\n")

        ckpt_mgr = WorkspaceCheckpointManager(workspace_dir=self.test_dir, artifact_store=self.artifact_store)
        ckpt_rec = ckpt_mgr.create_snapshot(
            checkpoint_id="ckpt-test-001",
            workspace=ws,
            stage="PRE_EDIT",
            metadata={"session_id": "sess-snap-1", "task_id": "T-SNAP"},
        )
        self.assertEqual(ckpt_rec.checkpoint_id, "ckpt-test-001")

        # Verify snapshot artifact exists in ArtifactStore
        snap_artifacts = self.artifact_store.list_artifacts(category=ArtifactCategory.SNAPSHOTS)
        self.assertTrue(len(snap_artifacts) >= 1)
        snap_rec = snap_artifacts[0]
        self.assertEqual(snap_rec.category, "snapshots")
        self.assertIn("snapshot_ckpt-test-001.json", snap_rec.name)

    def test_orchestrator_state_backward_compatibility(self):
        """OrchestratorState retains backward-compatible dictionaries alongside artifact_store."""
        state = OrchestratorState(
            user_request="Build authentication module",
            artifact_store=self.artifact_store,
        )
        # Legacy dictionary access continues working seamlessly
        state.code_output = {"files": ["auth.py"], "summary": "Created auth.py"}
        state.test_output = {"passed": 5, "failed": 0}
        state.review_output = {"verdict": "PASS"}

        self.assertEqual(state.code_output["files"], ["auth.py"])
        self.assertEqual(state.test_output["passed"], 5)
        self.assertEqual(state.review_output["verdict"], "PASS")
        self.assertIsNotNone(state.artifact_store)


if __name__ == "__main__":
    unittest.main()
