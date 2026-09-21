"""
Unit and integration tests for Issue #85:
Comprehensive File Operations Model (delete_file, rename_file, move_file, apply_patch).
"""
import os
import shutil
import tempfile
from pathlib import Path
import pytest

from agent_orchestrator.tools.workspace import (
    WorkspaceManager,
    PathTraversalError,
)
from agent_orchestrator.runtime.idempotency import ConcurrencyConflictError
from agent_orchestrator.security.mutation_authorizer import (
    MutationAuthorizer,
    MutationPolicy,
    MutationType,
    MutationAuthorizationError,
)
from agent_orchestrator.security.file_access_policy import (
    FileAccessPolicy,
    FileAccessMode,
    FileAccessDeniedError,
)
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.mcp.servers.filesystem_server import FilesystemMCPServer


@pytest.fixture
def temp_workspace(tmp_path):
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)
    return ws_dir


class TestWorkspaceDeleteOperations:
    def test_delete_file_basic(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("test.py", "print('hello world')")
        assert (temp_workspace / "test.py").exists()

        res = ws.delete_file("test.py")
        assert res is True
        assert not (temp_workspace / "test.py").exists()

    def test_delete_nonexistent_file(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        res = ws.delete_file("nonexistent.txt")
        assert res is False

    def test_delete_directory_recursive(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("pkg/sub/mod.py", "x = 1")
        assert (temp_workspace / "pkg" / "sub" / "mod.py").exists()

        res = ws.delete_file("pkg", recursive=True)
        assert res is True
        assert not (temp_workspace / "pkg").exists()

    def test_delete_occ_version_mismatch(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("state.py", "v1")

        # Attempt deletion with stale version
        with pytest.raises(ConcurrencyConflictError):
            ws.delete_file("state.py", expected_version=99)

        # Correct version succeeds
        del_res = ws.delete_file("state.py", expected_version=1)
        assert del_res is True
        assert not (temp_workspace / "state.py").exists()

    def test_delete_occ_hash_check(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("config.json", '{"a": 1}')
        from agent_orchestrator.tools.change_tracker import compute_sha256
        correct_hash = compute_sha256('{"a": 1}')

        with pytest.raises(ConcurrencyConflictError):
            ws.delete_file("config.json", expected_hash="0000000000000000000000000000000000000000000000000000000000000000")

        del_res = ws.delete_file("config.json", expected_hash=correct_hash)
        assert del_res is True

    def test_delete_mutation_authorizer_blocked(self, temp_workspace):
        # Create policy blocking deletion of critical paths
        policy = MutationPolicy(
            forbidden_write_paths=["critical.py", "production/*"],
        )
        authorizer = MutationAuthorizer(policy=policy)
        ws = WorkspaceManager(temp_workspace, mutation_authorizer=authorizer)

        # Create file bypassing authorizer for setup
        (temp_workspace / "critical.py").write_text("SECRET = True", encoding="utf-8")

        with pytest.raises((PermissionError, MutationAuthorizationError)):
            ws.delete_file("critical.py")

    def test_delete_idempotency_key(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("temp.txt", "abc")

        res1 = ws.delete_file("temp.txt", operation_id="del-op-1")
        assert res1 is True

        # Second call with same operation_id returns cached result idempotently
        res2 = ws.delete_file("temp.txt", operation_id="del-op-1")
        assert res2 is True


class TestWorkspaceRenameAndMoveOperations:
    def test_rename_file_in_place(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("old_name.py", "a = 10")

        res = ws.rename_file("old_name.py", "new_name.py")
        assert res is True
        assert not (temp_workspace / "old_name.py").exists()
        assert (temp_workspace / "new_name.py").exists()
        assert (temp_workspace / "new_name.py").read_text(encoding="utf-8") == "a = 10"

    def test_rename_to_nested_directory(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("root_file.txt", "content")

        res = ws.rename_file("root_file.txt", "deep/nested/dir/moved_file.txt")
        assert res is True
        assert (temp_workspace / "deep/nested/dir/moved_file.txt").exists()

    def test_rename_destination_conflict_handling(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("file1.txt", "one")
        ws.write_file("file2.txt", "two")

        # Conflict without overwrite raises FileExistsError
        with pytest.raises(FileExistsError):
            ws.rename_file("file1.txt", "file2.txt", overwrite=False)

        # Conflict with overwrite=True succeeds
        res = ws.rename_file("file1.txt", "file2.txt", overwrite=True)
        assert res is True
        assert (temp_workspace / "file2.txt").read_text(encoding="utf-8") == "one"
        assert not (temp_workspace / "file1.txt").exists()

    def test_rename_occ_version_mismatch(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("source.py", "code")

        with pytest.raises(ConcurrencyConflictError):
            ws.rename_file("source.py", "dest.py", expected_version=42)

    def test_rename_path_traversal_denied(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("safe.py", "code")

        with pytest.raises(PathTraversalError):
            ws.rename_file("safe.py", "../../outside.py")

        with pytest.raises(PathTraversalError):
            ws.rename_file("../../outside.py", "safe.py")

    def test_move_file(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("service.py", "class Service: pass")

        res = ws.move_file("service.py", "src/services")
        assert res == "src/services/service.py"
        assert (temp_workspace / "src/services/service.py").exists()
        assert not (temp_workspace / "service.py").exists()


class TestWorkspaceApplyPatchOperations:
    def test_apply_single_file_patch(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        initial_code = (
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def sub(a, b):\n"
            "    return a - b\n"
        )
        ws.write_file("math_ops.py", initial_code)

        patch = (
            "--- a/math_ops.py\n"
            "+++ b/math_ops.py\n"
            "@@ -1,5 +1,6 @@\n"
            " def add(a, b):\n"
            "+    # Added docstring\n"
            "     return a + b\n"
            " \n"
            " def sub(a, b):\n"
            "-    return a - b\n"
            "+    return a - b  # subtraction\n"
        )

        res = ws.apply_patch(patch)
        assert res["success"] is True
        assert res["files_patched"] == 1

        content = ws.read_file("math_ops.py")
        assert "# Added docstring" in content
        assert "return a - b  # subtraction" in content

    def test_apply_multi_file_patch(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("file1.py", "x = 1\ny = 2\n")
        ws.write_file("file2.py", "foo = 'bar'\n")

        multi_patch = (
            "--- a/file1.py\n"
            "+++ b/file1.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-x = 1\n"
            "+x = 100\n"
            " y = 2\n"
            "--- a/file2.py\n"
            "+++ b/file2.py\n"
            "@@ -1,1 +1,2 @@\n"
            " foo = 'bar'\n"
            "+baz = 'qux'\n"
        )

        res = ws.apply_patch(multi_patch)
        assert res["success"] is True
        assert res["files_patched"] == 2
        assert "x = 100" in ws.read_file("file1.py")
        assert "baz = 'qux'" in ws.read_file("file2.py")

    def test_apply_patch_create_new_file(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)

        new_file_patch = (
            "--- /dev/null\n"
            "+++ b/new_module.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+def hello():\n"
            "+    return 'world'\n"
            "+\n"
        )

        res = ws.apply_patch(new_file_patch)
        assert res["success"] is True
        assert (temp_workspace / "new_module.py").exists()
        assert "def hello():" in ws.read_file("new_module.py")

    def test_apply_patch_delete_file(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("deprecated.py", "old_code = True\n")

        delete_file_patch = (
            "--- a/deprecated.py\n"
            "+++ /dev/null\n"
            "@@ -1,1 +0,0 @@\n"
            "-old_code = True\n"
        )

        res = ws.apply_patch(delete_file_patch)
        assert res["success"] is True
        assert not (temp_workspace / "deprecated.py").exists()

    def test_apply_patch_atomic_rollback_on_failure(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        ws.write_file("fileA.py", "line_a\n")
        ws.write_file("fileB.py", "line_b\n")

        # Patch modifies fileA, but fails on fileB due to bad context
        failing_patch = (
            "--- a/fileA.py\n"
            "+++ b/fileA.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-line_a\n"
            "+line_a_modified\n"
            "--- a/fileB.py\n"
            "+++ b/fileB.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-non_existent_context\n"
            "+something_else\n"
        )

        res = ws.apply_patch(failing_patch)
        assert res["success"] is False

        # Verify atomic rollback: fileA remains unchanged!
        assert ws.read_file("fileA.py") == "line_a\n"
        assert ws.read_file("fileB.py") == "line_b\n"


class TestBuiltinToolsIntegration:
    def test_builtin_tool_registry_file_operations(self, temp_workspace):
        ws = WorkspaceManager(temp_workspace)
        registry = BuiltinToolRegistry(workspace=ws)

        # 1. write file via tool
        registry.call_tool("write_file", {"filepath": "app.py", "content": "print(1)"})
        assert (temp_workspace / "app.py").exists()

        # 2. rename file via tool
        res_rename = registry.call_tool("rename_file", {"old_filepath": "app.py", "new_filepath": "main.py"})
        assert res_rename.get("success") is True
        assert (temp_workspace / "main.py").exists()
        assert not (temp_workspace / "app.py").exists()

        # 3. move file via tool
        res_move = registry.call_tool("move_file", {"source_filepath": "main.py", "target_dir": "src"})
        assert res_move.get("success") is True
        assert (temp_workspace / "src" / "main.py").exists()

        # 4. apply patch via tool
        patch_str = (
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-print(1)\n"
            "+print(42)\n"
        )
        res_patch = registry.call_tool("apply_patch", {"patch_content": patch_str})
        assert res_patch.get("success") is True
        assert "print(42)" in ws.read_file("src/main.py")

        # 5. delete file via tool
        res_del = registry.call_tool("delete_file", {"filepath": "src/main.py"})
        assert res_del.get("success") is True
        assert not (temp_workspace / "src" / "main.py").exists()


class TestFilesystemMCPServerOperations:
    def test_mcp_server_rename_move_apply_patch(self, temp_workspace):
        server = FilesystemMCPServer(root_dir=temp_workspace)

        # Write initial file
        server.write_file(path="module.py", content="x = 10\n")

        # Rename
        res_ren = server.rename_file(old_path="module.py", new_path="renamed_module.py")
        assert res_ren["success"] is True
        assert not (temp_workspace / "module.py").exists()
        assert (temp_workspace / "renamed_module.py").exists()

        # Move
        res_mov = server.move_file(source_path="renamed_module.py", target_dir="pkg")
        assert res_mov["success"] is True
        assert (temp_workspace / "pkg" / "renamed_module.py").exists()

        # Apply patch
        patch_code = (
            "--- a/pkg/renamed_module.py\n"
            "+++ b/pkg/renamed_module.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-x = 10\n"
            "+x = 999\n"
        )
        res_pat = server.apply_patch(patch=patch_code)
        assert res_pat["success"] is True
        assert (temp_workspace / "pkg" / "renamed_module.py").read_text(encoding="utf-8") == "x = 999\n"

        # Delete
        res_del = server.delete_file(path="pkg/renamed_module.py")
        assert res_del["success"] is True
        assert not (temp_workspace / "pkg" / "renamed_module.py").exists()
