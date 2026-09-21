"""
Distributed Tracing Package for Multi-Agent Orchestrator.
Provides OpenTelemetry-aligned distributed tracing with correlation IDs,
parent-child span hierarchies, and standard library context propagation.
"""
from .span import Span, SpanEvent, SpanStatus, SpanType, TraceTree
from .tracer import (
    Tracer,
    SpanScope,
    get_tracer,
    get_current_trace_id,
    get_current_span,
    get_current_span_id,
    trace_span,
)

__all__ = [
    "Span",
    "SpanEvent",
    "SpanStatus",
    "SpanType",
    "TraceTree",
    "Tracer",
    "SpanScope",
    "get_tracer",
    "get_current_trace_id",
    "get_current_span",
    "get_current_span_id",
    "trace_span",
]
