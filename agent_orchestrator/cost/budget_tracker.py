"""
Budget Specification & Real-Time Tracking Engine.
Enforces per-task, per-agent, and per-session token and financial limits.
"""
from dataclasses import dataclass, field
import threading
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class BudgetAction(str, Enum):
    HALT = "HALT"
    WARN = "WARN"
    DOWNGRADE_TIER = "DOWNGRADE_TIER"


class BudgetExceededError(Exception):
    """Raised when an operation breaches a configured token or financial budget."""
    def __init__(self, message: str, budget_type: str, limit: float, current: float):
        super().__init__(message)
        self.budget_type = budget_type
        self.limit = limit
        self.current = current


@dataclass
class BudgetSpec:
    """Configurable budget constraints across scopes."""
    max_tokens_per_task: int = 0              # 0 = unlimited
    max_cost_usd_per_task: float = 0.0        # 0.0 = unlimited
    max_session_cost_usd: float = 0.0         # 0.0 = unlimited
    max_session_tokens: int = 0               # 0 = unlimited
    agent_budgets: Dict[str, float] = field(default_factory=dict) # agent_name -> max_cost_usd
    on_budget_exceeded: str = BudgetAction.HALT


class BudgetTracker:
    """
    Thread-safe ledger tracking cumulative token usage, compute costs, and budget enforcement.
    """

    def __init__(self, spec: Optional[BudgetSpec] = None):
        self._lock = threading.Lock()
        self.spec = spec or BudgetSpec()
        self.reset(reset_spec=False)

    def reset(self, reset_spec: bool = False, new_spec: Optional[BudgetSpec] = None):
        """
        Resets all tracked expenditures.
        Preserves the active budget policy unless explicitly requested to reset or replace it.
        """
        with self._lock:
            if new_spec is not None:
                self.spec = new_spec
            elif reset_spec:
                self.spec = BudgetSpec()
            elif not hasattr(self, "spec") or self.spec is None:
                self.spec = BudgetSpec()

            self.session_prompt_tokens: int = 0
            self.session_completion_tokens: int = 0
            self.session_total_tokens: int = 0
            self.session_cost_usd: float = 0.0
            self.task_spends: Dict[str, Dict[str, Any]] = {}
            self.agent_spends: Dict[str, Dict[str, Any]] = {}
            self.tool_spends: Dict[str, float] = {}

    def set_spec(self, spec: BudgetSpec):
        """Updates the active budget specification."""
        with self._lock:
            self.spec = spec

    def record_spend(
        self,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        tool_name: Optional[str] = None,
    ):
        """
        Records token and financial spend against session, task, agent, and tool ledgers.
        """
        with self._lock:
            total_toks = prompt_tokens + completion_tokens
            self.session_prompt_tokens += prompt_tokens
            self.session_completion_tokens += completion_tokens
            self.session_total_tokens += total_toks
            self.session_cost_usd = round(self.session_cost_usd + cost_usd, 6)

            # Per-task spend
            if task_id:
                if task_id not in self.task_spends:
                    self.task_spends[task_id] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
                t_entry = self.task_spends[task_id]
                t_entry["prompt_tokens"] += prompt_tokens
                t_entry["completion_tokens"] += completion_tokens
                t_entry["total_tokens"] += total_toks
                t_entry["cost_usd"] = round(t_entry["cost_usd"] + cost_usd, 6)

            # Per-agent spend
            if agent_name:
                agent_clean = agent_name.upper().strip()
                if agent_clean not in self.agent_spends:
                    self.agent_spends[agent_clean] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
                a_entry = self.agent_spends[agent_clean]
                a_entry["prompt_tokens"] += prompt_tokens
                a_entry["completion_tokens"] += completion_tokens
                a_entry["total_tokens"] += total_toks
                a_entry["cost_usd"] = round(a_entry["cost_usd"] + cost_usd, 6)

            # Per-tool spend
            if tool_name:
                self.tool_spends[tool_name] = round(self.tool_spends.get(tool_name, 0.0) + cost_usd, 6)

    def check_budget(
        self,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Checks if any budget threshold has been exceeded.
        Returns (is_exceeded, violation_reason).
        """
        with self._lock:
            # 1. Session cost budget
            if self.spec.max_session_cost_usd > 0.0 and self.session_cost_usd > self.spec.max_session_cost_usd:
                return True, f"Session cost budget exceeded: ${self.session_cost_usd:.4f} > ${self.spec.max_session_cost_usd:.4f}"

            # 2. Session token budget
            if self.spec.max_session_tokens > 0 and self.session_total_tokens > self.spec.max_session_tokens:
                return True, f"Session token budget exceeded: {self.session_total_tokens} > {self.spec.max_session_tokens}"

            # 3. Task cost budget
            if task_id and self.spec.max_cost_usd_per_task > 0.0:
                t_cost = self.task_spends.get(task_id, {}).get("cost_usd", 0.0)
                if t_cost > self.spec.max_cost_usd_per_task:
                    return True, f"Task [{task_id}] cost budget exceeded: ${t_cost:.4f} > ${self.spec.max_cost_usd_per_task:.4f}"

            # 4. Task token budget
            if task_id and self.spec.max_tokens_per_task > 0:
                t_tokens = self.task_spends.get(task_id, {}).get("total_tokens", 0)
                if t_tokens > self.spec.max_tokens_per_task:
                    return True, f"Task [{task_id}] token budget exceeded: {t_tokens} > {self.spec.max_tokens_per_task}"

            # 5. Per-agent budget
            if agent_name:
                agent_clean = agent_name.upper().strip()
                agent_limit = self.spec.agent_budgets.get(agent_clean, 0.0)
                if agent_limit > 0.0:
                    a_cost = self.agent_spends.get(agent_clean, {}).get("cost_usd", 0.0)
                    if a_cost > agent_limit:
                        return True, f"Agent [{agent_clean}] cost budget exceeded: ${a_cost:.4f} > ${agent_limit:.4f}"

            return False, None

    def assert_budget(
        self,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ):
        """
        Enforces budget: raises BudgetExceededError if limit is breached and action is HALT.
        """
        exceeded, reason = self.check_budget(task_id=task_id, agent_name=agent_name)
        if exceeded:
            if self.spec.on_budget_exceeded == "HALT":
                raise BudgetExceededError(
                    message=reason or "Budget limit exceeded",
                    budget_type="BUDGET_LIMIT",
                    limit=self.spec.max_session_cost_usd or self.spec.max_cost_usd_per_task,
                    current=self.session_cost_usd,
                )

    def get_task_spend(self, task_id: str) -> Dict[str, Any]:
        """Returns spend metrics for a specific task."""
        with self._lock:
            return dict(self.task_spends.get(task_id, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}))

    def get_agent_spend(self, agent_name: str) -> Dict[str, Any]:
        """Returns spend metrics for a specific agent role."""
        with self._lock:
            agent_clean = agent_name.upper().strip()
            return dict(self.agent_spends.get(agent_clean, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}))

    def get_session_spend(self) -> Dict[str, Any]:
        """Returns aggregate session spend metrics."""
        with self._lock:
            return {
                "prompt_tokens": self.session_prompt_tokens,
                "completion_tokens": self.session_completion_tokens,
                "total_tokens": self.session_total_tokens,
                "cost_usd": self.session_cost_usd,
            }

    def get_summary(self) -> Dict[str, Any]:
        """Returns a snapshot summary of current expenditures and budgets."""
        with self._lock:
            return {
                "session_tokens": self.session_total_tokens,
                "session_cost_usd": self.session_cost_usd,
                "task_spends": dict(self.task_spends),
                "agent_spends": dict(self.agent_spends),
                "tool_spends": dict(self.tool_spends),
                "spec": {
                    "max_session_cost_usd": self.spec.max_session_cost_usd,
                    "max_session_tokens": self.spec.max_session_tokens,
                    "max_cost_usd_per_task": self.spec.max_cost_usd_per_task,
                    "max_tokens_per_task": self.spec.max_tokens_per_task,
                }
            }


# Global singleton BudgetTracker
budget_tracker = BudgetTracker()
