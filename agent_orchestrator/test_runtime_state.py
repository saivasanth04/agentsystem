"""
Unit and Integration tests for Rich Runtime State Management (Issue #12).
Tests ExecutableTask lifecycle, TokenUsage cost tracking, Checkpoint snapshots,
Observation records, TaskAttempt history, ReAct telemetry, and OrchestratorState summary.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.runtime.task_graph import (
    TaskDAG,
    ExecutableTask,
    TaskState,
    TokenUsage,
    ObservationRecord,
    CheckpointRecord,
    TaskAttemptRecord,
    TaskPermissions,
    RetryPolicy,
)
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.runtime.dag_scheduler import ConcurrentDAGScheduler
from agent_orchestrator.state import OrchestratorState, TaskStatus, ReviewVerdict
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestRuntimeStateDataStructures(unittest.TestCase):
    """Unit tests for foundational telemetry data structures."""

    def test_token_usage_calculations_and_serialization(self):
        tu = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        cost = tu.calculate_cost(price_per_1m_prompt=0.15, price_per_1m_completion=0.60)
        expected_cost = (1000 / 1_000_000 * 0.15) + (500 / 1_000_000 * 0.60)
        self.assertAlmostEqual(cost, expected_cost, places=8)
        self.assertEqual(tu.cost_usd, cost)

        # Serialization roundtrip
        d = tu.to_dict()
        tu2 = TokenUsage.from_dict(d)
        self.assertEqual(tu2.prompt_tokens, 1000)
        self.assertEqual(tu2.completion_tokens, 500)
        self.assertEqual(tu2.total_tokens, 1500)
        self.assertAlmostEqual(tu2.cost_usd, cost, places=8)

        # Add
        tu.add(TokenUsage(prompt_tokens=500, completion_tokens=500, total_tokens=1000))
        self.assertEqual(tu.prompt_tokens, 1500)
        self.assertEqual(tu.completion_tokens, 1000)
        self.assertEqual(tu.total_tokens, 2500)

    def test_observation_record_serialization(self):
        obs = ObservationRecord(
            turn=1,
            tool_name="read_file",
            input_args={"filepath": "main.py"},
            output_result={"content": "print('hello')"},
            is_error=False,
        )
        d = obs.to_dict()
        obs2 = ObservationRecord.from_dict(d)
        self.assertEqual(obs2.turn, 1)
        self.assertEqual(obs2.tool_name, "read_file")
        self.assertEqual(obs2.input_args, {"filepath": "main.py"})
        self.assertFalse(obs2.is_error)

    def test_checkpoint_record_serialization(self):
        ckpt = CheckpointRecord(
            checkpoint_id="ckpt-101",
            stage="PRE_EXECUTION",
            file_hashes={"src/app.py": "abc123hash"},
        )
        d = ckpt.to_dict()
        ckpt2 = CheckpointRecord.from_dict(d)
        self.assertEqual(ckpt2.checkpoint_id, "ckpt-101")
        self.assertEqual(ckpt2.stage, "PRE_EXECUTION")
        self.assertEqual(ckpt2.file_hashes["src/app.py"], "abc123hash")

    def test_task_attempt_record_serialization(self):
        attempt = TaskAttemptRecord(
            attempt_number=1,
            agent_name="CODER",
            started_at="2026-09-18T10:00:00",
            completed_at="2026-09-18T10:00:05",
            status="COMPLETED",
            tools_used=["write_file", "ast_syntax_check"],
            skills_used=["frontend-design"],
            observations=[
                ObservationRecord(turn=1, tool_name="write_file", input_args={}, output_result={"success": True})
            ],
            errors=[],
            verification_result={"passed": True, "verified_outputs": ["app.py"]},
            token_usage=TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300),
            duration_seconds=5.0,
        )
        d = attempt.to_dict()
        attempt2 = TaskAttemptRecord.from_dict(d)
        self.assertEqual(attempt2.attempt_number, 1)
        self.assertEqual(attempt2.agent_name, "CODER")
        self.assertEqual(len(attempt2.tools_used), 2)
        self.assertEqual(len(attempt2.observations), 1)
        self.assertEqual(attempt2.token_usage.total_tokens, 300)
        self.assertEqual(attempt2.duration_seconds, 5.0)


class TestExecutableTaskState(unittest.TestCase):
    """Unit tests for rich ExecutableTask state lifecycle and checkpointing."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_checkpoint_generation_on_workspace(self):
        self.workspace.write_file("file1.py", "def add(a, b): return a + b\n")
        self.workspace.write_file("file2.py", "def sub(a, b): return a - b\n")

        task = ExecutableTask(
            task_id="T-10",
            objective="Add math functions",
            required_capabilities=["python"],
        )

        ckpt_pre = task.create_checkpoint(self.workspace, "PRE_EXECUTION")
        self.assertEqual(ckpt_pre.stage, "PRE_EXECUTION")
        self.assertIn("file1.py", ckpt_pre.file_hashes)
        self.assertIn("file2.py", ckpt_pre.file_hashes)
        self.assertEqual(len(task.checkpoints), 1)

        # Modify a file and create post checkpoint
        self.workspace.write_file("file1.py", "def add(a, b): return a + b + 0\n")
        ckpt_post = task.create_checkpoint(self.workspace, "POST_EXECUTION")
        self.assertEqual(len(task.checkpoints), 2)
        self.assertNotEqual(ckpt_pre.file_hashes["file1.py"], ckpt_post.file_hashes["file1.py"])
        self.assertEqual(ckpt_pre.file_hashes["file2.py"], ckpt_post.file_hashes["file2.py"])

    def test_record_attempt_and_telemetry_aggregation(self):
        task = ExecutableTask(
            task_id="T-1",
            objective="Build API Endpoint",
            required_capabilities=["python", "backend"],
        )

        att1 = TaskAttemptRecord(
            attempt_number=1,
            agent_name="CODER",
            started_at="2026-09-18T10:00:00",
            completed_at="2026-09-18T10:00:03",
            status="FAILED",
            tools_used=["write_file"],
            skills_used=["api-and-interface-design"],
            observations=[ObservationRecord(turn=1, tool_name="write_file", input_args={}, output_result={"success": False})],
            errors=["SyntaxError: invalid syntax"],
            token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            duration_seconds=3.0,
        )
        task.record_attempt(att1)

        self.assertEqual(len(task.attempts), 1)
        self.assertEqual(task.tools_used, ["write_file"])
        self.assertEqual(task.skills_used, ["api-and-interface-design"])
        self.assertEqual(task.errors, ["SyntaxError: invalid syntax"])
        self.assertEqual(task.token_usage.total_tokens, 150)
        self.assertEqual(task.duration_seconds, 3.0)

        att2 = TaskAttemptRecord(
            attempt_number=2,
            agent_name="CODER",
            started_at="2026-09-18T10:00:05",
            completed_at="2026-09-18T10:00:09",
            status="COMPLETED",
            tools_used=["write_file", "ast_syntax_check"],
            skills_used=["api-and-interface-design", "debugging-and-error-recovery"],
            observations=[ObservationRecord(turn=1, tool_name="ast_syntax_check", input_args={}, output_result={"valid": True})],
            errors=[],
            token_usage=TokenUsage(prompt_tokens=150, completion_tokens=80, total_tokens=230),
            duration_seconds=4.0,
        )
        task.record_attempt(att2)

        self.assertEqual(len(task.attempts), 2)
        self.assertEqual(sorted(task.tools_used), ["ast_syntax_check", "write_file"])
        self.assertEqual(sorted(task.skills_used), ["api-and-interface-design", "debugging-and-error-recovery"])
        self.assertEqual(len(task.observations), 2)
        self.assertEqual(task.token_usage.total_tokens, 380)
        self.assertEqual(task.duration_seconds, 7.0)

    def test_executable_task_serialization_roundtrip(self):
        task = ExecutableTask(
            task_id="T-42",
            objective="Full Stack Component",
            parent_task_id="T-ROOT",
            required_capabilities=["python", "react"],
            required_tools=["write_file", "read_file"],
            preferred_skills=["frontend-design"],
            dependencies=["T-31"],
            inputs=["spec.json"],
            outputs=["Component.jsx"],
            acceptance_tests=["test_component.py"],
        )
        att = TaskAttemptRecord(
            attempt_number=1,
            agent_name="CODER",
            started_at="2026-09-18T10:00:00",
            completed_at="2026-09-18T10:00:05",
            status="COMPLETED",
            tools_used=["write_file"],
            skills_used=["frontend-design"],
            token_usage=TokenUsage(prompt_tokens=300, completion_tokens=150, total_tokens=450),
            duration_seconds=5.0,
        )
        task.record_attempt(att)

        d = task.to_dict()
        task2 = ExecutableTask.from_dict(d)

        self.assertEqual(task2.task_id, "T-42")
        self.assertEqual(task2.parent_task_id, "T-ROOT")
        self.assertEqual(len(task2.attempts), 1)
        self.assertEqual(task2.token_usage.total_tokens, 450)
        self.assertEqual(task2.tools_used, ["write_file"])
        self.assertEqual(task2.duration_seconds, 5.0)


