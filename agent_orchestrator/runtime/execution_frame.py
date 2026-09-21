"""
Agent Execution Frame: Serializable, Re-entrant Agent State Snapshot.
Captures conversational history, observations, turn counters, working hypotheses,
and in-flight actions enabling agents to pause mid-loop, persist to disk/database,
and resume seamlessly.
"""
from dataclasses import dataclass, field
from datetime import datetime
import json
import time
from typing import Any, Dict, List, Optional
import uuid


@dataclass
class AgentExecutionFrame:
    """
    Serializable execution state of an agent at a specific moment in time.
    """
    frame_id: str = field(default_factory=lambda: f"frame-{uuid.uuid4().hex[:8]}")
    agent_id: str = "AGENT"
    role: str = "CODER"
    session_id: str = "default-session"
    task_id: Optional[str] = None
    turn: int = 0
    max_turns: int = 15
    messages: List[Dict[str, Any]] = field(default_factory=list)
    observations: List[Dict[str, Any]] = field(default_factory=list)
    in_flight_tool_call: Optional[Dict[str, Any]] = None
    working_hypotheses: List[str] = field(default_factory=list)
    scratchpad: str = ""
    pause_reason: Optional[str] = None
    paused_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "agent_id": self.agent_id,
            "role": self.role,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "turn": self.turn,
            "max_turns": self.max_turns,
            "messages": self.messages,
            "observations": self.observations,
            "in_flight_tool_call": self.in_flight_tool_call,
            "working_hypotheses": list(self.working_hypotheses),
            "scratchpad": self.scratchpad,
            "pause_reason": self.pause_reason,
            "paused_at": self.paused_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentExecutionFrame":
        return cls(
            frame_id=data.get("frame_id") or f"frame-{uuid.uuid4().hex[:8]}",
            agent_id=data.get("agent_id", "AGENT"),
            role=data.get("role", "CODER"),
            session_id=data.get("session_id", "default-session"),
            task_id=data.get("task_id"),
            turn=int(data.get("turn", 0)),
            max_turns=int(data.get("max_turns", 15)),
            messages=list(data.get("messages", [])),
            observations=list(data.get("observations", [])),
            in_flight_tool_call=data.get("in_flight_tool_call"),
            working_hypotheses=list(data.get("working_hypotheses", [])),
            scratchpad=data.get("scratchpad", ""),
            pause_reason=data.get("pause_reason"),
            paused_at=float(data.get("paused_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    @classmethod
    def from_json(cls, json_str: str) -> "AgentExecutionFrame":
        return cls.from_dict(json.loads(json_str))
