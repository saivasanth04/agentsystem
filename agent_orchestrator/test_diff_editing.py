"""
Unit and Integration Tests for Issue #18: Delta-Based and Diff Editing Suite.
Tests:
- Line-bounded and fuzzy search & replace
- Atomic line insertions and deletions
- Aider-style SEARCH/REPLACE block parsing
- Truncation placeholder detection
- WorkspaceManager and SandboxedWorkspace integration
- BuiltinToolRegistry and role mappings
- FilesystemMCPServer delta tools
- CoderAgent deliverable overwrite protections
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agent_orchestrator.tools.diff_editor import (
    DiffEditError,
    apply_diff_blocks,
    delete_lines,
    detect_truncation_placeholders,
    generate_unified_diff,
    insert_lines,
    search_and_replace,
)
from agent_orchestrator.tools.workspace import SandboxedWorkspace, WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.mcp.servers.filesystem_server import FilesystemMCPServer
from agent_orchestrator.agents.coder import CoderAgent
from agent_orchestrator.state import OrchestratorState


class TestDiffEditorEngine(unittest.TestCase):
    def test_exact_search_and_replace(self):
        content = "def calculate(a, b):\n    return a + b\n"
        new_content, count, diff = search_and_replace(
            content=content,
            search="return a + b",
            replace="return a * b",
            filepath="calc.py",
        )
        self.assertEqual(count, 1)
        self.assertIn("return a * b", new_content)
        self.assertIn("-    return a + b", diff)
        self.assertIn("+    return a * b", diff)

    def test_line_bounded_search_and_replace(self):
        content = "item = 1\nitem = 2\nitem = 3\nitem = 4\n"
        # Only replace 'item = 2' bounded to lines 2-3
        new_content, count, diff = search_and_replace(
            content=content,
            search="item = 2",
            replace="item = 200",
            start_line=2,
            end_line=3,
        )
        self.assertEqual(count, 1)
        self.assertIn("item = 200", new_content)
        self.assertIn("item = 1", new_content)

    def test_fuzzy_whitespace_search_and_replace(self):
        # Source has 4 spaces and trailing newline
        content = "class Service:\n    def run(self):\n        start()\n        finish()\n"
        # Search block has slightly altered leading spaces/tabs but same stripped lines
        search_block = "def run(self):\n  start()\n  finish()"
        replacement = "def run(self):\n        start()\n        log_step()\n        finish()"

        new_content, count, diff = search_and_replace(
            content=content,
            search=search_block,
            replace=replacement,
            fuzzy=True,
        )
        self.assertEqual(count, 1)
        self.assertIn("log_step()", new_content)

    def test_insert_lines_before_and_after(self):
        content = "line1\nline2\nline3\n"
        # Insert after line 1
        after_text, diff1 = insert_lines(content, line_number=1, new_content="inserted_after_1", position="after")
        lines = after_text.splitlines()
        self.assertEqual(lines[1], "inserted_after_1")

        # Insert before line 1 (prepends)
        before_text, diff2 = insert_lines(content, line_number=1, new_content="prepended_header", position="before")
        self.assertTrue(before_text.startswith("prepended_header\n"))

    def test_delete_lines(self):
        content = "line1\nline2\nline3\nline4\n"
        new_content, diff = delete_lines(content, start_line=2, end_line=3)
        self.assertEqual(new_content, "line1\nline4\n")
        self.assertIn("-line2", diff)
        self.assertIn("-line3", diff)

    def test_apply_diff_blocks_multiple_hunks(self):
        content = (
            "import os\n"
            "import sys\n\n"
            "def foo():\n"
            "    return 1\n\n"
            "def bar():\n"
            "    return 2\n"
        )
        blocks = """
<<<<<<< SEARCH
def foo():
    return 1
=======
def foo():
    return 42
>>>>>>> REPLACE

<<<<<<< SEARCH
def bar():
    return 2
=======
def bar():
    return 99
