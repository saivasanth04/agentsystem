"""
Unit and Integration Tests for Distributed Tracing (Issue #54).
Validates Span, TraceTree, Tracer, Context Propagation, Thread Isolation,
OTLP-compliance, SQLite persistence, and multi-agent hierarchical tracing.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import tempfile
import time
import unittest

from agent_orchestrator.tracing.span import Span, SpanEvent, SpanStatus, SpanType, TraceTree
from agent_orchestrator.tracing.tracer import (
    Tracer,
    get_tracer,
    get_current_trace_id,
    get_current_span,
    get_current_span_id,
    trace_span,
)
from agent_orchestrator.logging.context import get_log_context, set_log_context, clear_log_context
from agent_orchestrator.persistence.state_store import SQLiteStateStore


class TestDistributedTracing(unittest.TestCase):
    def setUp(self):
        clear_log_context()
        self.tracer = Tracer(name="test_tracer")

    def tearDown(self):
        clear_log_context()
        self.tracer.clear()

    def test_span_lifecycle_and_events(self):
        """Tests span creation, status updates, events, and duration calculation."""
        trace_id = self.tracer.start_trace(metadata={"env": "test"})
        self.assertEqual(get_current_trace_id(), trace_id)

        with self.tracer.start_as_current_span(
            "Task: Build Auth Module",
            span_type=SpanType.TASK,
            attributes={"task_id": "task-01"},
        ) as span:
            self.assertEqual(span.name, "Task: Build Auth Module")
            self.assertEqual(span.span_type, SpanType.TASK)
            self.assertEqual(span.trace_id, trace_id)
            self.assertEqual(get_current_span_id(), span.span_id)
            self.assertEqual(get_current_span(), span)

            span.set_attribute("framework", "flask")
            span.add_event("checkpoint_created", {"checkpoint_id": "ckpt-01"})
            time.sleep(0.01)

        self.assertIsNotNone(span.end_time)
        self.assertGreater(span.duration_seconds, 0.0)
        self.assertEqual(span.status, SpanStatus.OK.value)
        self.assertEqual(len(span.events), 1)
        self.assertEqual(span.events[0].name, "checkpoint_created")
        self.assertEqual(span.attributes.get("framework"), "flask")
        # Context should be restored to None
        self.assertIsNone(get_current_span())

    def test_hierarchical_nesting(self):
        """
        Tests the full causal hierarchy:
        Task
         └── Agent
              ├── LLM call
              ├── Tool call
              │    └── MCP call
              ├── Retrieval
              └── Verification
        """
        trace_id = self.tracer.start_trace()

        with self.tracer.start_as_current_span("Task: Generate Auth", span_type=SpanType.TASK) as task_span:
            with self.tracer.start_as_current_span("Agent: CODER", span_type=SpanType.AGENT) as agent_span:
                with self.tracer.start_as_current_span("LLM Call: sonnet", span_type=SpanType.LLM_CALL) as llm_span:
                    llm_span.set_attribute("prompt_tokens", 120)

                with self.tracer.start_as_current_span("Tool: mcp_filesystem", span_type=SpanType.TOOL_CALL) as tool_span:
                    with self.tracer.start_as_current_span("MCP Call: filesystem.read", span_type=SpanType.MCP_CALL) as mcp_span:
                        mcp_span.set_attribute("server", "filesystem")

                with self.tracer.start_as_current_span("Retrieval: CBM Subgraph", span_type=SpanType.RETRIEVAL) as ret_span:
                    ret_span.set_attribute("nodes_count", 5)

            with self.tracer.start_as_current_span("Verification: task-01", span_type=SpanType.VERIFICATION) as verif_span:
                verif_span.set_attribute("passed", True)

        tree = self.tracer.get_trace(trace_id)
        self.assertIsNotNone(tree)
        self.assertEqual(len(tree.root_spans), 1)

        root = tree.root_spans[0]
        self.assertEqual(root.span_type, SpanType.TASK)
        # Root should have 2 children: Agent and Verification
        self.assertEqual(len(root.children), 2)
        child_types = [c.span_type for c in root.children]
        self.assertIn(SpanType.AGENT, child_types)
        self.assertIn(SpanType.VERIFICATION, child_types)

        agent_child = next(c for c in root.children if c.span_type == SpanType.AGENT)
        # Agent should have 3 children: LLM_CALL, TOOL_CALL, RETRIEVAL
        self.assertEqual(len(agent_child.children), 3)
        agent_sub_types = [c.span_type for c in agent_child.children]
        self.assertIn(SpanType.LLM_CALL, agent_sub_types)
        self.assertIn(SpanType.TOOL_CALL, agent_sub_types)
        self.assertIn(SpanType.RETRIEVAL, agent_sub_types)

        tool_child = next(c for c in agent_child.children if c.span_type == SpanType.TOOL_CALL)
        # Tool call should have 1 child: MCP_CALL
        self.assertEqual(len(tool_child.children), 1)
        self.assertEqual(tool_child.children[0].span_type, SpanType.MCP_CALL)

        # Verify parent span IDs
        self.assertEqual(agent_child.parent_span_id, root.span_id)
        self.assertEqual(tool_child.parent_span_id, agent_child.span_id)
        self.assertEqual(tool_child.children[0].parent_span_id, tool_child.span_id)

    def test_context_propagation_to_logging(self):
        """Verifies that active trace_id and span_id automatically propagate to get_log_context()."""
        trace_id = self.tracer.start_trace()
        with self.tracer.start_as_current_span("Task: Context Check", span_type=SpanType.TASK) as span:
            ctx = get_log_context()
            self.assertEqual(ctx.get("trace_id"), trace_id)
            self.assertEqual(ctx.get("span_id"), span.span_id)

        # Outside span, span_id is cleared
        ctx_after = get_log_context()
        self.assertIsNone(ctx_after.get("span_id"))

    def test_thread_isolation(self):
        """Tests that concurrent threads execute with their own isolated active spans."""
        trace_id = self.tracer.start_trace()
        results = {}

        def _worker(worker_id: int):
            with self.tracer.start_as_current_span(f"Worker-{worker_id}", span_type=SpanType.TASK) as span:
                time.sleep(0.02)
                active_id = get_current_span_id()
                results[worker_id] = (span.span_id, active_id)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_worker, i) for i in range(4)]
            for f in futures:
                f.result()

        self.assertEqual(len(results), 4)
        for worker_id, (expected_id, actual_id) in results.items():
            self.assertEqual(expected_id, actual_id)

    def test_otlp_and_json_serialization(self):
        """Tests OTLP dictionary formatting and JSON tree export."""
        trace_id = self.tracer.start_trace(metadata={"session": "sess-test"})
        with self.tracer.start_as_current_span("Root Task", span_type=SpanType.TASK) as span:
            span.add_event("start_ev", {"step": 1})
            span.set_attribute("key", "val")

        # Test OTLP export
        otlp = span.to_otlp_dict()
        self.assertEqual(otlp["traceId"], trace_id)
        self.assertEqual(otlp["spanId"], span.span_id)
        self.assertEqual(otlp["name"], "Root Task")
        self.assertEqual(len(otlp["events"]), 1)

        # Test JSON serialization
        json_str = self.tracer.export_trace_json(trace_id)
        self.assertIsNotNone(json_str)
        data = json.loads(json_str)
        self.assertEqual(data["trace_id"], trace_id)
        self.assertEqual(data["total_spans"], 1)
        self.assertEqual(data["root_spans"][0]["name"], "Root Task")

    def test_state_store_persistence(self):
        """Tests SQLite persistence of trace trees."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_traces.db")
            store = SQLiteStateStore(db_path=db_path)

            trace_id = "trace-persist-123"
            trace_dict = {
                "trace_id": trace_id,
                "metadata": {"user": "test_user"},
                "total_spans": 3,
                "duration_seconds": 1.45,
                "root_spans": [{"name": "Task 1", "span_id": "sp-1", "children": []}],
            }

            store.save_trace(trace_id, trace_dict, session_id="sess-persist-1")

            loaded = store.get_trace(trace_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["trace_id"], trace_id)
            self.assertEqual(loaded["total_spans"], 3)
            self.assertEqual(loaded["duration_seconds"], 1.45)

            session_traces = store.get_session_traces("sess-persist-1")
            self.assertEqual(len(session_traces), 1)
            self.assertEqual(session_traces[0]["trace_id"], trace_id)
            store.close()

    def test_error_status_recording_on_exception(self):
        """Tests that uncaught exceptions automatically set ERROR status on the span."""
        trace_id = self.tracer.start_trace()
        span_ref = None
        try:
            with self.tracer.start_as_current_span("Failing Step", span_type=SpanType.TOOL_CALL) as span:
                span_ref = span
                raise ValueError("Simulated tool crash")
        except ValueError:
            pass

        self.assertIsNotNone(span_ref)
        self.assertEqual(span_ref.status, SpanStatus.ERROR.value)
        self.assertEqual(span_ref.status_message, "Simulated tool crash")
        self.assertTrue(any(e.name == "exception" for e in span_ref.events))


if __name__ == "__main__":
    unittest.main()
