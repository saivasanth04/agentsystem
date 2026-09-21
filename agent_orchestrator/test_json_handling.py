"""
Tests for Safe JSON Handling & Structured Output Pipeline (Issue #56).
Covers:
1. Algorithmic dirty-JSON repair (trailing commas, quotes, Python literals, truncated JSON, control chars)
2. Pydantic validation & schema-constrained structured output
3. Self-correction feedback retry loop
4. Explicit failure state (no silent masquerading as success)
5. LLMClient.chat_json and chat_with_tools integration
6. Diagnostics fallback trigger on parse error
"""

import json
import unittest
from unittest.mock import MagicMock, patch
from pydantic import BaseModel, Field
from typing import List, Optional

from agent_orchestrator.runtime.json_repair import extract_json_candidate, repair_json, loads_repaired
from agent_orchestrator.runtime.structured_output import (
    StructuredOutputEngine,
    StructuredOutputResult,
    StructuredOutputError,
)
from agent_orchestrator.llm import LLMClient
from agent_orchestrator.runtime.diagnostics import FailureDiagnostician


class DummyTaskModel(BaseModel):
    task_id: str
    title: str
    priority: int = Field(default=1)
    tags: List[str] = Field(default_factory=list)


class TestJSONRepair(unittest.TestCase):
    """Test pure-Python algorithmic dirty-JSON repair."""

    def test_trailing_commas(self):
        dirty = '{"name": "agent", "items": [1, 2, 3,], "nested": {"key": "val",},}'
        parsed = loads_repaired(dirty)
        self.assertEqual(parsed["name"], "agent")
        self.assertEqual(parsed["items"], [1, 2, 3])
        self.assertEqual(parsed["nested"]["key"], "val")

    def test_single_quotes(self):
        dirty = "{'agent_name': 'orchestrator', 'action': 'run_tests'}"
        parsed = loads_repaired(dirty)
        self.assertEqual(parsed["agent_name"], "orchestrator")
        self.assertEqual(parsed["action"], "run_tests")

    def test_python_literals(self):
        dirty = '{"is_active": True, "is_deleted": False, "metadata": None}'
        parsed = loads_repaired(dirty)
        self.assertTrue(parsed["is_active"])
        self.assertFalse(parsed["is_deleted"])
        self.assertIsNone(parsed["metadata"])

    def test_unescaped_newlines_in_strings(self):
        dirty = '{"code": "def hello():\n    print(\'hi\')\n    return True"}'
        parsed = loads_repaired(dirty)
        self.assertIn("def hello():", parsed["code"])

    def test_truncated_json_closure(self):
        dirty = '{"tasks": [{"task_id": "T-1", "title": "Build API"'
        parsed = loads_repaired(dirty)
        self.assertIn("tasks", parsed)
        self.assertEqual(len(parsed["tasks"]), 1)
        self.assertEqual(parsed["tasks"][0]["task_id"], "T-1")

    def test_markdown_fence_and_preamble(self):
        dirty = (
            "Here is the requested output:\n\n"
            "```json\n"
            "{\n"
            '  "status": "success",\n'
            '  "count": 42\n'
            "}\n"
            "```\n"
            "Hope this helps!"
        )
        parsed = loads_repaired(dirty)
        self.assertEqual(parsed["status"], "success")
        self.assertEqual(parsed["count"], 42)

    def test_c_style_comments(self):
        dirty = """
        {
            // Set the execution mode
            "mode": "autonomous", /* inline comment */
            "timeout": 30
        }
        """
        parsed = loads_repaired(dirty)
        self.assertEqual(parsed["mode"], "autonomous")
        self.assertEqual(parsed["timeout"], 30)


