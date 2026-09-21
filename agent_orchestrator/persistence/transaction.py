"""
WorkspaceTransaction & TransactionManager: Atomic Workspace State Boundaries and Rollback Controller.
Enables transactional execution where groups of file modifications across tasks or iterations
can be committed together or cleanly rolled back to a baseline checkpoint upon failure or regression.
"""
from datetime import datetime
import os
from typing import Any, Dict, List, Optional, Union

from .checkpoint_manager import WorkspaceCheckpointManager
from ..tools.workspace import WorkspaceManager


class TransactionError(Exception):
    """Raised when transaction lifecycle operations fail."""
    pass


class WorkspaceTransaction:
    """
    Represents an atomic workspace transaction.
    Upon begin(), captures an immutable snapshot checkpoint of the workspace.
    Upon commit(), finalizes changes.
    Upon rollback(), restores all files to the baseline snapshot and deletes newly created files.
    """

    def __init__(
        self,
        workspace: WorkspaceManager,
        checkpoint_manager: WorkspaceCheckpointManager,
        name: str,
        scope: str = "task",
        metadata: Optional[Dict[str, Any]] = None,
        git_server: Optional[Any] = None,
    ):
        self.workspace = workspace
        self.checkpoint_manager = checkpoint_manager
        self.name = name
        self.scope = scope
        self.metadata = metadata or {}
        self.git_server = git_server

        clean_name = name.replace(":", "_").replace("/", "_").replace("\\", "_")
        clean_scope = scope.replace(":", "_").replace("/", "_").replace("\\", "_")
        self.checkpoint_id = f"tx-{clean_scope}-{clean_name}-baseline"

        self.is_active = False
        self.is_committed = False
        self.is_rolled_back = False
        self.started_at: Optional[str] = None
        self.ended_at: Optional[str] = None
        self.rollback_result: Optional[Dict[str, Any]] = None
        self.operations: List[Any] = []

    @property
    def tx_id(self) -> str:
        return self.checkpoint_id

    def record_operation(self, op: Any) -> None:
        """Records an operation executed under this transaction."""
        if hasattr(op, "transaction_id"):
            op.transaction_id = self.tx_id
        self.operations.append(op)
        try:
            from ..runtime.idempotency import global_operation_ledger
            global_operation_ledger.record(op)
        except Exception:
            pass

    def begin(self) -> str:
        """Captures baseline snapshot and begins the transaction."""
        if self.is_active:
            raise TransactionError(f"Transaction '{self.name}' is already active.")
        if self.is_committed or self.is_rolled_back:
            raise TransactionError(f"Transaction '{self.name}' has already finished.")

        self.started_at = datetime.now().isoformat()
        meta = dict(self.metadata)
        meta.update({
            "transaction_name": self.name,
            "scope": self.scope,
            "stage": "TRANSACTION_BASELINE",
            "started_at": self.started_at,
        })

        self.checkpoint_manager.create_snapshot(
            checkpoint_id=self.checkpoint_id,
            workspace=self.workspace,
            stage="TRANSACTION_BASELINE",
            metadata=meta,
        )
        self.is_active = True
        return self.checkpoint_id

    def commit(self) -> Dict[str, Any]:
        """Finalizes the transaction and returns a change manifest."""
        if not self.is_active:
            raise TransactionError(f"Cannot commit inactive transaction '{self.name}'.")

        self.ended_at = datetime.now().isoformat()
        self.is_active = False
        self.is_committed = True

        for op in self.operations:
            if getattr(op, "status", None) != "NO_OP":
                op.status = "COMMITTED"

        # Compute changes relative to baseline snapshot
        manifest = None
        try:
            manifest = self.checkpoint_manager.get_checkpoint_manifest(self.checkpoint_id, self.workspace)
        except Exception:
            pass

        manifest_dict = manifest.to_dict() if manifest and hasattr(manifest, "to_dict") else {}
        return {
            "status": "COMMITTED",
            "transaction_name": self.name,
            "checkpoint_id": self.checkpoint_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "manifest": manifest_dict,
            "operations_count": len(self.operations),
            "success": True,
        }

    def rollback(self, reason: str = "") -> Dict[str, Any]:
        """
        Reverts the workspace to the exact state captured at transaction begin.
        Restores modified/deleted files, deletes newly added files, and resets workspace journal.
        """
        if not self.is_active and not (self.is_committed and reason == "FORCED_ROLLBACK"):
            if self.is_rolled_back:
                return self.rollback_result or {"status": "ALREADY_ROLLED_BACK", "success": True}
            raise TransactionError(f"Cannot rollback inactive transaction '{self.name}'.")

        self.ended_at = datetime.now().isoformat()
        self.is_active = False
        self.is_rolled_back = True

        for op in self.operations:
            if hasattr(op, "status"):
                op.status = "ROLLED_BACK"

        # Perform physical restoration
        res = self.checkpoint_manager.rollback_to_checkpoint(self.checkpoint_id, self.workspace)

        # Clear workspace in-memory change journal
        if hasattr(self.workspace, "clear_change_manifest"):
            self.workspace.clear_change_manifest()

        self.rollback_result = {
            "status": "ROLLED_BACK",
            "transaction_name": self.name,
            "checkpoint_id": self.checkpoint_id,
            "reason": reason,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "restored_files": res.get("restored_files", []),
            "deleted_files": res.get("deleted_files", []),
            "success": True,
        }
        return self.rollback_result

    def get_changes(self) -> Any:
        """Returns the ChangeManifest of changes made during this active transaction."""
        return self.checkpoint_manager.get_checkpoint_manifest(self.checkpoint_id, self.workspace)

    def __enter__(self):
        self.begin()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            if self.is_active:
                self.rollback(reason=f"Exception raised: {str(exc_val)}")
            return False  # Propagate exception
        return True


