"""
State representation for the Task-Orchestrator multi-agent workflow.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


from .runtime.task_graph import (
    TaskDAG,
    ExecutableTask,
    TaskState,
    TaskPermissions,
    RetryPolicy,
    TokenUsage,
    TaskAttemptRecord,
    CheckpointRecord,
    ObservationRecord,
)


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NEED_VERIFICATION = "NEED_VERIFICATION"
    INCOMPLETE = "INCOMPLETE"
    STOPPED = "STOPPED"
    NEED_MORE_EVIDENCE = "NEED_MORE_EVIDENCE"


class ReviewVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNDECIDED = "UNDECIDED"


@dataclass
class AgentMessage:
    agent_name: str
    stage: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    content: str = ""
    structured_data: Optional[Dict[str, Any]] = None


@dataclass
class ReplanRecord:
    iteration: int
    trigger_reason: str
    feedback_summary: str
    remediation_plan: List[str]
    rollback_executed: bool = False
    rollback_details: Optional[Dict[str, Any]] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    invalid_assumptions: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    invalid_task_ids: List[str] = field(default_factory=list)
    pruned_task_ids: List[str] = field(default_factory=list)
    injected_task_ids: List[str] = field(default_factory=list)
    parallel_groups: List[List[str]] = field(default_factory=list)


@dataclass
class OrchestratorState:
    # 1. Initial input & Session Identity
    user_request: str
    status: TaskStatus = TaskStatus.PENDING
    session_id: Optional[str] = None
    workspace_dir: Optional[str] = None
    git_branch: Optional[str] = "main"
    git_commit: Optional[str] = ""

    # 2. Stage 0, 1 & 2: Discover, Understand & Decompose (DAG & Legacy list)
    project_profile: Optional[Dict[str, Any]] = None
    environment_profile: Optional[Dict[str, Any]] = None
    task_understanding: Optional[Dict[str, Any]] = None
    task_decomposition: Optional[List[Dict[str, Any]]] = None
    task_dag: Optional[TaskDAG] = None

    # 3. Intermediate agent artifacts
    plan_output: Optional[Dict[str, Any]] = None
    specification_output: Optional[Dict[str, Any]] = None
    architecture_output: Optional[Dict[str, Any]] = None
    code_output: Optional[Dict[str, Any]] = None
    test_output: Optional[Dict[str, Any]] = None
    review_output: Optional[Dict[str, Any]] = None
    baseline_test_output: Optional[Dict[str, Any]] = None
    existing_test_context: Optional[Dict[str, Any]] = None
    artifact_store: Optional[Any] = None
    event_bus: Optional[Any] = None

    # 4. Review & Re-planning state
    verdict: ReviewVerdict = ReviewVerdict.UNDECIDED
    replan_history: List[ReplanRecord] = field(default_factory=list)
    rollback_history: List[Dict[str, Any]] = field(default_factory=list)
    current_iteration: int = 0
    max_iterations: int = 3

    # 5. Runtime Telemetry & Execution Aggregates
    total_token_usage: TokenUsage = field(default_factory=TokenUsage)
    total_cost_usd: float = 0.0
    total_duration_seconds: float = 0.0
    telemetry: Optional[Any] = None
    telemetry_engine: Optional[Any] = None
    trace_tree: Optional[Dict[str, Any]] = None
    tracer: Optional[Any] = None
    execution_snapshot: Optional[Any] = None

    # 6. Full conversation & event trace
    messages: List[AgentMessage] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def add_message(self, agent_name: str, stage: str, content: str, structured_data: Optional[Dict[str, Any]] = None):
        msg = AgentMessage(
            agent_name=agent_name,
            stage=stage,
            content=content,
            structured_data=structured_data,
        )
        self.messages.append(msg)
        self.updated_at = datetime.now().isoformat()

    def get_recent_messages(self, k: int = 10) -> List[AgentMessage]:
        """Returns the most recent k messages from the trace."""
        return self.messages[-k:] if k > 0 else []

    def get_messages_by_role(self, role: str) -> List[AgentMessage]:
        """Filters messages by agent name/role (case-insensitive)."""
        clean_role = role.upper().strip()
        return [m for m in self.messages if m.agent_name.upper().strip() == clean_role]

    def get_messages_by_stage(self, stage: str) -> List[AgentMessage]:
        """Filters messages by stage (case-insensitive)."""
        clean_stage = stage.upper().strip()
        return [m for m in self.messages if m.stage.upper().strip() == clean_stage]

    def get_trace_summary(self, max_tokens: int = 1000) -> str:
        """Returns a compact summary of the message trace suitable for context injection."""
        if not self.messages:
            return "No messages recorded."
        lines = ["**Execution Message Trace**:"]
        for m in self.messages[-10:]:
            lines.append(f"• [{m.agent_name} | {m.stage}]: {m.content}")
        summary = "\n".join(lines)
        max_chars = max_tokens * 4
        if len(summary) > max_chars:
            return summary[:max_chars] + "\n... [Trace truncated] ..."
        return summary


    def record_replan(self, trigger_reason: str, feedback_summary: str, remediation_plan: List[str]):
        record = ReplanRecord(
            iteration=self.current_iteration,
            trigger_reason=trigger_reason,
            feedback_summary=feedback_summary,
            remediation_plan=remediation_plan,
        )
        self.replan_history.append(record)
        self.updated_at = datetime.now().isoformat()

    def get_execution_summary(self) -> Dict[str, Any]:
        """Returns comprehensive aggregated runtime telemetry across all tasks and attempts."""
        tasks = self.task_dag.list_tasks() if self.task_dag else []
        completed = [t for t in tasks if t.state == TaskState.COMPLETED]
        failed = [t for t in tasks if t.state == TaskState.FAILED]
        running = [t for t in tasks if t.state in (TaskState.RUNNING, TaskState.VERIFYING)]
        pending = [t for t in tasks if t.state in (TaskState.PENDING, TaskState.READY, TaskState.BLOCKED)]

        distinct_tools = set()
        distinct_skills = set()
        total_attempts = 0
        total_checkpoints = 0
        total_observations = 0

        for t in tasks:
            distinct_tools.update(t.tools_used)
            distinct_skills.update(t.skills_used)
            total_attempts += len(t.attempts)
            total_checkpoints += len(t.checkpoints)
            total_observations += len(t.observations)

        summary = {
            "status": self.status.value if isinstance(self.status, TaskStatus) else str(self.status),
            "verdict": self.verdict.value if isinstance(self.verdict, ReviewVerdict) else str(self.verdict),
            "total_tasks": len(tasks),
            "completed_tasks": len(completed),
            "failed_tasks": len(failed),
            "running_tasks": len(running),
            "pending_tasks": len(pending),
            "total_attempts": total_attempts,
            "total_checkpoints": total_checkpoints,
            "total_observations": total_observations,
            "total_tokens": self.total_token_usage.to_dict(),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_duration_seconds": round(self.total_duration_seconds, 4),
            "distinct_tools_used": sorted(list(distinct_tools)),
            "distinct_skills_used": sorted(list(distinct_skills)),
            "replan_iterations": self.current_iteration,
            "messages_count": len(self.messages),
        }
        if self.telemetry:
            if hasattr(self.telemetry, "to_dict"):
                summary["telemetry"] = self.telemetry.to_dict()
            elif isinstance(self.telemetry, dict):
                summary["telemetry"] = self.telemetry
        return summary

    def get_task_tree(self) -> List[Dict[str, Any]]:
        """Returns hierarchical tree structure of tasks based on parent_task_id."""
        if not self.task_dag:
            return []
        tasks = self.task_dag.list_tasks()
        task_map = {t.task_id: {**t.to_dict(), "children": []} for t in tasks}
        roots = []
        for t in tasks:
            t_dict = task_map[t.task_id]
            if t.parent_task_id and t.parent_task_id in task_map:
                task_map[t.parent_task_id]["children"].append(t_dict)
            else:
                roots.append(t_dict)
        return roots

    def to_dict(self) -> Dict[str, Any]:
        """Serializes orchestrator state into JSON-friendly dictionary."""
        return {
            "session_id": self.session_id,
            "user_request": self.user_request,
            "status": self.status.value if isinstance(self.status, TaskStatus) else str(self.status),
            "verdict": self.verdict.value if isinstance(self.verdict, ReviewVerdict) else str(self.verdict),
            "workspace_dir": self.workspace_dir,
            "git_branch": self.git_branch,
            "git_commit": self.git_commit,
            "current_iteration": self.current_iteration,
            "max_iterations": self.max_iterations,
            "project_profile": self.project_profile,
            "environment_profile": self.environment_profile,
            "task_understanding": self.task_understanding,
            "task_decomposition": self.task_decomposition,
            "plan_output": self.plan_output,
            "specification_output": self.specification_output,
            "architecture_output": self.architecture_output,
            "code_output": self.code_output,
            "test_output": self.test_output,
            "review_output": self.review_output,
            "baseline_test_output": self.baseline_test_output,
            "existing_test_context": self.existing_test_context,
            "replan_history": [r.to_dict() if hasattr(r, "to_dict") else r for r in self.replan_history],
            "rollback_history": self.rollback_history,
            "total_token_usage": self.total_token_usage.to_dict() if hasattr(self.total_token_usage, "to_dict") else self.total_token_usage,
            "total_cost_usd": self.total_cost_usd,
            "total_duration_seconds": self.total_duration_seconds,
            "messages": [m.to_dict() if hasattr(m, "to_dict") else m for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

