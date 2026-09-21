"""
Tests for Issue #92: Workflow Persistence & Crash Recovery.
Verifies diagnosis of interrupted sessions, DAG reconciliation, graph state reconstruction,
and end-to-end resumption.
"""
from datetime import datetime
import os
import shutil
import tempfile
import time
import pytest

from agent_orchestrator.state import OrchestratorState, TaskStatus, ReviewVerdict
from agent_orchestrator.runtime.task_graph import (
    TaskDAG,
    ExecutableTask,
    TaskState,
    ObservationRecord,
    ArtifactRecord,
)
from agent_orchestrator.persistence.state_store import SQLiteStateStore
from agent_orchestrator.persistence.recovery import SessionRecoveryEngine, SessionRecoveryReport
from agent_orchestrator.orchestrator import TaskOrchestrator, OrchestratorConfig
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.llm import LLMClient


class MockLLM(LLMClient):
    """Mock LLM returning deterministic responses for recovery tests."""
    def __init__(self):
        super().__init__()

    def chat(self, messages, **kwargs):
        return "Mock LLM output"

    def chat_json(self, messages, **kwargs):
        content = messages[-1].get("content", "")
        if "Review" in content or "Quality Audit" in content:
            return {"verdict": "PASS", "score_out_of_100": 95, "summary": "All tests passed"}
        return {}


@pytest.fixture
def temp_workspace():
    tmp_dir = tempfile.mkdtemp(prefix="test_recovery_")
    ws = WorkspaceManager(tmp_dir)
    yield ws
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def state_store(temp_workspace):
    db_path = os.path.join(temp_workspace.root_dir, ".orchestrator", "orchestrator_state.db")
    store = SQLiteStateStore(db_path=db_path)
    yield store
    store.close()


def test_crash_diagnosis_answers_all_questions(state_store):
    session_id = "sess-crash-test-01"

    # Create session state
    state = OrchestratorState(user_request="Build resilient cache subsystem")
    state.status = TaskStatus.IN_PROGRESS
    state.verdict = ReviewVerdict.UNDECIDED
    state_store.save_session(session_id, state)

    # Task 1: COMPLETED
    t1 = ExecutableTask(
        task_id="T-01",
        objective="Implement Cache Interface",
        state=TaskState.COMPLETED,
        result_data={"code": "class Cache: pass"},
        completed_at=datetime.now().isoformat(),
    )
    t1.typed_artifacts.append(ArtifactRecord.create(
        name="cache_interface.py",
        content="class Cache: pass",
        task_id="T-01",
        execution_id=session_id,
    ))
    state_store.save_task(session_id, t1)

    # Record file change for Task 1
    state_store.save_file_change(
        session_id=session_id,
        task_id="T-01",
        change={
            "filepath": "src/cache.py",
            "operation": "CREATE",
            "lines_added": 10,
            "lines_removed": 0,
        },
    )

    # Task 2: RUNNING (crashed midway)
    t2 = ExecutableTask(
        task_id="T-02",
        objective="Implement LRU Eviction Logic",
        state=TaskState.RUNNING,
        dependencies=["T-01"],
    )
    state_store.save_task(session_id, t2)

    # Save step-level micro checkpoints for Task 2
    state_store.save_task_step(
        session_id=session_id,
        task_id="T-02",
        turn=1,
        stage="EXECUTE",
        thought="Writing eviction policy...",
        tool_name="filesystem.write_file",
        tool_args={"path": "src/lru.py", "content": "# lru"},
        tool_result={"bytes_written": 6},
    )
    state_store.save_task_step(
        session_id=session_id,
        task_id="T-02",
        turn=2,
        stage="EXECUTE",
        thought="Running pytest...",
        tool_name="terminal.execute",
        tool_args={"command": "pytest tests/test_lru.py"},
        tool_result={"exit_code": 1, "stderr": "ImportError"},
    )

    # Task 3: PENDING
    t3 = ExecutableTask(
        task_id="T-03",
        objective="Integration Verification",
        state=TaskState.PENDING,
        dependencies=["T-02"],
    )
    state_store.save_task(session_id, t3)

    # 1. Diagnose crash
    report = SessionRecoveryEngine.diagnose_session(session_id, state_store)

    assert isinstance(report, SessionRecoveryReport)
    assert report.session_id == session_id
    assert report.total_tasks == 3
    assert len(report.completed_tasks) == 1
    assert report.completed_tasks[0]["task_id"] == "T-01"

    # Check question 1: What was completed?
    assert len(report.completed_tasks) == 1
    assert report.progress_percentage == pytest.approx(33.33, 0.1)

    # Check question 2: What changed?
    assert len(report.modified_files) == 1
    assert report.modified_files[0]["filepath"] == "src/cache.py"

    # Check question 3: What tool ran?
    assert len(report.last_tool_invocations) >= 1
    last_inv = next(inv for inv in report.last_tool_invocations if inv["task_id"] == "T-02")
    assert last_inv["tool_name"] == "terminal.execute"
    assert last_inv["turn"] == 2

    # Check question 4: What should resume?
    assert "T-02" in report.suggested_resume_point

    # Check human-readable summary
    summary_text = report.summary()
    assert "1. WHAT WAS COMPLETED:" in summary_text
    assert "2. WHAT CHANGED" in summary_text
    assert "3. WHAT TOOL RAN" in summary_text
    assert "4. WHAT SHOULD RESUME:" in summary_text
    assert "src/cache.py" in summary_text
    assert "terminal.execute" in summary_text


