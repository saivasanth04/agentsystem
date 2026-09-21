"""
Comprehensive unit and integration tests for Issue #19: Cryptographic & Semantic Change Tracking.
Validates:
1. Cryptographic hashing (SHA-256 before/after hash generation)
2. Unified diff generation, lines added/removed, changed line numbers
3. AST symbol diffing (classes, functions, methods added, modified, deleted)
4. ChangeJournal cumulative tracking in WorkspaceManager and SandboxedWorkspace
5. Checkpoint snapshot diffing in WorkspaceCheckpointManager
6. SQLiteStateStore persistence of file changes and change manifests
7. CoderAgent and ReviewerAgent prompt integration with exact diffs and symbols
8. Tool access via BuiltinToolRegistry and FilesystemMCPServer
"""
import hashlib
import json
import os
import shutil
import tempfile
import unittest

from agent_orchestrator.tools.change_tracker import (
    compute_sha256,
    extract_ast_symbols,
    diff_ast_symbols,
    compute_file_change,
    ChangeJournal,
    diff_directories,
    SymbolChange,
    FileChangeRecord,
    ChangeManifest,
)
from agent_orchestrator.tools.workspace import WorkspaceManager, SandboxedWorkspace
from agent_orchestrator.persistence.checkpoint_manager import WorkspaceCheckpointManager
from agent_orchestrator.persistence.state_store import SQLiteStateStore
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.mcp.servers.filesystem_server import FilesystemMCPServer
from agent_orchestrator.state import OrchestratorState
from agent_orchestrator.agents.reviewer import ReviewerAgent


class MockLLM:
    def __init__(self, responses=None):
        self.responses = responses or []
        self.call_count = 0
        self.captured_prompts = []

    def chat_with_tools(self, messages, tools=None, tool_choice=None, model=None, temperature=0.2):
        user_msg = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        self.captured_prompts.append(user_msg)
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
            return resp
        return {
            "content": json.dumps({
                "verdict": "PASS",
                "score_out_of_100": 95,
                "summary": "Verified diff and AST symbols.",
            }),
            "tool_calls": [],
        }

    def chat_json(self, messages, model=None, temperature=0.2):
        return {"verdict": "PASS", "score_out_of_100": 95, "summary": "Done"}

    def _extract_json(self, text):
        try:
            return json.loads(text)
        except Exception:
            return {}


class TestCryptographicHashing(unittest.TestCase):
    def test_compute_sha256_deterministic(self):
        content = "def hello_world():\n    return 'hello'\n"
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.assertEqual(compute_sha256(content), expected)
        self.assertEqual(compute_sha256(content.encode("utf-8")), expected)

    def test_compute_sha256_differs_on_mutation(self):
        content_a = "x = 1\n"
        content_b = "x = 2\n"
        self.assertNotEqual(compute_sha256(content_a), compute_sha256(content_b))


class TestUnifiedDiffAndLineDeltas(unittest.TestCase):
    def test_create_file_change(self):
        content = "line 1\nline 2\nline 3\n"
        rec = compute_file_change("test.txt", before_content=None, after_content=content)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.change_type, "CREATED")
        self.assertIsNone(rec.before_hash)
        self.assertEqual(rec.after_hash, compute_sha256(content))
        self.assertEqual(rec.lines_added, 3)
        self.assertEqual(rec.lines_removed, 0)
        self.assertIn("+line 1", rec.diff)

    def test_delete_file_change(self):
        content = "line 1\nline 2\n"
        rec = compute_file_change("test.txt", before_content=content, after_content=None)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.change_type, "DELETED")
        self.assertEqual(rec.before_hash, compute_sha256(content))
        self.assertIsNone(rec.after_hash)
        self.assertEqual(rec.lines_added, 0)
        self.assertEqual(rec.lines_removed, 2)
        self.assertIn("-line 1", rec.diff)

    def test_modify_file_change(self):
        before = "line 1\nline 2\nline 3\n"
        after = "line 1\nline TWO\nline 3\nline 4\n"
        rec = compute_file_change("test.txt", before_content=before, after_content=after)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.change_type, "MODIFIED")
        self.assertEqual(rec.before_hash, compute_sha256(before))
        self.assertEqual(rec.after_hash, compute_sha256(after))
        self.assertEqual(rec.lines_added, 2)
        self.assertEqual(rec.lines_removed, 1)
        self.assertIn("-line 2", rec.diff)
        self.assertIn("+line TWO", rec.diff)
        self.assertIn("+line 4", rec.diff)

    def test_identical_content_returns_none(self):
        content = "line 1\nline 2\n"
        rec = compute_file_change("test.txt", before_content=content, after_content=content)
        self.assertIsNone(rec)


