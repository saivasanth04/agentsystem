"""
Unit and Integration Tests for Issue #23: Existing-Test Awareness in Multi-Agent Coding Orchestrator.
Verifies:
- Discovery of existing test frameworks (pytest, unittest, jest, etc.)
- Extraction of fixtures from conftest.py (scope, docstrings, names)
- Extraction of authoritative CI commands from .github/workflows/*.yml
- Detection of mocking patterns (unittest.mock, pytest-mock, etc.)
- Detection of test conventions (function vs class, assertion syntax)
- Sibling test sampling and few-shot formatting
- Pre-flight baseline test execution
- inspect_existing_tests tool in BuiltinToolRegistry
- TesterAgent prompt and test summary enrichment
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.runtime.test_detector import ExistingTestDetector, RepoTestContext
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.agents.tester import TesterAgent
from agent_orchestrator.state import OrchestratorState


class TestExistingTestAwareness(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_existing_test_awareness_")
        self.workspace_path = Path(self.test_dir)
        self.workspace = WorkspaceManager(self.workspace_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_pytest_fixture_and_conftest_discovery(self):
        """Verify conftest.py parsing extracts fixtures with scope and docstrings."""
        conftest_content = '''
import pytest

@pytest.fixture(scope="session")
def db_connection():
    """Provides a shared database connection session."""
    return {"status": "connected", "db": "test_db"}

@pytest.fixture
def auth_token(db_connection):
    """Generates a valid JWT auth token."""
    return "bearer-token-12345"
'''
        self.workspace.write_file("conftest.py", conftest_content)
        self.workspace.write_file("tests/test_auth.py", "def test_login(auth_token):\n    assert auth_token == 'bearer-token-12345'\n")

        context: RepoTestContext = ExistingTestDetector.scan(self.workspace)

        self.assertTrue(context.has_existing_tests)
        self.assertEqual(context.test_framework, "pytest")
        self.assertIn("tests", context.test_directories)
        self.assertIn("tests/test_auth.py", context.existing_test_files)

        # Check fixtures
        fixture_names = [f["name"] for f in context.fixtures]
        self.assertIn("db_connection", fixture_names)
        self.assertIn("auth_token", fixture_names)

        db_fix = next(f for f in context.fixtures if f["name"] == "db_connection")
        self.assertEqual(db_fix["scope"], "session")
        self.assertIn("shared database connection", db_fix["docstring"])

    def test_github_ci_workflow_command_extraction(self):
        """Verify .github/workflows/*.yml parsing extracts authoritative test command."""
        ci_workflow_content = '''
name: CI Pipeline
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Run Pytest with Coverage
        run: pytest --cov=src --cov-report=term-missing -v
'''
        self.workspace.write_file(".github/workflows/test.yml", ci_workflow_content)
        self.workspace.write_file("tests/test_sample.py", "def test_sample():\n    assert 1 + 1 == 2\n")

        context = ExistingTestDetector.scan(self.workspace)

        self.assertTrue(context.has_existing_tests)
        self.assertEqual(len(context.ci_test_commands), 1)
        self.assertEqual(context.ci_test_commands[0]["command"], "pytest --cov=src --cov-report=term-missing -v")
        self.assertEqual(context.authoritative_ci_command, "pytest --cov=src --cov-report=term-missing -v")

    def test_mock_pattern_detection(self):
        """Verify detection of unittest.mock, pytest-mock, and responses."""
        test_content_1 = '''
from unittest.mock import patch, MagicMock

def test_service_mock():
    with patch("services.api.fetch") as mock_fetch:
        mock_fetch.return_value = {"ok": True}
        assert mock_fetch()["ok"] is True
'''
        test_content_2 = '''
def test_user_mocker(mocker):
    spy = mocker.spy(user_service, "get_user")
    mocker.patch("services.db.query", return_value=[])
'''
        self.workspace.write_file("tests/test_mock1.py", test_content_1)
        self.workspace.write_file("tests/test_mock2.py", test_content_2)

        context = ExistingTestDetector.scan(self.workspace)
        mock_libs = [m["library"] for m in context.mocks]
        self.assertIn("unittest.mock", mock_libs)
        self.assertIn("pytest-mock", mock_libs)

    def test_conventions_and_sibling_sampling(self):
        """Verify conventions extraction and sibling test file sampling."""
        sibling_code = '''
def test_math_addition():
    """Test addition functionality."""
    result = 10 + 5
    assert result == 15

def test_math_subtraction():
    result = 10 - 5
    assert result == 5
'''
        self.workspace.write_file("tests/test_math.py", sibling_code)

        context = ExistingTestDetector.scan(self.workspace)
        conventions = context.conventions

        self.assertIn("function_based", conventions.get("test_style", ""))
        self.assertIn("assert statement", conventions.get("assertion_style", ""))
        self.assertEqual(conventions.get("naming_convention"), "test_*.py")

        # Sibling sample
        self.assertEqual(len(context.sample_tests), 1)
        self.assertEqual(context.sample_tests[0]["filepath"], "tests/test_math.py")
        self.assertIn("test_math_addition", context.sample_tests[0]["content"])

        # Prompt context markdown
        prompt_ctx = context.to_prompt_context()
        self.assertIn("Existing Repository Test Architecture & Conventions", prompt_ctx)
        self.assertIn("**Detected Test Framework**: pytest", prompt_ctx)
        self.assertIn("Sibling Test Example", prompt_ctx)
        self.assertIn("test_math_addition", prompt_ctx)


    def test_baseline_test_execution(self):
        """Verify baseline test execution runs existing tests and captures pass/fail status."""
        # 1. Passing test
        self.workspace.write_file(
            "test_pass.py",
            "import unittest\nclass PassTest(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"
        )
        res = ExistingTestDetector.run_baseline(
            self.workspace,
            command='python -m unittest discover -s . -p "test_pass.py"'
        )
        self.assertTrue(res["passed"])
        self.assertEqual(res["exit_code"], 0)
        self.assertFalse(res["skipped"])

        # 2. Empty workspace -> skipped
        empty_dir = tempfile.mkdtemp(prefix="empty_ws_")
        try:
            res_empty = ExistingTestDetector.run_baseline(empty_dir)
            self.assertTrue(res_empty["skipped"])
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

    def test_inspect_existing_tests_tool(self):
        """Verify BuiltinToolRegistry exposes inspect_existing_tests tool."""
        self.workspace.write_file("conftest.py", "@pytest.fixture\ndef app(): pass\n")
        self.workspace.write_file("tests/test_demo.py", "def test_demo(app): assert app is None\n")

        registry = BuiltinToolRegistry(workspace=self.workspace)
        self.assertTrue(hasattr(registry, "inspect_tests_tool"))

        # Verify tool is available to TESTER, CODER, PLANNER, REVIEWER
        tester_tools = [t.name for t in registry.get_tools_for_agent("TESTER")]
        coder_tools = [t.name for t in registry.get_tools_for_agent("CODER")]
        planner_tools = [t.name for t in registry.get_tools_for_agent("PLANNER")]
        reviewer_tools = [t.name for t in registry.get_tools_for_agent("REVIEWER")]

        self.assertIn("inspect_existing_tests", tester_tools)
        self.assertIn("inspect_existing_tests", coder_tools)
        self.assertIn("inspect_existing_tests", planner_tools)
        self.assertIn("inspect_existing_tests", reviewer_tools)

        # Call the tool
        tool_res = registry.call_tool("inspect_existing_tests", {})
        self.assertTrue(tool_res.get("success"))
        self.assertEqual(tool_res.get("test_framework"), "pytest")
        self.assertIn("tests", tool_res.get("test_directories", []))
        self.assertIn("app", [f["name"] for f in tool_res.get("fixtures", [])])

    def test_tester_agent_prompt_and_summary_enrichment(self):
        """Verify TesterAgent inspects repository and enriches prompt & summary with existing test context."""
        # Create existing tests and fixtures
        self.workspace.write_file("conftest.py", "@pytest.fixture\ndef sample_fixture(): return 42\n")
        self.workspace.write_file("tests/test_existing.py", "def test_existing(sample_fixture): assert sample_fixture == 42\n")

        # Mock LLM and ReactLoop
        mock_llm = MagicMock()
        mock_llm.generate.return_value = json.dumps({
            "thought": "Using existing pytest fixtures to write unit tests",
            "tool": "complete_task",
            "args": {"summary": "Generated tests matching existing conventions"},
        })

        registry = BuiltinToolRegistry(workspace=self.workspace)
        tester = TesterAgent(llm=mock_llm, workspace=self.workspace, tool_registry=registry)

        captured_user_prompt = []
        original_run = tester.react_loop.run

        def mock_react_run(*args, **kwargs):
            user_prompt = kwargs.get("user_prompt", "")
            captured_user_prompt.append(user_prompt)
            return {
                "final_output": {"summary": "Completed testing with existing fixtures"},
                "turns_taken": 1,
                "history_events": [],
            }

        tester.react_loop.run = mock_react_run

        state = OrchestratorState(user_request="Add subtract function and test it")
        summary = tester.execute(state, task_info={"task_id": "T-01"})

        self.assertTrue(len(captured_user_prompt) > 0)
        prompt_text = captured_user_prompt[0]

        # Verify prompt contains existing test architecture
        self.assertIn("Existing Repository Test Architecture & Conventions", prompt_text)
        self.assertIn("pytest", prompt_text)
        self.assertIn("sample_fixture", prompt_text)
        self.assertIn("REPOSITORY TEST CONVENTIONS & FIXTURES", prompt_text)

        # Verify test summary contains existing test context
        self.assertIn("existing_test_context", summary)
        self.assertTrue(summary["existing_test_context"]["has_existing_tests"])
        self.assertEqual(summary["existing_test_context"]["test_framework"], "pytest")


if __name__ == "__main__":
    unittest.main()
