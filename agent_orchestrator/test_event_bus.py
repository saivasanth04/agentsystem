"""
Unit & Integration Tests for Production-Grade Lifecycle Event Bus (Issue #51).
Validates:
1. Strongly-typed EventType enum and ExecutionEvent model serialization.
2. Thread-safe EventBus pub/sub subscriptions (exact event type and '*' wildcard).
3. Dispatch and capture of all 10 canonical lifecycle events:
   TASK_CREATED, TASK_STARTED, AGENT_SELECTED, TOOL_CALLED, TOOL_COMPLETED,
   FILE_CHANGED, TEST_STARTED, TEST_FAILED, REPLAN_STARTED, TASK_COMPLETED.
4. In-memory history filtering and querying by type, task_id, session_id.
5. SQLite state store event persistence and historical re-hydration.
6. WorkspaceManager integration with automatic FILE_CHANGED broadcasting.
7. ReActAgentLoop integration with TOOL_CALLED and TOOL_COMPLETED events.
8. ConcurrentDAGScheduler integration with TASK_STARTED, AGENT_SELECTED, TASK_COMPLETED.
9. BuildVerificationPipeline integration with TEST_STARTED, TEST_PASSED, TEST_FAILED.
10. Backward-compatible bridge to legacy on_event_callback handlers.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.runtime.event_bus import (
    EventBus,
    EventType,
    ExecutionEvent,
)
from agent_orchestrator.persistence.state_store import SQLiteStateStore
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.runtime.task_graph import TaskDAG, ExecutableTask, TaskState
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.runtime.dag_scheduler import ConcurrentDAGScheduler
from agent_orchestrator.runtime.build_pipeline import BuildVerificationPipeline, PipelineStage
from agent_orchestrator.orchestrator import TaskOrchestrator


class TestEventBus(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_event_bus_")
        self.db_path = os.path.join(self.test_dir, "test_events.db")
        self.state_store = SQLiteStateStore(db_path=self.db_path)
        self.session_id = "sess-event-test-001"
        self.bus = EventBus(state_store=self.state_store, session_id=self.session_id)

    def tearDown(self):
        if hasattr(self, "state_store") and hasattr(self.state_store._local, "conn") and self.state_store._local.conn:
            try:
                self.state_store._local.conn.close()
            except Exception:
                pass
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_event_model_serialization(self):
        """Validates ExecutionEvent dataclass properties and serialization."""
        event = ExecutionEvent(
            event_type=EventType.TASK_CREATED,
            session_id=self.session_id,
            task_id="T-01",
            agent_name="PLANNER",
            payload={"objective": "Implement user auth", "dependencies": []},
        )
        d = event.to_dict()
        self.assertEqual(d["event_type"], "TASK_CREATED")
        self.assertEqual(d["session_id"], self.session_id)
        self.assertEqual(d["task_id"], "T-01")
        self.assertEqual(d["agent_name"], "PLANNER")
        self.assertEqual(d["payload"]["objective"], "Implement user auth")

        hydrated = ExecutionEvent.from_dict(d)
        self.assertEqual(hydrated.event_type, EventType.TASK_CREATED)
        self.assertEqual(hydrated.task_id, "T-01")
        self.assertEqual(hydrated.event_id, event.event_id)

    def test_pub_sub_exact_and_wildcard(self):
        """Validates exact event-type subscriptions, wildcard listeners, and unsubscribe."""
        task_created_events = []
        all_events = []

        def on_task_created(evt: ExecutionEvent):
            task_created_events.append(evt)

        def on_any_event(evt: ExecutionEvent):
            all_events.append(evt)

        self.bus.subscribe(EventType.TASK_CREATED, on_task_created)
        self.bus.subscribe("*", on_any_event)

        # Emit TASK_CREATED
        self.bus.publish(EventType.TASK_CREATED, task_id="T-1", payload={"name": "Task 1"})
        # Emit FILE_CHANGED
        self.bus.publish(EventType.FILE_CHANGED, payload={"filepath": "main.py"})

        self.assertEqual(len(task_created_events), 1)
        self.assertEqual(task_created_events[0].task_id, "T-1")

        self.assertEqual(len(all_events), 2)
        self.assertEqual(all_events[0].event_type, EventType.TASK_CREATED)
        self.assertEqual(all_events[1].event_type, EventType.FILE_CHANGED)

        # Unsubscribe
        self.bus.unsubscribe(EventType.TASK_CREATED, on_task_created)
        self.bus.publish(EventType.TASK_CREATED, task_id="T-2", payload={"name": "Task 2"})
        self.assertEqual(len(task_created_events), 1)  # Unsubscribed, so not incremented
        self.assertEqual(len(all_events), 3)          # Wildcard still receives it

    def test_all_ten_canonical_lifecycle_events(self):
        """
        Validates emission and capture of all 10 canonical lifecycle events required by Issue #51:
        TASK_CREATED, TASK_STARTED, AGENT_SELECTED, TOOL_CALLED, TOOL_COMPLETED,
        FILE_CHANGED, TEST_STARTED, TEST_FAILED, REPLAN_STARTED, TASK_COMPLETED.
        """
        captured_types = []
        self.bus.subscribe("*", lambda evt: captured_types.append(evt.event_type_value))

        canonical_events = [
            (EventType.TASK_CREATED, {"objective": "Create database schema"}),
            (EventType.TASK_STARTED, {"status": "RUNNING"}),
            (EventType.AGENT_SELECTED, {"score": 3.85, "capabilities": ["backend", "sql"]}),
            (EventType.TOOL_CALLED, {"tool_name": "write_file", "arguments": {"filepath": "schema.sql"}}),
            (EventType.TOOL_COMPLETED, {"tool_name": "write_file", "status": "SUCCESS", "duration_seconds": 0.05}),
            (EventType.FILE_CHANGED, {"filepath": "schema.sql", "change_type": "CREATED", "version": "v1"}),
            (EventType.TEST_STARTED, {"stage": "UNIT_TEST", "command": "pytest"}),
            (EventType.TEST_FAILED, {"stage": "UNIT_TEST", "exit_code": 1, "error": "AssertionError"}),
            (EventType.REPLAN_STARTED, {"iteration": 1, "root_cause": "SyntaxError"}),
            (EventType.TASK_COMPLETED, {"verified_outputs": ["schema.sql"]}),
        ]

        for ev_type, payload in canonical_events:
            self.bus.publish(ev_type, payload=payload, task_id="T-01", agent_name="CODER")

        for ev_type, _ in canonical_events:
            self.assertIn(ev_type.value, captured_types)

        self.assertEqual(len(captured_types), 10)

    def test_event_bus_filtering_and_query(self):
        """Validates querying in-memory event stream with multiple filter criteria."""
        self.bus.publish(EventType.TASK_CREATED, task_id="T-01", session_id=self.session_id)
        self.bus.publish(EventType.TASK_STARTED, task_id="T-01", session_id=self.session_id)
        self.bus.publish(EventType.TASK_CREATED, task_id="T-02", session_id=self.session_id)
        self.bus.publish(EventType.TASK_STARTED, task_id="T-02", session_id=self.session_id)
        self.bus.publish(EventType.TASK_COMPLETED, task_id="T-01", session_id=self.session_id)

        all_evts = self.bus.get_events()
        self.assertEqual(len(all_evts), 5)

        t1_evts = self.bus.get_events(task_id="T-01")
        self.assertEqual(len(t1_evts), 3)

        created_evts = self.bus.get_events(event_type=EventType.TASK_CREATED)
        self.assertEqual(len(created_evts), 2)

        last_2 = self.bus.get_events(limit=2)
        self.assertEqual(len(last_2), 2)
        self.assertEqual(last_2[-1].event_type, EventType.TASK_COMPLETED)

    def test_sqlite_persistence_and_rehydration(self):
        """Validates SQLite persistence of ExecutionEvents and retrieval through SQLiteStateStore."""
        self.bus.publish(
            EventType.TOOL_CALLED,
            task_id="T-SQL",
            agent_name="CODER",
            payload={"tool_name": "replace_file_content", "filepath": "app.py"},
        )
        self.bus.publish(
            EventType.TEST_FAILED,
            task_id="T-SQL",
            agent_name="TESTER",
            payload={"stage": "UNIT_TEST", "exit_code": 2, "error": "ImportError: no module named app"},
        )

        stored = self.state_store.get_events(self.session_id)
        self.assertEqual(len(stored), 2)
        self.assertEqual(stored[0].event_type, "TOOL_CALLED")
        self.assertEqual(stored[0].payload["tool_name"], "replace_file_content")
        self.assertEqual(stored[1].event_type, "TEST_FAILED")
        self.assertEqual(stored[1].payload["exit_code"], 2)

        # Filter by event type in SQLite
        filtered = self.state_store.get_events(self.session_id, event_type="TEST_FAILED")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].event_type, "TEST_FAILED")

    def test_workspace_file_changed_broadcasting(self):
        """Validates WorkspaceManager automatically broadcasts FILE_CHANGED events through EventBus."""
        workspace = WorkspaceManager(root_dir=self.test_dir)
        workspace.set_event_bus(self.bus)

        file_events = []
        self.bus.subscribe(EventType.FILE_CHANGED, lambda evt: file_events.append(evt))

        # Write new file -> CREATED
        workspace.write_file("service.py", "def run(): pass\n")
        self.assertEqual(len(file_events), 1)
        self.assertEqual(file_events[0].event_type, EventType.FILE_CHANGED)
        self.assertEqual(file_events[0].payload["filepath"], "service.py")
        self.assertEqual(file_events[0].payload["change_type"], "CREATED")
        self.assertEqual(file_events[0].payload["revision"], 1)

        # Replace content -> MODIFIED
        workspace.replace_file_content(
            "service.py",
            target_content="def run(): pass\n",
            replacement_content="def run():\n    return 42\n",
        )
        self.assertEqual(len(file_events), 2)
        self.assertEqual(file_events[1].payload["change_type"], "MODIFIED")
        self.assertEqual(file_events[1].payload["revision"], 2)

        # Delete file -> DELETED
        workspace.delete_file("service.py")
        self.assertEqual(len(file_events), 3)
        self.assertEqual(file_events[2].payload["change_type"], "DELETED")

    def test_react_loop_tool_events(self):
        """Validates ReActAgentLoop emits TOOL_CALLED and TOOL_COMPLETED events through EventBus."""
        mock_llm = MagicMock(spec=["chat_json"])
        mock_llm.chat_json.side_effect = [
            # Turn 1: tool call
            {
                "thought": "I will read config.py",
                "tool_call": {
                    "name": "mock_tool",
                    "arguments": {"key": "val"},
                },
            },
            # Turn 2: final output
            {
                "thought": "Done",
                "final_output": "Completed successfully.",
            },
        ]

        mock_registry = MagicMock()
        mock_registry.call_tool.return_value = {"success": True, "output": "ok"}
        mock_registry.get_tools_for_agent.return_value = []

        react_loop = ReActAgentLoop(
            llm=mock_llm,
            tool_registry=mock_registry,
            max_turns=3,
            event_bus=self.bus,
        )

        tool_events = []
        self.bus.subscribe(EventType.TOOL_CALLED, lambda evt: tool_events.append(evt))
        self.bus.subscribe(EventType.TOOL_COMPLETED, lambda evt: tool_events.append(evt))

        react_loop.run(
            system_prompt="System",
            user_prompt="Run tool",
            available_tools=["mock_tool"],
            task_id="T-REACT",
            agent_name="CODER",
        )

        called_events = [e for e in tool_events if e.event_type == EventType.TOOL_CALLED]
        completed_events = [e for e in tool_events if e.event_type == EventType.TOOL_COMPLETED]

        self.assertEqual(len(called_events), 1)
        self.assertEqual(called_events[0].task_id, "T-REACT")
        self.assertEqual(called_events[0].payload["tool_name"], "mock_tool")

        self.assertEqual(len(completed_events), 1)
        self.assertEqual(completed_events[0].payload["tool_name"], "mock_tool")
        self.assertEqual(completed_events[0].payload["status"], "SUCCESS")

    def test_dag_scheduler_lifecycle_events(self):
        """Validates ConcurrentDAGScheduler emits TASK_STARTED, AGENT_SELECTED, and TASK_COMPLETED."""
        dag = TaskDAG()
        t1 = ExecutableTask(
            task_id="T-SCHED-1",
            objective="Build microservice API",
            required_capabilities=["backend"],
            state=TaskState.READY,
        )
        dag.add_task(t1)

        scheduler = ConcurrentDAGScheduler(max_workers=1, event_bus=self.bus)

        lifecycle_events = []
        self.bus.subscribe(EventType.TASK_STARTED, lambda evt: lifecycle_events.append(evt))
        self.bus.subscribe(EventType.AGENT_SELECTED, lambda evt: lifecycle_events.append(evt))
        self.bus.subscribe(EventType.TASK_COMPLETED, lambda evt: lifecycle_events.append(evt))

        mock_orch = MagicMock()
        mock_agent = MagicMock()
        mock_agent.name = "CODER"
        mock_agent.execute.return_value = {"success": True, "written_files": []}
        mock_orch.select_agent.return_value = mock_agent
        mock_orch.agent_registry.discover.return_value = [(MagicMock(name="CODER"), 4.0)]
        mock_orch.skill_registry.discover.return_value = []
        mock_orch.verification_gate.verify_task.return_value = MagicMock(passed=True, verified_outputs=[], failure_reasons=[], stdout="")
        mock_orch.message_bus = None
        mock_orch.active_session_id = self.session_id

        scheduler.execute_ready_wave(
            task_dag=dag,
            orchestrator=mock_orch,
            state_data={"user_request": "Build microservice API"},
        )

        started = [e for e in lifecycle_events if e.event_type == EventType.TASK_STARTED]
        selected = [e for e in lifecycle_events if e.event_type == EventType.AGENT_SELECTED]
        completed = [e for e in lifecycle_events if e.event_type == EventType.TASK_COMPLETED]

        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].task_id, "T-SCHED-1")

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].agent_name, "CODER")

        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].task_id, "T-SCHED-1")

    def test_build_pipeline_test_events(self):
        """Validates BuildVerificationPipeline emits TEST_STARTED, TEST_PASSED, and TEST_FAILED."""
        pipeline = BuildVerificationPipeline(
            workspace_dir=self.test_dir,
            event_bus=self.bus,
        )

        test_events = []
        self.bus.subscribe(EventType.TEST_STARTED, lambda evt: test_events.append(evt))
        self.bus.subscribe(EventType.TEST_PASSED, lambda evt: test_events.append(evt))
        self.bus.subscribe(EventType.TEST_FAILED, lambda evt: test_events.append(evt))

        # 1. Successful test command
        custom_passing = {PipelineStage.UNIT_TEST: "python -c \"import sys; sys.exit(0)\""}
        report_pass = pipeline.execute(stages=[PipelineStage.UNIT_TEST], custom_commands=custom_passing)
        self.assertTrue(report_pass.passed)

        started = [e for e in test_events if e.event_type == EventType.TEST_STARTED]
        passed = [e for e in test_events if e.event_type == EventType.TEST_PASSED]
        self.assertEqual(len(started), 1)
        self.assertEqual(len(passed), 1)
        self.assertEqual(passed[0].payload["stage"], "UNIT_TEST")

        # 2. Failing test command
        custom_failing = {PipelineStage.UNIT_TEST: "python -c \"import sys; sys.exit(1)\""}
        report_fail = pipeline.execute(stages=[PipelineStage.UNIT_TEST], custom_commands=custom_failing)
        self.assertFalse(report_fail.passed)

        failed = [e for e in test_events if e.event_type == EventType.TEST_FAILED]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].payload["stage"], "UNIT_TEST")
        self.assertEqual(failed[0].payload["exit_code"], 1)

    def test_legacy_on_event_callback_bridge(self):
        """Validates EventBus translates ExecutionEvents to legacy (stage, message, payload) callbacks."""
        legacy_calls = []

        def legacy_logger(stage: str, msg: str, payload=None):
            legacy_calls.append((stage, msg, payload))

        bridged_bus = EventBus(on_event_callback=legacy_logger, session_id="sess-legacy-001")
        bridged_bus.publish(
            EventType.TASK_CREATED,
            task_id="T-LEGACY-1",
            payload={"objective": "Migrate database"},
        )
        bridged_bus.publish(
            EventType.FILE_CHANGED,
            payload={"filepath": "db.py", "change_type": "MODIFIED"},
        )

        self.assertEqual(len(legacy_calls), 2)
        self.assertEqual(legacy_calls[0][0], "TASK_CREATED")
        self.assertIn("Migrate database", legacy_calls[0][1])
        self.assertEqual(legacy_calls[1][0], "FILE_CHANGED")
        self.assertIn("db.py", legacy_calls[1][1])


if __name__ == "__main__":
    unittest.main()
