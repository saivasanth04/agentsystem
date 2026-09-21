"""
Unit and Integration Tests for Issue #46: Robust Distributed Task & Artifact Identity.
Verifies task_id, attempt_id, execution_id, parent_task_id, dependency IDs, and artifact IDs.
"""
import hashlib
import json
import os
import shutil
import tempfile
import unittest

from agent_orchestrator.runtime.task_graph import (
    ArtifactRecord,
    ExecutableTask,
    ObservationRecord,
    RetryPolicy,
    TaskAttemptRecord,
    TaskDAG,
    TaskPermissions,
    TaskState,
    TokenUsage,
    generate_task_id,
    spawn_child_subtasks,
)
from agent_orchestrator.persistence.state_store import SQLiteStateStore


class TestTaskIdentity(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_identity.db")
        self.state_store = SQLiteStateStore(db_path=self.db_path)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_generate_task_id_uniqueness_and_format(self):
        """Verify generate_task_id produces sequential and scoped IDs."""
        id1 = generate_task_id(prefix="T")
        id2 = generate_task_id(prefix="T")
        self.assertTrue(id1.startswith("T-"))
        self.assertTrue(id2.startswith("T-"))
        self.assertNotEqual(id1, id2)

        # Scoped to execution_id
        scoped_id = generate_task_id(prefix="TASK", execution_id="run-999")
        self.assertTrue(scoped_id.startswith("run-999:TASK-"))

    def test_task_attempt_record_identity(self):
        """Verify TaskAttemptRecord auto-generates attempt_id and retains execution_id."""
        attempt = TaskAttemptRecord(
            attempt_number=1,
            agent_name="CODER",
            execution_id="exec-42",
        )
        self.assertTrue(attempt.attempt_id.startswith("att-1-"))
        self.assertEqual(attempt.execution_id, "exec-42")

        # Verify serialization round-trip
        data = attempt.to_dict()
        self.assertIn("attempt_id", data)
        self.assertIn("execution_id", data)

        restored = TaskAttemptRecord.from_dict(data)
        self.assertEqual(restored.attempt_id, attempt.attempt_id)
        self.assertEqual(restored.execution_id, "exec-42")

    def test_observation_record_identity_and_linkage(self):
        """Verify ObservationRecord generates observation_id and links to attempt_id and execution_id."""
        obs = ObservationRecord(
            turn=1,
            tool_name="read_file",
            input_args={"path": "main.py"},
            output_result="print('hello')",
            attempt_id="att-1-abcdef",
            execution_id="exec-42",
        )
        self.assertTrue(obs.observation_id.startswith("obs-"))
        self.assertEqual(obs.attempt_id, "att-1-abcdef")
        self.assertEqual(obs.execution_id, "exec-42")

        data = obs.to_dict()
        self.assertEqual(data["observation_id"], obs.observation_id)
        self.assertEqual(data["attempt_id"], "att-1-abcdef")
        self.assertEqual(data["execution_id"], "exec-42")

        restored = ObservationRecord.from_dict(data)
        self.assertEqual(restored.observation_id, obs.observation_id)
        self.assertEqual(restored.attempt_id, "att-1-abcdef")
        self.assertEqual(restored.execution_id, "exec-42")

    def test_artifact_record_content_hash_and_identity(self):
        """Verify ArtifactRecord SHA-256 content hashing and identity generation."""
        content = "def add(a, b): return a + b\n"
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        artifact = ArtifactRecord.create(
            name="math_module.py",
            content=content,
            artifact_type="CODE_MODULE",
            task_id="T-01",
            attempt_id="att-1-12345",
            execution_id="exec-99",
            metadata={"language": "python"},
        )

        self.assertEqual(artifact.content_hash, expected_hash)
        self.assertTrue(artifact.artifact_id.startswith(f"art-{expected_hash[:12]}"))
        self.assertEqual(artifact.size_bytes, len(content.encode("utf-8")))
        self.assertEqual(artifact.task_id, "T-01")
        self.assertEqual(artifact.attempt_id, "att-1-12345")
        self.assertEqual(artifact.execution_id, "exec-99")

        # Dict content hashing (deterministic)
        dict_content = {"status": "ok", "count": 5}
        art2 = ArtifactRecord.create(name="result.json", content=dict_content)
        art3 = ArtifactRecord.create(name="result.json", content={"count": 5, "status": "ok"})
        # Keys sorted, so content_hash must be identical
        self.assertEqual(art2.content_hash, art3.content_hash)

        # Serialization round-trip
        data = artifact.to_dict()
        restored = ArtifactRecord.from_dict(data)
        self.assertEqual(restored.artifact_id, artifact.artifact_id)
        self.assertEqual(restored.content_hash, expected_hash)
        self.assertEqual(restored.size_bytes, artifact.size_bytes)
        self.assertEqual(restored.metadata, {"language": "python"})

    def test_executable_task_dual_artifact_sync(self):
        """Verify ExecutableTask maintains backward-compatible dict artifacts and typed ArtifactRecords."""
        task = ExecutableTask(
            task_id="T-01",
            objective="Build component",
            execution_id="sess-001",
        )

        # Add via raw dict
        task.add_artifact({"name": "output.txt", "content": "hello world"})
        self.assertEqual(len(task.artifacts), 1)
        self.assertEqual(len(task.typed_artifacts), 1)
        self.assertIsInstance(task.typed_artifacts[0], ArtifactRecord)
        self.assertEqual(task.typed_artifacts[0].task_id, "T-01")
        self.assertEqual(task.typed_artifacts[0].execution_id, "sess-001")
        self.assertTrue(task.typed_artifacts[0].content_hash)

        # Add via typed ArtifactRecord
        art_typed = ArtifactRecord.create(name="extra.log", content="logs", task_id="T-01")
        task.add_artifact(art_typed)
        self.assertEqual(len(task.artifacts), 2)
        self.assertEqual(len(task.typed_artifacts), 2)
        self.assertEqual(task.artifacts[1]["name"], "extra.log")

    def test_spawn_child_subtasks_parent_and_execution_identity(self):
        """Verify spawn_child_subtasks links parent_task_id and inherits execution_id."""
        t1 = ExecutableTask(task_id="T-01", objective="Setup", execution_id="run-alpha")
        t2 = ExecutableTask(task_id="T-02", objective="Build Large Module", dependencies=["T-01"], execution_id="run-alpha")
        t3 = ExecutableTask(task_id="T-03", objective="Deploy", dependencies=["T-02"], execution_id="run-alpha")

        dag = TaskDAG([t1, t2, t3])

        child_a = ExecutableTask(task_id="T-02-A", objective="Subpart 1")
        child_b = ExecutableTask(task_id="T-02-B", objective="Subpart 2")

        spawn_child_subtasks(dag, parent_task_id="T-02", child_tasks=[child_a, child_b])

        # Verify parent linkage and execution inheritance
        self.assertEqual(child_a.parent_task_id, "T-02")
        self.assertEqual(child_b.parent_task_id, "T-02")
        self.assertEqual(child_a.execution_id, "run-alpha")
        self.assertEqual(child_b.execution_id, "run-alpha")

        # Verify dependency inheritance from parent: children inherit parent's dependencies
        self.assertIn("T-01", child_a.dependencies)
        self.assertIn("T-01", child_b.dependencies)

        # Downstream task T-03 should now depend on children
        t3_updated = dag.get_task("T-03")
        self.assertIn("T-02-A", t3_updated.dependencies)
        self.assertIn("T-02-B", t3_updated.dependencies)
        self.assertNotIn("T-02", t3_updated.dependencies)

    def test_sqlite_state_store_task_identity_persistence(self):
        """Verify SQLite state store persists and re-hydrates attempts, observations, and typed artifacts."""
        session_id = "sess-distributed-test"
        task = ExecutableTask(
            task_id="T-10",
            objective="Compile distributed kernel",
            execution_id=session_id,
            parent_task_id="T-PARENT",
        )

        obs = ObservationRecord(
            turn=1,
            tool_name="gcc",
            input_args={"flag": "-O3"},
            output_result="compiled successfully",
            execution_id=session_id,
        )

        attempt = TaskAttemptRecord(
            attempt_number=1,
            agent_name="CODER",
            execution_id=session_id,
            observations=[obs],
            tools_used=["gcc"],
        )
        task.record_attempt(attempt)

        artifact = ArtifactRecord.create(
            name="kernel.bin",
            content=b"\x7fELF\x02\x01\x01",
            artifact_type="BINARY",
            task_id="T-10",
            attempt_id=attempt.attempt_id,
            execution_id=session_id,
        )
        task.add_artifact(artifact)

        # Save to database
        self.state_store.save_task(session_id, task)

        # Load from database
        loaded_tasks = self.state_store.load_tasks(session_id)
        self.assertEqual(len(loaded_tasks), 1)

        loaded_task = loaded_tasks[0]
        self.assertEqual(loaded_task.task_id, "T-10")
        self.assertEqual(loaded_task.parent_task_id, "T-PARENT")
        self.assertEqual(loaded_task.execution_id, session_id)

        # Check attempt identity
        self.assertEqual(len(loaded_task.attempts), 1)
        loaded_attempt = loaded_task.attempts[0]
        self.assertEqual(loaded_attempt.attempt_id, attempt.attempt_id)
        self.assertEqual(loaded_attempt.execution_id, session_id)

        # Check observation identity
        self.assertEqual(len(loaded_task.observations), 1)
        loaded_obs = loaded_task.observations[0]
        self.assertEqual(loaded_obs.observation_id, obs.observation_id)
        self.assertEqual(loaded_obs.attempt_id, attempt.attempt_id)
        self.assertEqual(loaded_obs.execution_id, session_id)

        # Check typed artifacts table persistence
        self.assertEqual(len(loaded_task.typed_artifacts), 1)
        loaded_art = loaded_task.typed_artifacts[0]
        self.assertEqual(loaded_art.artifact_id, artifact.artifact_id)
        self.assertEqual(loaded_art.content_hash, artifact.content_hash)
        self.assertEqual(loaded_art.name, "kernel.bin")
        self.assertEqual(loaded_art.artifact_type, "BINARY")
        self.assertEqual(loaded_art.size_bytes, 7)
        self.assertEqual(loaded_art.task_id, "T-10")
        self.assertEqual(loaded_art.attempt_id, attempt.attempt_id)
        self.assertEqual(loaded_art.execution_id, session_id)


if __name__ == "__main__":
    unittest.main()