class TestReActLoopTelemetry(unittest.TestCase):
    """Unit tests for telemetry capture inside ReActAgentLoop."""

    def test_react_loop_records_tokens_tools_and_observations(self):
        mock_llm = MagicMock()
        mock_registry = MagicMock()

        # Step 1: Tool call write_file
        # Step 2: Complete task
        mock_registry.get_schemas.return_value = []
        mock_registry.call_tool.side_effect = [
            {"success": True, "file": "app.py"},
            {"success": True, "output": "Done!"},
        ]

        mock_llm.chat_with_tools.side_effect = [
            {
                "content": "Writing file now",
                "tool_calls": [{"id": "call_1", "name": "write_file", "arguments": {"filepath": "app.py", "content": "print(1)"}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20},
            },
            {
                "content": "Task finished",
                "tool_calls": [{"id": "call_2", "name": "complete_task", "arguments": {}}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 15},
            },
        ]

        loop = ReActAgentLoop(mock_llm, mock_registry, max_turns=5)
        res = loop.run(
            system_prompt="System instructions",
            user_prompt="Write app.py",
        )

        self.assertIn("token_usage", res)
        self.assertIn("tools_used", res)
        self.assertIn("observations", res)
        self.assertIn("errors", res)

        self.assertEqual(res["tools_used"], ["complete_task", "write_file"])
        self.assertEqual(len(res["observations"]), 2)
        self.assertEqual(res["observations"][0].tool_name, "write_file")
        self.assertEqual(res["observations"][1].tool_name, "complete_task")
        self.assertGreater(res["token_usage"].total_tokens, 0)
        self.assertEqual(res["turns_taken"], 2)


class TestOrchestratorStateSummaryAndTree(unittest.TestCase):
    """Unit tests for OrchestratorState execution summary and hierarchical task tree."""

    def test_orchestrator_state_summary_and_tree(self):
        state = OrchestratorState(user_request="Build microservices and frontend")

        t1 = ExecutableTask(task_id="T-ROOT", objective="Root Project Architecture", state=TaskState.COMPLETED)
        t2 = ExecutableTask(task_id="T-BACKEND", parent_task_id="T-ROOT", objective="Backend API", state=TaskState.COMPLETED)
        t3 = ExecutableTask(task_id="T-FRONTEND", parent_task_id="T-ROOT", objective="Frontend UI", state=TaskState.COMPLETED)
        t4 = ExecutableTask(task_id="T-INTEG", parent_task_id="T-BACKEND", objective="Integration Tests", state=TaskState.PENDING)

        t1.record_attempt(TaskAttemptRecord(
            attempt_number=1,
            agent_name="ARCHITECTURE",
            started_at="2026-09-18T10:00:00",
            completed_at="2026-09-18T10:00:04",
            status="COMPLETED",
            tools_used=["list_files"],
            skills_used=["codebase-design"],
            token_usage=TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300),
            duration_seconds=4.0,
        ))

        t2.record_attempt(TaskAttemptRecord(
            attempt_number=1,
            agent_name="CODER",
            started_at="2026-09-18T10:00:05",
            completed_at="2026-09-18T10:00:10",
            status="COMPLETED",
            tools_used=["write_file", "ast_syntax_check"],
            skills_used=["api-and-interface-design"],
            token_usage=TokenUsage(prompt_tokens=400, completion_tokens=200, total_tokens=600),
            duration_seconds=5.0,
        ))

        dag = TaskDAG([t1, t2, t3, t4])
        state.task_dag = dag
        state.verdict = ReviewVerdict.PASS
        state.status = TaskStatus.COMPLETED

        tot_tokens = TokenUsage()
        tot_cost = 0.0
        tot_duration = 0.0
        for t in dag.list_tasks():
            tot_tokens.add(t.token_usage)
            tot_cost += t.cost
            tot_duration += t.duration_seconds
        state.total_token_usage = tot_tokens
        state.total_cost_usd = tot_cost
        state.total_duration_seconds = tot_duration

        # Test Execution Summary
        summary = state.get_execution_summary()
        self.assertEqual(summary["total_tasks"], 4)
        self.assertEqual(summary["completed_tasks"], 3)
        self.assertEqual(summary["pending_tasks"], 1)
        self.assertEqual(summary["total_attempts"], 2)
        self.assertEqual(summary["total_tokens"]["total_tokens"], 900)
        self.assertEqual(summary["total_duration_seconds"], 9.0)
        self.assertEqual(sorted(summary["distinct_tools_used"]), ["ast_syntax_check", "list_files", "write_file"])
        self.assertEqual(sorted(summary["distinct_skills_used"]), ["api-and-interface-design", "codebase-design"])
        self.assertEqual(summary["status"], "COMPLETED")
        self.assertEqual(summary["verdict"], "PASS")

        # Test Hierarchical Task Tree
        tree = state.get_task_tree()
        self.assertEqual(len(tree), 1)  # T-ROOT is the single root
        root_node = tree[0]
        self.assertEqual(root_node["task_id"], "T-ROOT")
        self.assertEqual(len(root_node["children"]), 2)  # T-BACKEND and T-FRONTEND
        backend_node = [c for c in root_node["children"] if c["task_id"] == "T-BACKEND"][0]
        self.assertEqual(len(backend_node["children"]), 1)  # T-INTEG is child of T-BACKEND
        self.assertEqual(backend_node["children"][0]["task_id"], "T-INTEG")


if __name__ == "__main__":
    unittest.main()
