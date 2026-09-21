"""
Unit and Integration Tests for Network Access Policy (Issue #81).
Validates:
  1. NetworkAccessPolicy modes (DISABLED, ALLOWLIST_ONLY, UNRESTRICTED)
  2. Cloud metadata / SSRF prevention (169.254.169.254, metadata.google.internal)
  3. Blocked ports and blocked network utilities
  4. Dead proxy environment variable injection for air-gapped sandboxes
  5. Integration with LocalProcessSandbox
  6. Integration with ToolPermissionPolicyEngine (RBAC + ABAC)
  7. Integration with ReActAgentLoop task permissions
"""
import os
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.security.network_policy import (
    NetworkAccessMode,
    NetworkAccessPolicy,
    NetworkAccessDecision,
    NetworkAccessDeniedError,
)
from agent_orchestrator.security.sandbox import (
    SandboxPolicy,
    LocalProcessSandbox,
)
from agent_orchestrator.runtime.permission_policy import (
    ToolPermissionPolicyEngine,
    ToolOperationType,
    PermissionPolicy,
)
from agent_orchestrator.runtime.task_graph import TaskPermissions
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.workspace import WorkspaceManager
import tempfile
import shutil


class TestNetworkAccessPolicy(unittest.TestCase):
    """Unit tests for NetworkAccessPolicy core evaluation engine."""

    def test_disabled_mode_blocks_all_hosts(self):
        """Mode DISABLED must reject all host evaluations unconditionally."""
        policy = NetworkAccessPolicy(mode=NetworkAccessMode.DISABLED)
        decision = policy.evaluate_host("pypi.org")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.mode, NetworkAccessMode.DISABLED)
        self.assertIn("air-gapped", decision.reason)
        self.assertIsNotNone(decision.suggested_action)

    def test_disabled_mode_blocks_network_commands(self):
        """Mode DISABLED must reject commands invoking network binaries like curl, wget, nc."""
        policy = NetworkAccessPolicy(mode=NetworkAccessMode.DISABLED)
        for net_cmd in ["curl -s https://example.com", "wget http://test.org", "nc -zv 10.0.0.1 80"]:
            decision = policy.evaluate_command(net_cmd)
            self.assertFalse(decision.allowed, f"Expected '{net_cmd}' to be blocked")
            self.assertIn("invokes network utility", decision.reason)

    def test_disabled_mode_allows_offline_commands(self):
        """Mode DISABLED must not block safe local compilation/testing commands."""
        policy = NetworkAccessPolicy(mode=NetworkAccessMode.DISABLED)
        for safe_cmd in ["pytest tests/", "python main.py", "npm test", "cargo build"]:
            decision = policy.evaluate_command(safe_cmd)
            self.assertTrue(decision.allowed, f"Expected '{safe_cmd}' to be allowed")

    def test_allowlist_only_permits_matched_domains(self):
        """Mode ALLOWLIST_ONLY must allow domains matching the allowlist including wildcards."""
        policy = NetworkAccessPolicy(
            mode=NetworkAccessMode.ALLOWLIST_ONLY,
            allowed_domains=["*.pypi.org", "pypi.org", "*.github.com"],
        )
        self.assertTrue(policy.evaluate_host("pypi.org").allowed)
        self.assertTrue(policy.evaluate_host("files.pythonhosted.org").allowed is False)
        self.assertTrue(policy.evaluate_host("api.github.com").allowed)
        self.assertTrue(policy.evaluate_host("github.com").allowed)

        # Disallowed domain
        denied = policy.evaluate_host("evil.com")
        self.assertFalse(denied.allowed)
        self.assertIn("not in the authorized egress allowlist", denied.reason)
        self.assertIn("add it to task allowed_domains", denied.suggested_action)

    def test_ssrf_cloud_metadata_blocked_in_all_modes(self):
        """Cloud instance metadata (169.254.169.254, metadata.google.internal) must be blocked across all modes."""
        for mode in [NetworkAccessMode.DISABLED, NetworkAccessMode.ALLOWLIST_ONLY, NetworkAccessMode.UNRESTRICTED]:
            policy = NetworkAccessPolicy(mode=mode, allowed_domains=["*"])
            # IP metadata
            dec_ip = policy.evaluate_host("169.254.169.254")
            self.assertFalse(dec_ip.allowed)
            # Hostname metadata
            dec_host = policy.evaluate_host("metadata.google.internal")
            self.assertFalse(dec_host.allowed)
            # In URL
            dec_url = policy.evaluate_url("http://169.254.169.254/latest/meta-data/")
            self.assertFalse(dec_url.allowed)
            # In command
            dec_cmd = policy.evaluate_command("python -c \"import urllib.request; urllib.request.urlopen('http://169.254.169.254')\"")
            self.assertFalse(dec_cmd.allowed)

    def test_blocked_ports(self):
        """Sensitive ports (22, 23, 25, 445, 3389) must be blocked even in UNRESTRICTED mode."""
        policy = NetworkAccessPolicy(mode=NetworkAccessMode.UNRESTRICTED)
        dec_ssh = policy.evaluate_host("example.com", port=22)
        self.assertFalse(dec_ssh.allowed)
        self.assertIn("Port 22 is explicitly blocked", dec_ssh.reason)

        dec_http = policy.evaluate_host("example.com", port=443)
        self.assertTrue(dec_http.allowed)

    def test_proxy_env_generation(self):
        """get_disabled_proxy_env must configure dead proxies pointing to 127.0.0.1:0."""
        policy = NetworkAccessPolicy(mode=NetworkAccessMode.DISABLED)
        env = policy.get_disabled_proxy_env()
        self.assertEqual(env.get("HTTP_PROXY"), "http://127.0.0.1:0")
        self.assertEqual(env.get("HTTPS_PROXY"), "http://127.0.0.1:0")
        self.assertEqual(env.get("ALL_PROXY"), "socks5://127.0.0.1:0")

    def test_serialization(self):
        """Serialization to/from dict must preserve policy rules."""
        policy = NetworkAccessPolicy(
            mode=NetworkAccessMode.ALLOWLIST_ONLY,
            allowed_domains=["*.npmjs.org"],
            blocked_domains=["malicious.com"],
            blocked_ports=[8080],
        )
        data = policy.to_dict()
        restored = NetworkAccessPolicy.from_dict(data)
        self.assertEqual(restored.mode, NetworkAccessMode.ALLOWLIST_ONLY)
        self.assertEqual(restored.allowed_domains, ["*.npmjs.org"])
        self.assertEqual(restored.blocked_domains, ["malicious.com"])
        self.assertEqual(restored.blocked_ports, [8080])


