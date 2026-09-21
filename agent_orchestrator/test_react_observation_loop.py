"""
Unit and integration tests for Enforced ReAct Observation/Action Loop (Issue #41).
Verifies:
1. ReActStep and ReActTrajectory serialization, step recording, and summaries.
2. Thought extraction and multi-turn Thought -> Action -> Observation -> Reflection tracing.
3. Rejection of blind Turn 1 JSON/deliverable bypasses when ReAct tool execution is enforced.
4. Active CoderAgent multi-turn execution (write_file -> ast_syntax_check -> complete_task).
5. Trajectory preservation in loop_result and code_summary.
"""
import json
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.runtime.react_step import ReActStep, ReActTrajectory
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.agents.coder import CoderAgent
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.state import OrchestratorState


class TestReActStepAndTrajectory(unittest.TestCase):
    """Tests ReActStep and ReActTrajectory data models."""

    def test_react_step_fields_and_serialization(self):
        step = ReActStep(
            turn=1,
            thought="I need to inspect the workspace directory to find source files.",
            action_tool="list_directory",
            action_input={"dir_path": "."},
            observation=["main.py", "utils.py"],
            reflection="Found 2 files. Next step is to read main.py.",
            duration_seconds=0.045,
            status="SUCCESS",
            is_error=False,
        )

        d = step.to_dict()
        self.assertEqual(d["turn"], 1)
        self.assertEqual(d["thought"], "I need to inspect the workspace directory to find source files.")
        self.assertEqual(d["action_tool"], "list_directory")
        self.assertEqual(d["action_input"], {"dir_path": "."})
        self.assertEqual(d["observation"], ["main.py", "utils.py"])
        self.assertEqual(d["reflection"], "Found 2 files. Next step is to read main.py.")
        self.assertEqual(d["duration_seconds"], 0.045)
        self.assertEqual(d["status"], "SUCCESS")
        self.assertFalse(d["is_error"])
        self.assertIn("timestamp", d)

    def test_react_trajectory_operations(self):
        traj = ReActTrajectory()
        self.assertEqual(traj.total_turns, 0)
        self.assertEqual(traj.tools_invoked, [])

        step1 = ReActStep(
            turn=1,
            thought="Read main.py",
            action_tool="read_file",
            action_input={"filepath": "main.py"},
            observation="print('hello')",
        )
        step2 = ReActStep(
            turn=2,
            thought="Write updated main.py",
            action_tool="write_file",
            action_input={"filepath": "main.py", "content": "print('world')"},
            observation={"success": True},
        )
        traj.add_step(step1)
        traj.add_step(step2)

        self.assertEqual(traj.total_turns, 2)
        self.assertEqual(traj.tools_invoked, ["read_file", "write_file"])

        summary = traj.summary()
        self.assertIn("ReAct Trajectory (2 steps)", summary)
        self.assertIn("read_file", summary)
        self.assertIn("write_file", summary)

        d = traj.to_dict()
        self.assertEqual(d["total_steps"], 2)
        self.assertEqual(d["tools_invoked"], ["read_file", "write_file"])
        self.assertEqual(len(d["steps"]), 2)


class MockLLMReAct:
    """Mock LLM that returns a planned sequence of responses."""
    def __init__(self, turns):
        self.turns = list(turns)
        self.turn_idx = 0
        self.prompts_received = []

    def chat_with_tools(self, messages, tools=None, tool_choice=None, model=None, temperature=0.2):
        self.prompts_received.append(messages)
        if self.turn_idx < len(self.turns):
            resp = self.turns[self.turn_idx]
            self.turn_idx += 1
            return resp
        return {"content": "No more turns configured.", "tool_calls": []}


