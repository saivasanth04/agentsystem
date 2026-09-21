"""
Cancellation Token and Source: Thread-safe cooperative and preemptive cancellation infrastructure.
Allows tasks, agent loops, workflows, and sandboxed subprocesses to be aborted deterministically.
"""
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable, Dict, List, Optional


class TaskCancelledError(Exception):
    """Raised when an active task or agent loop is interrupted by a cancellation signal."""
    def __init__(self, message: str = "Task was cancelled", reason: Optional[str] = None):
        super().__init__(message)
        self.reason = reason or message


@dataclass
class CancellationToken:
    """
    Read-only cancellation token passed down to workers, loops, tools, and sandboxes.
    """
    _source: "CancellationSource" = field(repr=False)

    @property
    def is_cancelled(self) -> bool:
        return self._source.is_cancelled

    @property
    def cancel_reason(self) -> Optional[str]:
        return self._source.cancel_reason

    @property
    def cancelled_at(self) -> Optional[float]:
        return self._source.cancelled_at

    def register_callback(self, callback: Callable[[str], None]) -> None:
        """Registers a callback to be invoked immediately upon cancellation."""
        self._source.register_callback(callback)

    def throw_if_cancelled(self) -> None:
        """Raises TaskCancelledError if cancellation has been requested."""
        if self.is_cancelled:
            raise TaskCancelledError(
                message=f"Operation cancelled: {self.cancel_reason}",
                reason=self.cancel_reason,
            )


class CancellationSource:
    """
    Controls and triggers cancellation signals across an execution hierarchy.
    """
    def __init__(self, parent: Optional["CancellationToken"] = None):
        self._is_cancelled = False
        self._cancel_reason: Optional[str] = None
        self._cancelled_at: Optional[float] = None
        self._callbacks: List[Callable[[str], None]] = []
        self._lock = threading.RLock()
        self._token = CancellationToken(_source=self)

        # Link to parent token if present
        if parent:
            parent.register_callback(self._on_parent_cancelled)

    def _on_parent_cancelled(self, reason: str) -> None:
        self.cancel(reason=f"Cascaded from parent: {reason}")

    @property
    def token(self) -> CancellationToken:
        return self._token

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._is_cancelled

    @property
    def cancel_reason(self) -> Optional[str]:
        with self._lock:
            return self._cancel_reason

    @property
    def cancelled_at(self) -> Optional[float]:
        with self._lock:
            return self._cancelled_at

    def cancel(self, reason: str = "Execution stopped by user") -> bool:
        """
        Triggers cancellation and invokes all registered callbacks safely.
        Returns True if this call triggered cancellation, False if already cancelled.
        """
        callbacks_to_invoke = []
        with self._lock:
            if self._is_cancelled:
                return False
            self._is_cancelled = True
            self._cancel_reason = reason
            self._cancelled_at = time.time()
            callbacks_to_invoke = list(self._callbacks)

        for cb in callbacks_to_invoke:
            try:
                cb(reason)
            except Exception:
                pass
        return True

    def register_callback(self, callback: Callable[[str], None]) -> None:
        """Registers a callback. If already cancelled, callback executes immediately."""
        invoke_now = False
        reason = ""
        with self._lock:
            if self._is_cancelled:
                invoke_now = True
                reason = self._cancel_reason or "Cancelled"
            else:
                self._callbacks.append(callback)

        if invoke_now:
            try:
                callback(reason)
            except Exception:
                pass

    def create_child_token(self) -> CancellationToken:
        """Creates a child token linked to this cancellation source."""
        child_source = CancellationSource(parent=self.token)
        return child_source.token
