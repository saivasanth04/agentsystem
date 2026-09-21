"""
Idempotency, Operation Identity, and Concurrency Control Engine.
Provides deterministic operation hashing, deduplication ledger, and Optimistic Concurrency Control (OCC).
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import threading
from typing import Any, Dict, List, Optional, Union


class ConcurrencyConflictError(Exception):
    """Raised when an operation's expected_hash mismatches the actual target file hash (Compare-And-Swap failure)."""
    pass


@dataclass
class OperationRecord:
    operation_id: str
    tool_name: str
    filepath: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)
    before_hash: Optional[str] = None
    after_hash: Optional[str] = None
    result: Dict[str, Any] = field(default_factory=dict)
    status: str = "COMMITTED"  # COMMITTED | NO_OP | FAILED | CONFLICT | ROLLED_BACK
    no_op: bool = False
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "tool_name": self.tool_name,
            "filepath": self.filepath,
            "arguments": self.arguments,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "result": self.result,
            "status": self.status,
            "no_op": self.no_op,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OperationRecord":
        return cls(
            operation_id=data.get("operation_id", ""),
            tool_name=data.get("tool_name", ""),
            filepath=data.get("filepath"),
            arguments=data.get("arguments") or {},
            before_hash=data.get("before_hash"),
            after_hash=data.get("after_hash"),
            result=data.get("result") or {},
            status=data.get("status", "COMMITTED"),
            no_op=bool(data.get("no_op", False)),
            task_id=data.get("task_id"),
            session_id=data.get("session_id"),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )


def compute_operation_id(
    tool_name_or_scope: str,
    arguments_or_tool_name: Union[Dict[str, Any], str],
    arguments: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """
    Computes a deterministic, collision-resistant operation fingerprint
    based on tool name, arguments, task_id, and session_id.
    Accepts either (tool_name, arguments, task_id, session_id) or (task_id, tool_name, arguments).
    """
    if isinstance(arguments_or_tool_name, str):
        # Called as (task_id/scope, tool_name, arguments)
        t_id = task_id or tool_name_or_scope
        tool_name = arguments_or_tool_name
        args = arguments or {}
    else:
        # Called as (tool_name, arguments, task_id, session_id)
        tool_name = tool_name_or_scope
        args = arguments_or_tool_name
        t_id = task_id

    normalized_args = {k: v for k, v in args.items() if k not in ("operation_id", "idempotency_key")}
    raw_payload = json.dumps(
        {
            "tool": tool_name,
            "args": normalized_args,
            "task_id": t_id,
            "session_id": session_id,
        },
        sort_keys=True,
        default=str,
    )
    chash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
    return f"op-{chash[:16]}"


class OperationLedger:
    """
    Thread-safe ledger tracking executed mutating operations.
    Supports idempotency token verification, result caching, and operation history.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._records: Dict[str, OperationRecord] = {}
        self._file_history: Dict[str, List[str]] = {}

    def has_executed(self, operation_id: str) -> bool:
        """Returns True if operation_id was already executed successfully."""
        with self._lock:
            rec = self._records.get(operation_id)
            return rec is not None and rec.status in ("COMMITTED", "NO_OP")

    def get_record(self, operation_id: str) -> Optional[OperationRecord]:
        """Retrieves OperationRecord for a given operation_id."""
        with self._lock:
            return self._records.get(operation_id)

    def get(self, operation_id: str) -> Optional[OperationRecord]:
        """Alias for get_record."""
        return self.get_record(operation_id)

    def record(self, record: OperationRecord) -> None:
        """Records an executed operation in the ledger."""
        with self._lock:
            self._records[record.operation_id] = record
            if record.filepath:
                norm_fp = record.filepath.replace("\\", "/").strip("/")
                if norm_fp not in self._file_history:
                    self._file_history[norm_fp] = []
                self._file_history[norm_fp].append(record.operation_id)

    def get_file_history(self, filepath: str) -> List[OperationRecord]:
        """Returns all operations affecting a specific relative file path."""
        norm_fp = filepath.replace("\\", "/").strip("/")
        with self._lock:
            op_ids = self._file_history.get(norm_fp, [])
            return [self._records[oid] for oid in op_ids if oid in self._records]

    def clear(self) -> None:
        """Clears all records from the ledger."""
        with self._lock:
            self._records.clear()
            self._file_history.clear()


# Global singleton instance for runtime deduplication
global_operation_ledger = OperationLedger()