class TestASTSymbolDiffing(unittest.TestCase):
    def test_extract_ast_symbols(self):
        code = '''
class Calculator:
    def add(self, a, b):
        return a + b

    async def fetch_result(self):
        pass

def standalone(x):
    return x * 2
'''
        symbols = extract_ast_symbols(code, "calc.py")
        self.assertIn("class:Calculator", symbols)
        self.assertIn("method:Calculator.add", symbols)
        self.assertIn("method:Calculator.fetch_result", symbols)
        self.assertIn("function:standalone", symbols)

    def test_diff_ast_symbols_added_modified_removed(self):
        before = '''
class Service:
    def process(self):
        return 1

    def old_method(self):
        pass

def helper():
    return True
'''
        after = '''
class Service:
    def process(self):
        # modified implementation
        return 2

    def new_method(self):
        pass

def helper():
    return True

def added_helper():
    return False
'''
        changes = diff_ast_symbols(before, after, "service.py")
        names_types = {(c.name, c.change_type) for c in changes}
        self.assertIn(("Service.process", "MODIFIED"), names_types)
        self.assertIn(("Service.new_method", "ADDED"), names_types)
        self.assertIn(("Service.old_method", "REMOVED"), names_types)
        self.assertIn(("added_helper", "ADDED"), names_types)
        # helper was unchanged so it should not appear
        self.assertNotIn(("helper", "MODIFIED"), names_types)
        self.assertNotIn(("helper", "ADDED"), names_types)
        self.assertNotIn(("helper", "REMOVED"), names_types)


