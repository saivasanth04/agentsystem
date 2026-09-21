"""
Unit tests for Issue #32: Human Approval Gates and Destructive Action Classification.
"""
import unittest
from unittest.mock import MagicMock
from agent_orchestrator.runtime.approval_gate import (
    ApprovalGate,
    AutoApprovalGate,
    InteractiveApprovalGate,
    PolicyBasedApprovalGate,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalDecision,
    DestructiveActionType,
    RiskLevel,
    DestructiveActionClassifier,
)
from agent_orchestrator.runtime.react_loop import ReActAgentLoop


class TestDestructiveActionClassifier(unittest.TestCase):
    def test_delete_file_tool_classified(self):
        res = DestructiveActionClassifier.classify_action("delete_file", {"filepath": "src/app.py"})
        self.assertIsNotNone(res)
        act_type, risk, target, desc = res
        self.assertEqual(act_type, DestructiveActionType.DELETE)
        self.assertEqual(risk, RiskLevel.CRITICAL)
        self.assertEqual(target, "src/app.py")

    def test_delete_lines_tool_classified(self):
        res = DestructiveActionClassifier.classify_action("delete_lines", {"filepath": "src/app.py", "start_line": 1, "end_line": 5})
        self.assertIsNotNone(res)
        act_type, risk, target, desc = res
        self.assertEqual(act_type, DestructiveActionType.DELETE)
        self.assertEqual(risk, RiskLevel.MEDIUM)

    def test_shell_rm_rf_classified(self):
        res = DestructiveActionClassifier.classify_action("terminal_execute", {"command": "rm -rf /tmp/data"})
        self.assertIsNotNone(res)
        act_type, risk, target, desc = res
        self.assertEqual(act_type, DestructiveActionType.DELETE)
        self.assertEqual(risk, RiskLevel.CRITICAL)

    def test_git_reset_classified(self):
        res = DestructiveActionClassifier.classify_action("bash", {"command": "git reset --hard HEAD~1"})
        self.assertIsNotNone(res)
        act_type, risk, target, desc = res
        self.assertEqual(act_type, DestructiveActionType.GIT_RESET)
        self.assertEqual(risk, RiskLevel.HIGH)

    def test_git_push_classified(self):
        res = DestructiveActionClassifier.classify_action("run_command", {"command": "git push origin main"})
        self.assertIsNotNone(res)
        act_type, risk, target, desc = res
        self.assertEqual(act_type, DestructiveActionType.GIT_PUSH)
        self.assertEqual(risk, RiskLevel.CRITICAL)

    def test_database_migration_classified(self):
        for cmd in ["alembic upgrade head", "prisma migrate deploy", "python manage.py migrate", "DROP TABLE users;"]:
            res = DestructiveActionClassifier.classify_action("terminal_execute", {"command": cmd})
            self.assertIsNotNone(res, f"Failed to classify db migration: {cmd}")
            act_type, risk, target, desc = res
            self.assertEqual(act_type, DestructiveActionType.DATABASE_MIGRATION)
            self.assertEqual(risk, RiskLevel.CRITICAL)

    def test_package_installation_classified(self):
        for cmd in ["pip install requests", "npm install express", "yarn add lodash"]:
            res = DestructiveActionClassifier.classify_action("run_command", {"command": cmd})
            self.assertIsNotNone(res, f"Failed to classify package install: {cmd}")
            act_type, risk, target, desc = res
            self.assertEqual(act_type, DestructiveActionType.PACKAGE_INSTALLATION)
            self.assertEqual(risk, RiskLevel.HIGH)

    def test_skill_installation_classified(self):
        res = DestructiveActionClassifier.classify_action("install_skill", {"skill_name": "react-testing"})
        self.assertIsNotNone(res)
        act_type, risk, target, desc = res
        self.assertEqual(act_type, DestructiveActionType.PACKAGE_INSTALLATION)

    def test_network_request_classified(self):
        for cmd in ["curl https://api.example.com", "wget http://example.com/file.tar.gz"]:
            res = DestructiveActionClassifier.classify_action("bash", {"command": cmd})
            self.assertIsNotNone(res, f"Failed to classify network request: {cmd}")
            act_type, risk, target, desc = res
            self.assertEqual(act_type, DestructiveActionType.NETWORK_REQUEST)
            self.assertEqual(risk, RiskLevel.HIGH)

    def test_secret_access_classified(self):
        # Reading .env file
        res = DestructiveActionClassifier.classify_action("read_file", {"filepath": ".env"})
        self.assertIsNotNone(res)
        act_type, risk, target, desc = res
        self.assertEqual(act_type, DestructiveActionType.SECRET_ACCESS)
        self.assertEqual(risk, RiskLevel.HIGH)

        # Catting .env in shell
        res2 = DestructiveActionClassifier.classify_action("terminal_execute", {"command": "cat .env.production"})
        self.assertIsNotNone(res2)
        act_type2, risk2, target2, desc2 = res2
        self.assertEqual(act_type2, DestructiveActionType.SECRET_ACCESS)

    def test_safe_read_and_test_commands_not_destructive(self):
        for cmd in ["pytest tests/", "python -m unittest", "ls -la", "grep -r foo src/"]:
            res = DestructiveActionClassifier.classify_action("terminal_execute", {"command": cmd})
            self.assertIsNone(res, f"Safe command should not be destructive: {cmd}")


