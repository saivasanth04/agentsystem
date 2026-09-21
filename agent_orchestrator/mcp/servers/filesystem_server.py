"""
Filesystem MCP Server.
Provides standardized, sandboxed filesystem inspection, reading, writing, and directory listing over MCP.
"""
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base_server import BaseMCPServer
from ...tools.workspace import safe_resolve_path, PathTraversalError
from ...security.file_access_policy import FileAccessMode, FileAccessPolicy


class FilesystemMCPServer(BaseMCPServer):
    """
    Standard MCP Filesystem Server bound to a target workspace directory.
    """

    def __init__(
        self,
        root_dir: Path,
        name: str = "mcp-server-filesystem",
        file_access_policy: Optional[Any] = None,
    ):
        super().__init__(
            name=name,
            version="1.0.0",
            description="Sandboxed workspace filesystem server with robust multi-file capabilities.",
        )
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        from ...tools.change_tracker import ChangeJournal
        from ...security.file_access_policy import FileAccessPolicy
        if isinstance(file_access_policy, dict):
            self.file_access_policy = FileAccessPolicy.from_dict(file_access_policy)
        elif isinstance(file_access_policy, FileAccessPolicy):
            self.file_access_policy = file_access_policy
        else:
            self.file_access_policy = file_access_policy or FileAccessPolicy()
        self.change_journal = ChangeJournal()
        self._register_fs_tools()

    def set_file_access_policy(self, policy: Any) -> None:
        """Configures or updates the active FileAccessPolicy."""
        from ...security.file_access_policy import FileAccessPolicy
        if isinstance(policy, dict):
            self.file_access_policy = FileAccessPolicy.from_dict(policy)
        elif isinstance(policy, FileAccessPolicy):
            self.file_access_policy = policy
        else:
            self.file_access_policy = policy or FileAccessPolicy()

    def _resolve_path(self, rel_path: str) -> Path:
        return safe_resolve_path(self.root_dir, rel_path)

    def _check_access(self, rel_norm: str, mode: Any) -> Optional[Dict[str, Any]]:
        """Evaluates file access policy, returning an error response dict if denied, or None if allowed."""
        if hasattr(self, "file_access_policy") and self.file_access_policy:
            decision = self.file_access_policy.evaluate(rel_norm, mode)
            if not decision.allowed:
                return {
                    "error": f"File access denied: {decision.reason}",
                    "path": rel_norm,
                    "reason": decision.reason,
                    "suggested_action": decision.suggested_action,
                    "success": False,
                }
        return None

    def _register_fs_tools(self) -> None:
        # 1. read_file
        self.register_tool(
            name="read_file",
            description="Reads complete content of a text/code file within the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path in workspace"},
                },
                "required": ["path"],
            },
            handler=self.read_file,
        )

        # 2. write_file
        self.register_tool(
            name="write_file",
            description="Writes or overwrites content to a file in workspace, creating parent directories.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path in workspace"},
                    "content": {"type": "string", "description": "Text or code content to write"},
                },
                "required": ["path", "content"],
            },
            handler=self.write_file,
        )

        # 3. list_directory
        self.register_tool(
            name="list_directory",
            description="Lists all files and directories under a relative path or workspace root.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative directory path (default: root)", "default": ""},
                    "recursive": {"type": "boolean", "description": "Whether to list recursively", "default": True},
                },
            },
            handler=self.list_directory,
        )

        # 4. delete_file
        self.register_tool(
            name="delete_file",
            description="Deletes a file or directory from the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to delete"},
                },
                "required": ["path"],
            },
            handler=self.delete_file,
        )

        # 5. get_file_info
        self.register_tool(
            name="get_file_info",
            description="Retrieves metadata (size, last modified, exists, is_dir) for a workspace path.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to inspect"},
                },
                "required": ["path"],
            },
            handler=self.get_file_info,
        )

        # 6. replace_file_content
        self.register_tool(
            name="replace_file_content",
            description="Surgically replaces target substring or lines with replacement content.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to file"},
                    "target_content": {"type": "string", "description": "Exact lines or substring to replace"},
                    "replacement_content": {"type": "string", "description": "New content to replace with"},
                    "start_line": {"type": "integer", "description": "Optional 1-indexed start line"},
                    "end_line": {"type": "integer", "description": "Optional 1-indexed end line"},
                    "allow_multiple": {"type": "boolean", "description": "Replace multiple occurrences", "default": False},
                    "fuzzy": {"type": "boolean", "description": "Fuzzy whitespace tolerance", "default": True},
                },
                "required": ["target_content", "replacement_content"],
            },
            handler=self.replace_file_content,
        )

        # 7. insert_lines
        self.register_tool(
            name="insert_lines",
            description="Inserts content before or after a 1-indexed line number in a file.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to file"},
                    "line_number": {"type": "integer", "description": "1-indexed line number to insert at"},
                    "content": {"type": "string", "description": "New lines/content to insert"},
                    "position": {"type": "string", "description": "'after' or 'before'", "default": "after"},
                },
                "required": ["line_number", "content"],
            },
            handler=self.insert_lines,
        )

        # 8. delete_lines
        self.register_tool(
            name="delete_lines",
            description="Deletes lines in 1-indexed range [start_line, end_line] from a file.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to file"},
                    "start_line": {"type": "integer", "description": "1-indexed start line"},
                    "end_line": {"type": "integer", "description": "1-indexed end line (inclusive)"},
                },
                "required": ["start_line", "end_line"],
            },
            handler=self.delete_lines,
        )

        # 9. apply_diff_blocks
        self.register_tool(
            name="apply_diff_blocks",
            description="Applies one or more Aider-style SEARCH/REPLACE blocks to a file.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to file"},
                    "diff_blocks": {"type": "string", "description": "SEARCH/REPLACE blocks"},
                    "fuzzy": {"type": "boolean", "description": "Fuzzy whitespace tolerance", "default": True},
                },
                "required": ["diff_blocks"],
            },
            handler=self.apply_diff_blocks,
        )

        # 10. get_workspace_changes
        self.register_tool(
            name="get_workspace_changes",
            description="Inspects file changes, diffs, line counts, and AST symbol changes (classes/functions/methods added/modified/deleted) in workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Optional specific relative path to inspect changes for"},
                },
            },
            handler=self.get_workspace_changes,
        )

        # 11. rollback_to_checkpoint
        self.register_tool(
            name="rollback_to_checkpoint",
            description="Rolls back workspace files to a snapshot checkpoint, restoring modified files and deleting newly added files.",
            input_schema={
                "type": "object",
                "properties": {
                    "checkpoint_id": {"type": "string", "description": "Snapshot checkpoint ID to roll back to (default: most recent)"},
                    "reason": {"type": "string", "description": "Reason for rolling back changes", "default": ""},
                },
            },
            handler=self.rollback_to_checkpoint,
        )

        # 12. rename_file
        self.register_tool(
            name="rename_file",
            description="Renames or moves a file atomically within the workspace with boundary validation and conflict detection.",
            input_schema={
                "type": "object",
                "properties": {
                    "old_path": {"type": "string", "description": "Current relative path of the file"},
                    "new_path": {"type": "string", "description": "Target new relative path"},
                    "expected_version": {"type": "integer", "description": "Optional OCC expected version number"},
                    "expected_hash": {"type": "string", "description": "Optional OCC expected SHA-256 hash"},
                    "overwrite": {"type": "boolean", "description": "Whether to overwrite existing destination", "default": False},
                },
                "required": ["old_path", "new_path"],
            },
            handler=self.rename_file,
        )

        # 13. move_file
        self.register_tool(
            name="move_file",
            description="Moves a file into a destination directory within the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": "Current relative path of the file"},
                    "target_dir": {"type": "string", "description": "Destination directory relative path"},
                    "expected_version": {"type": "integer", "description": "Optional OCC expected version number"},
                    "expected_hash": {"type": "string", "description": "Optional OCC expected SHA-256 hash"},
                    "overwrite": {"type": "boolean", "description": "Whether to overwrite existing destination", "default": False},
                },
                "required": ["source_path", "target_dir"],
            },
            handler=self.move_file,
        )

        # 14. apply_patch
        self.register_tool(
            name="apply_patch",
            description="Applies a standard unified diff patch (diff -u or git format-patch) to workspace files with fuzz tolerance.",
            input_schema={
                "type": "object",
                "properties": {
                    "patch": {"type": "string", "description": "Complete unified diff patch text"},
                    "fuzz_factor": {"type": "integer", "description": "Fuzz tolerance for line offsets (default: 2)", "default": 2},
                },
                "required": ["patch"],
            },
            handler=self.apply_patch,
        )


    def read_file(self, path: Optional[str] = None, filepath: Optional[str] = None) -> Dict[str, Any]:
        target_path = filepath or path or ""
        target = self._resolve_path(target_path)
        rel_norm = str(target.relative_to(self.root_dir)).replace("\\", "/")
        denial = self._check_access(rel_norm, FileAccessMode.READ)
        if denial:
            return denial
        if not target.exists() or not target.is_file():
            return {"error": f"File '{target_path}' does not exist.", "success": False}
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"path": target_path, "content": content, "size_bytes": len(content), "success": True}

    def write_file(self, path: Optional[str] = None, content: str = "", filepath: Optional[str] = None) -> Dict[str, Any]:
        target_path = filepath or path or ""
        target = self._resolve_path(target_path)
        rel_norm = str(target.relative_to(self.root_dir)).replace("\\", "/")
        denial = self._check_access(rel_norm, FileAccessMode.WRITE)
        if denial:
            return denial
        before_content = target.read_text(encoding="utf-8", errors="replace") if target.exists() and target.is_file() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", errors="replace")
        self.change_journal.record_mutation(rel_norm, before_content, content)
        return {"path": target_path, "bytes_written": len(content), "status": "saved", "success": True}

    def list_directory(self, path: Optional[str] = None, recursive: bool = True, filepath: Optional[str] = None) -> Dict[str, Any]:
        target_path = filepath or path or ""
        target = self._resolve_path(target_path) if target_path else self.root_dir
        if not target.exists():
            return {"error": f"Directory '{target_path}' does not exist.", "success": False}

        results = []
        ignore_dirs = {"__pycache__", ".git", ".pytest_cache", "node_modules", ".venv", "venv"}

        iterator = target.rglob("*") if recursive else target.glob("*")
        for p in iterator:
            rel = p.relative_to(self.root_dir)
            rel_str = str(rel).replace("\\", "/")
            if any(part in ignore_dirs for part in rel.parts):
                continue
            if hasattr(self, "file_access_policy") and self.file_access_policy:
                if self.file_access_policy.is_blocked(rel_str):
                    continue
            results.append({
                "path": str(rel),
                "is_dir": p.is_dir(),
                "size_bytes": p.stat().st_size if p.is_file() else 0,
            })

        return {"root": str(self.root_dir), "entries": sorted(results, key=lambda x: x["path"]), "success": True}

    def delete_file(self, path: Optional[str] = None, filepath: Optional[str] = None) -> Dict[str, Any]:
        target_path = filepath or path or ""
        target = self._resolve_path(target_path)
        rel_norm = str(target.relative_to(self.root_dir)).replace("\\", "/")
        denial = self._check_access(rel_norm, FileAccessMode.DELETE)
        if denial:
            return denial
        if not target.exists():
            return {"error": f"Path '{target_path}' does not exist.", "success": False}
        before_content = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
        if target.is_file():
            target.unlink()
            self.change_journal.record_mutation(rel_norm, before_content, None)
        elif target.is_dir():
            shutil.rmtree(target)
        return {"path": target_path, "status": "deleted", "success": True}

    def get_file_info(self, path: Optional[str] = None, filepath: Optional[str] = None) -> Dict[str, Any]:
        target_path = filepath or path or ""
        target = self._resolve_path(target_path)
        rel_norm = str(target.relative_to(self.root_dir)).replace("\\", "/")
        denial = self._check_access(rel_norm, FileAccessMode.READ)
        if denial:
            return denial
        exists = target.exists()
        return {
            "path": target_path,
            "exists": exists,
            "is_file": target.is_file() if exists else False,
            "is_dir": target.is_dir() if exists else False,
            "size_bytes": target.stat().st_size if exists and target.is_file() else 0,
            "success": True,
        }

    def replace_file_content(
        self,
        target_content: str,
        replacement_content: str,
        path: Optional[str] = None,
        filepath: Optional[str] = None,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        allow_multiple: bool = False,
        fuzzy: bool = True,
    ) -> Dict[str, Any]:
        target_path = filepath or path or ""
        target = self._resolve_path(target_path)
        rel_norm = str(target.relative_to(self.root_dir)).replace("\\", "/")
        denial = self._check_access(rel_norm, FileAccessMode.WRITE)
        if denial:
            return denial
        if not target.exists() or not target.is_file():
            return {"error": f"File '{target_path}' does not exist.", "success": False}
        try:
            from ...tools.diff_editor import search_and_replace
            content = target.read_text(encoding="utf-8", errors="replace")
            new_content, count, diff = search_and_replace(
                content=content,
                search=target_content,
                replace=replacement_content,
                filepath=str(target_path),
                start_line=start_line,
                end_line=end_line,
                allow_multiple=allow_multiple,
                fuzzy=fuzzy,
            )
            target.write_text(new_content, encoding="utf-8", errors="replace")
            self.change_journal.record_mutation(rel_norm, content, new_content)
            return {
                "path": target_path,
                "occurrences_replaced": count,
                "diff": diff,
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def insert_lines(
        self,
        line_number: int,
        content: str,
        path: Optional[str] = None,
        filepath: Optional[str] = None,
        position: str = "after",
    ) -> Dict[str, Any]:
        target_path = filepath or path or ""
        target = self._resolve_path(target_path)
        rel_norm = str(target.relative_to(self.root_dir)).replace("\\", "/")
        denial = self._check_access(rel_norm, FileAccessMode.WRITE)
        if denial:
            return denial
        if not target.exists() or not target.is_file():
            return {"error": f"File '{target_path}' does not exist.", "success": False}
        try:
            from ...tools.diff_editor import insert_lines as do_insert
            current_content = target.read_text(encoding="utf-8", errors="replace")
            new_content, diff = do_insert(
                content=current_content,
                line_number=line_number,
                new_content=content,
                position=position,
                filepath=str(target_path),
            )
            target.write_text(new_content, encoding="utf-8", errors="replace")
            self.change_journal.record_mutation(rel_norm, current_content, new_content)
            return {
                "path": target_path,
                "diff": diff,
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def delete_lines(
        self,
        start_line: int,
        end_line: int,
        path: Optional[str] = None,
        filepath: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_path = filepath or path or ""
        target = self._resolve_path(target_path)
        rel_norm = str(target.relative_to(self.root_dir)).replace("\\", "/")
        denial = self._check_access(rel_norm, FileAccessMode.WRITE)
        if denial:
            return denial
        if not target.exists() or not target.is_file():
            return {"error": f"File '{target_path}' does not exist.", "success": False}
        try:
            from ...tools.diff_editor import delete_lines as do_delete
            current_content = target.read_text(encoding="utf-8", errors="replace")
            new_content, diff = do_delete(
                content=current_content,
                start_line=start_line,
                end_line=end_line,
                filepath=str(target_path),
            )
            target.write_text(new_content, encoding="utf-8", errors="replace")
            self.change_journal.record_mutation(rel_norm, current_content, new_content)
            return {
                "path": target_path,
                "diff": diff,
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def apply_diff_blocks(
        self,
        diff_blocks: str,
        path: Optional[str] = None,
        filepath: Optional[str] = None,
        fuzzy: bool = True,
    ) -> Dict[str, Any]:
        target_path = filepath or path or ""
        target = self._resolve_path(target_path)
        rel_norm = str(target.relative_to(self.root_dir)).replace("\\", "/")
        denial = self._check_access(rel_norm, FileAccessMode.WRITE)
        if denial:
            return denial
        if not target.exists() or not target.is_file():
            return {"error": f"File '{target_path}' does not exist.", "success": False}
        try:
            from ...tools.diff_editor import apply_diff_blocks as do_apply_blocks
            current_content = target.read_text(encoding="utf-8", errors="replace")
            new_content, count, diff = do_apply_blocks(
                content=current_content,
                diff_blocks=diff_blocks,
                filepath=str(target_path),
                fuzzy=fuzzy,
            )
            target.write_text(new_content, encoding="utf-8", errors="replace")
            self.change_journal.record_mutation(rel_norm, current_content, new_content)
            return {
                "path": target_path,
                "blocks_applied": count,
                "diff": diff,
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def get_workspace_changes(self, path: Optional[str] = None, filepath: Optional[str] = None) -> Dict[str, Any]:
        target_path = filepath or path
        manifest = self.change_journal.get_manifest()
        m_dict = manifest.to_dict()
        if target_path:
            norm = target_path.replace("\\", "/").strip("/")
            file_rec = m_dict.get("file_changes", {}).get(norm)
            return {"filepath": target_path, "change": file_rec, "found": bool(file_rec), "success": True}
        return {"manifest": m_dict, "success": True}

    def rollback_to_checkpoint(self, checkpoint_id: Optional[str] = None, reason: str = "") -> Dict[str, Any]:
        from ...persistence.checkpoint_manager import WorkspaceCheckpointManager
        manager = WorkspaceCheckpointManager(str(self.root_dir))
        target_id = checkpoint_id
        if not target_id:
            snaps = manager.list_snapshots()
            if not snaps:
                return {"error": "No snapshot checkpoints found to roll back to.", "success": False}
            target_id = snaps[-1]
        try:
            from ...tools.workspace import WorkspaceManager
            ws = WorkspaceManager(str(self.root_dir))
            res = manager.rollback_to_checkpoint(target_id, ws)
            self.change_journal.clear()
            res["reason"] = reason
            return res
        except Exception as e:
            return {"error": str(e), "checkpoint_id": target_id, "success": False}

    def rename_file(
        self,
        old_path: Optional[str] = None,
        new_path: Optional[str] = None,
        expected_version: Optional[int] = None,
        expected_hash: Optional[str] = None,
        overwrite: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        target_old = old_path or kwargs.get("source_path") or ""
        target_new = new_path or kwargs.get("destination_path") or ""
        if not target_old or not target_new:
            return {"error": "Both old_path and new_path must be provided.", "success": False}
        try:
            from ...tools.workspace import WorkspaceManager
            ws = WorkspaceManager(str(self.root_dir), file_access_policy=self.file_access_policy)
            ws.rename_file(
                old_path=target_old,
                new_path=target_new,
                expected_version=expected_version,
                expected_hash=expected_hash,
                overwrite=overwrite,
            )
            self.change_journal.record_mutation(
                target_new.replace("\\", "/").strip("/"),
                None,
                ws.read_file(target_new) if (ws.root_dir / target_new).exists() else None,
            )
            return {
                "old_path": target_old,
                "new_path": target_new,
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def move_file(
        self,
        source_path: Optional[str] = None,
        target_dir: Optional[str] = None,
        expected_version: Optional[int] = None,
        expected_hash: Optional[str] = None,
        overwrite: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        target_src = source_path or kwargs.get("old_path") or ""
        target_dst_dir = target_dir or kwargs.get("destination_dir") or ""
        if not target_src or target_dst_dir is None:
            return {"error": "Both source_path and target_dir must be provided.", "success": False}
        try:
            from ...tools.workspace import WorkspaceManager
            ws = WorkspaceManager(str(self.root_dir), file_access_policy=self.file_access_policy)
            new_rel = ws.move_file(
                source_path=target_src,
                target_dir=target_dst_dir,
                expected_version=expected_version,
                expected_hash=expected_hash,
                overwrite=overwrite,
            )
            return {
                "old_path": target_src,
                "new_path": new_rel,
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def apply_patch(
        self,
        patch: Optional[str] = None,
        patch_content: Optional[str] = None,
        fuzz_factor: int = 2,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        patch_text = patch or patch_content or kwargs.get("content") or ""
        if not patch_text.strip():
            return {"error": "Patch content cannot be empty.", "success": False}
        try:
            from ...tools.workspace import WorkspaceManager
            ws = WorkspaceManager(str(self.root_dir), file_access_policy=self.file_access_policy)
            res = ws.apply_patch(patch_content=patch_text, fuzz_factor=fuzz_factor)
            return res
        except Exception as e:
            return {"error": str(e), "success": False}