class TestChangeJournalAndWorkspace(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(root_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_workspace_journal_records_mutations(self):
        # 1. Create file
        self.ws.write_file("app.py", "def main():\n    print('init')\n")
        manifest = self.ws.get_change_manifest()
        self.assertEqual(manifest.created_files, ["app.py"])
        self.assertIn("app.py", manifest.file_changes)
        rec = manifest.file_changes["app.py"]
        self.assertEqual(rec.change_type, "CREATED")
        self.assertTrue(len(rec.changed_symbols) > 0)
        self.assertEqual(rec.changed_symbols[0].name, "main")

        # 2. Modify file via replace_file_content
        self.ws.replace_file_content("app.py", "print('init')", "print('updated')")
        manifest2 = self.ws.get_change_manifest()
        # Since cumulative from start of session, still created relative to initial empty state
        self.assertIn("app.py", manifest2.created_files)

        # Clear journal to track subsequent delta
        self.ws.clear_change_manifest()
        self.ws.insert_lines("app.py", line_number=1, content="def extra():\n    pass\n", position="before")
        manifest_delta = self.ws.get_change_manifest()
        self.assertEqual(manifest_delta.modified_files, ["app.py"])
        rec_delta = manifest_delta.file_changes["app.py"]
        self.assertEqual(rec_delta.change_type, "MODIFIED")
        sym_names = [s.name for s in rec_delta.changed_symbols]
        self.assertIn("extra", sym_names)

        # 3. Delete file
        self.ws.delete_file("app.py")
        manifest3 = self.ws.get_change_manifest()
        self.assertIn("app.py", manifest3.deleted_files)

    def test_sandboxed_workspace_uncommitted_changes(self):
        self.ws.write_file("base.py", "x = 1\n")
        sandbox = SandboxedWorkspace(main_workspace=self.ws, sandbox_id="test_sb")
        try:
            # Modify base.py and create new.py in sandbox
            sandbox.replace_file_content("base.py", "x = 1", "x = 2")
            sandbox.write_file("new.py", "y = 10\n")

            changes = sandbox.get_uncommitted_changes()
            self.assertIn("new.py", changes["created"])
            self.assertIn("base.py", changes["modified"])
            self.assertIn("manifest", changes)
            manifest = changes["manifest"]
            self.assertIn("new.py", manifest["created_files"])
            self.assertIn("base.py", manifest["modified_files"])
            self.assertIn("diff", manifest["file_changes"]["base.py"])
        finally:
            sandbox.cleanup()


class TestCheckpointManagerDiffs(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(root_dir=self.test_dir)
        self.ckpt_mgr = WorkspaceCheckpointManager(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_diff_snapshots(self):
        self.ws.write_file("mod.py", "def a(): return 1\n")
        self.ckpt_mgr.create_snapshot("ckpt_1", self.ws)

        self.ws.replace_file_content("mod.py", "return 1", "return 2")
        self.ws.write_file("extra.txt", "hello")
        self.ckpt_mgr.create_snapshot("ckpt_2", self.ws)

        manifest = self.ckpt_mgr.diff_snapshots("ckpt_1", "ckpt_2")
        self.assertIn("mod.py", manifest.modified_files)
        self.assertIn("extra.txt", manifest.created_files)
        self.assertIn("def a()", manifest.file_changes["mod.py"].diff)

    def test_get_checkpoint_manifest(self):
        self.ws.write_file("code.py", "v = 'v1'\n")
        self.ckpt_mgr.create_snapshot("base_ckpt", self.ws)

        self.ws.replace_file_content("code.py", "v1", "v2")
        manifest = self.ckpt_mgr.get_checkpoint_manifest("base_ckpt", self.ws)
        self.assertIn("code.py", manifest.modified_files)
        rec = manifest.file_changes["code.py"]
        self.assertIn("-v = 'v1'", rec.diff)
        self.assertIn("+v = 'v2'", rec.diff)


class TestSQLiteStateStorePersistence(unittest.TestCase):
    def setUp(self):
        self.state_store = SQLiteStateStore(":memory:")

    def test_save_and_retrieve_file_changes(self):
        rec = compute_file_change(
            "module.py",
            before_content="def old(): pass\n",
            after_content="def new(): pass\n",
        )
        self.assertIsNotNone(rec)
        cid = self.state_store.save_file_change("sess-1", "T-01", rec)
        self.assertTrue(cid.startswith("chg-sess-1-T-01-"))

        task_changes = self.state_store.get_task_changes("sess-1", "T-01")
        self.assertEqual(len(task_changes), 1)
        item = task_changes[0]
        self.assertEqual(item["filepath"], "module.py")
        self.assertEqual(item["operation"], "MODIFIED")
        self.assertEqual(item["before_hash"], rec.before_hash)
        self.assertEqual(item["after_hash"], rec.after_hash)
        self.assertEqual(item["lines_added"], 1)
        self.assertEqual(item["lines_removed"], 1)
        self.assertIn("diff", item)
        self.assertTrue(len(item["changed_symbols"]) > 0)

        session_changes = self.state_store.get_session_changes("sess-1")
        self.assertEqual(len(session_changes), 1)

    def test_save_change_manifest(self):
        journal = ChangeJournal(session_id="sess-2", task_id="T-02")
        journal.record_mutation("file1.py", None, "x = 1\n")
        journal.record_mutation("file2.py", "y = 1\n", "y = 2\n")
        manifest = journal.get_manifest()

        cids = self.state_store.save_change_manifest("sess-2", "T-02", manifest)
        self.assertEqual(len(cids), 2)
        changes = self.state_store.get_task_changes("sess-2", "T-02")
        self.assertEqual(len(changes), 2)


class TestAgentReviewerAndCoderIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(root_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_reviewer_prompt_includes_diffs_and_symbols(self):
        self.ws.write_file("core.py", "class Auth:\n    def login(self):\n        return False\n")
        self.ws.clear_change_manifest()

        # Mutate
        self.ws.replace_file_content("core.py", "return False", "return True")
        manifest = self.ws.get_change_manifest()

        state = OrchestratorState(user_request="Update Auth.login to return True")
        state.code_output = {
            "summary": "Updated login to True",
            "written_files": ["core.py"],
            "change_manifest": manifest.to_dict(),
        }
        state.test_output = {
            "execution_success": True,
            "exit_code": 0,
            "stdout": "All tests passed",
            "stderr": "",
        }

        llm = MockLLM()
        tools = BuiltinToolRegistry(workspace=self.ws, llm=llm)
        reviewer = ReviewerAgent(llm=llm, workspace=self.ws, tool_registry=tools)
        review_result = reviewer.execute(state)

        self.assertEqual(review_result.get("verdict"), "PASS")
        self.assertTrue(len(llm.captured_prompts) > 0)
        prompt_text = llm.captured_prompts[0]

        # Verify exact diff and AST symbols are present in prompt
        self.assertIn("Code Changes & Diff Analysis:", prompt_text)
        self.assertIn("MODIFIED: core.py", prompt_text)
        self.assertIn("Before SHA:", prompt_text)
        self.assertIn("After SHA:", prompt_text)
        self.assertIn("Auth.login", prompt_text)
        self.assertIn("-        return False", prompt_text)
        self.assertIn("+        return True", prompt_text)

    def test_coder_outputs_change_manifest(self):
        from agent_orchestrator.agents.coder import CoderAgent

        llm = MockLLM([{
            "content": json.dumps({
                "summary": "Implemented feature in calc.py",
                "files": [{"filepath": "calc.py", "content": "def calc(): return 42\n"}],
            }),
            "tool_calls": [],
        }])
        tools = BuiltinToolRegistry(workspace=self.ws, llm=llm)
        coder = CoderAgent(llm=llm, workspace=self.ws, tool_registry=tools)
        state = OrchestratorState(user_request="Build calculator")
        res = coder.execute(state)

        self.assertIn("change_manifest", res)
        self.assertIsNotNone(res["change_manifest"])
        self.assertIn("calc.py", res["change_manifest"]["created_files"])


class TestBuiltinAndMCPToolAccess(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(root_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_builtin_tool_get_workspace_changes(self):
        reg = BuiltinToolRegistry(workspace=self.ws)
        self.ws.write_file("sample.py", "def foo(): pass\n")

        # Query all changes
        res_all = reg.call_tool("get_workspace_changes", {})
        self.assertTrue(res_all["success"])
        self.assertIn("sample.py", res_all["created_files"])

        # Query single file
        res_file = reg.call_tool("get_workspace_changes", {"filepath": "sample.py"})
        self.assertTrue(res_file["success"])
        self.assertTrue(res_file["found"])
        self.assertEqual(res_file["change"]["change_type"], "CREATED")
        self.assertEqual(res_file["change"]["lines_added"], 1)

    def test_filesystem_mcp_get_workspace_changes(self):
        fs_server = FilesystemMCPServer(root_dir=self.test_dir)
        write_res = fs_server.write_file(path="mcp_test.py", content="x = 10\n")
        self.assertTrue(write_res["success"])

        changes = fs_server.get_workspace_changes()
        self.assertTrue(changes["success"])
        manifest = changes["manifest"]
        self.assertIn("mcp_test.py", manifest["created_files"])

        # Modify via replace_file_content
        replace_res = fs_server.replace_file_content(
            path="mcp_test.py",
            target_content="10",
            replacement_content="20",
        )
        self.assertTrue(replace_res["success"])

        file_chg = fs_server.get_workspace_changes(path="mcp_test.py")
        self.assertTrue(file_chg["found"])


if __name__ == "__main__":
    unittest.main()
