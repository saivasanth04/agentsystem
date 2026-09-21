"""
Distributed Tracer Engine with Context Propagation and Hierarchical Spans.
Manages trace lifecycles, thread/async-safe context propagation via contextvars,
and span nesting across tasks, agents, tools, LLM calls, and verifications.
"""
from contextlib import contextmanager
from contextvars import ContextVar, Token
from functools import wraps
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Optional
import uuid

from .span import Span, SpanType, SpanStatus, TraceTree

# Thread-safe and async-safe ContextVars for active trace and span
_current_trace_id_var: ContextVar[Optional[str]] = ContextVar("current_trace_id", default=None)
_current_span_var: ContextVar[Optional[Span]] = ContextVar("current_span", default=None)


def get_current_trace_id() -> Optional[str]:
    """Returns the currently active trace_id in the execution context."""
    return _current_trace_id_var.get()


def get_current_span() -> Optional[Span]:
    """Returns the currently active Span in the execution context."""
    return _current_span_var.get()


def get_current_span_id() -> Optional[str]:
    """Returns the active span_id if a span is active."""
    span = _current_span_var.get()
    return span.span_id if span else None


class SpanScope:
    """
    Context manager for scoping an active Span.
    Handles start, parent-child linking, contextvar setting, and exit finalization.
    """

    def __init__(self, tracer: "Tracer", span: Span):
        self.tracer = tracer
        self.span = span
        self._span_token: Optional[Token] = None
        self._trace_token: Optional[Token] = None

    def __enter__(self) -> Span:
        self._span_token = _current_span_var.set(self.span)
        self._trace_token = _current_trace_id_var.set(self.span.trace_id)
        return self.span

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if exc_type is not None:
            self.span.set_status(SpanStatus.ERROR, str(exc_val))
            self.span.add_event("exception", {"type": exc_type.__name__, "message": str(exc_val)})
        else:
            if self.span.status == SpanStatus.UNSET.value:
                self.span.set_status(SpanStatus.OK)

        self.span.end()

        if self._span_token is not None:
            _current_span_var.reset(self._span_token)
        if self._trace_token is not None:
            _current_trace_id_var.reset(self._trace_token)
        return False


class Tracer:
    """
    Tracer instance responsible for creating spans and managing TraceTree instances.
    """

    def __init__(self, name: str = "default"):
        self.name = name
        self._traces: Dict[str, TraceTree] = {}
        self._lock = threading.RLock()

    def start_trace(self, trace_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Starts a new trace and registers it in the tracer."""
        tid = trace_id or uuid.uuid4().hex
        with self._lock:
            if tid not in self._traces:
                self._traces[tid] = TraceTree(trace_id=tid, metadata=metadata or {})
        _current_trace_id_var.set(tid)
        return tid

    def get_or_create_trace(self, trace_id: str) -> TraceTree:
        """Retrieves an existing TraceTree or creates a new one."""
        with self._lock:
            if trace_id not in self._traces:
                self._traces[trace_id] = TraceTree(trace_id=trace_id)
            return self._traces[trace_id]

    def get_trace(self, trace_id: str) -> Optional[TraceTree]:
        """Returns TraceTree for trace_id if exists."""
        with self._lock:
            return self._traces.get(trace_id)

    def list_traces(self) -> List[str]:
        """Returns all registered trace IDs."""
        with self._lock:
            return list(self._traces.keys())

    def start_span(
        self,
        name: str,
        span_type: SpanType,
        attributes: Optional[Dict[str, Any]] = None,
        parent_span_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Span:
        """
        Creates and registers a new span.
        If parent_span_id is not specified, uses the current active span as parent.
        """
        active_span = get_current_span()
        resolved_trace_id = trace_id or get_current_trace_id() or (active_span.trace_id if active_span else uuid.uuid4().hex)
        resolved_parent_id = parent_span_id if parent_span_id is not None else (active_span.span_id if active_span else None)

        span = Span(
            name=name,
            span_type=span_type,
            trace_id=resolved_trace_id,
            parent_span_id=resolved_parent_id,
            attributes=attributes or {},
        )

        trace_tree = self.get_or_create_trace(resolved_trace_id)
        with self._lock:
            if resolved_parent_id:
                parent = trace_tree.find_span(resolved_parent_id)
                if parent:
                    parent.children.append(span)
                elif active_span and active_span.span_id == resolved_parent_id:
                    active_span.children.append(span)
                else:
                    trace_tree.add_root_span(span)
            else:
                trace_tree.add_root_span(span)

        return span

    def start_as_current_span(
        self,
        name: str,
        span_type: SpanType,
        attributes: Optional[Dict[str, Any]] = None,
        parent_span_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> SpanScope:
        """
        Creates a new span and returns a context manager that activates it
        in the current execution context.
        """
        span = self.start_span(
            name=name,
            span_type=span_type,
            attributes=attributes,
            parent_span_id=parent_span_id,
            trace_id=trace_id,
        )
        return SpanScope(tracer=self, span=span)

    def export_trace_dict(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """Exports full trace tree as a dictionary."""
        tree = self.get_trace(trace_id)
        return tree.to_dict() if tree else None

    def export_trace_json(self, trace_id: str, indent: int = 2) -> Optional[str]:
        """Exports full trace tree as formatted JSON."""
        tree = self.get_trace(trace_id)
        return tree.to_json(indent=indent) if tree else None

    def clear(self) -> None:
        """Clears all in-memory traces."""
        with self._lock:
            self._traces.clear()


_TRACERS: Dict[str, Tracer] = {}
_TRACERS_LOCK = threading.RLock()


def get_tracer(name: str = "orchestrator") -> Tracer:
    """Gets or creates a named Tracer instance (singleton per name)."""
    with _TRACERS_LOCK:
        if name not in _TRACERS:
            _TRACERS[name] = Tracer(name=name)
        return _TRACERS[name]


def trace_span(
    name: Optional[str] = None,
    span_type: SpanType = SpanType.CUSTOM,
    attributes: Optional[Dict[str, Any]] = None,
    tracer_name: str = "orchestrator",
) -> Callable:
    """Decorator to trace a function or method invocation as a span."""
    def decorator(fn: Callable) -> Callable:
        span_name = name or fn.__qualname__

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer(tracer_name)
            with tracer.start_as_current_span(span_name, span_type=span_type, attributes=attributes):
                return fn(*args, **kwargs)
        return wrapper
    return decorator
