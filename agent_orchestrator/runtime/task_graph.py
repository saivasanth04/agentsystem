"""
TaskDAG: Production-Grade Topological Task Graph Engine for Autonomous Coding Agents.
Manages dependencies, execution lifecycle state machines, cycle detection, and dynamic remediation injection.
"""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any, Dict, List, Optional, Set, Union
import uuid


class TaskState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    SKIPPED_REDUNDANT = "SKIPPED_REDUNDANT"
    CANCELLED = "CANCELLED"


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def calculate_cost(self, price_per_1m_prompt: float = 0.15, price_per_1m_completion: float = 0.60) -> float:
        self.cost_usd = (self.prompt_tokens / 1_000_000 * price_per_1m_prompt) + (self.completion_tokens / 1_000_000 * price_per_1m_completion)
        return self.cost_usd

    def add(self, other: "TokenUsage"):
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.cost_usd += other.cost_usd

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "TokenUsage":
        if not data:
            return cls()
        return cls(
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
        )


@dataclass
class ArtifactRecord:
    artifact_id: str
    name: str
    artifact_type: str = "VERIFIED_OUTPUT"  # FILE_DIFF | VERIFIED_OUTPUT | TERMINAL_LOG | TEST_REPORT | JSON_METRICS | GENERIC
    category: str = "reports"  # plans | specifications | patches | logs | test_results | reports | snapshots
    uri_or_path: str = ""
    content_hash: str = ""
    size_bytes: int = 0
    mime_type: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    task_id: Optional[str] = None
    attempt_id: Optional[str] = None
    execution_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @classmethod
    def create(
        cls,
        name: str,
        content: Union[str, bytes, Dict[str, Any], List[Any]],
        artifact_type: str = "VERIFIED_OUTPUT",
        category: str = "reports",
        uri_or_path: str = "",
        task_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        mime_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ArtifactRecord":
        if isinstance(content, str):
            raw_bytes = content.encode("utf-8", errors="replace")
        elif isinstance(content, (bytes, bytearray)):
            raw_bytes = bytes(content)
        else:
            raw_bytes = json.dumps(content, default=str, sort_keys=True).encode("utf-8")

        chash = hashlib.sha256(raw_bytes).hexdigest()
        aid = f"art-{chash[:12]}"
        return cls(
            artifact_id=aid,
            name=name,
            artifact_type=artifact_type,
            category=category,
            uri_or_path=uri_or_path,
            content_hash=chash,
            size_bytes=len(raw_bytes),
            mime_type=mime_type,
            metadata=metadata or {},
            task_id=task_id,
            attempt_id=attempt_id,
            execution_id=execution_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "artifact_type": self.artifact_type,
            "category": self.category,
            "uri_or_path": self.uri_or_path,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
            "metadata": self.metadata,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "execution_id": self.execution_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArtifactRecord":
        return cls(
            artifact_id=data.get("artifact_id") or f"art-{uuid.uuid4().hex[:12]}",
            name=data.get("name", "artifact"),
            artifact_type=data.get("artifact_type", "GENERIC"),
            category=data.get("category", "reports"),
            uri_or_path=data.get("uri_or_path", ""),
            content_hash=data.get("content_hash", ""),
            size_bytes=int(data.get("size_bytes", 0)),
            mime_type=data.get("mime_type"),
            metadata=data.get("metadata") or {},
            task_id=data.get("task_id"),
            attempt_id=data.get("attempt_id"),
            execution_id=data.get("execution_id"),
            created_at=data.get("created_at", datetime.now().isoformat()),
        )


@dataclass
class ObservationRecord:
    turn: int = 1
    tool_name: str = "UNKNOWN"
    input_args: Dict[str, Any] = field(default_factory=dict)
    output_result: Any = None
    is_error: bool = False
    duration_seconds: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    observation_id: str = ""
    attempt_id: Optional[str] = None
    execution_id: Optional[str] = None

    def __init__(
        self,
        turn: int = 1,
        tool_name: str = "UNKNOWN",
        input_args: Optional[Dict[str, Any]] = None,
        output_result: Any = None,
        is_error: bool = False,
        duration_seconds: float = 0.0,
        timestamp: Optional[str] = None,
        observation_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        **kwargs,
    ):
        self.turn = turn
        self.tool_name = tool_name
        self.input_args = input_args if input_args is not None else kwargs.get("arguments", {})
        self.output_result = output_result if output_result is not None else kwargs.get("output")
        if "status" in kwargs:
            self.is_error = (kwargs["status"] == "ERROR")
        else:
            self.is_error = is_error
        self.duration_seconds = duration_seconds
        self.timestamp = timestamp or datetime.now().isoformat()
        self.observation_id = observation_id or kwargs.get("id") or f"obs-{uuid.uuid4().hex[:8]}"
        self.attempt_id = attempt_id or kwargs.get("attempt_id")
        self.execution_id = execution_id or kwargs.get("execution_id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "attempt_id": self.attempt_id,
            "execution_id": self.execution_id,
            "turn": self.turn,
            "tool_name": self.tool_name,
            "input_args": self.input_args,
            "arguments": self.input_args,
            "output_result": self.output_result,
            "output": self.output_result,
            "is_error": self.is_error,
            "status": "ERROR" if self.is_error else "SUCCESS",
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ObservationRecord":
        return cls(
            turn=int(data.get("turn", 1)),
            tool_name=data.get("tool_name", "UNKNOWN"),
            input_args=data.get("input_args") or data.get("arguments") or {},
            output_result=data.get("output_result") if "output_result" in data else data.get("output"),
            is_error=bool(data.get("is_error", False) or data.get("status") == "ERROR"),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
            observation_id=data.get("observation_id"),
            attempt_id=data.get("attempt_id"),
            execution_id=data.get("execution_id"),
        )


@dataclass
class CheckpointRecord:
    checkpoint_id: str
    stage: str  # PRE_EXECUTION | POST_EXECUTION
    file_hashes: Dict[str, str] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "stage": self.stage,
            "file_hashes": self.file_hashes,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckpointRecord":
        return cls(
            checkpoint_id=data.get("checkpoint_id", "ckpt-default"),
            stage=data.get("stage", "PRE_EXECUTION"),
            file_hashes=data.get("file_hashes") or {},
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )


@dataclass
class TaskAttemptRecord:
    attempt_number: int
    agent_name: str
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "COMPLETED"
    attempt_id: str = ""
    execution_id: Optional[str] = None
    tools_used: List[str] = field(default_factory=list)
    skills_used: List[str] = field(default_factory=list)
    observations: List[ObservationRecord] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    verification_result: Optional[Dict[str, Any]] = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    duration_seconds: float = 0.0
    model_used: Optional[str] = None
    llm_latency_seconds: float = 0.0

    def __post_init__(self):
        if not self.attempt_id:
            self.attempt_id = f"att-{self.attempt_number}-{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "execution_id": self.execution_id,
            "attempt_number": self.attempt_number,
            "agent_name": self.agent_name,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "tools_used": self.tools_used,
            "skills_used": self.skills_used,
            "observations": [o.to_dict() for o in self.observations],
            "errors": self.errors,
            "verification_result": self.verification_result,
            "token_usage": self.token_usage.to_dict(),
            "duration_seconds": round(self.duration_seconds, 3),
            "model_used": self.model_used,
            "llm_latency_seconds": round(self.llm_latency_seconds, 4),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskAttemptRecord":
        return cls(
            attempt_number=int(data.get("attempt_number", 1)),
            agent_name=data.get("agent_name", "UNKNOWN"),
            started_at=data.get("started_at", datetime.now().isoformat()),
            completed_at=data.get("completed_at", datetime.now().isoformat()),
            status=data.get("status", "COMPLETED"),
            attempt_id=data.get("attempt_id") or "",
            execution_id=data.get("execution_id"),
            tools_used=data.get("tools_used") or [],
            skills_used=data.get("skills_used") or [],
            observations=[ObservationRecord.from_dict(o) for o in (data.get("observations") or [])],
            errors=data.get("errors") or [],
            verification_result=data.get("verification_result"),
            token_usage=TokenUsage.from_dict(data.get("token_usage")),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            model_used=data.get("model_used"),
            llm_latency_seconds=float(data.get("llm_latency_seconds", 0.0)),
        )


@dataclass
class TaskPermissions:
    allowed_read_paths: List[str] = field(default_factory=lambda: ["*"])
    allowed_write_paths: List[str] = field(default_factory=lambda: ["*"])
    allowed_commands: List[str] = field(default_factory=list)
    network_allowed: bool = False
    allowed_domains: List[str] = field(default_factory=list)
    blocked_domains: List[str] = field(default_factory=list)
    allowed_paths: List[str] = field(default_factory=lambda: ["*"])
    blocked_paths: List[str] = field(default_factory=list)
    read_only_paths: List[str] = field(default_factory=list)
    sensitive_paths: List[str] = field(default_factory=list)
    resource_budget: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed_read_paths": self.allowed_read_paths,
            "allowed_write_paths": self.allowed_write_paths,
            "allowed_commands": self.allowed_commands,
            "network_allowed": self.network_allowed,
            "allowed_domains": self.allowed_domains,
            "blocked_domains": self.blocked_domains,
            "allowed_paths": self.allowed_paths,
            "blocked_paths": self.blocked_paths,
            "read_only_paths": self.read_only_paths,
            "sensitive_paths": self.sensitive_paths,
            "resource_budget": self.resource_budget.to_dict() if hasattr(self.resource_budget, "to_dict") else (self.resource_budget if isinstance(self.resource_budget, dict) else None),
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "TaskPermissions":
        if not data:
            return cls()
        rb_data = data.get("resource_budget")
        rb = None
        if rb_data:
            try:
                from ..security.resource_budget import ResourceBudget
                rb = ResourceBudget.from_dict(rb_data) if isinstance(rb_data, dict) else rb_data
            except Exception:
                rb = rb_data
        return cls(
            allowed_read_paths=data.get("allowed_read_paths", ["*"]),
            allowed_write_paths=data.get("allowed_write_paths", ["*"]),
            allowed_commands=data.get("allowed_commands", []),
            network_allowed=bool(data.get("network_allowed", False)),
            allowed_domains=data.get("allowed_domains", []),
            blocked_domains=data.get("blocked_domains", []),
            allowed_paths=data.get("allowed_paths", ["*"]),
            blocked_paths=data.get("blocked_paths", []),
            read_only_paths=data.get("read_only_paths", []),
            sensitive_paths=data.get("sensitive_paths", []),
            resource_budget=rb,
        )


@dataclass
class RetryPolicy:
    max_retries: int = 2
    retry_delay_seconds: float = 1.0
    exponential_backoff: bool = True
    current_retry: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "retry_delay_seconds": self.retry_delay_seconds,
            "exponential_backoff": self.exponential_backoff,
            "current_retry": self.current_retry,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "RetryPolicy":
        if not data:
            return cls()
        return cls(
            max_retries=int(data.get("max_retries", 2)),
            retry_delay_seconds=float(data.get("retry_delay_seconds", 1.0)),
            exponential_backoff=bool(data.get("exponential_backoff", True)),
            current_retry=int(data.get("current_retry", 0)),
        )


@dataclass
class ExecutableTask:
    task_id: str
    objective: str
    dependencies: List[str] = field(default_factory=list)
    state: TaskState = TaskState.PENDING
    required_capabilities: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    preferred_skills: List[str] = field(default_factory=list)
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    acceptance_tests: List[str] = field(default_factory=list)
    permissions: TaskPermissions = field(default_factory=TaskPermissions)
    resource_budget: Optional[Any] = None
    timeout_seconds: int = 180
    max_turns: int = 15
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    owner_agent: Optional[str] = None
    parent_task_id: Optional[str] = None
    execution_id: Optional[str] = None
    attempts: List[TaskAttemptRecord] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    skills_used: List[str] = field(default_factory=list)
    observations: List[ObservationRecord] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    checkpoints: List[CheckpointRecord] = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    cost: float = 0.0
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    typed_artifacts: List[ArtifactRecord] = field(default_factory=list)
    result_data: Optional[Any] = None
    error_message: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: float = 0.0

    def add_artifact(self, artifact: Union[ArtifactRecord, Dict[str, Any]]):
        """Adds an artifact, synchronizing typed_artifacts and artifacts list."""
        if isinstance(artifact, ArtifactRecord):
            self.typed_artifacts.append(artifact)
            self.artifacts.append(artifact.to_dict())
        elif isinstance(artifact, dict):
            if "artifact_id" in artifact and "content_hash" in artifact:
                rec = ArtifactRecord.from_dict(artifact)
            else:
                name = artifact.get("name") or "output"
                att_id = artifact.get("attempt_id") or (self.attempts[-1].attempt_id if self.attempts else None)
                rec = ArtifactRecord.create(
                    name=name,
                    content=artifact,
                    task_id=self.task_id,
                    attempt_id=att_id,
                    execution_id=self.execution_id or artifact.get("execution_id"),
                )
            self.typed_artifacts.append(rec)
            self.artifacts.append(artifact)

    def record_attempt(self, attempt: TaskAttemptRecord):
        self.attempts.append(attempt)
        for t in attempt.tools_used:
            if t not in self.tools_used:
                self.tools_used.append(t)
        for s in attempt.skills_used:
            if s not in self.skills_used:
                self.skills_used.append(s)
        self.observations.extend(attempt.observations)
        self.errors.extend(attempt.errors)
        self.token_usage.add(attempt.token_usage)
        self.cost += attempt.token_usage.cost_usd
        self.duration_seconds += attempt.duration_seconds

    def create_checkpoint(self, workspace: Any, stage: str = "PRE_EXECUTION") -> CheckpointRecord:
        hashes: Dict[str, str] = {}
        if workspace and hasattr(workspace, "list_files") and hasattr(workspace, "read_file"):
            for rel in workspace.list_files():
                if ".sandboxes" in rel:
                    continue
                content = workspace.read_file(rel)
                if content is not None:
                    h = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:12]
                    hashes[rel] = h
        ckpt = CheckpointRecord(
            checkpoint_id=f"ckpt-{self.task_id}-{stage.lower()}-{len(self.checkpoints)+1}",
            stage=stage,
            file_hashes=hashes,
        )
        self.checkpoints.append(ckpt)
        return ckpt

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "step_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "execution_id": self.execution_id,
            "objective": self.objective,
            "subtask_name": self.objective,
            "description": self.objective,
            "dependencies": self.dependencies,
            "state": self.state.value if isinstance(self.state, TaskState) else str(self.state),
            "required_capabilities": self.required_capabilities,
            "required_tools": self.required_tools,
            "preferred_skills": self.preferred_skills,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "acceptance_tests": self.acceptance_tests,
            "permissions": self.permissions.to_dict() if hasattr(self.permissions, "to_dict") else self.permissions,
            "resource_budget": self.resource_budget.to_dict() if hasattr(self.resource_budget, "to_dict") else (self.resource_budget if isinstance(self.resource_budget, dict) else None),
            "timeout_seconds": self.timeout_seconds,
            "max_turns": self.max_turns,
            "retry_policy": self.retry_policy.to_dict() if hasattr(self.retry_policy, "to_dict") else self.retry_policy,
            "owner_agent": self.owner_agent,
            "attempts": [a.to_dict() for a in self.attempts],
            "tools_used": self.tools_used,
            "skills_used": self.skills_used,
            "observations": [o.to_dict() for o in self.observations],
            "errors": self.errors,
            "checkpoints": [c.to_dict() for c in self.checkpoints],
            "token_usage": self.token_usage.to_dict(),
            "cost": round(self.cost, 6),
            "artifacts": self.artifacts,
            "typed_artifacts": [a.to_dict() for a in self.typed_artifacts],
            "result_data": self.result_data,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 3),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutableTask":
        raw_id = data.get("task_id") or data.get("id") or str(data.get("step_id") or "T-1")
        task_id = str(raw_id)
        if not task_id.startswith("T-") and task_id.isdigit():
            task_id = f"T-{task_id}"

        objective = data.get("objective") or data.get("subtask_name") or data.get("description") or "Execute Task"
        
        raw_deps = data.get("dependencies") or []
        deps: List[str] = []
        for d in raw_deps:
            s_d = str(d)
            if not s_d.startswith("T-") and s_d.isdigit():
                deps.append(f"T-{s_d}")
            else:
                deps.append(s_d)

        raw_state = str(data.get("state", "PENDING")).upper()
        try:
            state = TaskState(raw_state)
        except ValueError:
            state = TaskState.PENDING

        permissions = TaskPermissions.from_dict(data.get("permissions"))
        retry_policy = RetryPolicy.from_dict(data.get("retry_policy"))
        task_rb_data = data.get("resource_budget")
        task_rb = None
        if task_rb_data:
            try:
                from ..security.resource_budget import ResourceBudget
                task_rb = ResourceBudget.from_dict(task_rb_data) if isinstance(task_rb_data, dict) else task_rb_data
            except Exception:
                task_rb = task_rb_data

        attempts = [TaskAttemptRecord.from_dict(a) for a in (data.get("attempts") or [])]
        observations = [ObservationRecord.from_dict(o) for o in (data.get("observations") or [])]
        checkpoints = [CheckpointRecord.from_dict(c) for c in (data.get("checkpoints") or [])]
        token_usage = TokenUsage.from_dict(data.get("token_usage"))

        typed_artifacts = []
        raw_typed = data.get("typed_artifacts") or []
        for a in raw_typed:
            if isinstance(a, ArtifactRecord):
                typed_artifacts.append(a)
            elif isinstance(a, dict):
                typed_artifacts.append(ArtifactRecord.from_dict(a))
        if not typed_artifacts and data.get("artifacts"):
            for a in data["artifacts"]:
                if isinstance(a, dict):
                    rec = ArtifactRecord.create(
                        name=a.get("name", "artifact"),
                        content=a,
                        task_id=task_id,
                        execution_id=data.get("execution_id"),
                    )
                    typed_artifacts.append(rec)

        return cls(
            task_id=task_id,
            parent_task_id=data.get("parent_task_id"),
            execution_id=data.get("execution_id"),
            objective=objective,
            dependencies=deps,
            state=state,
            required_capabilities=data.get("required_capabilities") or [],
            required_tools=data.get("required_tools") or [],
            preferred_skills=data.get("preferred_skills") or [],
            inputs=data.get("inputs") or [],
            outputs=data.get("outputs") or [],
            acceptance_tests=data.get("acceptance_tests") or [],
            permissions=permissions,
            resource_budget=task_rb,
            timeout_seconds=int(data.get("timeout_seconds", 180)),
            max_turns=int(data.get("max_turns", 15)),
            retry_policy=retry_policy,
            owner_agent=data.get("owner_agent") or data.get("assigned_agent"),
            attempts=attempts,
            tools_used=data.get("tools_used") or [],
            skills_used=data.get("skills_used") or [],
            observations=observations,
            errors=data.get("errors") or [],
            checkpoints=checkpoints,
            token_usage=token_usage,
            cost=float(data.get("cost", 0.0)),
            artifacts=data.get("artifacts") or [],
            typed_artifacts=typed_artifacts,
            result_data=data.get("result_data"),
            error_message=data.get("error_message"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
        )


import threading


class TaskDAG:
    """
    Topological Task DAG Manager for autonomous multi-agent execution.
    Provides thread-safe dependency resolution, ready-task scheduling, cycle detection,
    dependency-scoped artifact routing, dynamic task insertion, and state tracking.
    """

    def __init__(self, tasks: Optional[List[ExecutableTask]] = None):
        self._lock = threading.RLock()
        self._tasks: Dict[str, ExecutableTask] = {}
        if tasks:
            for task in tasks:
                self.add_task(task)

    def add_task(self, task: ExecutableTask):
        with self._lock:
            self._tasks[task.task_id] = task

    @property
    def tasks(self) -> Dict[str, ExecutableTask]:
        with self._lock:
            return dict(self._tasks)

    def get_task(self, task_id: str) -> Optional[ExecutableTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self) -> List[ExecutableTask]:
        with self._lock:
            return list(self._tasks.values())

    def get_ready_tasks(self) -> List[ExecutableTask]:
        """
        Returns all tasks whose dependencies are ALL COMPLETED and whose
        current state is PENDING or READY.
        Handles FAILED and SKIPPED/SKIPPED_REDUNDANT dependencies properly
        to prevent orphaned PENDING states or execution deadlocks.
        """
        with self._lock:
            ready: List[ExecutableTask] = []
            for task in self._tasks.values():
                if task.state in (TaskState.PENDING, TaskState.READY):
                    deps_satisfied = True
                    has_failed_dep = False
                    has_skipped_dep = False
                    missing_deps = []

                    for dep_id in task.dependencies:
                        dep_task = self._tasks.get(dep_id)
                        if not dep_task:
                            missing_deps.append(dep_id)
                            deps_satisfied = False
                            continue
                        if dep_task.state == TaskState.FAILED:
                            has_failed_dep = True
                            deps_satisfied = False
                            break
                        elif dep_task.state in (TaskState.SKIPPED, TaskState.SKIPPED_REDUNDANT):
                            has_skipped_dep = True
                            deps_satisfied = False
                            break
                        elif dep_task.state != TaskState.COMPLETED:
                            deps_satisfied = False

                    if missing_deps:
                        task.state = TaskState.BLOCKED
                        task.error_message = f"Blocked: Nonexistent prerequisite dependency {missing_deps} in task DAG."
                        task.completed_at = datetime.now().isoformat()
                    elif deps_satisfied:
                        task.state = TaskState.READY
                        ready.append(task)
                    elif has_failed_dep:
                        task.state = TaskState.BLOCKED
                        task.error_message = f"Blocked: Upstream dependency failed."
                    elif has_skipped_dep:
                        task.state = TaskState.SKIPPED_REDUNDANT
                        task.error_message = f"Pruned because upstream dependency was skipped or pruned."
                        task.completed_at = datetime.now().isoformat()
            return ready

    def get_parent_artifacts(self, task_id: str) -> Dict[str, Any]:
        """
        Extracts verified outputs, artifacts, and return data strictly from
        the declared upstream direct parent dependencies of task_id.
        Prevents global context pollution.
        """
        with self._lock:
            task = self.get_task(task_id)
            if not task or not task.dependencies:
                return {}

            parent_context: Dict[str, Any] = {}
            for dep_id in task.dependencies:
                dep_task = self._tasks.get(dep_id)
                if dep_task:
                    parent_context[dep_id] = {
                        "task_id": dep_task.task_id,
                        "objective": dep_task.objective,
                        "outputs": dep_task.outputs,
                        "artifacts": dep_task.artifacts,
                        "result_data": dep_task.result_data,
                        "state": dep_task.state.value if isinstance(dep_task.state, TaskState) else str(dep_task.state),
                    }
            return parent_context

    def mark_task_running(self, task_id: str, owner_agent: Optional[str] = None):
        with self._lock:
            task = self.get_task(task_id)
            if task:
                task.state = TaskState.RUNNING
                task.started_at = datetime.now().isoformat()
                if owner_agent:
                    task.owner_agent = owner_agent

    def mark_task_verifying(self, task_id: str):
        with self._lock:
            task = self.get_task(task_id)
            if task:
                task.state = TaskState.VERIFYING

    def mark_task_completed(
        self,
        task_id: str,
        artifacts: Optional[List[Union[ArtifactRecord, Dict[str, Any]]]] = None,
        result_data: Optional[Any] = None,
    ):
        with self._lock:
            task = self.get_task(task_id)
            if task:
                task.state = TaskState.COMPLETED
                task.completed_at = datetime.now().isoformat()
                if artifacts:
                    for a in artifacts:
                        task.add_artifact(a)
                if result_data is not None:
                    task.result_data = result_data

    def mark_task_failed(self, task_id: str, error_message: Optional[str] = None):
        with self._lock:
            task = self.get_task(task_id)
            if task:
                task.state = TaskState.FAILED
                task.completed_at = datetime.now().isoformat()
                task.error_message = error_message

    def is_all_completed(self) -> bool:
        with self._lock:
            if not self._tasks:
                return True
            return all(
                task.state in (TaskState.COMPLETED, TaskState.SKIPPED, TaskState.SKIPPED_REDUNDANT)
                for task in self._tasks.values()
            )

    def get_descendant_task_ids(self, task_id: str) -> Set[str]:
        """
        Returns the transitive closure of all downstream tasks that directly
        or indirectly depend on task_id.
        """
        with self._lock:
            children_map: Dict[str, List[str]] = {}
            for t in self._tasks.values():
                for dep in t.dependencies:
                    children_map.setdefault(dep, []).append(t.task_id)

            descendants: Set[str] = set()
            queue = deque(children_map.get(task_id, []))
            while queue:
                curr = queue.popleft()
                if curr not in descendants and curr in self._tasks:
                    descendants.add(curr)
                    for child in children_map.get(curr, []):
                        if child not in descendants:
                            queue.append(child)
            return descendants

    def prune_tasks(self, task_ids: List[str], reason: str = "", cascade: bool = True) -> List[str]:
        """
        Prunes redundant downstream tasks by transitioning them to SKIPPED_REDUNDANT.
        If cascade is True, transitively prunes all downstream dependent tasks as well.
        """
        pruned = []
        with self._lock:
            target_ids = set(task_ids)
            if cascade:
                for tid in list(target_ids):
                    target_ids.update(self.get_descendant_task_ids(tid))

            for tid in target_ids:
                task = self.get_task(tid)
                if task and task.state in (TaskState.PENDING, TaskState.READY, TaskState.BLOCKED):
                    task.state = TaskState.SKIPPED_REDUNDANT
                    task.error_message = reason or "Pruned as redundant downstream task due to early goal satisfaction."
                    task.completed_at = datetime.now().isoformat()
                    pruned.append(tid)
        return pruned

    def invalidate_dependent_tasks(self, task_ids: List[str], reason: str = "") -> List[str]:
        """
        Invalidates and prunes all downstream transitive dependent tasks of the given task IDs.
        Transitions all uncompleted descendants (in PENDING, READY, BLOCKED states)
        to SKIPPED_REDUNDANT with the specified reason.
        """
        with self._lock:
            descendants: Set[str] = set()
            for tid in task_ids:
                descendants.update(self.get_descendant_task_ids(tid))

            pruned = []
            for tid in descendants:
                task = self.get_task(tid)
                if task and task.state in (TaskState.PENDING, TaskState.READY, TaskState.BLOCKED):
                    task.state = TaskState.SKIPPED_REDUNDANT
                    task.error_message = reason or f"Pruned as stale downstream dependent of invalidated task(s): {task_ids}"
                    task.completed_at = datetime.now().isoformat()
                    pruned.append(tid)
            return pruned

    def has_failures(self) -> bool:
        with self._lock:
            return any(task.state == TaskState.FAILED for task in self._tasks.values())

    def get_failed_tasks(self) -> List[ExecutableTask]:
        with self._lock:
            return [task for task in self._tasks.values() if task.state == TaskState.FAILED]

    def detect_cycles(self) -> bool:
        """Detects if there is any directed cycle in the dependency graph."""
        with self._lock:
            visited: Dict[str, int] = {}

            def has_cycle(node_id: str) -> bool:
                visited[node_id] = 1
                node = self._tasks.get(node_id)
                if node:
                    for dep_id in node.dependencies:
                        if dep_id in self._tasks:
                            if visited.get(dep_id, 0) == 1:
                                return True
                            if visited.get(dep_id, 0) == 0 and has_cycle(dep_id):
                                return True
                visited[node_id] = 2
                return False

            for task_id in self._tasks:
                if visited.get(task_id, 0) == 0:
                    if has_cycle(task_id):
                        return True
            return False

    def topological_sort(self) -> List[ExecutableTask]:
        """Returns tasks ordered by dependency topology."""
        with self._lock:
            if self.detect_cycles():
                raise ValueError("Cannot topologically sort DAG containing cycles!")

            visited: Set[str] = set()
            order: List[ExecutableTask] = []

            def dfs(task_id: str):
                if task_id in visited or task_id not in self._tasks:
                    return
                visited.add(task_id)
                task = self._tasks[task_id]
                for dep_id in task.dependencies:
                    dfs(dep_id)
                order.append(task)

            for task_id in self._tasks:
                dfs(task_id)

            return order

    def inject_remediation_task(
        self,
        remediation_task: ExecutableTask,
        failed_task_id: Optional[str] = None,
        invalidate_downstream: bool = False,
    ):
        """
        Dynamically grafts a remediation task into the DAG.
        If failed_task_id is given, links dependencies appropriately.
        If invalidate_downstream is True, prunes all downstream tasks of failed_task_id
        to prevent executing stale subtasks against the new remediation architecture.
        """
        with self._lock:
            if failed_task_id and failed_task_id in self._tasks:
                failed_task = self._tasks[failed_task_id]
                if not remediation_task.inputs:
                    remediation_task.inputs = list(failed_task.inputs)
                if not remediation_task.outputs:
                    remediation_task.outputs = list(failed_task.outputs)

                if invalidate_downstream:
                    self.invalidate_dependent_tasks(
                        [failed_task_id],
                        reason=f"Invalidated downstream dependent because parent task [{failed_task_id}] failed and changed architecture/contract."
                    )
                else:
                    for task in self._tasks.values():
                        if failed_task_id in task.dependencies:
                            task.dependencies = [
                                remediation_task.task_id if d == failed_task_id else d
                                for d in task.dependencies
                            ]
                            if task.state == TaskState.BLOCKED:
                                task.state = TaskState.PENDING

            self.add_task(remediation_task)

    def spawn_child_subtasks(
        self,
        parent_task_id: str,
        new_subtasks: List[ExecutableTask],
        replace_parent_in_downstream: bool = True,
    ):
        """
        Spawns subtasks mid-flight from an executing task.
        New subtasks inherit parent dependencies, parent_task_id, and execution_id,
        and are added to the active DAG.
        Downstream tasks depending on parent_task_id are updated to depend on child subtasks.
        """
        with self._lock:
            parent_task = self.get_task(parent_task_id)
            child_ids = [s.task_id for s in new_subtasks]
            for subtask in new_subtasks:
                if parent_task:
                    subtask.parent_task_id = parent_task_id
                    if not subtask.execution_id:
                        subtask.execution_id = parent_task.execution_id
                    if not subtask.dependencies:
                        subtask.dependencies = list(parent_task.dependencies)
                self.add_task(subtask)

            if replace_parent_in_downstream and child_ids:
                for task in self._tasks.values():
                    if task.task_id not in child_ids and parent_task_id in task.dependencies:
                        task.dependencies.remove(parent_task_id)
                        for cid in child_ids:
                            if cid not in task.dependencies:
                                task.dependencies.append(cid)

    def to_list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [task.to_dict() for task in self._tasks.values()]

    @classmethod
    def from_list(cls, tasks_data: List[Dict[str, Any]]) -> "TaskDAG":
        dag = cls()
        for item in tasks_data:
            if isinstance(item, dict):
                dag.add_task(ExecutableTask.from_dict(item))
            elif isinstance(item, ExecutableTask):
                dag.add_task(item)
        return dag


def spawn_child_subtasks(
    task_dag: TaskDAG,
    parent_task_id: str,
    child_tasks: List[ExecutableTask],
    replace_parent_in_downstream: bool = True,
) -> List[ExecutableTask]:
    """Module-level helper to spawn child subtasks in a TaskDAG."""
    task_dag.spawn_child_subtasks(
        parent_task_id=parent_task_id,
        new_subtasks=child_tasks,
        replace_parent_in_downstream=replace_parent_in_downstream,
    )
    return child_tasks


def generate_task_id(prefix: str = "T", execution_id: Optional[str] = None) -> str:
    """Generates a globally unique, collision-resistant task identifier."""
    uid = uuid.uuid4().hex[:8]
    if execution_id:
        return f"{execution_id}:{prefix}-{uid}"
    return f"{prefix}-{uid}"