class WorkspaceTransactionManager:
    """
    Coordinates workspace transactions across agent tasks and workflow iterations.
    """

    def __init__(
        self,
        workspace: WorkspaceManager,
        checkpoint_manager: WorkspaceCheckpointManager,
        git_server: Optional[Any] = None,
    ):
        self.workspace = workspace
        self.checkpoint_manager = checkpoint_manager
        self.git_server = git_server
        self._active_transactions: Dict[str, WorkspaceTransaction] = {}
        self._transaction_history: List[Dict[str, Any]] = []

    def begin_transaction(
        self,
        name: str,
        scope: str = "task",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WorkspaceTransaction:
        """Starts a new named transaction."""
        tx_key = f"{scope}:{name}"
        if tx_key in self._active_transactions and self._active_transactions[tx_key].is_active:
            return self._active_transactions[tx_key]

        tx = WorkspaceTransaction(
            workspace=self.workspace,
            checkpoint_manager=self.checkpoint_manager,
            name=name,
            scope=scope,
            metadata=metadata,
            git_server=self.git_server,
        )
        tx.begin()
        self._active_transactions[tx_key] = tx
        return tx

    def commit_transaction(self, name: str, scope: str = "task") -> Dict[str, Any]:
        """Commits an active transaction."""
        tx_key = f"{scope}:{name}"
        tx = self._active_transactions.get(tx_key)
        if not tx:
            raise TransactionError(f"No active transaction found for '{tx_key}'.")
        result = tx.commit()
        self._transaction_history.append(result)
        del self._active_transactions[tx_key]
        return result

    def rollback_transaction(self, name: str, scope: str = "task", reason: str = "") -> Dict[str, Any]:
        """Rolls back an active transaction."""
        tx_key = f"{scope}:{name}"
        tx = self._active_transactions.get(tx_key)
        if not tx:
            # Fallback: check if checkpoint exists directly in checkpoint manager
            clean_name = name.replace(":", "_").replace("/", "_").replace("\\", "_")
            clean_scope = scope.replace(":", "_").replace("/", "_").replace("\\", "_")
            fallback_ckpt = f"tx-{clean_scope}-{clean_name}-baseline"
            if fallback_ckpt in self.checkpoint_manager.list_snapshots():
                res = self.checkpoint_manager.rollback_to_checkpoint(fallback_ckpt, self.workspace)
                if hasattr(self.workspace, "clear_change_manifest"):
                    self.workspace.clear_change_manifest()
                return {
                    "status": "ROLLED_BACK",
                    "transaction_name": name,
                    "checkpoint_id": fallback_ckpt,
                    "reason": reason,
                    "restored_files": res.get("restored_files", []),
                    "deleted_files": res.get("deleted_files", []),
                    "success": True,
                }
            raise TransactionError(f"No active transaction or snapshot found for '{tx_key}'.")

        result = tx.rollback(reason=reason)
        self._transaction_history.append(result)
        del self._active_transactions[tx_key]
        return result

    def transaction(
        self,
        name: str,
        scope: str = "task",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WorkspaceTransaction:
        """Context manager factory for transactions."""
        return WorkspaceTransaction(
            workspace=self.workspace,
            checkpoint_manager=self.checkpoint_manager,
            name=name,
            scope=scope,
            metadata=metadata,
            git_server=self.git_server,
        )

    def get_history(self) -> List[Dict[str, Any]]:
        return list(self._transaction_history)
