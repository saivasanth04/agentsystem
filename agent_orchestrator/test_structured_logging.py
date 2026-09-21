"""
Unit Test Suite for Structured Logging Subsystem (Issue #53).
Tests JSON formatting, console formatting, contextvars propagation,
thread isolation, session file sinks (JSONL), and orchestrator backward-compatibility.
"""
import concurrent.futures
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.logging import (
    ConsoleLogFormatter,
    JsonLogFormatter,
    LogContext,
    StructuredLogger,
    add_session_file_sink,
    clear_log_context,
    configure_logging,
    get_log_context,
    get_logger,
    remove_session_file_sink,
    set_log_context,
)
from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.persistence.artifact_store import ArtifactCategory, ArtifactStore
from agent_orchestrator.state import OrchestratorState


class TestStructuredLogging(unittest.TestCase):

    def setUp(self):
        clear_log_context()
        self.test_dir = tempfile.mkdtemp(prefix="test_logging_")

    def tearDown(self):
        clear_log_context()
        if os.path.exists(self.test_dir):
            try:
                shutil.rmtree(self.test_dir)
            except Exception:
                pass

    def test_json_formatter_keys_and_timestamp(self):
        """Validates JSON log schema, timestamp, level, logger, message, stage, and payload."""
        formatter = JsonLogFormatter()
        record = logging.LogRecord(
            name="test_orchestrator",
            level=logging.INFO,
            pathname=__file__,
            lineno=45,
            msg="Dispatching Quality Audit [REVIEWER]",
            args=(),
            exc_info=None,
        )
        record.stage = "EXECUTE AGENT"  # type: ignore
        record.payload = {"agent": "REVIEWER", "skills": ["code-review"]}  # type: ignore

        formatted_str = formatter.format(record)
        data = json.loads(formatted_str)

        self.assertIn("timestamp", data)
        self.assertEqual(data["level"], "INFO")
        self.assertEqual(data["logger"], "test_orchestrator")
        self.assertEqual(data["message"], "Dispatching Quality Audit [REVIEWER]")
        self.assertEqual(data["stage"], "EXECUTE AGENT")
        self.assertEqual(data["payload"]["agent"], "REVIEWER")
        self.assertEqual(data["line"], 45)

    def test_console_formatter_output(self):
        """Validates [ORCHESTRATOR | STAGE] formatting for console logs."""
        formatter = ConsoleLogFormatter()

        # With stage
        record_with_stage = logging.LogRecord(
            name="orchestrator",
            level=logging.INFO,
            pathname=__file__,
            lineno=60,
            msg="Dispatching Quality Audit [REVIEWER]",
            args=(),
            exc_info=None,
        )
        record_with_stage.stage = "EXECUTE AGENT"  # type: ignore
        formatted = formatter.format(record_with_stage)
        self.assertEqual(formatted, "[ORCHESTRATOR | EXECUTE AGENT] Dispatching Quality Audit [REVIEWER]")

        # Without stage
        record_no_stage = logging.LogRecord(
            name="tools.dispatcher",
            level=logging.WARNING,
            pathname=__file__,
            lineno=70,
            msg="Fallback to local tool",
            args=(),
            exc_info=None,
        )
        formatted_no_stage = formatter.format(record_no_stage)
        self.assertEqual(formatted_no_stage, "[WARNING] [tools.dispatcher] Fallback to local tool")

    def test_context_propagation_via_contextvars(self):
        """Validates that LogContext injects session, task, and agent metadata into JSON logs."""
        formatter = JsonLogFormatter()

        with LogContext(session_id="sess-xyz-123", task_id="T-01", agent_name="CODER"):
            ctx = get_log_context()
            self.assertEqual(ctx["session_id"], "sess-xyz-123")
            self.assertEqual(ctx["task_id"], "T-01")
            self.assertEqual(ctx["agent_name"], "CODER")

            record = logging.LogRecord(
                name="orchestrator",
                level=logging.INFO,
                pathname=__file__,
                lineno=90,
                msg="Task started",
                args=(),
                exc_info=None,
            )
            data = json.loads(formatter.format(record))
            self.assertEqual(data["session_id"], "sess-xyz-123")
            self.assertEqual(data["task_id"], "T-01")
            self.assertEqual(data["agent_name"], "CODER")

        # After exiting context, contextvars should be restored
        cleared_ctx = get_log_context()
        self.assertNotIn("session_id", cleared_ctx)
        self.assertNotIn("task_id", cleared_ctx)
        self.assertNotIn("agent_name", cleared_ctx)

    def test_log_level_filtering(self):
        """Verifies that debug messages are ignored when log level is set to INFO."""
        logger_name = "test_level_filter"
        raw_logger = logging.getLogger(logger_name)
        raw_logger.setLevel(logging.INFO)
        raw_logger.handlers.clear()

        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        raw_logger.addHandler(CaptureHandler())

        structured = StructuredLogger(raw_logger)
        structured.debug("Debug diagnostic chatter")
        structured.info("Info milestone")
        structured.error("Error failure")

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].levelname, "INFO")
        self.assertEqual(records[0].getMessage(), "Info milestone")
        self.assertEqual(records[1].levelname, "ERROR")
        self.assertEqual(records[1].getMessage(), "Error failure")

    def test_thread_isolated_contexts(self):
        """Validates that concurrent worker threads maintain isolated log contexts."""
        formatter = JsonLogFormatter()
        results = {}

        def worker(thread_idx: int):
            task_id = f"T-WORKER-{thread_idx}"
            agent_name = f"AGENT-{thread_idx}"
            session_id = f"sess-thread-{thread_idx}"

            with LogContext(session_id=session_id, task_id=task_id, agent_name=agent_name):
                rec = logging.LogRecord(
                    name="worker",
                    level=logging.INFO,
                    pathname=__file__,
                    lineno=130,
                    msg=f"Worker {thread_idx} executing",
                    args=(),
                    exc_info=None,
                )
                formatted = formatter.format(rec)
                results[thread_idx] = json.loads(formatted)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker, i) for i in range(4)]
            concurrent.futures.wait(futures)

        for i in range(4):
            self.assertIn(i, results)
            self.assertEqual(results[i]["task_id"], f"T-WORKER-{i}")
            self.assertEqual(results[i]["agent_name"], f"AGENT-{i}")
            self.assertEqual(results[i]["session_id"], f"sess-thread-{i}")

    def test_jsonl_file_sink(self):
        """Validates writing and parsing structured JSON Lines (JSONL) on disk."""
        session_id = "sess-jsonl-test"
        log_dir = Path(self.test_dir) / "logs"

        logger_name = "test_sink_logger"
        logger = get_logger(logger_name)
        handler = add_session_file_sink(
            session_id=session_id,
            log_dir=log_dir,
            logger_name=logger_name,
            level=logging.DEBUG,
        )

        try:
            with LogContext(session_id=session_id, task_id="T-SYNC"):
                logger.log_event("START", "Session initialized", {"config": "test"})
                logger.log_event("EXECUTE AGENT", "Dispatching Coder", {"agent": "CODER"})
                logger.log_event("SUCCESS", "Workflow finished", {"verdict": "PASS"})
                logger.flush()

            log_file = log_dir / f"session_{session_id}.jsonl"
            self.assertTrue(log_file.exists())

            with open(log_file, "r", encoding="utf-8") as f:
                lines = [json.loads(line.strip()) for line in f if line.strip()]

            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[0]["stage"], "START")
            self.assertEqual(lines[0]["session_id"], session_id)
            self.assertEqual(lines[0]["task_id"], "T-SYNC")
            self.assertEqual(lines[1]["stage"], "EXECUTE AGENT")
            self.assertEqual(lines[2]["stage"], "SUCCESS")
        finally:
            remove_session_file_sink(handler, logger_name=logger_name)

    def test_orchestrator_default_event_logger_bridge(self):
        """Verifies TaskOrchestrator bridges on_event calls to StructuredLogger."""
        orch = TaskOrchestrator()
        # Verify logger instance attached
        self.assertIsNotNone(getattr(orch, "logger", None))

        # Capture log event
        captured = []
        mock_log = MagicMock()
        orch.logger.log_event = mock_log  # type: ignore

        orch.on_event("5. CHECK RESULT", "Reviewer Verdict: PASS", {"score": 98})
        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        self.assertEqual(kwargs.get("stage"), "5. CHECK RESULT")
        self.assertEqual(kwargs.get("message"), "Reviewer Verdict: PASS")
        self.assertEqual(kwargs.get("payload"), {"score": 98})

    def test_orchestrator_user_callback_backward_compatibility(self):
        """Verifies TaskOrchestrator invokes user-provided on_event_callback seamlessly."""
        mock_cb = MagicMock()
        orch = TaskOrchestrator(on_event_callback=mock_cb)

        orch.on_event("START", "Starting test run", {"foo": "bar"})
        mock_cb.assert_called_once_with("START", "Starting test run", {"foo": "bar"})

    def test_artifact_store_log_registration(self):
        """Verifies session JSONL log is saved to ArtifactStore under category 'logs'."""
        storage_root = os.path.join(self.test_dir, "artifacts")
        artifact_store = ArtifactStore(storage_root=storage_root)

        session_id = "sess-artifact-log-01"
        sample_log = '{"timestamp": "2026-09-20T05:00:00Z", "level": "INFO", "message": "done"}\n'

        rec = artifact_store.put(
            category=ArtifactCategory.LOGS,
            name=f"session_{session_id}.jsonl",
            content=sample_log,
            session_id=session_id,
            mime_type="application/x-ndjson",
        )

        self.assertTrue(rec.artifact_id.startswith("art-"))
        self.assertEqual(rec.category, "logs")

        read_content = artifact_store.read_text(rec.artifact_id)
        self.assertEqual(read_content, sample_log)


if __name__ == "__main__":
    unittest.main()
