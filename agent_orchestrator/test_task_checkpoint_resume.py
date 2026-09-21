"""
Tests for Issue #14: Step-Level Checkpointing & Task Resumption (resume(task_id)).
"""
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Optional

from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.persistence.state_store import SQLiteStateStore
from agent_orchestrator.persistence.checkpoint_manager import WorkspaceCheckpointManager
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.runtime.task_graph import ExecutableTask, TaskState, ObservationRecord
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry


class MockLLMStepCalling:
    """Mock LLM simulating multi-turn tool calling and resumption."""
    def __init__(self, turns_plan: List[Dict[str, Any]]):
        self.turns_plan = turns_plan
        self.call_count = 0

    def chat_with_tools(self, messages, tools=None, tool_choice=None, model=None, temperature=0.2):
        if self.call_count < len(self.turns_plan):
            resp = self.turns_plan[self.call_count]
            self.call_count += 1
            return resp
        return {
            "content": "Task completed successfully.",
            "final_output": {"status": "SUCCESS", "deliverables": {"summary": "Done"}},
            "tool_calls": [],
        }

    def chat_json(self, messages, model=None, temperature=0.2):
        return {"final_output": {"status": "SUCCESS"}}

    def _extract_json(self, text):
        return {}


class TestTaskCheckpointResume(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_ckpt_resume_")
        self.workspace = WorkspaceManager(self.test_dir)
        self.state_store = SQLiteStateStore(workspace_dir=self.test_dir)
        self.checkpoint_manager = WorkspaceCheckpointManager(workspace_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_step_level_checkpointing_in_react_loop(self):
        """Verify that ReActAgentLoop records step-level checkpoints and observations to SQLite in real time."""
        tool_reg = BuiltinToolRegistry(self.workspace)
        mock_llm = MockLLMStepCalling([
            {
                "content": "I will write a file math.py",
                "tool_calls": [{
                    "id": "call_1",
                    "name": "write_file",
                    "arguments": {"filepath": "math.py", "content": "def add(a, b): return a + b"},
                }],
            },
            {
                "content": "Now completing task",
                "tool_calls": [{
                    "id": "call_2",
                    "name": "complete_task",
                    "arguments": {"output": {"summary": "Created math.py"}},
                }],
            }
        ])

        loop = ReActAgentLoop(llm=mock_llm, tool_registry=tool_reg)
        session_id = "sess-step-test-1"
        task_id = "T-01"

        res = loop.run(
            system_prompt="You are a coder.",
            user_prompt="Write math.py",
            state_store=self.state_store,
            session_id=session_id,
            task_id=task_id,
            checkpoint_manager=self.checkpoint_manager,
            workspace=self.workspace,
        )

        self.assertEqual(res["turns_taken"], 2)
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "math.py")))

        # Check recorded step transcripts in SQLite
        transcripts = self.state_store.load_task_steps(session_id, task_id)
        self.assertGreaterEqual(len(transcripts), 2)

        stages = [t["stage"] for t in transcripts]
        self.assertIn("ACTION_PLANNED", stages)
        self.assertIn("TOOL_EXECUTED", stages)
        self.assertIn("TASK_FINISHED", stages)

        # Check latest transcript query
        latest = self.state_store.load_latest_task_transcript(session_id, task_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["turn"], 2)
        self.assertGreaterEqual(len(latest["messages"]), 3)

    def test_task_resume_from_exact_step(self):
        """Verify resume_task loads previous turn state and continues without restarting from turn 0."""
        # 1. Simulate a crashed task after Turn 1
        session_id = "sess-crash-test-2"
        task_id = "T-02"

        # Turn 1: Wrote a helper file
        self.workspace.write_file("util.py", "# Helper code\n")
        obs1 = ObservationRecord(
            turn=1,
            tool_name="write_file",
            input_args={"filepath": "util.py", "content": "# Helper code\n"},
            output_result={"success": True, "path": "util.py"},
            status="SUCCESS",
        )
        self.state_store.save_observation(session_id, task_id, obs1)
        self.state_store.save_task_step(
            session_id=session_id,
            task_id=task_id,
            turn=1,
            stage="TOOL_EXECUTED",
            thought="Wrote util.py",
            tool_name="write_file",
            tool_args={"filepath": "util.py"},
            tool_result={"success": True},
            messages=[
                {"role": "system", "content": "You are a coder."},
                {"role": "user", "content": "Implement util.py and main.py"},
                {"role": "assistant", "content": "Writing util.py", "tool_calls": [{"id": "c1", "name": "write_file", "arguments": {}}]},
                {"role": "tool", "tool_call_id": "c1", "name": "write_file", "content": '{"success": true}'},
            ],
            observations=[obs1],
        )

        task = ExecutableTask(
            task_id=task_id,
            objective="Implement util.py and main.py",
            outputs=["main.py"],
            state=TaskState.RUNNING,
            owner_agent="CODER",
        )
        self.state_store.save_task(session_id, task)

        # 2. Set up orchestrator with Mock LLM providing turn 2 completion
        mock_llm = MockLLMStepCalling([
            {
                "content": "Now writing main.py and completing task",
                "tool_calls": [
                    {
                        "id": "c2",
                        "name": "write_file",
                        "arguments": {"filepath": "main.py", "content": "import util\nprint('Ready')"},
                    },
                    {
                        "id": "c3",
                        "name": "complete_task",
                        "arguments": {"output": {"summary": "Finished main.py and util.py"}},
                    },
                ],
            }
        ])

        orchestrator = TaskOrchestrator(
            workspace=self.workspace,
            state_store=self.state_store,
            checkpoint_manager=self.checkpoint_manager,
            llm=mock_llm,
        )

        # 3. Resume the task
        result = orchestrator.resume_task(task_id=task_id, session_id=session_id)

        self.assertEqual(result["task_id"], task_id)
        self.assertEqual(result["resumed_from_turn"], 1)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertTrue(result["verified"])
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "main.py")))

    def test_polymorphic_resume_with_task_id(self):
        """Verify orchestrator.resume(target_id) handles task_id directly."""
        session_id = "sess-poly-test-3"
        task_id = "T-03"

        task = ExecutableTask(
            task_id=task_id,
            objective="Build calculator.py",
            outputs=["calculator.py"],
            state=TaskState.RUNNING,
            owner_agent="CODER",
        )
        self.state_store.save_task(session_id, task)
        self.state_store.save_task_step(
            session_id=session_id,
            task_id=task_id,
            turn=1,
            stage="ACTION_PLANNED",
            thought="Starting calculator",
            messages=[{"role": "system", "content": "System prompt"}, {"role": "user", "content": "Build calculator"}],
        )

        mock_llm = MockLLMStepCalling([
            {
                "content": "Implementing calculator",
                "tool_calls": [{
                    "id": "c1",
                    "name": "write_file",
                    "arguments": {"filepath": "calculator.py", "content": "class Calculator: pass"},
                }, {
                    "id": "c2",
                    "name": "complete_task",
                    "arguments": {"output": {"summary": "Done"}},
                }],
            }
        ])

        orchestrator = TaskOrchestrator(
            workspace=self.workspace,
            state_store=self.state_store,
            checkpoint_manager=self.checkpoint_manager,
            llm=mock_llm,
        )

        # Resume by task_id
        res = orchestrator.resume(task_id)
        self.assertIsInstance(res, dict)
        self.assertEqual(res["task_id"], task_id)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "calculator.py")))

    def test_mutating_tool_filesystem_snapshot_and_rollback(self):
        """Verify mutating tool calls create step snapshots that can be cleanly rolled back."""
        # Initial workspace file
        self.workspace.write_file("stable.py", "# Stable v1\n")

        # Create pre-execution snapshot
        self.checkpoint_manager.create_snapshot("ckpt-T-04-pre_execution-1", self.workspace)

        # Mutate workspace
        self.workspace.write_file("stable.py", "# Broken v2 corrupted\n")
        self.workspace.write_file("junk.py", "# Extraneous junk\n")

        orchestrator = TaskOrchestrator(
            workspace=self.workspace,
            state_store=self.state_store,
            checkpoint_manager=self.checkpoint_manager,
        )

        # Rollback task to pre-execution checkpoint
        rb_res = orchestrator.rollback_task("T-04", "ckpt-T-04-pre_execution-1")
        self.assertTrue(rb_res["success"])

        # Check stable.py restored and junk.py deleted
        content = self.workspace.read_file("stable.py")
        self.assertIn("Stable v1", content)
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "junk.py")))


if __name__ == "__main__":
    unittest.main()

