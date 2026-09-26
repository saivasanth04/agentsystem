"""
Execution State.
Tracks the mutable lifecycle state of the Claude-style execution loop:
Reason -> Select Tool -> Execute -> Observation -> State Update -> Context Rebuild -> Reason Again
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Union

from .observation_engine import Observation
from .tool_policy import ToolPolicy


class LoopStatus:
    INITIALIZED = "INITIALIZED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TERMINATED = "TERMINATED"
    PAUSED = "PAUSED"


@dataclass
class ExecutionState:
    """
    Mutable state ledger for the execution loop.
    Enforces that observations are strictly structured and errors are decoupled from raw logs.
    """
    task_objective: str
    iteration: int = 0
    max_iterations: int = 20
    status: str = LoopStatus.INITIALIZED
    active_skills: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    tool_policy: Optional[ToolPolicy] = None

    # Step history
    reasoning_history: List[str] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    observations: List[Observation] = field(default_factory=list)

    # Context & working memory
    working_memory: Dict[str, Any] = field(default_factory=dict)
    diff: str = ""
    rebuilt_context: Optional[Any] = None  # OptimizedContextPackage

    # Outcome tracking
    exit_reason: Optional[str] = None
    final_response: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_reasoning(self, thought: str) -> None:
        """Records an explicit thought/plan from the Reason phase."""
        clean = (thought or "").strip()
        if clean:
            self.reasoning_history.append(clean)

    def record_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Records a selected tool invocation."""
        record = {
            "iteration": self.iteration,
            "tool": tool_name,
            "arguments": arguments,
            "call_id": call_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.tool_calls.append(record)
        return record

    def add_observation(self, obs: Observation) -> None:
        """Appends a structured observation to the state."""
        self.observations.append(obs)

    def get_latest_observation(self) -> Optional[Observation]:
        """Returns the most recent observation if any."""
        return self.observations[-1] if self.observations else None

    def get_active_errors(self) -> List[Observation]:
        """
        Retrieves all active error observations.
        Does not return raw logs; returns only structured error observations.
        """
        return [obs for obs in self.observations if obs.severity == "error"]

    def get_active_error_evidence(self) -> List[str]:
        """
        Formats active errors into concise strings for consumption by ContextCompiler and replanners.
        Strictly prevents raw logs from entering the prompt context.
        """
        errors = self.get_active_errors()
        return [obs.to_replan_summary() for obs in errors]

    def update_memory(self, key: str, value: Any) -> None:
        """Updates an entry in working memory."""
        self.working_memory[key] = value

    def is_terminal(self) -> bool:
        """Checks if the execution state has reached a terminal condition."""
        return self.status in (
            LoopStatus.COMPLETED,
            LoopStatus.FAILED,
            LoopStatus.TERMINATED,
        )

    def complete(self, response: str, reason: str = "Task objective satisfied") -> None:
        """Marks the execution as successfully completed."""
        self.status = LoopStatus.COMPLETED
        self.final_response = response
        self.exit_reason = reason
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def fail(self, reason: str) -> None:
        """Marks the execution as failed."""
        self.status = LoopStatus.FAILED
        self.exit_reason = reason
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def terminate(self, reason: str = "Max iterations exceeded") -> None:
        """Terminates execution due to budget or policy limits."""
        self.status = LoopStatus.TERMINATED
        self.exit_reason = reason
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Serializes execution state to a JSON-safe dictionary compatible with legacy and modern runtimes."""
        events = [{"tool": tc.get("tool"), "args": tc.get("arguments", {}), "iteration": tc.get("iteration")} for tc in self.tool_calls]
        return {
            "task_objective": self.task_objective,
            "iteration": self.iteration,
            "turns_taken": self.iteration,
            "max_iterations": self.max_iterations,
            "status": self.status,
            "success": self.status == LoopStatus.COMPLETED,
            "final_response": self.final_response or "",
            "final_output": self.final_response or "",
            "active_skills": self.active_skills,
            "capabilities": self.capabilities,
            "allowed_tools": self.allowed_tools,
            "tools_used": sorted(list({tc.get("tool") for tc in self.tool_calls if tc.get("tool")})),
            "tool_calls": self.tool_calls,
            "history_events": events,
            "tool_history": events,
            "reasoning_history": self.reasoning_history,
            "reasoning_history_count": len(self.reasoning_history),
            "tool_calls_count": len(self.tool_calls),
            "observations_count": len(self.observations),
            "observations": [o.to_dict() for o in self.observations],
            "active_errors": [o.to_dict() for o in self.get_active_errors()],
            "active_errors_count": len(self.get_active_errors()),
            "errors": self.get_active_error_evidence(),
            "working_memory": self.working_memory,
            "working_memory_keys": list(self.working_memory.keys()),
            "diff": self.diff,
            "diff_length": len(self.diff),
            "exit_reason": self.exit_reason,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.to_dict()

    def keys(self):
        return self.to_dict().keys()

    def items(self):
        return self.to_dict().items()

    def values(self):
        return self.to_dict().values()

