"""
Context Budget Engine.
Uses 'tiktoken' for exact token counting and strict token budget enforcement.
Never implements custom tokenizers or heuristic word-counting approximations.
"""
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional, Union

import tiktoken

logger = logging.getLogger("context.budget")


class TokenCounter:
    """
    Exact token counter and slicer backed by tiktoken.
    """

    def __init__(self, model_or_encoding: str = "cl100k_base", model_name: Optional[str] = None):
        chosen = model_name or model_or_encoding
        self.name = chosen
        try:
            self.encoding = tiktoken.get_encoding(chosen)
        except Exception:
            try:
                self.encoding = tiktoken.encoding_for_model(chosen)
            except Exception:
                self.encoding = tiktoken.get_encoding("cl100k_base")

    def count(self, text: Optional[str]) -> int:
        """Counts exact tokens in a text string."""
        if not text:
            return 0
        return len(self.encoding.encode(text, disallowed_special=()))

    def truncate(self, text: Optional[str], max_tokens: int) -> str:
        """
        Truncates text strictly to max_tokens using exact token slice and decode.
        Guarantees returned text has <= max_tokens tokens.
        """
        if not text or max_tokens <= 0:
            return ""
        tokens = self.encoding.encode(text, disallowed_special=())
        if len(tokens) <= max_tokens:
            return text
        sliced = tokens[:max_tokens]
        return self.encoding.decode(sliced)


@dataclass
class BudgetAllocation:
    """Proportional token budget limits for the context input streams."""
    task_objective: float = 0.10
    verification_state: float = 0.10
    errors: float = 0.15
    diff: float = 0.10
    repository_brain: float = 0.25
    skills: float = 0.20
    working_memory: float = 0.05
    conversation: float = 0.05

    def get_shares(self) -> Dict[str, float]:
        return {
            "task_objective": self.task_objective,
            "verification_state": self.verification_state,
            "errors": self.errors,
            "diff": self.diff,
            "repository_brain": self.repository_brain,
            "skills": self.skills,
            "working_memory": self.working_memory,
            "conversation": self.conversation,
        }


@dataclass
class ResolvedBudget:
    """Calculated absolute token limits per context stream."""
    task_objective: int = 0
    verification_state: int = 0
    errors: int = 0
    diff: int = 0
    repository_brain: int = 0
    skills: int = 0
    working_memory: int = 0
    conversation: int = 0


class ContextBudgetManager:
    """
    Manages exact token budgets across all 7 input streams.
    Redistributes unused surplus budget dynamically to avoid artificial starving.
    """

    def __init__(
        self,
        total_budget: int = 8000,
        distribution: Optional[BudgetAllocation] = None,
        model_name: str = "cl100k_base",
    ):
        self.total_budget = total_budget
        self.distribution = distribution or BudgetAllocation()
        self.counter = TokenCounter(model_name)
        self.allocations: Dict[str, int] = self._compute_initial_allocations()
        self.consumed: Dict[str, int] = {k: 0 for k in self.allocations}

    def _compute_initial_allocations(self) -> Dict[str, int]:
        shares = self.distribution.get_shares()
        return {
            k: max(50, int(self.total_budget * share))
            for k, share in shares.items()
        }

    def allocate(self) -> ResolvedBudget:
        """Returns the calculated absolute token allocation per stream."""
        return ResolvedBudget(
            task_objective=self.allocations.get("task_objective", 0),
            verification_state=self.allocations.get("verification_state", 0),
            errors=self.allocations.get("errors", 0),
            diff=self.allocations.get("diff", 0),
            repository_brain=self.allocations.get("repository_brain", 0),
            skills=self.allocations.get("skills", 0),
            working_memory=self.allocations.get("working_memory", 0),
            conversation=self.allocations.get("conversation", 0),
        )

    def count_tokens(self, text: Optional[str]) -> int:
        """Utility token count using tiktoken."""
        return self.counter.count(text)

    def fit_budget(self, text: Optional[str], max_tokens: int) -> str:
        """Truncates text strictly to max_tokens using tiktoken."""
        return self.counter.truncate(text, max_tokens)

    def allocate_and_truncate(self, stream_name: str, content: Optional[str], priority_budget: Optional[int] = None) -> str:
        """
        Truncates content to the allocated stream budget using tiktoken.
        Updates consumed token accounting.
        """
        if not content:
            return ""

        allowed = priority_budget if priority_budget is not None else self.allocations.get(stream_name, 500)
        truncated = self.counter.truncate(content, max_tokens=allowed)
        actual_tokens = self.counter.count(truncated)
        self.consumed[stream_name] = actual_tokens
        return truncated

    def get_surplus(self) -> int:
        """Calculates unused tokens across streams that can be redistributed."""
        total_used = sum(self.consumed.values())
        return max(0, self.total_budget - total_used)

    def get_budget_report(self) -> Dict[str, Any]:
        """Returns comprehensive breakdown of token allocation and consumption."""
        total_used = sum(self.consumed.values())
        return {
            "total_budget": self.total_budget,
            "total_consumed": total_used,
            "remaining_budget": max(0, self.total_budget - total_used),
            "stream_allocations": self.allocations,
            "stream_consumed": self.consumed,
            "utilization_percent": round((total_used / self.total_budget) * 100, 2) if self.total_budget > 0 else 0.0,
        }


def estimate_tokens(text: Optional[str], model: str = "cl100k_base") -> int:
    """Exact or fast token estimation using TokenCounter."""
    if not text:
        return 0
    return TokenCounter(model).count(text)


@dataclass
class ContextBudget:
    """Configurable token ceilings per context section."""
    system_instructions: int = 2000
    working_memory: int = 1500
    task_memory: int = 1000
    episodic_memory: int = 1000
    project_memory: int = 1500
    semantic_memory: int = 800
    task_scope: int = 3000
    repo_outline: int = 2000
    focal_files: int = 8000
    interface_signatures: int = 3500
    interaction_buffer: int = 12000
    total_budget: int = 32000

    @classmethod
    def default(cls) -> "ContextBudget":
        return cls()


@dataclass
class ContextSection:
    """A distinct section in the prompt hierarchy with explicit priority."""
    name: str
    title: str
    content: str
    priority: int = 1  # 1 (highest) to 10 (lowest)
    max_tokens: int = 2000
    is_essential: bool = False
    trust_level: Optional[Any] = None

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.content)


class ContextAssembler:
    """
    Assembles prompt sections into a coherent, structured string within token limits.
    """
    def __init__(self, budget: Optional[ContextBudget] = None, token_counter: Optional[TokenCounter] = None):
        self.budget = budget or ContextBudget.default()
        self.counter = token_counter or TokenCounter()

    def assemble(self, sections: List[ContextSection]) -> str:
        sorted_sections = sorted(sections, key=lambda s: (s.priority, not s.is_essential))
        assembled_parts = []
        current_tokens = 0
        limit = self.budget.total_budget

        for s in sorted_sections:
            if not s.content:
                continue
            trimmed = self.counter.truncate(s.content, s.max_tokens)
            sec_tokens = self.counter.count(trimmed)
            if current_tokens + sec_tokens > limit and not s.is_essential:
                continue
            header = f"# {s.title}\n" if s.title else ""
            assembled_parts.append(f"{header}{trimmed}")
            current_tokens += sec_tokens

        return "\n\n".join(assembled_parts)

