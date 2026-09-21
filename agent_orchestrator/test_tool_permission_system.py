"""
Unit and Integration Tests for Tool Permission System (Issue #31).
Validates:
  1. Role-based operation access (RBAC) across REVIEWER, TESTER, CODER, PLANNER
  2. Scoped writes (e.g. TESTER writes to tests/ only, blocked from src/)
  3. Scoped command execution (e.g. test runners allowed, destructive commands blocked)
  4. VCS commit approval requirements
  5. ReAct loop policy enforcement and actionable error feedback
  6. ToolRegistry tool filtering by agent role
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agent_orchestrator.runtime.permission_policy import (
    ToolPermissionPolicyEngine,
    ToolOperationType,
    PermissionPolicy,
    PolicyEvaluationResult,
)
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.runtime.task_graph import TaskPermissions


class TestToolPermissionSystem(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=self.temp_dir)
        self.tool_registry = BuiltinToolRegistry(workspace=self.workspace)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reviewer_read_allowed(self):
        """Reviewer must be allowed to perform READ operations."""
        res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="REVIEWER",
            tool_name="read_file",
            args={"filepath": "src/main.py"},
        )
        self.assertTrue(res.allowed)
        self.assertEqual(res.operation_type, ToolOperationType.READ)

    def test_reviewer_write_denied(self):
        """Reviewer must be strictly forbidden from performing WRITE operations."""
        res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="REVIEWER",
            tool_name="write_file",
            args={"filepath": "src/main.py", "content": "print('hello')"},
        )
        self.assertFalse(res.allowed)
        self.assertEqual(res.operation_type, ToolOperationType.WRITE)
        self.assertIn("Permission Denied", res.reason)
        self.assertIn("forbidden from performing 'WRITE'", res.reason)

    def test_reviewer_execute_denied(self):
        """Reviewer must be strictly forbidden from executing shell commands or scripts."""
        res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="REVIEWER",
            tool_name="terminal_execute",
            args={"command": "pytest"},
        )
        self.assertFalse(res.allowed)
        self.assertEqual(res.operation_type, ToolOperationType.EXECUTE)
        self.assertIn("forbidden from performing 'EXECUTE'", res.reason)

    def test_tester_write_tests_allowed(self):
        """Tester must be allowed to write test files under tests/ or matching test_*.py."""
        # tests/ directory
        res1 = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="TESTER",
            tool_name="write_file",
            args={"filepath": "tests/test_calculator.py", "content": "def test_add(): pass"},
        )
        self.assertTrue(res1.allowed)

        # test_*.py in root
        res2 = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="TESTER",
            tool_name="write_file",
            args={"filepath": "test_app.py", "content": "def test_app(): pass"},
        )
        self.assertTrue(res2.allowed)

    def test_tester_write_production_code_denied(self):
        """Tester must be forbidden from writing production code under src/ or core files."""
        res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="TESTER",
            tool_name="write_file",
            args={"filepath": "src/calculator.py", "content": "class Calculator: pass"},
        )
        self.assertFalse(res.allowed)
        self.assertIn("cannot modify protected path", res.reason)

    def test_tester_execute_test_runner_allowed(self):
        """Tester must be allowed to execute test runners like pytest and python -m unittest."""
        res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="TESTER",
            tool_name="terminal_execute",
            args={"command": "pytest tests/test_calculator.py"},
        )
        self.assertTrue(res.allowed)

    def test_tester_execute_destructive_command_denied(self):
        """Tester must be blocked from running destructive or arbitrary commands."""
        res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="TESTER",
            tool_name="terminal_execute",
            args={"command": "rm -rf /"},
        )
        self.assertFalse(res.allowed)
        self.assertIn("Destructive or unauthorized command", res.reason)

    def test_coder_write_task_scoped(self):
        """Coder write operations must respect task-level allowed_write_paths."""
        task_perms = TaskPermissions(allowed_write_paths=["src/api/*"])

        # Allowed path
        res_allowed = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="CODER",
            tool_name="write_file",
            args={"filepath": "src/api/users.py", "content": "pass"},
            task_permissions=task_perms,
        )
        self.assertTrue(res_allowed.allowed)

        # Disallowed path outside task scope
        res_denied = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="CODER",
            tool_name="write_file",
            args={"filepath": "src/db/connection.py", "content": "pass"},
            task_permissions=task_perms,
        )
        self.assertFalse(res_denied.allowed)
        self.assertIn("outside task allowed paths", res_denied.reason)

    def test_coder_git_commit_requires_approval(self):
        """Coder git_commit must be blocked unless explicitly authorized by task permissions."""
        # Unapproved
        res_denied = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="CODER",
            tool_name="git_commit",
            args={"message": "Commit from agent"},
        )
        self.assertFalse(res_denied.allowed)
        self.assertIn("requires explicit authorization", res_denied.reason)

        # Approved
        approved_perms = TaskPermissions(allowed_commands=["git commit"])
        res_allowed = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="CODER",
            tool_name="git_commit",
            args={"message": "Approved commit"},
            task_permissions=approved_perms,
        )
        self.assertTrue(res_allowed.allowed)

    def test_tool_registry_filters_tools_by_role(self):
        """ToolRegistry must not give executable skill tools to REVIEWER or PLANNER."""
        reviewer_tools = [t.name for t in self.tool_registry.get_tools_for_agent("REVIEWER")]
        self.assertNotIn("execute_skill_script", reviewer_tools)
        self.assertNotIn("write_file", reviewer_tools)
        self.assertIn("read_file", reviewer_tools)

        planner_tools = [t.name for t in self.tool_registry.get_tools_for_agent("PLANNER")]
        self.assertNotIn("execute_skill_script", planner_tools)
        self.assertIn("read_file", planner_tools)

    def test_tool_registry_call_tool_with_caller_role(self):
        """ToolRegistry.call_tool with caller_role must enforce policy."""
        res = self.tool_registry.call_tool(
            "write_file",
            {"filepath": "hack.py", "content": "evil"},
            caller_role="REVIEWER",
        )
        self.assertFalse(res.get("success"))
        self.assertIn("Permission Denied", res.get("error", ""))

    def test_react_loop_enforces_policy_and_provides_feedback(self):
        """ReActAgentLoop must block unauthorized tool calls and return error feedback to model."""
        mock_llm = MagicMock()
        turn_count = 0

        def mock_chat_with_tools(messages, **kwargs):
            nonlocal turn_count
            turn_count += 1
            if turn_count == 1:
                # Turn 1: Reviewer tries to call write_file (unauthorized)
                return {
                    "content": "Trying to modify code",
                    "tool_calls": [{
                        "id": "tc1",
                        "name": "write_file",
                        "arguments": {"filepath": "main.py", "content": "bad edit"},
                    }],
                }
            else:
                # Turn 2: Inspect observation for permission error, then call complete_task
                last_msg = str(messages[-1])
                self.assertIn("Permission Denied", last_msg)
                return {
                    "content": "Done reviewing",
                    "tool_calls": [{
                        "id": "tc2",
                        "name": "complete_task",
                        "arguments": {"summary": "Review complete", "verdict": "PASS"},
                    }],
                }

        mock_llm.chat_with_tools.side_effect = mock_chat_with_tools

        react_loop = ReActAgentLoop(llm=mock_llm, tool_registry=self.tool_registry, max_turns=3)
        result = react_loop.run(
            system_prompt="You are Reviewer.",
            user_prompt="Review the code.",
            agent_name="REVIEWER",
        )

        self.assertEqual(result["turns_taken"], 2)
        # Verify file was never written
        self.assertFalse(self.workspace.file_exists("main.py"))


if __name__ == "__main__":
    unittest.main()
