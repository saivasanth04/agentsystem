"""
Comprehensive Unit Tests for MCP Core Subsystem.
Validates protocol serialization, in-memory & stdio transports, client session, server implementations, and MCPManager.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.mcp.client import MCPClientSession
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.mcp.protocol import (
    JSONRPCError,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    MCPErrorCode,
    ToolCallResult,
    ToolDefinition,
)
from agent_orchestrator.mcp.servers import (
    BaseMCPServer,
    CodebaseMemoryMCPServer,
    FilesystemMCPServer,
    GitMCPServer,
    TerminalMCPServer,
)
from agent_orchestrator.mcp.transport import InMemoryTransport


class TestMCPProtocol(unittest.TestCase):
    def test_jsonrpc_request_serialization(self):
        req = JSONRPCRequest(id=1, method="tools/list", params={"cursor": None})
        d = req.to_dict()
        self.assertEqual(d["jsonrpc"], "2.0")
        self.assertEqual(d["id"], 1)
        self.assertEqual(d["method"], "tools/list")
        self.assertEqual(d["params"], {"cursor": None})

    def test_jsonrpc_response_serialization(self):
        resp = JSONRPCResponse(id=1, result={"tools": []})
        d = resp.to_dict()
        self.assertEqual(d["id"], 1)
        self.assertEqual(d["result"], {"tools": []})
        self.assertNotIn("error", d)

    def test_jsonrpc_error_serialization(self):
        resp = JSONRPCResponse(id=2, error={"code": MCPErrorCode.METHOD_NOT_FOUND, "message": "Method not found"})
        d = resp.to_dict()
        self.assertEqual(d["id"], 2)
        self.assertEqual(d["error"]["code"], MCPErrorCode.METHOD_NOT_FOUND)

    def test_tool_call_result_helpers(self):
        succ = ToolCallResult.success("File written", data={"bytes": 42})
        self.assertFalse(succ.isError)
        self.assertEqual(succ.content[0]["text"], "File written")
        self.assertEqual(succ.structured_data, {"bytes": 42})

        fail = ToolCallResult.failure("File not found")
        self.assertTrue(fail.isError)
        self.assertEqual(fail.content[0]["text"], "File not found")


class TestMCPServers(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_filesystem_server(self):
        fs = FilesystemMCPServer(root_dir=self.workspace)
        
        # Test write_file
        w_res = fs.write_file("test.py", "print('hello mcp')")
        self.assertTrue(w_res["success"])
        self.assertTrue((self.workspace / "test.py").exists())

        # Test read_file
        r_res = fs.read_file("test.py")
        self.assertTrue(r_res["success"])
        self.assertEqual(r_res["content"], "print('hello mcp')")

        # Test list_directory
        l_res = fs.list_directory()
        self.assertTrue(l_res["success"])
        self.assertTrue(any(e["path"] == "test.py" for e in l_res["entries"]))

        # Test get_file_info
        info_res = fs.get_file_info("test.py")
        self.assertTrue(info_res["exists"])
        self.assertTrue(info_res["is_file"])

        # Test delete_file
        del_res = fs.delete_file("test.py")
        self.assertTrue(del_res["success"])
        self.assertFalse((self.workspace / "test.py").exists())

    def test_terminal_server(self):
        term = TerminalMCPServer(working_dir=self.workspace)
        
        # Test check_environment
        env_res = term.check_environment()
        self.assertTrue(env_res["success"])
        self.assertIn("python_version", env_res)

        # Test terminal_execute
        exec_res = term.terminal_execute("echo 'mcp execution test'")
        self.assertTrue(exec_res["success"])
        self.assertIn("mcp execution test", exec_res["stdout"])

    def test_memory_server(self):
        mem = CodebaseMemoryMCPServer(workspace_dir=self.workspace)
        
        # Store memory
        s_res = mem.store_memory(key="arch_pattern", value="EventDriven Architecture", category="architecture")
        self.assertTrue(s_res["success"])

        # Search memory
        q_res = mem.search_memory(query="EventDriven")
        self.assertTrue(q_res["success"])
        self.assertEqual(len(q_res["memories"]), 1)
        self.assertEqual(q_res["memories"][0]["key"], "arch_pattern")

        # Create a test python file for AST indexing
        (self.workspace / "sample.py").write_text("class Calculator:\n    def add(self, a, b):\n        '''Add two numbers.'''\n        return a + b\n")
        idx_res = mem.index_codebase()
        self.assertTrue(idx_res["success"])
        self.assertGreaterEqual(idx_res["total_symbols"], 2)

        # Query symbols
        sym_res = mem.query_symbols("Calculator")
        self.assertTrue(sym_res["success"])
        self.assertTrue(any(m["name"] == "Calculator" for m in sym_res["matches"]))


class TestMCPClientAndManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir)
        self.manager = MCPManager(workspace_dir=self.workspace)

    def tearDown(self):
        self.manager.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_manager_server_discovery(self):
        servers = self.manager.discover_servers()
        server_names = [s["server_name"] for s in servers]
        self.assertIn("mcp-server-filesystem", server_names)
        self.assertIn("mcp-server-git", server_names)
        self.assertIn("mcp-server-terminal", server_names)
        self.assertIn("mcp-server-memory", server_names)

    def test_manager_tool_discovery(self):
        tools = self.manager.discover_tools()
        tool_names = [t.name for t in tools]
        self.assertIn("read_file", tool_names)
        self.assertIn("write_file", tool_names)
        self.assertIn("terminal_execute", tool_names)
        self.assertIn("store_memory", tool_names)

    def test_manager_dynamic_tool_invocation(self):
        # 1. Write file via MCP
        res = self.manager.call_tool("write_file", {"path": "main.py", "content": "def main(): return 42"})
        self.assertFalse(res.isError)
        self.assertTrue((self.workspace / "main.py").exists())

        # 2. Read file via MCP
        res_read = self.manager.call_tool("read_file", {"path": "main.py"})
        self.assertFalse(res_read.isError)
        self.assertIn("def main(): return 42", str(res_read.content))

        # 3. Store and Search Memory via MCP
        res_mem = self.manager.call_tool("store_memory", {"key": "goal", "value": "Build MCP layer"})
        self.assertFalse(res_mem.isError)

        res_q = self.manager.call_tool("search_memory", {"query": "goal"})
        self.assertFalse(res_q.isError)


if __name__ == "__main__":
    unittest.main()
