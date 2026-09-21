"""
Integration Tests for Unified Tool Dispatcher, Multi-Agent ReAct loop, and MCP Layer.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agent_orchestrator.agents.coder import CoderAgent
from agent_orchestrator.agents.tester import TesterAgent
from agent_orchestrator.llm import LLMClient
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.state import OrchestratorState
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestMCPIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_dir = Path(self.temp_dir)
        self.workspace = WorkspaceManager(self.workspace_dir)
        self.builtin_registry = BuiltinToolRegistry(self.workspace)
        self.mcp_manager = MCPManager(workspace_dir=self.workspace_dir)
        self.dispatcher = UnifiedToolDispatcher(self.builtin_registry, self.mcp_manager)

    def tearDown(self):
        self.dispatcher.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_unified_dispatcher_schema_aggregation(self):
        coder_schemas = self.dispatcher.get_schemas(agent_name="CODER")
        schema_names = [s["function"]["name"] for s in coder_schemas]
        
        # Should include both native builtin tools and MCP discovered tools
        self.assertIn("read_file", schema_names)
        self.assertIn("write_file", schema_names)
        self.assertIn("ast_syntax_check", schema_names)
        self.assertIn("store_memory", schema_names)
        self.assertIn("query_symbols", schema_names)

    def test_unified_dispatcher_routing(self):
        # 1. Route to MCP memory tool
        mem_res = self.dispatcher.call_tool("store_memory", {"key": "pattern", "value": "Factory Pattern"})
        self.assertTrue(mem_res.get("success"))

        # 2. Route to native syntax checker
        (self.workspace_dir / "valid.py").write_text("def test(): pass\n")
        syntax_res = self.dispatcher.call_tool("ast_syntax_check", {"filepath": "valid.py"})
        self.assertTrue(syntax_res.get("output", {}).get("valid", False) or syntax_res.get("valid", False))

    def test_coder_agent_with_mcp_tools(self):
        # Mock LLM to simulate tool calling ReAct turn
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.chat_json.side_effect = [
            # Turn 1: Call write_file tool
            {
                "thought": "I will write the math module",
                "tool_call": {
                    "name": "write_file",
                    "arguments": {"path": "math_utils.py", "content": "def add(a, b): return a + b\n"},
                },
            },
            # Turn 2: Finish
            {
                "thought": "Finished writing code",
                "final_output": {
                    "summary": "Implemented math_utils.py",
                    "files": [{"filepath": "math_utils.py", "content": "def add(a, b): return a + b\n"}],
                },
            },
        ]

        coder = CoderAgent(
            llm=mock_llm,
            workspace=self.workspace,
            tool_registry=self.dispatcher,
        )

        state = OrchestratorState(user_request="Implement math utils")
        result = coder.execute(state)

        self.assertIn("summary", result)
        self.assertTrue((self.workspace_dir / "math_utils.py").exists())
        self.assertIn("def add(a, b): return a + b", (self.workspace_dir / "math_utils.py").read_text())


if __name__ == "__main__":
    unittest.main()
