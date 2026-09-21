"""
Unit & Integration Tests for Production-Grade Telemetry & Observability Engine (Issue #52).
Validates:
1. LatencyMetric statistical calculations (count, total, min, max, mean, p50, p95).
2. Agent latency tracking and per-agent latency breakdowns.
3. LLM latency measurement and turn accumulation in ReAct loop.
4. Tool latency aggregation and error tracking.
5. Token usage partitioning by agent and by model.
6. Failure rate calculations (task failure rate, tool failure rate, verification failure rate).
7. Retry count tracking and retry rate metrics.
8. Retrieval count tracking (CBM, symbols, memory, file search).
9. File access tracking (read vs written vs deleted sets).
10. Model used tracking in TaskAttemptRecord and session model distribution.
11. Cost attribution (by agent, by model, productive vs wasted spend).
12. SQLite persistence and re-hydration of TelemetrySnapshot.
13. ArtifactStore registration of telemetry_report.json.
14. Backward compatibility with OrchestratorState.get_execution_summary().
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.telemetry.telemetry_engine import (
    AgentTelemetry,
    FileAccessTelemetry,
    LatencyMetric,
    LLMTelemetry,
    TelemetryEngine,
    TelemetrySnapshot,
    ToolTelemetry,
)
from agent_orchestrator.persistence.state_store import SQLiteStateStore
from agent_orchestrator.persistence.artifact_store import ArtifactStore, ArtifactCategory
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.runtime.task_graph import (
    ExecutableTask,
    TaskAttemptRecord,
    TaskDAG,
    TaskState,
    TokenUsage,
)
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.runtime.dag_scheduler import ConcurrentDAGScheduler
from agent_orchestrator.state import OrchestratorState, ReviewVerdict, TaskStatus
from agent_orchestrator.orchestrator import TaskOrchestrator


class TestTelemetryEngine(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_telemetry_")
        self.db_path = os.path.join(self.test_dir, "test_state.db")
        self.state_store = SQLiteStateStore(db_path=self.db_path)
        self.session_id = "sess-telem-test-001"
        self.engine = TelemetryEngine(session_id=self.session_id)

    def tearDown(self):
        if hasattr(self, "state_store") and hasattr(self.state_store._local, "conn") and self.state_store._local.conn:
            try:
                self.state_store._local.conn.close()
            except Exception:
                pass
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_latency_metric_statistics(self):
        """Validates statistical calculations (count, min, max, mean, p50, p95)."""
        metric = LatencyMetric()
        # Record 10 values
        durations = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        for d in durations:
            metric.record(d)

        self.assertEqual(metric.count, 10)
        self.assertAlmostEqual(metric.total_seconds, 5.5, places=2)
        self.assertAlmostEqual(metric.min_seconds, 0.1, places=2)
        self.assertAlmostEqual(metric.max_seconds, 1.0, places=2)
        self.assertAlmostEqual(metric.mean_seconds, 0.55, places=2)
        self.assertAlmostEqual(metric.p50_seconds, 0.5, places=1)
        self.assertAlmostEqual(metric.p95_seconds, 1.0, places=1)

        d_dict = metric.to_dict()
        hydrated = LatencyMetric.from_dict(d_dict)
        self.assertEqual(hydrated.count, 10)
        self.assertEqual(hydrated.mean_seconds, metric.mean_seconds)

    def test_agent_latency_tracking(self):
        """Validates agent latency accumulation and per-agent breakdown."""
        self.engine.record_agent_execution(
            agent_name="CODER",
            duration_seconds=2.5,
            status="COMPLETED",
            prompt_tokens=1000,
            completion_tokens=200,
            cost_usd=0.015,
        )
        self.engine.record_agent_execution(
            agent_name="CODER",
            duration_seconds=1.5,
            status="COMPLETED",
            prompt_tokens=800,
            completion_tokens=150,
            cost_usd=0.010,
        )
        self.engine.record_agent_execution(
            agent_name="REVIEWER",
            duration_seconds=3.0,
            status="COMPLETED",
            prompt_tokens=2000,
            completion_tokens=500,
            cost_usd=0.030,
        )

        snap = self.engine.get_snapshot()
        self.assertIn("CODER", snap.agent_latencies)
        self.assertIn("REVIEWER", snap.agent_latencies)

        coder_data = snap.agent_latencies["CODER"]
        self.assertEqual(coder_data["tasks_assigned"], 2)
        self.assertEqual(coder_data["tasks_completed"], 2)
        self.assertEqual(coder_data["latency"]["count"], 2)
        self.assertAlmostEqual(coder_data["latency"]["mean_seconds"], 2.0, places=2)
        self.assertEqual(coder_data["total_tokens"], 2150)
        self.assertAlmostEqual(coder_data["cost_usd"], 0.025, places=3)

    def test_llm_latency_measurement_in_react_loop(self):
        """Validates LLM latency timing and accumulation inside ReActAgentLoop."""
        mock_llm = MagicMock(spec=["chat_json"])
        mock_llm.chat_json.side_effect = [
            # Turn 1: tool call
            {
                "thought": "I will read app.py",
                "tool_call": {
                    "name": "read_file",
                    "arguments": {"filepath": "app.py"},
                },
            },
            # Turn 2: finish
            {
                "thought": "Finished work",
                "final_output": "All done.",
            },
        ]

        mock_registry = MagicMock()
        mock_registry.call_tool.return_value = {"success": True, "content": "print('hello')"}
        mock_registry.get_tools_for_agent.return_value = []

        react_loop = ReActAgentLoop(
            llm=mock_llm,
            tool_registry=mock_registry,
            max_turns=3,
            telemetry_engine=self.engine,
        )

        res = react_loop.run(
            system_prompt="System",
            user_prompt="Inspect app.py",
            model="o3-mini",
            task_id="T-LLM-01",
            agent_name="CODER",
        )

        self.assertIn("llm_latency_seconds", res)
        self.assertGreaterEqual(res["llm_latency_seconds"], 0.0)
        self.assertIn("tool_latency_seconds", res)

        snap = self.engine.get_snapshot()
        self.assertGreaterEqual(snap.llm_latency["count"], 2)
        self.assertIn("o3-mini", snap.model_distribution)
        self.assertGreaterEqual(snap.model_distribution["o3-mini"]["calls"], 2)

    def test_tool_latency_and_aggregation(self):
        """Validates tool execution timing, error counts, and failure rate calculation."""
        self.engine.record_tool_call("pytest", duration=1.2, is_error=False, cost_usd=0.001)
        self.engine.record_tool_call("pytest", duration=1.8, is_error=True, cost_usd=0.001)
        self.engine.record_tool_call("read_file", duration=0.05, is_error=False, cost_usd=0.0)

        snap = self.engine.get_snapshot()
        self.assertIn("pytest", snap.tool_latencies)
        self.assertIn("read_file", snap.tool_latencies)

        pyt = snap.tool_latencies["pytest"]
        self.assertEqual(pyt["call_count"], 2)
        self.assertEqual(pyt["error_count"], 1)
        self.assertAlmostEqual(pyt["failure_rate"], 0.5, places=2)
        self.assertAlmostEqual(pyt["latency"]["mean_seconds"], 1.5, places=2)

        self.assertAlmostEqual(snap.failure_rates["tool_failure_rate"], 0.3333, places=2)

    def test_token_usage_breakdown(self):
        """Validates token usage breakdown partitioned by agent and by model."""
        self.engine.record_llm_call(
            model="claude-3-5-sonnet",
            latency_seconds=0.8,
            prompt_tokens=1500,
            completion_tokens=300,
            cached_tokens=500,
            cost_usd=0.02,
            agent_name="CODER",
        )
        self.engine.record_llm_call(
            model="o3-mini",
            latency_seconds=0.4,
            prompt_tokens=800,
            completion_tokens=200,
            cached_tokens=0,
            cost_usd=0.005,
            agent_name="TESTER",
        )

        snap = self.engine.get_snapshot()
        tu = snap.token_usage
        self.assertEqual(tu["total_tokens"], 2800)
        self.assertEqual(tu["total_prompt_tokens"], 2300)
        self.assertEqual(tu["total_completion_tokens"], 500)
        self.assertEqual(tu["total_cached_tokens"], 500)

        # By agent
        self.assertEqual(tu["by_agent"]["CODER"]["total_tokens"], 1800)
        self.assertEqual(tu["by_agent"]["TESTER"]["total_tokens"], 1000)

        # By model
        self.assertEqual(tu["by_model"]["claude-3-5-sonnet"]["total_tokens"], 1800)
        self.assertEqual(tu["by_model"]["o3-mini"]["total_tokens"], 1000)

    def test_failure_rate_computation(self):
        """Validates task failure rate and verification stage failure rate."""
        self.engine.record_task_outcome("T-01", passed=True, cost_usd=0.01)
        self.engine.record_task_outcome("T-02", passed=False, cost_usd=0.01)
        self.engine.record_task_outcome("T-03", passed=True, cost_usd=0.01)

        self.engine.record_verification_stage("LINT", passed=True)
        self.engine.record_verification_stage("TEST", passed=False)

        snap = self.engine.get_snapshot()
        self.assertAlmostEqual(snap.failure_rates["task_failure_rate"], 0.3333, places=2)
        self.assertAlmostEqual(snap.failure_rates["verification_failure_rate"], 0.50, places=2)

    def test_retry_count_and_metrics(self):
        """Validates retry tracking, retry rate, and replan iterations."""
        self.engine.record_retry(task_id="T-01", retry_number=1, agent_name="CODER")
        self.engine.record_retry(task_id="T-01", retry_number=2, agent_name="CODER")
        self.engine.record_retry(task_id="T-02", retry_number=1, agent_name="TESTER")
        self.engine.record_replan_iteration(iteration=1, reason="Test failure")

        snap = self.engine.get_snapshot()
        retries = snap.retry_metrics
        self.assertEqual(retries["total_retries"], 3)
        self.assertEqual(retries["retried_tasks_count"], 2)
        self.assertEqual(retries["replan_iterations"], 1)

    def test_retrieval_count_tracking(self):
        """Validates CBM graph, symbol lookup, and memory retrieval telemetry."""
        self.engine.record_retrieval("cbm_subgraph", count=2)
        self.engine.record_retrieval("symbol_lookup", count=5)
        self.engine.record_retrieval("memory_query", count=1)

        snap = self.engine.get_snapshot()
        ret = snap.retrieval_metrics
        self.assertEqual(ret["cbm_subgraph"], 2)
        self.assertEqual(ret["symbol_lookup"], 5)
        self.assertEqual(ret["memory_query"], 1)
        self.assertEqual(ret["total"], 8)

    def test_files_accessed_tracking(self):
        """Validates distinct files read, written, and deleted in WorkspaceManager."""
        ws_dir = os.path.join(self.test_dir, "ws")
        os.makedirs(ws_dir, exist_ok=True)
        workspace = WorkspaceManager(root_dir=ws_dir)
        workspace.set_telemetry_engine(self.engine)

        workspace.write_file("src/main.py", "def main(): pass\n")
        workspace.write_file("config.json", "{}")
        content = workspace.read_file("src/main.py")
        self.assertIsNotNone(content)
        workspace.replace_file_content("src/main.py", "pass", "return 1")
        workspace.delete_file("config.json")

        snap = self.engine.get_snapshot()
        fa = snap.file_access
        self.assertIn("src/main.py", fa["files_read"])
        self.assertIn("src/main.py", fa["files_written"])
        self.assertIn("config.json", fa["files_written"])
        self.assertIn("config.json", fa["files_deleted"])
        self.assertEqual(fa["total_distinct_files"], 2)
        self.assertGreaterEqual(fa["total_mutations"], 3)

    def test_model_used_tracking(self):
        """Validates model_used field on TaskAttemptRecord and session distribution."""
        attempt = TaskAttemptRecord(
            attempt_number=1,
            agent_name="CODER",
            model_used="claude-3-7-sonnet",
            llm_latency_seconds=1.234,
        )
        d = attempt.to_dict()
        self.assertEqual(d["model_used"], "claude-3-7-sonnet")
        self.assertEqual(d["llm_latency_seconds"], 1.234)

        hydrated = TaskAttemptRecord.from_dict(d)
        self.assertEqual(hydrated.model_used, "claude-3-7-sonnet")
        self.assertEqual(hydrated.llm_latency_seconds, 1.234)

    def test_cost_breakdown_attribution(self):
        """Validates cost attribution by agent, model, and productive vs wasted spend."""
        self.engine.record_llm_call(
            model="gpt-4o",
            latency_seconds=1.0,
            prompt_tokens=2000,
            completion_tokens=500,
            cost_usd=0.02,
            agent_name="CODER",
        )
        self.engine.record_tool_call("run_tests", duration=2.0, cost_usd=0.005)
        self.engine.record_task_outcome("T-01", passed=True, cost_usd=0.015)
        self.engine.record_task_outcome("T-02", passed=False, cost_usd=0.010)

        snap = self.engine.get_snapshot()
        cost = snap.cost_breakdown
        self.assertAlmostEqual(cost["total_cost_usd"], 0.025, places=3)
        self.assertAlmostEqual(cost["llm_inference_cost_usd"], 0.020, places=3)
        self.assertAlmostEqual(cost["tool_compute_cost_usd"], 0.005, places=3)
        self.assertAlmostEqual(cost["productive_cost_usd"], 0.015, places=3)
        self.assertAlmostEqual(cost["wasted_cost_usd"], 0.010, places=3)
        self.assertIn("gpt-4o", cost["cost_by_model"])
        self.assertIn("CODER", cost["cost_by_agent"])

    def test_sqlite_persistence_and_rehydration(self):
        """Validates storing and retrieving TelemetrySnapshot in SQLite."""
        self.engine.record_tool_call("linter", duration=0.2, is_error=False, cost_usd=0.001)
        self.engine.record_agent_execution("CODER", duration_seconds=1.5, status="COMPLETED")
        snapshot = self.engine.get_snapshot()

        self.state_store.save_telemetry_snapshot(self.session_id, snapshot)
        retrieved_dict = self.state_store.get_telemetry_snapshot(self.session_id)
        self.assertIsNotNone(retrieved_dict)
        self.assertEqual(retrieved_dict["session_id"], self.session_id)
        self.assertIn("linter", retrieved_dict["tool_latencies"])

        hydrated = TelemetrySnapshot.from_dict(retrieved_dict)
        self.assertEqual(hydrated.session_id, self.session_id)
        self.assertIn("CODER", hydrated.agent_latencies)

    def test_artifact_store_telemetry_report(self):
        """Validates registering telemetry_report.json into ArtifactStore."""
        storage_root = os.path.join(self.test_dir, "artifacts")
        artifact_store = ArtifactStore(storage_root=storage_root, state_store=self.state_store)

        snapshot = self.engine.get_snapshot()
        rec = artifact_store.put(
            category=ArtifactCategory.REPORTS,
            name="telemetry_report",
            content=snapshot.to_dict(),
            session_id=self.session_id,
        )
        self.assertTrue(rec.artifact_id.startswith("art-"))
        self.assertEqual(rec.category, "reports")

        read_content = artifact_store.read_json(rec.artifact_id)
        self.assertEqual(read_content["session_id"], self.session_id)

    def test_orchestrator_state_backward_compatibility(self):
        """Validates OrchestratorState.get_execution_summary() backward compatibility."""
        state = OrchestratorState(user_request="Build microservice")
        summary = state.get_execution_summary()
        self.assertIn("total_tokens", summary)
        self.assertIn("total_cost_usd", summary)
        self.assertIn("total_duration_seconds", summary)

        # Attach telemetry snapshot
        state.telemetry = self.engine.get_snapshot()
        summary_with_telem = state.get_execution_summary()
        self.assertIn("telemetry", summary_with_telem)
        self.assertEqual(summary_with_telem["telemetry"]["session_id"], self.session_id)


if __name__ == "__main__":
    unittest.main()
