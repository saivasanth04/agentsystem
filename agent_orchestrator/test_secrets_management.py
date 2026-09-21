"""
Unit tests for Secret Management and Redaction (Issue #33).
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_orchestrator.config import OrchestratorConfig, DEFAULT_KEY, BASE_DIR
from agent_orchestrator.security.secrets import (
    SecretManager,
    EnvCredentialProvider,
    FileCredentialProvider,
    secret_manager,
)
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.runtime.react_loop import ReActAgentLoop


class TestSecretsManagement(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.workspace_path = Path(self.test_dir.name)
        self.workspace = WorkspaceManager(self.workspace_path)
        self.tool_registry = BuiltinToolRegistry(self.workspace)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_env_credential_provider(self):
        """Test retrieving credentials from environment variables."""
        provider = EnvCredentialProvider()
        with patch.dict(os.environ, {"UNIFIED_API_KEY": "env-test-key-12345"}):
            self.assertEqual(provider.get("api_key"), "env-test-key-12345")
            self.assertEqual(provider.get("UNIFIED_API_KEY"), "env-test-key-12345")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "openai-secret-key-67890"}, clear=False):
            if "UNIFIED_API_KEY" in os.environ:
                del os.environ["UNIFIED_API_KEY"]
            self.assertEqual(provider.get("api_key"), "openai-secret-key-67890")

    def test_file_credential_provider(self):
        """Test retrieving credentials from local files."""
        secret_file = os.path.join(self.workspace_path, "secrets.env")
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write("API_KEY=file-test-secret-abcdef\nOTHER_TOKEN=token123\n")

        provider = FileCredentialProvider(filepath=secret_file)
        self.assertEqual(provider.get("api_key"), "file-test-secret-abcdef")
        self.assertEqual(provider.get("OTHER_TOKEN"), "token123")
        self.assertIsNone(provider.get("NONEXISTENT"))

    def test_secret_manager_registration_and_redaction(self):
        """Test dynamic secret registration and text redaction."""
        sm = SecretManager()
        raw_secret = "super-secret-custom-token-xyz"
        sm.register_secret(raw_secret)

        text = f"Connecting using token: {raw_secret} on port 8080"
        redacted = sm.redact_text(text)
        self.assertNotIn(raw_secret, redacted)
        self.assertIn("[REDACTED_SECRET]", redacted)

    def test_secret_manager_pattern_redaction(self):
        """Test redaction of common secret patterns (API keys, tokens)."""
        sm = SecretManager()
        
        # Test sk- pattern (OpenAI / Unified key format)
        sk_key = "sk-unified-5f576366ef89baed5a7b76b9a8094edb1fc60efe"
        text = f"Error: Request failed with key {sk_key}"
        redacted = sm.redact_text(text)
        self.assertNotIn(sk_key, redacted)
        self.assertIn("[REDACTED_SECRET]", redacted)

        # Test Bearer token pattern
        bearer_text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"
        redacted_bearer = sm.redact_text(bearer_text)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz", redacted_bearer)
        self.assertIn("[REDACTED_SECRET]", redacted_bearer)

    def test_secret_manager_data_structure_redaction(self):
        """Test recursive redaction across dictionaries, lists, and tuples."""
        sm = SecretManager()
        secret_val = "sk-ant-api03-abcdef1234567890abcdef1234567890"
        
        nested_data = {
            "status": "success",
            "auth": {
                "key": secret_val,
                "headers": [f"Bearer {secret_val}", "Content-Type: application/json"],
            },
            "tuple_data": (secret_val, 123),
        }

        redacted_data = sm.redact_structure(nested_data)
        self.assertNotIn(secret_val, str(redacted_data))
        self.assertEqual(redacted_data["auth"]["key"], "[REDACTED_SECRET]")
        self.assertIn("[REDACTED_SECRET]", redacted_data["auth"]["headers"][0])
        self.assertEqual(redacted_data["tuple_data"][0], "[REDACTED_SECRET]")

    def test_tool_read_file_redaction(self):
        """Test that read_file scrubs secrets before returning content."""
        secret_content = "SECRET_KEY=sk-unified-abcdef1234567890abcdef12345\nDATABASE_URL=postgres://localhost"
        self.workspace.write_file("config.env", secret_content)

        result = self.tool_registry.call_tool("read_file", {"filepath": "config.env"})

        self.assertTrue(result.get("success", False))
        self.assertNotIn("sk-unified-abcdef1234567890abcdef12345", result.get("content", ""))
        self.assertIn("[REDACTED_SECRET]", result.get("content", ""))

    def test_tool_grep_search_redaction(self):
        """Test that regex_grep scrubs secrets in matched lines."""
        secret_content = "API_KEY=sk-unified-abcdef1234567890abcdef12345\nDEBUG=True"
        self.workspace.write_file("app.py", secret_content)

        result = self.tool_registry.call_tool("regex_grep", {"pattern": "API_KEY"})

        self.assertTrue(result.get("success", False))
        self.assertNotIn("sk-unified-abcdef1234567890abcdef12345", str(result.get("matches", [])))
        self.assertIn("[REDACTED_SECRET]", str(result.get("matches", [])))

    def test_config_no_hardcoded_credentials(self):
        """Ensure config file has zero hardcoded credentials and resolves safely."""
        config_file_path = BASE_DIR / "config.py"
        content = config_file_path.read_text(encoding="utf-8")
        
        # Verify no hardcoded sk-unified credentials exist in config.py source
        self.assertNotIn("sk-unified-5f576366ef89baed5a7b76b9a8094edb1fc60efe", content)
        self.assertNotIn("DEFAULT_KEY = \"sk-", content)

        # Test isolated SecretManager resolution with default
        isolated_sm = SecretManager(providers=[])
        resolved_key = isolated_sm.get_secret("api_key", default="mock-key-for-testing")
        self.assertEqual(resolved_key, "mock-key-for-testing")

    def test_react_loop_observation_redaction(self):
        """Ensure ReActAgentLoop sanitizes tool results before emitting observations and messages."""
        secret_token = "sk-test-token-12345678901234567890"

        # Register a tool that returns a secret
        def leak_secret_fn(args=None):
            return {"success": True, "token": secret_token, "msg": f"Key is {secret_token}"}

        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel

        class EmptyInput(BaseModel):
            pass

        leaky_tool = StructuredTool.from_function(
            func=leak_secret_fn,
            name="leak_secret",
            description="Leaky tool for testing",
            args_schema=EmptyInput,
        )
        self.tool_registry._tools["leak_secret"] = leaky_tool

        mock_llm = MagicMock()
        mock_llm.chat_with_tools.side_effect = [
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "leak_secret",
                            "arguments": "{}",
                        },
                    }
                ],
                "content": "Calling leaky tool",
            },
            {
                "tool_calls": [
                    {
                        "id": "call_2",
                        "function": {
                            "name": "complete_task",
                            "arguments": '{"summary": "Done"}',
                        },
                    }
                ],
                "content": "Task finished",
            },
        ]

        step_events = []
        def step_cb(stage, data):
            step_events.append((stage, data))

        loop = ReActAgentLoop(
            llm=mock_llm,
            tool_registry=self.tool_registry,
            max_turns=3,
            on_step_callback=step_cb,
        )

        result = loop.run(
            system_prompt="You are a test agent",
            user_prompt="Test secrets in loop",
            workspace=self.workspace,
        )
        self.assertEqual(len(result["errors"]), 0)
        self.assertTrue(bool(result.get("final_output")))

        # Check step events (OBSERVATION)
        obs_events = [data for stage, data in step_events if stage == "OBSERVATION"]
        self.assertGreater(len(obs_events), 0)
        for obs in obs_events:
            self.assertNotIn(secret_token, str(obs))

        # Check observations in result
        for obs in result.get("observations", []):
            self.assertNotIn(secret_token, str(obs.output_result))


if __name__ == "__main__":
    unittest.main()