class TestSandboxNetworkPolicyIntegration(unittest.TestCase):
    """Integration tests with LocalProcessSandbox."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sandbox_injects_dead_proxy_when_network_disabled(self):
        """LocalProcessSandbox must inject dead proxies into environment when network is disabled."""
        policy = SandboxPolicy(network_disabled=True)
        sandbox = LocalProcessSandbox(working_dir=self.temp_dir, policy=policy)
        env = sandbox.sanitize_environment()
        self.assertEqual(env.get("HTTP_PROXY"), "http://127.0.0.1:0")
        self.assertEqual(env.get("HTTPS_PROXY"), "http://127.0.0.1:0")
        self.assertEqual(env.get("ALL_PROXY"), "socks5://127.0.0.1:0")

    def test_sandbox_blocks_network_commands_when_network_disabled(self):
        """LocalProcessSandbox check_command_safety must block network utilities when network is disabled."""
        policy = SandboxPolicy(network_disabled=True)
        sandbox = LocalProcessSandbox(working_dir=self.temp_dir, policy=policy)
        violation = sandbox.check_command_safety("curl https://example.com")
        self.assertIsNotNone(violation)
        self.assertIn("network utility", violation)

    def test_sandbox_allows_safe_commands_when_network_disabled(self):
        """LocalProcessSandbox check_command_safety must allow normal safe commands."""
        policy = SandboxPolicy(network_disabled=True)
        sandbox = LocalProcessSandbox(working_dir=self.temp_dir, policy=policy)
        violation = sandbox.check_command_safety("python -m unittest discover")
        self.assertIsNone(violation)


class TestPermissionPolicyEngineNetworkIntegration(unittest.TestCase):
    """Integration tests with ToolPermissionPolicyEngine (RBAC + ABAC)."""

    def test_default_roles_blocked_from_network_tools(self):
        """REVIEWER, TESTER, and CODER roles must be blocked from NETWORK operations by default."""
        for role in ["REVIEWER", "TESTER", "CODER"]:
            res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
                agent_role=role,
                tool_name="http_request",
                args={"url": "https://pypi.org"},
            )
            self.assertFalse(res.allowed)
            self.assertEqual(res.operation_type, ToolOperationType.NETWORK)
            self.assertIn("Network access is disabled", res.reason)

    def test_task_permission_enables_network_with_allowlist(self):
        """When task permissions grant network_allowed=True with allowed_domains, matching URLs pass."""
        task_perms = TaskPermissions(
            network_allowed=True,
            allowed_domains=["*.pypi.org", "pypi.org"],
        )
        # Allowed target
        res_allowed = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="CODER",
            tool_name="http_request",
            args={"url": "https://pypi.org/simple"},
            task_permissions=task_perms,
        )
        self.assertTrue(res_allowed.allowed)

        # Denied target outside allowlist
        res_denied = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="CODER",
            tool_name="http_request",
            args={"url": "https://attacker.com/exfil"},
            task_permissions=task_perms,
        )
        self.assertFalse(res_denied.allowed)
        self.assertIn("not in the authorized egress allowlist", res_denied.reason)

    def test_execute_blocked_on_network_command_when_task_disallows_network(self):
        """When task specifies network_allowed=False, EXECUTE commands with curl/wget must be blocked."""
        task_perms = TaskPermissions(network_allowed=False)
        res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="CODER",
            tool_name="terminal_execute",
            args={"command": "curl -O https://example.com/script.sh"},
            task_permissions=task_perms,
        )
        self.assertFalse(res.allowed)
        self.assertIn("blocked by network policy", res.reason)


class TestReActLoopNetworkPolicyEnforcement(unittest.TestCase):
    """Integration tests with ReActAgentLoop."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=self.temp_dir)
        self.tool_registry = BuiltinToolRegistry(workspace=self.workspace)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_react_loop_blocks_network_tool_when_network_disallowed(self):
        """ReActAgentLoop must reject network tools when task permissions disallow network."""
        mock_llm = MagicMock()
        turn_count = 0

        def mock_chat_with_tools(messages, **kwargs):
            nonlocal turn_count
            turn_count += 1
            if turn_count == 1:
                return {
                    "content": "Fetching remote dependency",
                    "tool_calls": [{
                        "id": "tc1",
                        "name": "http_request",
                        "arguments": {"url": "https://example.com/api"},
                    }],
                }
            else:
                last_msg = str(messages[-1])
                # Verify error feedback was delivered to model
                assert "Permission Denied" in last_msg
                assert "Network access is disabled" in last_msg
                return {
                    "content": "Done without network",
                    "tool_calls": [{
                        "id": "tc2",
                        "name": "complete_task",
                        "arguments": {"summary": "Done offline"},
                    }],
                }

        mock_llm.chat_with_tools.side_effect = mock_chat_with_tools

        react_loop = ReActAgentLoop(llm=mock_llm, tool_registry=self.tool_registry, max_turns=3)
        task_perms = TaskPermissions(network_allowed=False)
        result = react_loop.run(
            system_prompt="You are Coder.",
            user_prompt="Fetch external API.",
            agent_name="CODER",
            permissions=task_perms,
        )

        self.assertEqual(result["turns_taken"], 2)


if __name__ == "__main__":
    unittest.main()
