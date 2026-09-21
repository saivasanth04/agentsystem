"""
Comprehensive test suite for Execution Sandboxing & Security Hardening (Issue #15).
Validates environment scrubbing, secret redaction, dangerous command blocking,
timeout handling, and integration with CodeExecutor, TerminalMCPServer, and VerificationEngine.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.security.sandbox import (
    BaseExecutionSandbox,
    DockerContainerSandbox,
    LocalProcessSandbox,
    SandboxPolicy,
    SandboxResult,
    create_sandbox,
)
from agent_orchestrator.tools.executor import CodeExecutor
from agent_orchestrator.mcp.servers.terminal_server import TerminalMCPServer
from agent_orchestrator.runtime.verification import TaskVerificationGate
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestLocalProcessSandbox(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_dir = Path(self.temp_dir)
        self.sandbox = LocalProcessSandbox(self.workspace_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_environment_sanitization(self):
        """Verify host API keys and tokens are stripped from child processes."""
        # Inject secrets into host environment
        os.environ["OPENAI_API_KEY"] = "sk-live-1234567890abcdef1234567890abcdef"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-testsecret1234567890abcdef"
        os.environ["CUSTOM_SECRET_TOKEN"] = "super-secret-auth-value"
        os.environ["DATABASE_PASSWORD"] = "mypassword123"

        code = (
            "import os, sys\n"
            "keys = ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'CUSTOM_SECRET_TOKEN', 'DATABASE_PASSWORD']\n"
            "leaked = [k for k in keys if k in os.environ]\n"
            "print('LEAKED:' + ','.join(leaked))\n"
        )
        res = self.sandbox.run_command([sys.executable, "-c", code])
        self.assertTrue(res.success)
        self.assertIn("LEAKED:", res.stdout)
        self.assertEqual("LEAKED:\n", res.stdout)

    def test_secret_redaction_in_output(self):
        """Verify API keys and bearer tokens are redacted from stdout and stderr."""
        code = (
            "import sys\n"
            "print('API response: sk-1234567890abcdef1234567890abcdef')\n"
            "print('GitHub: ghp_1234567890abcdef1234567890abcdef1234')\n"
            "sys.stderr.write('Failed with bearer Bearer 1234567890abcdef1234567890abcdef\\n')\n"
        )
        res = self.sandbox.run_command([sys.executable, "-c", code])
        self.assertTrue(res.success)
        self.assertNotIn("sk-1234567890abcdef1234567890abcdef", res.stdout)
        self.assertNotIn("ghp_1234567890abcdef1234567890abcdef1234", res.stdout)
        self.assertIn("[REDACTED_SECRET]", res.stdout)
        self.assertIn("[REDACTED_SECRET]", res.stderr)
        self.assertGreaterEqual(res.redacted_secrets_count, 2)

    def test_blocked_dangerous_commands(self):
        """Verify destructive commands are blocked before execution."""
        dangerous_cmds = [
            "rm -rf /",
            "rm -rf /etc",
            "rmdir /s /q c:\\",
            "format c:",
            "mkfs.ext4 /dev/sda1",
            ":(){ :|:& };:",
        ]
        for cmd in dangerous_cmds:
            res = self.sandbox.run_command(cmd)
            self.assertFalse(res.success)
            self.assertEqual(res.exit_code, 126)
            self.assertIn("Security Violation", res.stderr)

    def test_timeout_and_process_termination(self):
        """Verify long-running commands terminate cleanly on timeout."""
        code = "import time\ntime.sleep(10)\n"
        res = self.sandbox.run_command([sys.executable, "-c", code], timeout=1)
        self.assertFalse(res.success)
        self.assertTrue(res.timed_out)
        self.assertEqual(res.exit_code, -1)
        self.assertIn("timed out after 1 seconds", res.stderr)

    def test_output_truncation_limits(self):
        """Verify output exceeding maximum bytes is truncated."""
        policy = SandboxPolicy(max_output_bytes=200)
        custom_sb = LocalProcessSandbox(self.workspace_dir, policy=policy)
        code = "print('X' * 1000)"
        res = custom_sb.run_command([sys.executable, "-c", code])
        self.assertTrue(res.success)
        self.assertIn("[TRUNCATED: Max output bytes exceeded]", res.stdout)
        self.assertLessEqual(len(res.stdout), 300)


class TestSandboxIntegrations(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_dir = Path(self.temp_dir)
        self.workspace = WorkspaceManager(self.workspace_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_code_executor_with_sandbox(self):
        """Verify CodeExecutor delegates to sandbox and retains standard interface."""
        executor = CodeExecutor(self.workspace_dir)
        res = executor.run_command([sys.executable, "-c", "print('hello from code executor')"])
        self.assertTrue(res["success"])
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("hello from code executor", res["stdout"])

    def test_terminal_mcp_server_with_sandbox(self):
        """Verify TerminalMCPServer executes through sandbox and redacts secrets."""
        server = TerminalMCPServer(self.workspace_dir)
        cmd = f'"{sys.executable}" -c "print(\'Result: sk-1234567890abcdef1234567890abcdef\')"'
        res = server.terminal_execute(cmd)
        self.assertTrue(res["success"])
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("[REDACTED_SECRET]", res["stdout"])
        self.assertNotIn("sk-1234567890abcdef1234567890abcdef", res["stdout"])

    def test_verification_engine_with_sandbox(self):
        """Verify TaskVerificationGate fallback uses sandbox."""
        gate = TaskVerificationGate(self.workspace)
        cmd = f'"{sys.executable}" -c "print(\'verification passed\')"'
        res = gate._execute_command(cmd)
        self.assertTrue(res["success"])
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("verification passed", res["stdout"])


if __name__ == "__main__":
    unittest.main()
