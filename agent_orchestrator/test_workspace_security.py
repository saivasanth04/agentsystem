"""
Security and Path Traversal Hardening Test Suite (Issue #16).
Validates safe_resolve_path, WorkspaceManager, SandboxedWorkspace,
ReActAgentLoop permission checks, and FilesystemMCPServer boundary enforcement.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agent_orchestrator.tools.workspace import (
    PathTraversalError,
    SandboxedWorkspace,
    WorkspaceManager,
    safe_resolve_path,
)
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.mcp.servers.filesystem_server import FilesystemMCPServer


class TestSafeResolvePath(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.root = Path(self.temp_dir).resolve()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_valid_relative_paths(self):
        resolved = safe_resolve_path(self.root, "src/app.py")
        self.assertEqual(resolved, self.root / "src" / "app.py")
        self.assertTrue(resolved.is_relative_to(self.root))

    def test_relative_traversal_denied(self):
        dangerous_paths = [
            "../../evil.py",
            "../outside.txt",
            "foo/../../outside.py",
            "a/b/../../../secret.txt",
            "..",
            "../",
        ]
        for p in dangerous_paths:
            with self.assertRaises(PathTraversalError):
                safe_resolve_path(self.root, p)

    def test_valid_absolute_path_inside_root(self):
        abs_in_root = self.root / "module" / "main.py"
        resolved = safe_resolve_path(self.root, str(abs_in_root))
        self.assertEqual(resolved, abs_in_root)

    def test_absolute_traversal_outside_root(self):
        other_temp = tempfile.mkdtemp()
        try:
            outside_file = Path(other_temp) / "secret.txt"
            with self.assertRaises(PathTraversalError):
                safe_resolve_path(self.root, str(outside_file))
        finally:
            shutil.rmtree(other_temp, ignore_errors=True)

    def test_null_byte_rejection(self):
        with self.assertRaises(PathTraversalError):
            safe_resolve_path(self.root, "safe_path.py\0malicious.exe")

    def test_empty_path_rejection(self):
        with self.assertRaises(PathTraversalError):
            safe_resolve_path(self.root, "")


class TestWorkspaceManagerSecurity(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.outside_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        shutil.rmtree(self.outside_dir, ignore_errors=True)

    def test_write_file_traversal_blocked(self):
        traversal_target = Path(self.outside_dir) / "pwned.txt"
        rel_escape = os.path.relpath(traversal_target, self.ws.root_dir)

        with self.assertRaises(PathTraversalError):
            self.ws.write_file(rel_escape, "malicious payload")

        self.assertFalse(traversal_target.exists())

    def test_read_file_traversal_blocked(self):
        outside_secret = Path(self.outside_dir) / "host_secrets.env"
        outside_secret.write_text("API_KEY=12345", encoding="utf-8")
        rel_escape = os.path.relpath(outside_secret, self.ws.root_dir)

        with self.assertRaises(PathTraversalError):
            self.ws.read_file(rel_escape)

    def test_file_exists_traversal_blocked(self):
        outside_file = Path(self.outside_dir) / "file.txt"
        outside_file.write_text("test", encoding="utf-8")
        rel_escape = os.path.relpath(outside_file, self.ws.root_dir)

        with self.assertRaises(PathTraversalError):
            self.ws.file_exists(rel_escape)

    def test_delete_file_traversal_blocked(self):
        outside_file = Path(self.outside_dir) / "critical.dat"
        outside_file.write_text("critical", encoding="utf-8")
        rel_escape = os.path.relpath(outside_file, self.ws.root_dir)

        with self.assertRaises(PathTraversalError):
            self.ws.delete_file(rel_escape)

        self.assertTrue(outside_file.exists())

    def test_legitimate_writes_and_reads(self):
        written_path = self.ws.write_file("sub/dir/test.py", "print('safe')")
        self.assertTrue(written_path.exists())
        self.assertEqual(self.ws.read_file("sub/dir/test.py"), "print('safe')")
        self.assertTrue(self.ws.file_exists("sub/dir/test.py"))

    def test_valid_absolute_path_not_mangled(self):
        abs_target = self.ws.root_dir / "valid_target.py"
        written = self.ws.write_file(str(abs_target), "def foo(): pass")
        self.assertEqual(written, abs_target)
        self.assertTrue(abs_target.exists())
        self.assertFalse((self.ws.root_dir / self.ws.root_dir.name / "valid_target.py").exists())


class TestSandboxedWorkspaceSecurity(unittest.TestCase):
    def setUp(self):
        self.main_temp = tempfile.mkdtemp()
        self.main_ws = WorkspaceManager(self.main_temp)
        self.sandbox = SandboxedWorkspace(self.main_ws, sandbox_id="test_sb")

    def tearDown(self):
        self.sandbox.cleanup()
        shutil.rmtree(self.main_temp, ignore_errors=True)

    def test_sandbox_isolated_write_and_merge(self):
        self.sandbox.write_file("feature.py", "new_feature = True")
        self.assertFalse(self.main_ws.file_exists("feature.py"))

        merged = self.sandbox.merge_into_main()
        self.assertIn("feature.py", merged)
        self.assertTrue(self.main_ws.file_exists("feature.py"))
        self.assertEqual(self.main_ws.read_file("feature.py"), "new_feature = True")

    def test_sandbox_traversal_write_blocked(self):
        with self.assertRaises(PathTraversalError):
            self.sandbox.write_file("../../main_escape.py", "payload")


class TestRuntimePermissionTraversalHardening(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ws = WorkspaceManager(self.temp_dir)
        self.loop = ReActAgentLoop(llm=MagicMock(), tool_registry=MagicMock())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_prefix_traversal_permission_bypass_blocked(self):
        permissions = {"allowed_write_paths": ["src/*"]}
        # Attacker attempts to bypass "src/*" using relative traversal "src/../../outside.py"
        error = self.loop._check_permission(
            "write_file",
            {"filepath": "src/../../outside.py"},
            permissions,
            workspace=self.ws,
        )
        self.assertIsNotNone(error)
        self.assertIn("Permission Denied", error)

    def test_legitimate_path_within_permission_allowed(self):
        permissions = {"allowed_write_paths": ["src/*"]}
        error = self.loop._check_permission(
            "write_file",
            {"filepath": "src/components/button.py"},
            permissions,
            workspace=self.ws,
        )
        self.assertIsNone(error)


class TestFilesystemMCPServerSecurity(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.fs_server = FilesystemMCPServer(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mcp_write_traversal_denied(self):
        with self.assertRaises(PathTraversalError):
            self.fs_server.write_file(path="../../mcp_escape.txt", content="payload")

    def test_mcp_read_traversal_denied(self):
        with self.assertRaises(PathTraversalError):
            self.fs_server.read_file(path="../../mcp_read.txt")


if __name__ == "__main__":
    unittest.main()