class TestStructuredOutputEngine(unittest.TestCase):
    """Test StructuredOutputEngine pipeline stages."""

    def test_direct_valid_pydantic(self):
        def caller(messages, kwargs):
            return json.dumps({"task_id": "T-10", "title": "Unit Tests", "priority": 2, "tags": ["qa"]})

        result = StructuredOutputEngine.execute(
            llm_caller=caller,
            messages=[{"role": "user", "content": "generate task"}],
            schema=DummyTaskModel,
        )
        self.assertTrue(result.success)
        self.assertIsInstance(result.data, DummyTaskModel)
        self.assertEqual(result.data.task_id, "T-10")
        self.assertEqual(result.attempts, 1)

    def test_self_correction_retry_on_invalid_schema(self):
        call_count = 0

        def caller(messages, kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First attempt: missing required field 'title'
                return json.dumps({"task_id": "T-20"})
            # Second attempt: corrected response following feedback
            return json.dumps({"task_id": "T-20", "title": "Corrected Task", "priority": 1})

        result = StructuredOutputEngine.execute(
            llm_caller=caller,
            messages=[{"role": "user", "content": "generate task"}],
            schema=DummyTaskModel,
            max_retries=2,
        )
        self.assertTrue(result.success)
        self.assertEqual(call_count, 2)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.data.title, "Corrected Task")

    def test_failure_state_when_retries_exhausted(self):
        def caller(messages, kwargs):
            return "Completely invalid text without any JSON at all"

        result = StructuredOutputEngine.execute(
            llm_caller=caller,
            messages=[{"role": "user", "content": "generate"}],
            schema=DummyTaskModel,
            max_retries=1,
            raise_on_failure=False,
        )
        self.assertFalse(result.success)
        self.assertIsNone(result.data)
        self.assertEqual(result.attempts, 2)
        self.assertTrue(len(result.validation_errors) > 0)

        # Calling unwrap() should raise StructuredOutputError
        with self.assertRaises(StructuredOutputError):
            result.unwrap()


class TestLLMClientIntegration(unittest.TestCase):
    """Test LLMClient integration with structured output pipeline."""

    def test_chat_json_untyped_repair(self):
        client = LLMClient()
        dirty_response = "{'status': 'ok', 'count': 5,}"
        with patch.object(client, "chat", return_value=dirty_response):
            res = client.chat_json([{"role": "user", "content": "hi"}])
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["count"], 5)

    def test_chat_json_with_pydantic_schema(self):
        client = LLMClient()
        valid_response = json.dumps({"task_id": "T-99", "title": "Deploy Service", "priority": 3})
        with patch.object(client, "chat", return_value=valid_response):
            res = client.chat_json([{"role": "user", "content": "hi"}], schema=DummyTaskModel)
            self.assertIsInstance(res, DummyTaskModel)
            self.assertEqual(res.task_id, "T-99")
            self.assertEqual(res.priority, 3)

    def test_chat_with_tools_repairs_malformed_arguments(self):
        client = LLMClient()
        mock_choice = MagicMock()
        mock_msg = MagicMock()
        mock_tc = MagicMock()
        mock_tc.id = "call_abc"
        mock_tc.type = "function"
        mock_tc.function.name = "write_file"
        # Tool argument has trailing comma and single quotes
        mock_tc.function.arguments = "{'filepath': 'src/app.py', 'content': 'print(1)',}"
        mock_msg.content = "Calling tool"
        mock_msg.tool_calls = [mock_tc]
        mock_choice.message = mock_msg
        mock_choice.finish_reason = "tool_calls"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch.object(client.resilient_caller, "execute", return_value=mock_response):
            result = client.chat_with_tools([{"role": "user", "content": "test"}])
            self.assertEqual(len(result["tool_calls"]), 1)
            args = result["tool_calls"][0]["arguments"]
            self.assertEqual(args.get("filepath"), "src/app.py")
            self.assertEqual(args.get("content"), "print(1)")


class TestDiagnosticsFallbackTrigger(unittest.TestCase):
    """Verify FailureDiagnostician properly detects JSON parse failure and triggers heuristic fallback."""

    def test_diagnostics_triggers_fallback_on_parse_error(self):
        mock_llm = MagicMock()
        # LLM returns a parse error dictionary
        mock_llm.chat_json.return_value = {
            "raw_output": "Bad response",
            "parse_error": "Could not parse JSON directly",
            "_parse_error": True,
        }
        diagnostician = FailureDiagnostician(llm=mock_llm)
        report = diagnostician.diagnose_failure(
            task_title="Build Feature",
            execution_stderr="SyntaxError: invalid syntax in test.py",
            execution_stdout="",
        )
        # Should NOT be empty or poisoned with parse_error
        self.assertEqual(report.failure_type, "SYNTAX_ERROR")
        self.assertIn("Execution failure", report.root_cause_summary)


if __name__ == "__main__":
    unittest.main()