>>>>>>> REPLACE
"""
        new_content, count, diff = apply_diff_blocks(content, blocks, filepath="test.py")
        self.assertEqual(count, 2)
        self.assertIn("return 42", new_content)
        self.assertIn("return 99", new_content)
        self.assertIn("-    return 1", diff)
        self.assertIn("+    return 42", diff)

    def test_truncation_detection(self):
        lazy_samples = [
            "// ... rest of code unchanged ...\ndef test(): pass",
            "# ... existing code ...\ndef test(): pass",
            "/* ... remaining implementation ... */",
            "# code remains the same ...",
        ]
        for sample in lazy_samples:
            matches = detect_truncation_placeholders(sample)
            self.assertTrue(len(matches) > 0, f"Failed to detect truncation in: {sample}")

        legitimate_code = "def process_data(items):\n    # Process all valid items\n    return [i for i in items if i > 0]\n"
        self.assertEqual(detect_truncation_placeholders(legitimate_code), [])


class TestWorkspaceManagerDeltaIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(root_dir=Path(self.test_dir))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_workspace_delta_flow(self):
        self.ws.write_file("main.py", "def start():\n    pass\n")
        
        # Replace
        rep_res = self.ws.replace_file_content("main.py", "pass", "return True")
        self.assertTrue(rep_res["success"])
        self.assertIn("return True", self.ws.read_file("main.py"))

        # Insert
        ins_res = self.ws.insert_lines("main.py", line_number=1, content="    # initialize", position="after")
        self.assertTrue(ins_res["success"])
        self.assertIn("# initialize", self.ws.read_file("main.py"))

        # Delete
        del_res = self.ws.delete_lines("main.py", start_line=2, end_line=2)
        self.assertTrue(del_res["success"])
        self.assertNotIn("# initialize", self.ws.read_file("main.py"))

        # Apply diff blocks
        blocks = "<<<<<<< SEARCH\ndef start():\n=======\ndef launch():\n>>>>>>> REPLACE"
        blk_res = self.ws.apply_diff_blocks("main.py", blocks)
        self.assertTrue(blk_res["success"])
        self.assertIn("def launch():", self.ws.read_file("main.py"))

    def test_sandboxed_workspace_delta_isolation(self):
        self.ws.write_file("app.py", "version = 1.0\n")
        sandbox = SandboxedWorkspace(self.ws, sandbox_id="sb_delta_test")
        
        sandbox.replace_file_content("app.py", "version = 1.0", "version = 2.0")
        self.assertIn("version = 2.0", sandbox.read_file("app.py"))
        # Main workspace unaffected until merged
        self.assertIn("version = 1.0", self.ws.read_file("app.py"))

        sandbox.merge_into_main()
        self.assertIn("version = 2.0", self.ws.read_file("app.py"))


class TestBuiltinToolRegistryDeltaIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(root_dir=Path(self.test_dir))
        self.registry = BuiltinToolRegistry(self.ws)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_registry_delta_tools(self):
        self.registry.call_tool("write_file", {"filepath": "calc.py", "content": "x = 10\ny = 20\n"})
        
        # replace_file_content
        r1 = self.registry.call_tool("replace_file_content", {
            "filepath": "calc.py",
            "target_content": "x = 10",
            "replacement_content": "x = 100",
        })
        self.assertTrue(r1["success"])
        self.assertIn("x = 100", self.ws.read_file("calc.py"))

        # insert_lines
        r2 = self.registry.call_tool("insert_lines", {
            "filepath": "calc.py",
            "line_number": 1,
            "content": "# config",
            "position": "before",
        })
        self.assertTrue(r2["success"])
        self.assertTrue(self.ws.read_file("calc.py").startswith("# config\n"))

        # delete_lines
        r3 = self.registry.call_tool("delete_lines", {
            "filepath": "calc.py",
            "start_line": 1,
            "end_line": 1,
        })
        self.assertTrue(r3["success"])
        self.assertFalse(self.ws.read_file("calc.py").startswith("# config\n"))

    def test_agent_role_delta_mappings(self):
        coder_tools = [t.name for t in self.registry.get_tools_for_agent("CODER")]
        for expected in ("replace_file_content", "insert_lines", "delete_lines", "apply_diff_blocks", "write_file", "read_file"):
            self.assertIn(expected, coder_tools)

        tester_tools = [t.name for t in self.registry.get_tools_for_agent("TESTER")]
        self.assertIn("replace_file_content", tester_tools)
        self.assertIn("insert_lines", tester_tools)
        self.assertIn("delete_lines", tester_tools)


class TestFilesystemMCPServerDeltaIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.server = FilesystemMCPServer(root_dir=Path(self.test_dir))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_mcp_delta_endpoints(self):
        self.server.write_file(path="server.py", content="PORT = 8080\nHOST = 'localhost'\n")

        res_rep = self.server.replace_file_content(path="server.py", target_content="8080", replacement_content="3000")
        self.assertTrue(res_rep["success"])
        self.assertIn("3000", self.server.read_file(path="server.py")["content"])

        res_ins = self.server.insert_lines(path="server.py", line_number=2, content="DEBUG = True")
        self.assertTrue(res_ins["success"])
        self.assertIn("DEBUG = True", self.server.read_file(path="server.py")["content"])

        res_del = self.server.delete_lines(path="server.py", start_line=3, end_line=3)
        self.assertTrue(res_del["success"])
        self.assertNotIn("DEBUG = True", self.server.read_file(path="server.py")["content"])


class TestCoderAgentDeliverableProtection(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(root_dir=Path(self.test_dir))
        self.registry = BuiltinToolRegistry(self.ws)
        self.mock_llm = MagicMock()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_lazy_truncation_overwrite_blocked(self):
        # Create an existing 5-line critical file
        original_content = "import sys\nimport os\n\ndef critical_core():\n    return 42\n"
        self.ws.write_file("core.py", original_content)

        # Mock LLM returning lazy deliverable with ellipsis placeholder
        lazy_deliverable = {
            "deliverables": {
                "files": [
                    {
                        "filepath": "core.py",
                        "content": "# ... rest of code unchanged ...\ndef critical_core():\n    return 99\n",
                    }
                ]
            }
        }
        self.mock_llm.chat_json.return_value = lazy_deliverable

        coder = CoderAgent(
            llm=self.mock_llm,
            workspace=self.ws,
            tool_registry=self.registry,
        )
        state = OrchestratorState(user_request="Update core.py")
        coder.execute(state)

        # Confirm the file was NOT overwritten with the lazy truncation placeholder!
        persisted = self.ws.read_file("core.py")
        self.assertEqual(persisted, original_content, "Existing file was wiped by lazy truncation deliverable!")


if __name__ == "__main__":
    unittest.main()