def test_dag_reconciliation_after_crash(state_store):
    session_id = "sess-reconcile-test"

    t1 = ExecutableTask(task_id="T-01", objective="Task 1", state=TaskState.COMPLETED)
    t2 = ExecutableTask(task_id="T-02", objective="Task 2", state=TaskState.RUNNING, dependencies=["T-01"])
    t3 = ExecutableTask(task_id="T-03", objective="Task 3", state=TaskState.PENDING, dependencies=["T-02"])

    state_store.save_task(session_id, t1)
    state_store.save_task(session_id, t2)
    state_store.save_task(session_id, t3)

    # Reconcile DAG
    task_dag, ready_tasks = SessionRecoveryEngine.reconcile_dag_for_resume(session_id, state_store)

    assert len(task_dag.list_tasks()) == 3
    # Interrupted task T-02 should now be reset to READY
    t2_reconciled = task_dag.get_task("T-02")
    assert t2_reconciled.state == TaskState.READY

    # Ready tasks should contain T-02
    assert len(ready_tasks) == 1
    assert ready_tasks[0].task_id == "T-02"

    # Task T-03 should still be PENDING (dep T-02 not completed)
    t3_reconciled = task_dag.get_task("T-03")
    assert t3_reconciled.state == TaskState.PENDING


def test_reconstruct_graph_state(state_store):
    session_id = "sess-graph-reconstruct"

    state = OrchestratorState(user_request="Build high performance worker pool")
    state.project_profile = {"primary_language": "python"}
    state.plan_output = {"steps": ["T-01", "T-02"]}
    state_store.save_session(session_id, state)

    t1 = ExecutableTask(
        task_id="T-01",
        objective="Create worker pool",
        state=TaskState.COMPLETED,
        result_data={"created": True},
    )
    t2 = ExecutableTask(
        task_id="T-02",
        objective="Add async dispatch",
        state=TaskState.PENDING,
        dependencies=["T-01"],
    )
    state_store.save_task(session_id, t1)
    state_store.save_task(session_id, t2)

    graph_state = SessionRecoveryEngine.reconstruct_graph_state(session_id, state_store)

    assert graph_state["user_request"] == "Build high performance worker pool"
    assert graph_state["project_profile"] == {"primary_language": "python"}
    assert len(graph_state["subtasks"]) == 2
    assert len(graph_state["completed_subtasks"]) == 1
    assert graph_state["step_results"]["T-01"] == {"created": True}


def test_list_recoverable_sessions(state_store):
    # Session 1: completed
    s1 = OrchestratorState(user_request="Completed task")
    s1.status = TaskStatus.COMPLETED
    s1.verdict = ReviewVerdict.PASS
    state_store.save_session("sess-done", s1)
    t1 = ExecutableTask(task_id="T-01", objective="Done", state=TaskState.COMPLETED)
    state_store.save_task("sess-done", t1)

    # Session 2: crashed/in-progress
    s2 = OrchestratorState(user_request="Crashed task")
    s2.status = TaskStatus.IN_PROGRESS
    s2.verdict = ReviewVerdict.UNDECIDED
    state_store.save_session("sess-crashed", s2)
    t2 = ExecutableTask(task_id="T-01", objective="Running", state=TaskState.RUNNING)
    state_store.save_task("sess-crashed", t2)

    recoverable = SessionRecoveryEngine.list_recoverable_sessions(state_store)
    recoverable_ids = [r["session_id"] for r in recoverable]

    assert "sess-crashed" in recoverable_ids
    assert "sess-done" not in recoverable_ids


def test_cleanup_stale_sandboxes(temp_workspace):
    sandboxes_dir = os.path.join(temp_workspace.root_dir, ".orchestrator", "sandboxes")
    os.makedirs(sandboxes_dir, exist_ok=True)

    dummy_old_sandbox = os.path.join(sandboxes_dir, "sb-old-crashed")
    os.makedirs(dummy_old_sandbox, exist_ok=True)
    with open(os.path.join(dummy_old_sandbox, "test.txt"), "w") as f:
        f.write("old data")

    # Set old timestamp
    old_time = time.time() - 7200
    os.utime(dummy_old_sandbox, (old_time, old_time))

    cleaned = SessionRecoveryEngine.cleanup_stale_sandboxes(
        workspace_dir=str(temp_workspace.root_dir),
        max_age_seconds=3600,
    )

    assert cleaned == 1
    assert not os.path.exists(dummy_old_sandbox)


def test_orchestrator_diagnose_and_resume(temp_workspace, state_store):
    mock_llm = MockLLM()
    orch = TaskOrchestrator(
        llm=mock_llm,
        workspace=temp_workspace,
        state_store=state_store,
    )

    session_id = "sess-e2e-resume"
    state = OrchestratorState(user_request="Add health check endpoint")
    state.status = TaskStatus.IN_PROGRESS
    state_store.save_session(session_id, state)

    t1 = ExecutableTask(
        task_id="T-01",
        objective="Create endpoint file",
        state=TaskState.COMPLETED,
        result_data={"success": True},
    )
    t2 = ExecutableTask(
        task_id="T-02",
        objective="Verify endpoint with tests",
        state=TaskState.RUNNING,
        dependencies=["T-01"],
    )
    state_store.save_task(session_id, t1)
    state_store.save_task(session_id, t2)

    # Call diagnose_session via orchestrator
    report = orch.diagnose_session(session_id)
    assert report.session_id == session_id
    assert len(report.completed_tasks) == 1
    assert len(report.interrupted_tasks) == 1

    # Call list_recoverable_sessions via orchestrator
    rec_sessions = orch.list_recoverable_sessions()
    assert any(s["session_id"] == session_id for s in rec_sessions)
