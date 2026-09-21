"""
SessionRecoveryEngine: Durable Workflow & Task Recovery Subsystem.
Enables instant crash diagnosis and deterministic resumption of interrupted multi-agent workflows.
Answers the 4 critical recovery questions:
  1. What was completed?
  2. What changed?
  3. What tool ran?
  4. What should resume?
"""
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

from .state_store import SQLiteStateStore
from ..runtime.task_graph import TaskDAG, ExecutableTask, TaskState


@dataclass
class SessionRecoveryReport:
    """
    Structured crash diagnosis report summarizing the state of an interrupted workflow.
    """
    session_id: str
    user_request: str
    status: str
    verdict: str
    created_at: str
    updated_at: str
    completed_tasks: List[Dict[str, Any]] = field(default_factory=list)
    interrupted_tasks: List[Dict[str, Any]] = field(default_factory=list)
    pending_tasks: List[Dict[str, Any]] = field(default_factory=list)
    failed_tasks: List[Dict[str, Any]] = field(default_factory=list)
    modified_files: List[Dict[str, Any]] = field(default_factory=list)
    last_tool_invocations: List[Dict[str, Any]] = field(default_factory=list)
    suggested_resume_point: str = "Start of workflow"
    total_tasks: int = 0
    progress_percentage: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_request": self.user_request,
            "status": self.status,
            "verdict": self.verdict,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_tasks": self.total_tasks,
            "completed_tasks_count": len(self.completed_tasks),
            "interrupted_tasks_count": len(self.interrupted_tasks),
            "pending_tasks_count": len(self.pending_tasks),
            "failed_tasks_count": len(self.failed_tasks),
            "progress_percentage": self.progress_percentage,
            "completed_tasks": self.completed_tasks,
            "interrupted_tasks": self.interrupted_tasks,
            "pending_tasks": self.pending_tasks,
            "failed_tasks": self.failed_tasks,
            "modified_files": self.modified_files,
            "last_tool_invocations": self.last_tool_invocations,
            "suggested_resume_point": self.suggested_resume_point,
        }

    def summary(self) -> str:
        """Generates a human-readable recovery summary."""
        lines = [
            f"=== Session Recovery Diagnosis [{self.session_id}] ===",
            f"Goal: '{self.user_request}'",
            f"Status: {self.status} | Verdict: {self.verdict} | Progress: {self.progress_percentage:.1f}% ({len(self.completed_tasks)}/{self.total_tasks} tasks)",
            "",
            "1. WHAT WAS COMPLETED:",
        ]
        if self.completed_tasks:
            for ct in self.completed_tasks:
                lines.append(f"  - [{ct.get('task_id')}] {ct.get('objective')} (Agent: {ct.get('owner_agent', 'N/A')})")
        else:
            lines.append("  - (No tasks completed prior to interruption)")

        lines.extend([
            "",
            "2. WHAT CHANGED (Files Modified):",
        ])
        if self.modified_files:
            for mf in self.modified_files:
                lines.append(f"  - {mf.get('filepath')} ({mf.get('operation', 'MODIFIED')}, +{mf.get('lines_added', 0)}/-{mf.get('lines_removed', 0)} lines)")
        else:
            lines.append("  - (No file changes recorded in durable state)")

        lines.extend([
            "",
            "3. WHAT TOOL RAN (Last Invocations):",
        ])
        if self.last_tool_invocations:
            for ti in self.last_tool_invocations:
                lines.append(f"  - Task [{ti.get('task_id')}] Turn {ti.get('turn', '?')}: Tool `{ti.get('tool_name')}` @ {ti.get('timestamp')}")
        else:
            lines.append("  - (No tool invocations recorded)")

        lines.extend([
            "",
            "4. WHAT SHOULD RESUME:",
            f"  -> {self.suggested_resume_point}",
            "==================================================",
        ])
        return "\n".join(lines)


