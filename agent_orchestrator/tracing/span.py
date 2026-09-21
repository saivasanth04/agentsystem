"""
Distributed Tracing Span and Trace Models.
Provides SpanType, Span, and TraceTree data structures for hierarchical
correlation tracing across Tasks, Agents, LLM calls, Tools, MCP calls,
Retrievals, and Verifications.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import time
from typing import Any, Dict, List, Optional, Union
import uuid


class SpanType(str, Enum):
    TASK = "TASK"
    AGENT = "AGENT"
    LLM_CALL = "LLM_CALL"
    TOOL_CALL = "TOOL_CALL"
    MCP_CALL = "MCP_CALL"
    RETRIEVAL = "RETRIEVAL"
    VERIFICATION = "VERIFICATION"
    CUSTOM = "CUSTOM"


class SpanStatus(str, Enum):
    OK = "OK"
    ERROR = "ERROR"
    UNSET = "UNSET"


@dataclass
class SpanEvent:
    name: str
    timestamp: float = field(default_factory=time.time)
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "timestamp": self.timestamp,
            "timestamp_iso": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
            "attributes": self.attributes,
        }


@dataclass
class Span:
    name: str
    span_type: SpanType
    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_span_id: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    duration_seconds: float = 0.0
    status: str = SpanStatus.UNSET.value
    status_message: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[SpanEvent] = field(default_factory=list)
    children: List["Span"] = field(default_factory=list)

    def set_attribute(self, key: str, value: Any) -> "Span":
        self.attributes[key] = value
        return self

    def set_attributes(self, attrs: Dict[str, Any]) -> "Span":
        self.attributes.update(attrs)
        return self

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> "Span":
        self.events.append(SpanEvent(name=name, timestamp=time.time(), attributes=attributes or {}))
        return self

    def set_status(self, status: Union[SpanStatus, str], description: Optional[str] = None) -> "Span":
        if isinstance(status, SpanStatus):
            self.status = status.value
        else:
            self.status = str(status).upper()
        if description:
            self.status_message = description
        return self

    def end(self, end_time: Optional[float] = None) -> "Span":
        if self.end_time is None:
            self.end_time = end_time if end_time is not None else time.time()
            self.duration_seconds = max(0.0, round(self.end_time - self.start_time, 6))
            if self.status == SpanStatus.UNSET.value:
                self.status = SpanStatus.OK.value
        return self

    def to_dict(self) -> Dict[str, Any]:
        start_iso = datetime.fromtimestamp(self.start_time, tz=timezone.utc).isoformat()
        end_iso = datetime.fromtimestamp(self.end_time, tz=timezone.utc).isoformat() if self.end_time else None
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "span_type": self.span_type.value if isinstance(self.span_type, SpanType) else str(self.span_type),
            "start_time": self.start_time,
            "start_time_iso": start_iso,
            "end_time": self.end_time,
            "end_time_iso": end_iso,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "status_message": self.status_message,
            "attributes": self.attributes,
            "events": [e.to_dict() for e in self.events],
            "children": [c.to_dict() for c in self.children],
        }

    def to_otlp_dict(self) -> Dict[str, Any]:
        """Converts span to OpenTelemetry-compatible span dict."""
        start_nano = int(self.start_time * 1e9)
        end_nano = int((self.end_time or time.time()) * 1e9)
        return {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id or "",
            "name": self.name,
            "kind": "SPAN_KIND_INTERNAL",
            "startTimeUnixNano": str(start_nano),
            "endTimeUnixNano": str(end_nano),
            "attributes": [
                {"key": k, "value": {"stringValue": str(v)}} for k, v in self.attributes.items()
            ] + [{"key": "span.type", "value": {"stringValue": str(self.span_type)}}],
            "status": {
                "code": 1 if self.status == SpanStatus.OK.value else (2 if self.status == SpanStatus.ERROR.value else 0),
                "message": self.status_message or "",
            },
            "events": [
                {
                    "timeUnixNano": str(int(e.timestamp * 1e9)),
                    "name": e.name,
                    "attributes": [{"key": k, "value": {"stringValue": str(v)}} for k, v in e.attributes.items()],
                }
                for e in self.events
            ],
        }


@dataclass
class TraceTree:
    trace_id: str
    root_spans: List[Span] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_root_span(self, span: Span) -> None:
        self.root_spans.append(span)

    def find_span(self, span_id: str) -> Optional[Span]:
        def _search(spans: List[Span]) -> Optional[Span]:
            for s in spans:
                if s.span_id == span_id:
                    return s
                found = _search(s.children)
                if found:
                    return found
            return None
        return _search(self.root_spans)

    def get_all_spans(self) -> List[Span]:
        all_spans: List[Span] = []
        def _collect(spans: List[Span]):
            for s in spans:
                all_spans.append(s)
                _collect(s.children)
        _collect(self.root_spans)
        return all_spans

    def to_dict(self) -> Dict[str, Any]:
        all_spans = self.get_all_spans()
        total_duration = 0.0
        if all_spans:
            starts = [s.start_time for s in all_spans]
            ends = [s.end_time or time.time() for s in all_spans]
            total_duration = round(max(ends) - min(starts), 6)
        return {
            "trace_id": self.trace_id,
            "metadata": self.metadata,
            "total_spans": len(all_spans),
            "duration_seconds": total_duration,
            "root_spans": [s.to_dict() for s in self.root_spans],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)
