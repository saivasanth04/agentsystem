"""
Thread-safe and Async-safe Context Management for Structured Logging.
Uses Python standard library contextvars to propagate execution metadata
(session_id, task_id, agent_name, correlation_id) across execution threads.
"""
from contextvars import ContextVar
from typing import Any, Dict, Optional


_session_id_var: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
_task_id_var: ContextVar[Optional[str]] = ContextVar("task_id", default=None)
_agent_name_var: ContextVar[Optional[str]] = ContextVar("agent_name", default=None)
_correlation_id_var: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)
_trace_id_var: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)
_span_id_var: ContextVar[Optional[str]] = ContextVar("span_id", default=None)


def set_log_context(
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    correlation_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
) -> None:
    """Sets contextual fields for the current execution context."""
    if session_id is not None:
        _session_id_var.set(session_id)
    if task_id is not None:
        _task_id_var.set(task_id)
    if agent_name is not None:
        _agent_name_var.set(agent_name)
    if correlation_id is not None:
        _correlation_id_var.set(correlation_id)
    if trace_id is not None:
        _trace_id_var.set(trace_id)
    if span_id is not None:
        _span_id_var.set(span_id)


def get_log_context() -> Dict[str, Any]:
    """Returns a dictionary of currently active non-empty contextual fields."""
    ctx: Dict[str, Any] = {}
    sid = _session_id_var.get()
    if sid is not None:
        ctx["session_id"] = sid
    tid = _task_id_var.get()
    if tid is not None:
        ctx["task_id"] = tid
    aname = _agent_name_var.get()
    if aname is not None:
        ctx["agent_name"] = aname
    cid = _correlation_id_var.get()
    if cid is not None:
        ctx["correlation_id"] = cid

    # Trace and span ID with fallback to active tracing context
    tr_id = _trace_id_var.get()
    sp_id = _span_id_var.get()
    if tr_id is None or sp_id is None:
        try:
            from ..tracing import get_current_trace_id, get_current_span_id
            if tr_id is None:
                tr_id = get_current_trace_id()
            if sp_id is None:
                sp_id = get_current_span_id()
        except Exception:
            pass

    if tr_id is not None:
        ctx["trace_id"] = tr_id
    if sp_id is not None:
        ctx["span_id"] = sp_id

    return ctx


def clear_log_context() -> None:
    """Clears all contextual fields in the current execution context."""
    _session_id_var.set(None)
    _task_id_var.set(None)
    _agent_name_var.set(None)
    _correlation_id_var.set(None)
    _trace_id_var.set(None)
    _span_id_var.set(None)


class LogContext:
    """
    Context manager for scoping contextual logging metadata.
    Restores previous context state on exit using ContextVar tokens.
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        correlation_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
    ):
        self.session_id = session_id
        self.task_id = task_id
        self.agent_name = agent_name
        self.correlation_id = correlation_id
        self.trace_id = trace_id
        self.span_id = span_id
        self._tokens: list = []

    def __enter__(self) -> "LogContext":
        if self.session_id is not None:
            self._tokens.append((_session_id_var, _session_id_var.set(self.session_id)))
        if self.task_id is not None:
            self._tokens.append((_task_id_var, _task_id_var.set(self.task_id)))
        if self.agent_name is not None:
            self._tokens.append((_agent_name_var, _agent_name_var.set(self.agent_name)))
        if self.correlation_id is not None:
            self._tokens.append((_correlation_id_var, _correlation_id_var.set(self.correlation_id)))
        if self.trace_id is not None:
            self._tokens.append((_trace_id_var, _trace_id_var.set(self.trace_id)))
        if self.span_id is not None:
            self._tokens.append((_span_id_var, _span_id_var.set(self.span_id)))
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        for var, token in reversed(self._tokens):
            var.reset(token)
        self._tokens.clear()
        return False
