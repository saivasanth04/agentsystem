"""
WorkspaceCheckpointManager: Physical File Snapshotting, Provenance Tracking, and Workspace Rollback Engine.
Provides transactional snapshots of workspace state and clean rollback upon task failure.
"""
from datetime import datetime
import hashlib
import os
import shutil
from typing import Any, Dict, List, Optional

from ..runtime.task_graph import CheckpointRecord
from ..tools.workspace import WorkspaceManager


class WorkspaceCheckpointManager:
    """
    Manages physical file snapshots in `.orchestrator/snapshots/<checkpoint_id>/`
    and executes transactional rollbacks when agent actions fail or corrupt code.
    """

    def __init__(self, workspace_dir: str, artifact_store: Optional[Any] = None):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.artifact_store = artifact_store
        self.snapshots_dir = os.path.join(self.workspace_dir, ".orchestrator", "snapshots")
        os.makedirs(self.snapshots_dir, exist_ok=True)

    def _get_snapshot_path(self, checkpoint_id: str) -> str:
        clean_id = checkpoint_id.replace(":", "_").replace("/", "_").replace("\\", "_")
        return os.path.join(self.snapshots_dir, clean_id)

    def _is_ignored(self, rel_path: str) -> bool:
        norm = rel_path.replace("\\", "/").strip("/")
        ignored_patterns = [
            ".orchestrator",
            ".sandboxes",
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".venv",
            "node_modules",
            ".DS_Store",
        ]
        return any(norm == p or norm.startswith(p + "/") or ("/" + p + "/") in norm for p in ignored_patterns)

    def create_snapshot(
        self,
        checkpoint_id: str,
        workspace: Optional[WorkspaceManager] = None,
        stage: str = "PRE_EXECUTION",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CheckpointRecord:
        """
        Creates a physical copy snapshot of all non-ignored workspace files
        and computes their SHA-256 integrity hashes.
        """
        target_ws = workspace or WorkspaceManager(self.workspace_dir)
        snapshot_dest = self._get_snapshot_path(checkpoint_id)
        os.makedirs(snapshot_dest, exist_ok=True)

        file_hashes: Dict[str, str] = {}
        all_files = target_ws.list_files()

        for rel_file in all_files:
            if self._is_ignored(rel_file):
                continue
            src_path = os.path.join(target_ws.root_dir, rel_file)
            if not os.path.isfile(src_path):
                continue

            # Read content & compute hash
            try:
                with open(src_path, "rb") as f:
                    content_bytes = f.read()
                file_hash = hashlib.sha256(content_bytes).hexdigest()
                file_hashes[rel_file] = file_hash

                # Copy to snapshot dir
                dest_file_path = os.path.join(snapshot_dest, rel_file)
                os.makedirs(os.path.dirname(dest_file_path), exist_ok=True)
                with open(dest_file_path, "wb") as df:
                    df.write(content_bytes)
            except Exception:
                continue

        if self.artifact_store and hasattr(self.artifact_store, "put"):
            try:
                snap_meta = {
                    "checkpoint_id": checkpoint_id,
                    "stage": stage,
                    "file_count": len(file_hashes),
                    "snapshot_dir": snapshot_dest,
                    **(metadata or {}),
                }
                self.artifact_store.put(
                    category="snapshots",
                    name=f"snapshot_{checkpoint_id}.json",
                    content={
                        "checkpoint_id": checkpoint_id,
                        "stage": stage,
                        "file_hashes": file_hashes,
                        "metadata": snap_meta,
                    },
                    session_id=(metadata or {}).get("session_id"),
                    task_id=(metadata or {}).get("task_id"),
                    artifact_type="WORKSPACE_SNAPSHOT",
                    metadata=snap_meta,
                )
            except Exception:
                pass

        return CheckpointRecord(
            checkpoint_id=checkpoint_id,
            stage=stage,
            file_hashes=file_hashes,
            timestamp=datetime.now().isoformat(),
        )

    def rollback_to_checkpoint(
        self,
        checkpoint_id: str,
        workspace: Optional[WorkspaceManager] = None,
    ) -> Dict[str, Any]:
        """
        Rolls back the workspace to the exact state captured at checkpoint_id.
        Restores modified/deleted files and deletes newly created files that weren't present in snapshot.
        """
        target_ws = workspace or WorkspaceManager(self.workspace_dir)
        snapshot_src = self._get_snapshot_path(checkpoint_id)

        if not os.path.isdir(snapshot_src):
            raise FileNotFoundError(f"Checkpoint snapshot '{checkpoint_id}' not found at {snapshot_src}")

        # Gather snapshot files
        snapshot_files: set = set()
        for root, _, files in os.walk(snapshot_src):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, snapshot_src).replace("\\", "/")
                if not self._is_ignored(rel_path):
                    snapshot_files.add(rel_path)

        # Gather current workspace files
        current_workspace_files: set = set()
        for root, _, files in os.walk(target_ws.root_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, target_ws.root_dir).replace("\\", "/")
                if not self._is_ignored(rel_path):
                    current_workspace_files.add(rel_path)

        restored_files = []
        deleted_files = []

        # 1. Delete files created after snapshot
        files_to_delete = current_workspace_files - snapshot_files
        for rel_f in files_to_delete:
            ws_path = os.path.join(target_ws.root_dir, rel_f)
            try:
                if os.path.isfile(ws_path):
                    os.remove(ws_path)
                    deleted_files.append(rel_f)
            except Exception:
                pass

        # 2. Restore all snapshot files into workspace
        for rel_f in snapshot_files:
            snap_file = os.path.join(snapshot_src, rel_f)
            ws_file = os.path.join(target_ws.root_dir, rel_f)
            os.makedirs(os.path.dirname(ws_file), exist_ok=True)
            shutil.copy2(snap_file, ws_file)
            restored_files.append(rel_f)

        return {
            "checkpoint_id": checkpoint_id,
            "restored_files": sorted(restored_files),
            "deleted_files": sorted(deleted_files),
            "status": "ROLLBACK_SUCCESS",
            "success": True,
        }

    def list_snapshots(self) -> List[str]:
        """Lists all stored snapshot checkpoint IDs."""
        if not os.path.isdir(self.snapshots_dir):
            return []
        return [d for d in os.listdir(self.snapshots_dir) if os.path.isdir(os.path.join(self.snapshots_dir, d))]

    def delete_snapshot(self, checkpoint_id: str):
        """Deletes a physical snapshot directory."""
        snapshot_path = self._get_snapshot_path(checkpoint_id)
        if os.path.isdir(snapshot_path):
            shutil.rmtree(snapshot_path, ignore_errors=True)

    def diff_snapshots(self, checkpoint_id_a: str, checkpoint_id_b: str) -> Any:
        """
        Computes a complete ChangeManifest between two snapshot checkpoints.
        """
        from ..tools.change_tracker import diff_directories
        path_a = self._get_snapshot_path(checkpoint_id_a)
        path_b = self._get_snapshot_path(checkpoint_id_b)
        if not os.path.isdir(path_a):
            raise FileNotFoundError(f"Checkpoint '{checkpoint_id_a}' not found.")
        if not os.path.isdir(path_b):
            raise FileNotFoundError(f"Checkpoint '{checkpoint_id_b}' not found.")
        return diff_directories(
            before_dir=path_a,
            after_dir=path_b,
            session_id=f"{checkpoint_id_a}_to_{checkpoint_id_b}",
        )

    def get_checkpoint_manifest(
        self,
        checkpoint_id: str,
        workspace: Optional[WorkspaceManager] = None,
    ) -> Any:
        """
        Computes a ChangeManifest comparing a snapshot checkpoint against current workspace state.
        """
        from ..tools.change_tracker import diff_directories
        target_ws = workspace or WorkspaceManager(self.workspace_dir)
        snap_path = self._get_snapshot_path(checkpoint_id)
        if not os.path.isdir(snap_path):
            raise FileNotFoundError(f"Checkpoint '{checkpoint_id}' not found.")
        return diff_directories(
            before_dir=snap_path,
            after_dir=target_ws.root_dir,
            session_id=checkpoint_id,
        )