class TestReActObservationLoopExecution(unittest.TestCase):
    """Integration tests verifying active observation/action loop enforcement."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(root_dir=self.test_dir)
        self.registry = BuiltinToolRegistry(workspace=self.ws)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_react_loop_records_thought_action_observation(self):
        """ReActAgentLoop must record thought, tool invocation, and environment observation in trajectory."""
        self.ws.write_file("data.txt", "initial data")

        # Turn 1: Thought + read_file tool call
        # Turn 2: Thought + complete_task tool call
        mock_turns = [
            {
                "content": "Thought: I should read data.txt to inspect its current content.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"filepath": "data.txt"}),
                        },
                    }
                ],
            },
            {
                "content": "Thought: The content is verified. I will now complete the task.",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "complete_task",
                            "arguments": json.dumps({"summary": "Task complete with data.txt inspected"}),
                        },
                    }
                ],
            },
        ]

        llm = MockLLMReAct(mock_turns)
        loop = ReActAgentLoop(llm=llm, tool_registry=self.registry, max_turns=5)

        result = loop.run(
            system_prompt="You are a ReAct agent.",
            user_prompt="Inspect data.txt and complete.",
        )

        self.assertEqual(result["turns_taken"], 2)
        traj = result.get("trajectory")
        self.assertIsNotNone(traj)
        self.assertIsInstance(traj, ReActTrajectory)
        self.assertEqual(len(traj.steps), 2)

        # Step 1 checks
        step1 = traj.steps[0]
        self.assertEqual(step1.turn, 1)
        self.assertIn("read data.txt", step1.thought)
        self.assertEqual(step1.action_tool, "read_file")
        self.assertEqual(step1.action_input.get("filepath"), "data.txt")
        self.assertIn("initial data", str(step1.observation))
        self.assertEqual(step1.status, "SUCCESS")
        self.assertFalse(step1.is_error)

        # Step 2 checks
        step2 = traj.steps[1]
        self.assertEqual(step2.turn, 2)
        self.assertIn("complete the task", step2.thought)
        self.assertEqual(step2.action_tool, "complete_task")
        self.assertEqual(step2.status, "SUCCESS")

    def test_coder_rejects_turn_1_blind_json_without_tools(self):
        """When enforce_react is active, attempting to dump raw JSON deliverables on Turn 1 without tools is rejected."""
        steps_observed = []

        def on_step_callback(stage, payload):
            steps_observed.append((stage, payload))

        # Turn 1: Tries to bypass tools by directly dumping JSON files deliverable
        # Turn 2: Follows feedback, executes write_file
        # Turn 3: Completes task
        mock_turns = [
            {
                "content": json.dumps({
                    "summary": "Hallucinated deliverable without invoking tools",
                    "files": [{"filepath": "solution.py", "content": "print('bypassed')"}],
                }),
                "tool_calls": [],
                "final_output": {
                    "summary": "Hallucinated deliverable without invoking tools",
                    "files": [{"filepath": "solution.py", "content": "print('bypassed')"}],
                },
            },
            {
                "content": "Thought: I was redirected to use tools. I will invoke write_file now.",
                "tool_calls": [
                    {
                        "id": "call_write",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({
                                "filepath": "solution.py",
                                "content": "def solution():\n    return 42\n",
                            }),
                        },
                    }
                ],
            },
            {
                "content": "Thought: File is written. Calling complete_task.",
                "tool_calls": [
                    {
                        "id": "call_finish",
                        "type": "function",
                        "function": {
                            "name": "complete_task",
                            "arguments": json.dumps({"summary": "Successfully wrote solution.py"}),
                        },
                    }
                ],
            },
        ]

        llm = MockLLMReAct(mock_turns)
        coder = CoderAgent(
            llm=llm,
            workspace=self.ws,
            tool_registry=self.registry,
            enforce_react=True,
        )
        coder.react_loop.on_step = on_step_callback

        state = OrchestratorState(user_request="Create solution.py")
        res = coder.execute(state, task_info={"outputs": ["solution.py"]})

        # Turn 1 bypass must have been intercepted
        stages = [s[0] for s in steps_observed]
        self.assertIn("TOOL_EXECUTION_REQUIRED", stages)

        # Confirm solution.py exists and has real content from tool execution
        self.assertTrue(self.ws.file_exists("solution.py"))
        content = self.ws.read_file("solution.py")
        self.assertIn("def solution():", content)

        # Check trajectory inside coder output
        traj = res.get("trajectory")
        self.assertIsNotNone(traj)
        self.assertIsInstance(traj, ReActTrajectory)
        # Turn 1: react_enforcer error step
        # Turn 2: write_file
        # Turn 3: complete_task
        self.assertEqual(len(traj.steps), 3)
        self.assertEqual(traj.steps[0].action_tool, "react_enforcer")
        self.assertTrue(traj.steps[0].is_error)
        self.assertEqual(traj.steps[1].action_tool, "write_file")
        self.assertEqual(traj.steps[2].action_tool, "complete_task")

    def test_coder_active_tools_multi_turn_execution(self):
        """CoderAgent executes write_file -> ast_syntax_check -> complete_task in an observation-driven loop."""
        mock_turns = [
            {
                "content": "Thought: I need to write math_utils.py with a gcd function.",
                "tool_calls": [
                    {
                        "id": "call_w",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({
                                "filepath": "math_utils.py",
                                "content": "def gcd(a, b):\n    while b:\n        a, b = b, a % b\n    return a\n",
                            }),
                        },
                    }
                ],
            },
            {
                "content": "Thought: math_utils.py written. Let me verify syntax with ast_syntax_check.",
                "tool_calls": [
                    {
                        "id": "call_ast",
                        "type": "function",
                        "function": {
                            "name": "ast_syntax_check",
                            "arguments": json.dumps({"filepath": "math_utils.py"}),
                        },
                    }
                ],
            },
            {
                "content": "Thought: Syntax is valid. Calling complete_task.",
                "tool_calls": [
                    {
                        "id": "call_done",
                        "type": "function",
                        "function": {
                            "name": "complete_task",
                            "arguments": json.dumps({"summary": "math_utils.py implemented and AST verified"}),
                        },
                    }
                ],
            },
        ]

        llm = MockLLMReAct(mock_turns)
        coder = CoderAgent(
            llm=llm,
            workspace=self.ws,
            tool_registry=self.registry,
            enforce_react=True,
        )

        state = OrchestratorState(user_request="Implement math_utils.py")
        res = coder.execute(state)

        self.assertTrue(self.ws.file_exists("math_utils.py"))
        traj = res.get("trajectory")
        self.assertIsNotNone(traj)
        self.assertEqual(traj.total_turns, 3)
        self.assertEqual(traj.tools_invoked, ["write_file", "ast_syntax_check", "complete_task"])

        # Check syntax check observation
        ast_step = traj.steps[1]
        self.assertEqual(ast_step.action_tool, "ast_syntax_check")
        self.assertEqual(ast_step.status, "SUCCESS")
        self.assertTrue(ast_step.observation.get("valid"))

    def test_trajectory_persisted_in_loop_result(self):
        """Verifies loop_result includes trajectory and matches step execution."""
        mock_turns = [
            {
                "content": "Thought: Creating config.json.",
                "tool_calls": [
                    {
                        "id": "call_cfg",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"filepath": "config.json", "content": "{\"debug\": true}"}),
                        },
                    }
                ],
            },
            {
                "content": "Thought: Done.",
                "tool_calls": [
                    {
                        "id": "call_c",
                        "type": "function",
                        "function": {
                            "name": "complete_task",
                            "arguments": json.dumps({"summary": "Config written"}),
                        },
                    }
                ],
            },
        ]

        llm = MockLLMReAct(mock_turns)
        loop = ReActAgentLoop(llm=llm, tool_registry=self.registry, max_turns=3)
        res = loop.run(
            system_prompt="You are an agent.",
            user_prompt="Write config.json",
        )

        self.assertIn("trajectory", res)
        traj = res["trajectory"]
        self.assertEqual(traj.total_turns, 2)
        summary_str = traj.summary()
        self.assertIn("write_file", summary_str)
        self.assertIn("complete_task", summary_str)


if __name__ == "__main__":
    unittest.main()
