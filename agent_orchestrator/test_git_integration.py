"""
Comprehensive Git Integration Test Suite (Issue #17).
Validates git_init, git_status, git_diff, git_log, git_show, git_blame,
git_branch, git_checkout, git_commit, git_restore, git_patch, and agent registry integration.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.mcp.servers.git_server import GitMCPServer
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestGitMCPServer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir).resolve()
        self.server = GitMCPServer(self.repo_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_git_init_and_status(self):
        """Verify git_init creates a repository and git_status inspects it."""
        init_res = self.server.git_init()
        self.assertTrue(init_res["success"])
        self.assertTrue((self.repo_dir / ".git").is_dir())

        status_res = self.server.git_status()
        self.assertTrue(status_res["success"])

    def test_git_commit_and_log(self):
        """Verify git_commit stages and records commits, and git_log retrieves history."""
        self.server.git_init()

        test_file = self.repo_dir / "app.py"
        test_file.write_text("def run(): pass\n", encoding="utf-8")

        commit_res = self.server.git_commit("Initial commit for app")
        self.assertTrue(commit_res["success"])

        log_res = self.server.git_log(max_count=2)
        self.assertTrue(log_res["success"])
        self.assertIn("Initial commit for app", log_res["stdout"])

    def test_git_diff_staged_and_unstaged(self):
        """Verify git_diff reports unstaged and staged unified diffs."""
        self.server.git_init()
        test_file = self.repo_dir / "calc.py"
        test_file.write_text("def add(a, b): return a + b\n", encoding="utf-8")
        self.server.git_commit("Add calc")

        # Unstaged edit
        test_file.write_text("def add(a, b): return a + b\ndef sub(a, b): return a - b\n", encoding="utf-8")
        diff_res = self.server.git_diff()
        self.assertTrue(diff_res["success"])
        self.assertIn("+def sub(a, b): return a - b", diff_res["stdout"])

        # Staged edit
        self.server._run_git(["add", "calc.py"])
        staged_diff = self.server.git_diff(staged=True)
        self.assertTrue(staged_diff["success"])
        self.assertIn("+def sub(a, b): return a - b", staged_diff["stdout"])

    def test_git_show_and_blame(self):
        """Verify git_show displays commit details and git_blame traces line provenance."""
        self.server.git_init()
        test_file = self.repo_dir / "module.py"
        test_file.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")
        self.server.git_commit("Commit module.py")

        show_res = self.server.git_show("HEAD")
        self.assertTrue(show_res["success"])
        self.assertIn("Commit module.py", show_res["stdout"])
        self.assertIn("+line 1", show_res["stdout"])

        blame_res = self.server.git_blame("module.py", start_line=1, end_line=3)
        self.assertTrue(blame_res["success"])
        self.assertIn("line 1", blame_res["stdout"])
        self.assertIn("line 2", blame_res["stdout"])

    def test_git_branch_and_checkout(self):
        """Verify branch creation, listing, switching, and deletion."""
        self.server.git_init()
        test_file = self.repo_dir / "README.md"
        test_file.write_text("# Project\n", encoding="utf-8")
        self.server.git_commit("Initial commit")

        # Create and switch to feature branch
        checkout_res = self.server.git_checkout("feature/test-branch", create_branch=True)
        self.assertTrue(checkout_res["success"])

        branch_res = self.server.git_branch()
        self.assertTrue(branch_res["success"])
        self.assertIn("feature/test-branch", branch_res["stdout"])

        # Switch back to main/master
        current_branch = "main" if "main" in branch_res["stdout"] else "master"
        switch_res = self.server.git_checkout(current_branch)
        self.assertTrue(switch_res["success"])

        # Delete feature branch
        del_res = self.server.git_branch(name="feature/test-branch", delete=True)
        self.assertTrue(del_res["success"])

    def test_git_restore(self):
        """Verify git_restore discards uncommitted changes to a file."""
        self.server.git_init()
        test_file = self.repo_dir / "config.json"
        test_file.write_text('{"env": "prod"}\n', encoding="utf-8")
        self.server.git_commit("Save config")

        # Corrupt file
        test_file.write_text('{"env": "broken"}\n', encoding="utf-8")
        self.assertIn('"broken"', test_file.read_text(encoding="utf-8"))

        # Restore file
        restore_res = self.server.git_restore("config.json")
        self.assertTrue(restore_res["success"])
        self.assertIn('"prod"', test_file.read_text(encoding="utf-8"))

    def test_git_patch_export_and_apply(self):
        """Verify git_patch exports unified diff and reapplies it via git apply."""
        self.server.git_init()
        test_file = self.repo_dir / "utils.py"
        test_file.write_text("VERSION = '1.0.0'\n", encoding="utf-8")
        self.server.git_commit("Add utils")

        # Make edit
        test_file.write_text("VERSION = '1.0.1'\nFEATURE = True\n", encoding="utf-8")
        export_res = self.server.git_patch(action="export")
        self.assertTrue(export_res["success"])
        patch_text = export_res["patch"]
        self.assertIn("VERSION = '1.0.1'", patch_text)

        # Revert file to original
        self.server.git_restore("utils.py")
        self.assertEqual("VERSION = '1.0.0'\n", test_file.read_text(encoding="utf-8"))

        # Apply exported patch
        apply_res = self.server.git_patch(action="apply", patch_content=patch_text)
        self.assertTrue(apply_res["success"])
        self.assertIn("VERSION = '1.0.1'", test_file.read_text(encoding="utf-8"))


class TestBuiltinToolRegistryGitIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)
        self.tools = BuiltinToolRegistry(self.workspace)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_coder_has_git_tools(self):
        coder_tools = [t.name for t in self.tools.get_tools_for_agent("CODER")]
        for expected in ["git_status", "git_diff", "git_commit", "git_restore", "git_patch"]:
            self.assertIn(expected, coder_tools)

    def test_reviewer_has_git_tools(self):
        reviewer_tools = [t.name for t in self.tools.get_tools_for_agent("REVIEWER")]
        for expected in ["git_diff", "git_log", "git_blame", "git_show", "git_status"]:
            self.assertIn(expected, reviewer_tools)

    def test_planner_has_git_tools(self):
        planner_tools = [t.name for t in self.tools.get_tools_for_agent("PLANNER")]
        for expected in ["git_status", "git_log", "git_branch"]:
            self.assertIn(expected, planner_tools)

    def test_call_git_tool_through_registry(self):
        init_res = self.tools.call_tool("git_init", {})
        self.assertTrue(init_res.get("success", False) or "exit_code" in init_res)

        status_res = self.tools.call_tool("git_status", {})
        self.assertTrue(status_res.get("success", False))


if __name__ == "__main__":
    unittest.main()