class SessionRecoveryEngine:
    """
    Engine for inspecting, diagnosing, and reconstructing interrupted multi-agent sessions.
    """

    @classmethod
    def diagnose_session(cls, session_id: str, state_store: SQLiteStateStore) -> SessionRecoveryReport:
        """
        Diagnoses an interrupted or crashed session by querying SQLite state store tables.
        """
        state = state_store.load_session(session_id)
        raw_sessions = state_store.list_sessions()
        session_meta = next((s for s in raw_sessions if s.get("session_id") == session_id), {})

        user_request = state.user_request if state else session_meta.get("user_request", f"Session {session_id}")
        status = state.status.value if state and hasattr(state.status, "value") else str(session_meta.get("status", "UNKNOWN"))
        verdict = state.verdict.value if state and hasattr(state.verdict, "value") else str(session_meta.get("verdict", "UNDECIDED"))
        created_at = state.created_at if state else session_meta.get("created_at", "")
        updated_at = state.updated_at if state else session_meta.get("updated_at", "")

        tasks = state_store.load_tasks(session_id)
        completed: List[Dict[str, Any]] = []
        interrupted: List[Dict[str, Any]] = []
        pending: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []

        last_tool_invocations: List[Dict[str, Any]] = []

        for t in tasks:
            t_dict = t.to_dict()
            steps = state_store.load_task_steps(session_id, t.task_id)
            latest_step = steps[-1] if steps else None

            if latest_step and latest_step.get("tool_name"):
                last_tool_invocations.append({
                    "task_id": t.task_id,
                    "turn": latest_step.get("turn"),
                    "tool_name": latest_step.get("tool_name"),
                    "tool_args": latest_step.get("tool_args"),
                    "timestamp": latest_step.get("timestamp"),
                })

            if t.state == TaskState.COMPLETED:
                completed.append(t_dict)
            elif t.state in (TaskState.RUNNING, TaskState.VERIFYING):
                t_dict["last_turn"] = latest_step.get("turn", 0) if latest_step else 0
                t_dict["last_tool"] = latest_step.get("tool_name") if latest_step else None
                t_dict["observations_count"] = len(t.observations)
                interrupted.append(t_dict)
            elif t.state == TaskState.FAILED:
                failed.append(t_dict)
            else:
                pending.append(t_dict)

        modified_files = state_store.get_session_changes(session_id)
        total_tasks = len(tasks)
        progress = (len(completed) / total_tasks * 100.0) if total_tasks > 0 else 0.0

        # Determine suggested resume point
        if not tasks:
            suggested = "Re-initialize workflow from planning and decomposition"
        elif interrupted:
            interrupted_ids = [it.get("task_id") for it in interrupted]
            suggested = f"Resume interrupted task(s) {interrupted_ids} from latest micro-step checkpoints"
        elif failed:
            failed_ids = [ft.get("task_id") for ft in failed]
            suggested = f"Re-attempt failed task(s) {failed_ids} via dynamic replanning"
        elif pending:
            # Find next ready tasks
            completed_ids = {c["task_id"] for c in completed}
            ready_next = [
                p["task_id"] for p in pending
                if all(dep in completed_ids for dep in p.get("dependencies", []))
            ]
            if ready_next:
                suggested = f"Execute next ready subtask wave: {ready_next}"
            else:
                suggested = "Resolve blocked dependencies for pending tasks"
        elif verdict == "PASS":
            suggested = "Workflow already completed and verified"
        else:
            suggested = "Proceed directly to final Reviewer node"

        return SessionRecoveryReport(
            session_id=session_id,
            user_request=user_request,
            status=status,
            verdict=verdict,
            created_at=created_at,
            updated_at=updated_at,
            total_tasks=total_tasks,
            progress_percentage=progress,
            completed_tasks=completed,
            interrupted_tasks=interrupted,
            pending_tasks=pending,
            failed_tasks=failed,
            modified_files=modified_files,
            last_tool_invocations=last_tool_invocations,
            suggested_resume_point=suggested,
        )

    @classmethod
    def reconcile_dag_for_resume(
        cls,
        session_id: str,
        state_store: SQLiteStateStore,
    ) -> Tuple[TaskDAG, List[ExecutableTask]]:
        """
        Reconciles task states in the DAG after a crash.
        Resets stale RUNNING and VERIFYING tasks back to READY (or checks retry limits),
        re-evaluates dependencies, and saves normalized states back to SQLite.
        """
        tasks = state_store.load_tasks(session_id)
        if not tasks:
            return TaskDAG([]), []

        completed_ids = {t.task_id for t in tasks if t.state == TaskState.COMPLETED}

        for t in tasks:
            # If task was interrupted while running or verifying, reset to READY to be safely re-executed
            if t.state in (TaskState.RUNNING, TaskState.VERIFYING):
                t.state = TaskState.READY
                state_store.save_task(session_id, t)
            elif t.state == TaskState.PENDING:
                # Check if all dependencies are satisfied
                if all(dep in completed_ids for dep in t.dependencies):
                    t.state = TaskState.READY
                    state_store.save_task(session_id, t)

        task_dag = TaskDAG(tasks)
        ready_tasks = task_dag.get_ready_tasks()
        return task_dag, ready_tasks

    @classmethod
    def reconstruct_graph_state(
        cls,
        session_id: str,
        state_store: SQLiteStateStore,
        workspace: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Reconstructs the full OrchestratorGraphState from SQLite durable storage for resumption.
        """
        loaded_state = state_store.load_session(session_id)
        if not loaded_state:
            raise ValueError(f"No stored session found for ID '{session_id}'")

        task_dag, _ = cls.reconcile_dag_for_resume(session_id, state_store)
        tasks = task_dag.list_tasks()
        completed_tasks = [t for t in tasks if t.state == TaskState.COMPLETED]

        step_results = {}
        for t in completed_tasks:
            if t.result_data:
                step_results[t.task_id] = t.result_data

        subtasks_list = task_dag.to_list()
        current_idx = len(completed_tasks)

        graph_state: Dict[str, Any] = {
            "user_request": loaded_state.user_request,
            "project_profile": loaded_state.project_profile,
            "environment_profile": loaded_state.environment_profile,
            "task_understanding": loaded_state.task_understanding,
            "task_decomposition": subtasks_list,
            "subtasks": subtasks_list,
            "current_subtask_index": current_idx,
            "completed_subtasks": [t.to_dict() for t in completed_tasks],
            "step_results": step_results,
            "plan_output": loaded_state.plan_output,
            "specification_output": loaded_state.specification_output,
            "architecture_output": loaded_state.architecture_output,
            "code_output": loaded_state.code_output,
            "test_output": loaded_state.test_output,
            "review_output": loaded_state.review_output,
            "verdict": loaded_state.verdict.value if hasattr(loaded_state.verdict, "value") else str(loaded_state.verdict),
            "iteration": loaded_state.current_iteration,
            "max_iterations": loaded_state.max_iterations,
            "remediation_plan": [],
            "replan_history": [r.__dict__ if hasattr(r, "__dict__") else r for r in loaded_state.replan_history],
            "target_agent_for_fix": "CODER",
            "status": "IN_PROGRESS",
            "messages": [m.__dict__ if hasattr(m, "__dict__") else m for m in loaded_state.messages],
            "baseline_test_info": loaded_state.baseline_test_output,
            "rollback_executed": False,
            "last_rollback": None,
        }
        return graph_state

    @classmethod
    def list_recoverable_sessions(cls, state_store: SQLiteStateStore) -> List[Dict[str, Any]]:
        """
        Lists all sessions that can be recovered (in-progress, interrupted, or stopped).
        """
        all_sessions = state_store.list_sessions()
        recoverable: List[Dict[str, Any]] = []

        for s in all_sessions:
            sid = s.get("session_id")
            if not sid:
                continue
            tasks = state_store.load_tasks(sid)
            total = len(tasks)
            completed = sum(1 for t in tasks if t.state == TaskState.COMPLETED)
            interrupted = sum(1 for t in tasks if t.state in (TaskState.RUNNING, TaskState.VERIFYING))
            failed = sum(1 for t in tasks if t.state == TaskState.FAILED)

            is_recoverable = (
                s.get("status") not in ("COMPLETED", "CANCELLED")
                or interrupted > 0
                or (total > 0 and completed < total)
                or (s.get("verdict") != "PASS" and s.get("status") != "CANCELLED")
            )

            if is_recoverable:
                recoverable.append({
                    "session_id": sid,
                    "user_request": s.get("user_request"),
                    "status": s.get("status"),
                    "verdict": s.get("verdict"),
                    "total_tasks": total,
                    "completed_tasks": completed,
                    "interrupted_tasks": interrupted,
                    "failed_tasks": failed,
                    "progress_percentage": round(completed / total * 100.0, 1) if total > 0 else 0.0,
                    "created_at": s.get("created_at"),
                    "updated_at": s.get("updated_at"),
                })

        return recoverable

    @classmethod
    def cleanup_stale_sandboxes(cls, workspace_dir: str, max_age_seconds: int = 3600) -> int:
        """
        Cleans up orphaned sandbox directories left behind by crashed processes.
        """
        sandboxes_dir = os.path.join(workspace_dir, ".orchestrator", "sandboxes")
        if not os.path.exists(sandboxes_dir):
            return 0

        now = time.time()
        cleaned = 0
        try:
            for item in os.listdir(sandboxes_dir):
                item_path = os.path.join(sandboxes_dir, item)
                if os.path.isdir(item_path):
                    try:
                        mtime = os.path.getmtime(item_path)
                        if (now - mtime) > max_age_seconds:
                            shutil.rmtree(item_path, ignore_errors=True)
                            cleaned += 1
                    except Exception:
                        pass
        except Exception:
            pass

        return cleaned
