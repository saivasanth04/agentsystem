"""
ReAct Step and Execution Trajectory Data Models.
Tracks the structured Thought -> Action -> Observation -> Reflection progression
across autonomous agent turns.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ReActStep:
    """A single Reasoning-Action-Observation turn step."""
    turn: int
    thought: str = ""                     # Explicit mental model or plan before taking action
    action_tool: str = ""                 # Tool name invoked
    action_input: Dict[str, Any] = field(default_factory=dict) # Tool arguments
    observation: Any = None               # Tool output / environment feedback
    reflection: Optional[str] = None      # Assessment of observation before next turn
    duration_seconds: float = 0.0
    status: str = "SUCCESS"               # "SUCCESS" or "ERROR"
    is_error: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn": self.turn,
            "thought": self.thought,
            "action_tool": self.action_tool,
            "action_input": self.action_input,
            "observation": self.observation,
            "reflection": self.reflection,
            "duration_seconds": round(self.duration_seconds, 4),
            "status": self.status,
            "is_error": self.is_error,
            "timestamp": self.timestamp,
        }


@dataclass
class ReActTrajectory:
    """An ordered sequence of ReActSteps capturing the complete reasoning-action-observation trace."""
    steps: List[ReActStep] = field(default_factory=list)

    def add_step(self, step: ReActStep) -> None:
        self.steps.append(step)

    @property
    def total_turns(self) -> int:
        return len(self.steps)

    @property
    def tools_invoked(self) -> List[str]:
        return [s.action_tool for s in self.steps if s.action_tool]

    def summary(self) -> str:
        lines = [f"ReAct Trajectory ({len(self.steps)} steps):"]
        for s in self.steps:
            status_icon = "❌" if s.is_error else "✅"
            th = f" [Thought: {s.thought[:60]}...]" if s.thought else ""
            lines.append(f"  • Turn {s.turn}: {status_icon} {s.action_tool}({list(s.action_input.keys())}){th}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_steps": len(self.steps),
            "tools_invoked": self.tools_invoked,
            "steps": [s.to_dict() for s in self.steps],
        }


__all__ = ["ReActStep", "ReActTrajectory"]