class TestApprovalGates(unittest.TestCase):
    def test_auto_approval_gate(self):
        gate = AutoApprovalGate()
        req = ApprovalRequest(
            action_type=DestructiveActionType.DELETE,
            tool_name="delete_file",
            target="tmp.txt",
            command_or_details="delete tmp.txt",
        )
        dec = gate.request_approval(req)
        self.assertTrue(dec.approved)
        self.assertEqual(dec.status, "AUTO_APPROVED")
        self.assertEqual(len(gate.history), 1)

    def test_policy_based_gate_denies_without_operator(self):
        policy = ApprovalPolicy(require_approval_for={DestructiveActionType.GIT_PUSH})
        gate = PolicyBasedApprovalGate(policy=policy, operator_gate=None)
        req = ApprovalRequest(
            action_type=DestructiveActionType.GIT_PUSH,
            tool_name="terminal_execute",
            target="git push",
            command_or_details="git push origin main",
        )
        dec = gate.request_approval(req)
        self.assertFalse(dec.approved)
        self.assertEqual(dec.status, "DENIED")

    def test_policy_based_gate_auto_approves_exempt_actions(self):
        policy = ApprovalPolicy(require_approval_for={DestructiveActionType.DELETE})
        gate = PolicyBasedApprovalGate(policy=policy)
        req = ApprovalRequest(
            action_type=DestructiveActionType.NETWORK_REQUEST,
            tool_name="terminal_execute",
            target="curl",
            command_or_details="curl https://google.com",
        )
        dec = gate.request_approval(req)
        self.assertTrue(dec.approved)
        self.assertEqual(dec.status, "AUTO_APPROVED")

    def test_interactive_gate_callback(self):
        mock_cb = MagicMock(return_value=True)
        gate = InteractiveApprovalGate(prompt_callback=mock_cb)
        req = ApprovalRequest(
            action_type=DestructiveActionType.DATABASE_MIGRATION,
            tool_name="terminal_execute",
            target="alembic",
            command_or_details="alembic upgrade head",
        )
        dec = gate.request_approval(req)
        mock_cb.assert_called_once_with(req)
        self.assertTrue(dec.approved)
        self.assertEqual(dec.status, "APPROVED")


class TestReActLoopApprovalGateIntegration(unittest.TestCase):
    def test_react_loop_denied_destructive_action(self):
        # Mock LLM calling delete_file
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "content": "Deleting file",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "delete_file", "arguments": {"filepath": "src/important.py"}},
                }
            ],
        }

        # Denying approval gate
        denying_gate = AutoApprovalGate(default_decision=False, default_reason="Security policy violation")
        mock_tools = MagicMock()
        mock_tools.get_schemas.return_value = []

        loop = ReActAgentLoop(llm=mock_llm, tool_registry=mock_tools, max_turns=1, approval_gate=denying_gate)
        res = loop.run(
            system_prompt="system",
            user_prompt="delete file",
            agent_name="CODER",
        )

        obs = res.get("observations", [])
        self.assertTrue(any(o.is_error for o in obs))
        self.assertTrue(any("Approval Denied" in str(o.output_result) for o in obs))
        # Ensure tool was NOT actually executed
        mock_tools.call_tool.assert_not_called()


if __name__ == "__main__":
    unittest.main()
