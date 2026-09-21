"""
Task Memory & Inter-Subtask Knowledge Distillation.
Maintains curated memories per executable task (assumptions, constraints, decisions, takeaways)
and synthesizes them for downstream dependent tasks in the DAG.
"""
from dataclasses import dataclass, field
from datetime import datetime
import threading
from typing import Any, Dict, List, Optional


@dataclass
class TaskMemory:
    """
    Curated memory for an individual task.
    Captures assumptions, technical decisions, discovered constraints,
    and key takeaways to be transferred to dependent downstream tasks.
    """
    task_id: str
    goal: str = ""
    assumptions: List[str] = field(default_factory=list)
    hypotheses: List[Dict[str, Any]] = field(default_factory=list)
    discovered_constraints: List[str] = field(default_factory=list)
    technical_decisions: List[Dict[str, Any]] = field(default_factory=list)
    key_takeaways: List[str] = field(default_factory=list)
    artifacts_created: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def record_assumption(self, assumption: str) -> None:
        clean = assumption.strip()
        if clean and clean not in self.assumptions:
            self.assumptions.append(clean)
            self.updated_at = datetime.now().isoformat()

    def record_constraint(self, constraint: str) -> None:
        clean = constraint.strip()
        if clean and clean not in self.discovered_constraints:
            self.discovered_constraints.append(clean)
            self.updated_at = datetime.now().isoformat()

    def record_decision(self, decision: str, rationale: str = "") -> None:
        clean_dec = decision.strip()
        if clean_dec:
            self.technical_decisions.append({
                "decision": clean_dec,
                "rationale": rationale.strip(),
                "timestamp": datetime.now().isoformat(),
            })
            self.updated_at = datetime.now().isoformat()

    def record_takeaway(self, takeaway: str) -> None:
        clean = takeaway.strip()
        if clean and clean not in self.key_takeaways:
            self.key_takeaways.append(clean)
            self.updated_at = datetime.now().isoformat()

    def record_artifact(self, artifact_path: str) -> None:
        clean = artifact_path.strip()
        if clean and clean not in self.artifacts_created:
            self.artifacts_created.append(clean)
            self.updated_at = datetime.now().isoformat()

    def distill_for_dependents(self) -> Dict[str, Any]:
        """
        Distills task knowledge into a concise structure for dependent downstream tasks.
        """
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "key_takeaways": list(self.key_takeaways),
            "discovered_constraints": list(self.discovered_constraints),
            "technical_decisions": [d.get("decision", "") for d in self.technical_decisions],
            "artifacts_created": list(self.artifacts_created),
        }

    def get_summary(self, max_tokens: int = 1000) -> str:
        """
        Formats a token-bounded markdown summary for prompt injection.
        """
        lines = [f"**Task Memory [{self.task_id}]**: {self.goal}"]

        if self.discovered_constraints:
            lines.append("Discovered Constraints:")
            for c in self.discovered_constraints[-5:]:
                lines.append(f"  • {c}")

        if self.technical_decisions:
            lines.append("Technical Decisions:")
            for d in self.technical_decisions[-5:]:
                rat = f" (Rationale: {d['rationale']})" if d.get("rationale") else ""
                lines.append(f"  • {d['decision']}{rat}")

        if self.key_takeaways:
            lines.append("Key Takeaways:")
            for t in self.key_takeaways[-5:]:
                lines.append(f"  • {t}")

        if self.artifacts_created:
            lines.append(f"Artifacts Created: {', '.join(self.artifacts_created[-10:])}")

        summary = "\n".join(lines)
        max_chars = max_tokens * 4
        if len(summary) > max_chars:
            return summary[:max_chars] + "\n... [Task memory truncated] ..."
        return summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "assumptions": list(self.assumptions),
            "hypotheses": list(self.hypotheses),
            "discovered_constraints": list(self.discovered_constraints),
            "technical_decisions": list(self.technical_decisions),
            "key_takeaways": list(self.key_takeaways),
            "artifacts_created": list(self.artifacts_created),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskMemory":
        return cls(
            task_id=data["task_id"],
            goal=data.get("goal", ""),
            assumptions=list(data.get("assumptions", [])),
            hypotheses=list(data.get("hypotheses", [])),
            discovered_constraints=list(data.get("discovered_constraints", [])),
            technical_decisions=list(data.get("technical_decisions", [])),
            key_takeaways=list(data.get("key_takeaways", [])),
            artifacts_created=list(data.get("artifacts_created", [])),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
        )


class TaskManagerMemoryStore:
    """
    Thread-safe registry for task memories in an execution session.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._tasks: Dict[str, TaskMemory] = {}

    def get_or_create(self, task_id: str, goal: str = "") -> TaskMemory:
        with self._lock:
            if task_id not in self._tasks:
                self._tasks[task_id] = TaskMemory(task_id=task_id, goal=goal)
            elif goal and not self._tasks[task_id].goal:
                self._tasks[task_id].goal = goal
            return self._tasks[task_id]

    def get(self, task_id: str) -> Optional[TaskMemory]:
        with self._lock:
            return self._tasks.get(task_id)

    def record_decision(self, task_id: str, decision: str, rationale: str = "") -> None:
        mem = self.get_or_create(task_id)
        mem.record_decision(decision, rationale)

    def record_constraint(self, task_id: str, constraint: str) -> None:
        mem = self.get_or_create(task_id)
        mem.record_constraint(constraint)

    def record_takeaway(self, task_id: str, takeaway: str) -> None:
        mem = self.get_or_create(task_id)
        mem.record_takeaway(takeaway)

    def distill_task(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            mem = self._tasks.get(task_id)
            if mem:
                return mem.distill_for_dependents()
            return {}

    def get_memories_for_tasks(self, task_ids: List[str]) -> List[TaskMemory]:
        with self._lock:
            return [self._tasks[tid] for tid in task_ids if tid in self._tasks]

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {tid: mem.to_dict() for tid, mem in self._tasks.items()}
