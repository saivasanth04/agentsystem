"""
Log Formatters for Structured Logging.
Provides:
1. JsonLogFormatter: Produces machine-readable, schema-compliant JSON/JSONL records.
2. ConsoleLogFormatter: Produces human-friendly terminal logs preserving the
   canonical '[ORCHESTRATOR | STAGE]' format.
"""
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, Optional

from .context import get_log_context


class JsonLogFormatter(logging.Formatter):
    """
    Formats LogRecords into single-line JSON strings (JSON Lines / JSONL).
    Enriches each record with execution context (session_id, task_id, agent_name)
    and structured payloads.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Resolve timestamp in ISO 8601 UTC
        record_time = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()

        # Retrieve active context
        ctx = get_log_context()

        # Context precedence: record attributes override global contextvars
        session_id = getattr(record, "session_id", None) or ctx.get("session_id")
        task_id = getattr(record, "task_id", None) or ctx.get("task_id")
        agent_name = getattr(record, "agent_name", None) or ctx.get("agent_name")
        correlation_id = getattr(record, "correlation_id", None) or ctx.get("correlation_id")
        trace_id = getattr(record, "trace_id", None) or ctx.get("trace_id")
        span_id = getattr(record, "span_id", None) or ctx.get("span_id")
        stage = getattr(record, "stage", None)
        payload = getattr(record, "payload", None)

        log_data: Dict[str, Any] = {
            "timestamp": record_time,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if stage is not None:
            log_data["stage"] = stage
        if session_id is not None:
            log_data["session_id"] = session_id
        if task_id is not None:
            log_data["task_id"] = task_id
        if agent_name is not None:
            log_data["agent_name"] = agent_name
        if correlation_id is not None:
            log_data["correlation_id"] = correlation_id
        if trace_id is not None:
            log_data["trace_id"] = trace_id
        if span_id is not None:
            log_data["span_id"] = span_id
        if payload is not None:
            log_data["payload"] = payload

        # Capture exception traceback if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            log_data["exception"] = record.exc_text

        # Extra metadata for diagnostics
        log_data["module"] = record.module
        log_data["line"] = record.lineno

        return json.dumps(log_data, default=str)


class ConsoleLogFormatter(logging.Formatter):
    """
    Human-readable console log formatter.
    Preserves the familiar '[ORCHESTRATOR | STAGE] Message' style for stages,
    and falls back to standard level/logger headers for general logs.
    """

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        stage = getattr(record, "stage", None)

        if stage:
            formatted = f"[ORCHESTRATOR | {str(stage).upper()}] {msg}"
        else:
            formatted = f"[{record.levelname}] [{record.name}] {msg}"

        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)
        elif record.exc_text:
            formatted += "\n" + record.exc_text

        return formatted
