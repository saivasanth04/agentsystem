"""
ResourceBudget & ResourceUsageTracker: Deterministic Multi-Dimensional Resource Budgeting (Issue #82).
Enforces fine-grained quotas across all 8 execution dimensions:
  1. max processes
  2. max memory
  3. max CPU
  4. max disk
  5. max network
  6. max tool calls
  7. max tokens
  8. max runtime
"""
from dataclasses import dataclass, field
from enum import Enum
import json
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union


class BudgetAction(str, Enum):
    HALT = "HALT"
    WARN = "WARN"


class ResourceBudgetExceededError(PermissionError):
    """Raised when an autonomous agent or tool breaches a configured resource budget."""
    def __init__(
        self,
        message: str,
        resource_type: str,
        limit: float,
        current: float,
        suggested_action: Optional[str] = None,
    ):
        super().__init__(message)
        self.resource_type = resource_type
        self.limit = limit
        self.current = current
        self.suggested_action = suggested_action

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": "ResourceBudgetExceededError",
            "message": str(self),
            "resource_type": self.resource_type,
            "limit": self.limit,
            "current": self.current,
            "suggested_action": self.suggested_action,
        }


@dataclass
class ResourceBudgetDecision:
    """Outcome of evaluating a resource consumption against the active budget."""
    allowed: bool
    resource_type: str = ""
    limit: float = 0.0
    current: float = 0.0
    reason: str = ""
    suggested_action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "resource_type": self.resource_type,
            "limit": self.limit,
            "current": self.current,
            "reason": self.reason,
            "suggested_action": self.suggested_action,
        }


@dataclass
class ResourceBudget:
    """
    Defines the resource envelope for an agent execution task or sandbox session.
    A value of 0 or 0.0 signifies unconstrained / unlimited for that specific dimension.
    """
    # 1. Process limits
    max_processes: int = 50                     # Max concurrent child processes
    # 2. Memory limits
    max_memory_mb: float = 1024.0               # Max resident set size (RSS) in MB
    # 3. CPU limits
    max_cpu_percent: float = 90.0               # Max CPU % utilization across cores
    max_cpu_cores: int = 2                      # Max CPU cores (Docker/cgroups)
    # 4. Disk limits
    max_disk_write_mb: float = 250.0            # Max cumulative MB written to workspace
    max_single_file_mb: float = 25.0            # Max size of an individual file write in MB
    # 5. Network limits
    max_network_requests: int = 100             # Max outbound HTTP / network requests
    max_network_mb: float = 50.0                # Max network data transfer in MB
    # 6. Tool call limits
    max_tool_calls_total: int = 50              # Max cumulative tool calls across task
    max_tool_calls_per_turn: int = 10           # Max tool calls within a single turn
    max_consecutive_identical_tool_calls: int = 3 # Max consecutive identical tool invocations before circuit breaker (0=disabled)
    # 7. Token limits
    max_tokens: int = 100_000                   # Max prompt + completion tokens
    # 8. Runtime limits
    max_runtime_seconds: float = 180.0          # Max cumulative execution time (wall-clock)
    max_command_timeout_seconds: float = 30.0   # Max per-command execution time
    on_budget_exceeded: str = BudgetAction.HALT
    action: str = BudgetAction.HALT

    def __post_init__(self):
        if self.action != BudgetAction.HALT and self.on_budget_exceeded == BudgetAction.HALT:
            self.on_budget_exceeded = self.action
        elif self.on_budget_exceeded != BudgetAction.HALT and self.action == BudgetAction.HALT:
            self.action = self.on_budget_exceeded

    def is_unconstrained(self) -> bool:
        return (
            self.max_processes == 0
            and self.max_memory_mb == 0.0
            and self.max_cpu_percent == 0.0
            and self.max_disk_write_mb == 0.0
            and self.max_network_requests == 0
            and self.max_tool_calls_total == 0
            and self.max_consecutive_identical_tool_calls == 0
            and self.max_tokens == 0
            and self.max_runtime_seconds == 0.0
        )

    def to_dict(self) -> Dict[str, Any]:
        act_val = self.on_budget_exceeded.value if hasattr(self.on_budget_exceeded, "value") else str(self.on_budget_exceeded)
        return {
            "max_processes": self.max_processes,
            "max_memory_mb": self.max_memory_mb,
            "max_cpu_percent": self.max_cpu_percent,
            "max_cpu_cores": self.max_cpu_cores,
            "max_disk_write_mb": self.max_disk_write_mb,
            "max_single_file_mb": self.max_single_file_mb,
            "max_network_requests": self.max_network_requests,
            "max_network_mb": self.max_network_mb,
            "max_tool_calls_total": self.max_tool_calls_total,
            "max_tool_calls_per_turn": self.max_tool_calls_per_turn,
            "max_consecutive_identical_tool_calls": self.max_consecutive_identical_tool_calls,
            "max_tokens": self.max_tokens,
            "max_runtime_seconds": self.max_runtime_seconds,
            "max_command_timeout_seconds": self.max_command_timeout_seconds,
            "on_budget_exceeded": act_val,
            "action": act_val,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ResourceBudget":
        if not data:
            return cls()
        eff_action = str(data.get("action") or data.get("on_budget_exceeded") or BudgetAction.HALT)
        return cls(
            max_processes=int(data.get("max_processes", 50)),
            max_memory_mb=float(data.get("max_memory_mb", 1024.0)),
            max_cpu_percent=float(data.get("max_cpu_percent", 90.0)),
            max_cpu_cores=int(data.get("max_cpu_cores", 2)),
            max_disk_write_mb=float(data.get("max_disk_write_mb", 250.0)),
            max_single_file_mb=float(data.get("max_single_file_mb", 25.0)),
            max_network_requests=int(data.get("max_network_requests", 100)),
            max_network_mb=float(data.get("max_network_mb", 50.0)),
            max_tool_calls_total=int(data.get("max_tool_calls_total", 50)),
            max_tool_calls_per_turn=int(data.get("max_tool_calls_per_turn", 10)),
            max_consecutive_identical_tool_calls=int(data.get("max_consecutive_identical_tool_calls", 3)),
            max_tokens=int(data.get("max_tokens", 100_000)),
            max_runtime_seconds=float(data.get("max_runtime_seconds", 180.0)),
            max_command_timeout_seconds=float(data.get("max_command_timeout_seconds", 30.0)),
            on_budget_exceeded=eff_action,
            action=eff_action,
        )

    @classmethod
    def unconstrained(cls) -> "ResourceBudget":
        """Returns a completely unconstrained resource budget with all limits disabled."""
        return cls(
            max_processes=0,
            max_memory_mb=0.0,
            max_cpu_percent=0.0,
            max_cpu_cores=0,
            max_disk_write_mb=0.0,
            max_single_file_mb=0.0,
            max_network_requests=0,
            max_network_mb=0.0,
            max_tool_calls_total=0,
            max_tool_calls_per_turn=0,
            max_consecutive_identical_tool_calls=0,
            max_tokens=0,
            max_runtime_seconds=0.0,
            max_command_timeout_seconds=0.0,
        )


class ResourceUsageTracker:
    """
    Thread-safe ledger and real-time evaluator for all 8 resource dimensions.
    """

    def __init__(self, budget: Optional[ResourceBudget] = None):
        self.budget = budget or ResourceBudget()
        self._lock = threading.RLock()
        self.start_time = time.time()
        self.reset()

    def reset(self):
        """Resets all tracked metrics to zero."""
        with self._lock:
            self.start_time = time.time()
            self.current_processes: int = 0
            self.peak_processes: int = 0
            self.current_memory_mb: float = 0.0
            self.peak_memory_mb: float = 0.0
            self.peak_cpu_percent: float = 0.0
            self.cumulative_disk_written_bytes: int = 0
            self.network_requests_count: int = 0
            self.network_bytes_transferred: int = 0
            self.total_tool_calls: int = 0
            self.tool_calls_by_name: Dict[str, int] = {}
            self.last_tool_fingerprint: Optional[str] = None
            self.consecutive_identical_tool_calls: int = 0
            self.prompt_tokens: int = 0
            self.completion_tokens: int = 0
            self.total_tokens: int = 0

    def set_budget(self, budget: ResourceBudget):
        """Updates the active budget."""
        with self._lock:
            self.budget = budget

    def record_tool_call(
        self, tool_name: str, count: int = 1, arguments: Optional[Dict[str, Any]] = None
    ) -> ResourceBudgetDecision:
        """Records tool invocations and checks against cumulative tool ceiling and repetition circuit breaker."""
        with self._lock:
            clean_name = (tool_name or "unknown").strip().lower()

            # Check consecutive identical tool invocations (Circuit Breaker)
            if arguments is not None:
                try:
                    args_serialized = json.dumps(arguments, sort_keys=True, default=str)
                except Exception:
                    args_serialized = str(arguments)
                fingerprint = f"{clean_name}:{args_serialized}"
            else:
                fingerprint = clean_name

            if self.last_tool_fingerprint == fingerprint:
                self.consecutive_identical_tool_calls += count
            else:
                self.last_tool_fingerprint = fingerprint
                self.consecutive_identical_tool_calls = count

            if (
                self.budget.max_consecutive_identical_tool_calls > 0
                and self.consecutive_identical_tool_calls > self.budget.max_consecutive_identical_tool_calls
            ):
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="consecutive_identical_tool_calls",
                    limit=float(self.budget.max_consecutive_identical_tool_calls),
                    current=float(self.consecutive_identical_tool_calls),
                    reason=(
                        f"Repetitive tool call loop detected: '{clean_name}' was invoked "
                        f"{self.consecutive_identical_tool_calls} times consecutively with identical arguments "
                        f"(threshold: {self.budget.max_consecutive_identical_tool_calls})."
                    ),
                    suggested_action="Vary tool arguments, inspect earlier tool outputs, or synthesize your final deliverable.",
                )

            self.total_tool_calls += count
            self.tool_calls_by_name[clean_name] = self.tool_calls_by_name.get(clean_name, 0) + count

            if self.budget.max_tool_calls_total > 0 and self.total_tool_calls > self.budget.max_tool_calls_total:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="tool_calls",
                    limit=float(self.budget.max_tool_calls_total),
                    current=float(self.total_tool_calls),
                    reason=f"Tool call budget exceeded: {self.total_tool_calls} > {self.budget.max_tool_calls_total} total tool calls.",
                    suggested_action="Consolidate tool operations or increase max_tool_calls_total on the task budget.",
                )
            return ResourceBudgetDecision(allowed=True, resource_type="tool_calls", current=float(self.total_tool_calls))

    def get_utilization_ratio(
        self, current_turn: Optional[int] = None, max_turns: Optional[int] = None
    ) -> Dict[str, float]:
        """Calculates resource consumption ratios against configured limits."""
        with self._lock:
            elapsed = time.time() - self.start_time
            ratios: Dict[str, float] = {}

            if self.budget.max_tool_calls_total > 0:
                ratios["tool_calls"] = min(1.0, self.total_tool_calls / float(self.budget.max_tool_calls_total))
            if self.budget.max_tokens > 0:
                ratios["tokens"] = min(1.0, self.total_tokens / float(self.budget.max_tokens))
            if self.budget.max_runtime_seconds > 0:
                ratios["runtime"] = min(1.0, elapsed / float(self.budget.max_runtime_seconds))
            if current_turn is not None and max_turns is not None and max_turns > 0:
                ratios["turns"] = min(1.0, current_turn / float(max_turns))

            max_ratio = max(ratios.values()) if ratios else 0.0
            ratios["max_ratio"] = round(max_ratio, 3)
            return ratios

    def record_file_write(self, filepath: str, bytes_written: int) -> ResourceBudgetDecision:
        """Verifies single file write size and cumulative workspace disk write budget."""
        with self._lock:
            single_mb = bytes_written / (1024.0 * 1024.0)
            if self.budget.max_single_file_mb > 0 and single_mb > self.budget.max_single_file_mb:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="single_file_size",
                    limit=self.budget.max_single_file_mb,
                    current=round(single_mb, 2),
                    reason=f"Single file write size exceeded: {single_mb:.2f}MB > {self.budget.max_single_file_mb:.2f}MB limit for '{filepath}'.",
                    suggested_action="Write data in smaller chunks or raise max_single_file_mb.",
                )

            self.cumulative_disk_written_bytes += max(0, bytes_written)
            total_disk_mb = self.cumulative_disk_written_bytes / (1024.0 * 1024.0)

            if self.budget.max_disk_write_mb > 0 and total_disk_mb > self.budget.max_disk_write_mb:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="disk_write",
                    limit=self.budget.max_disk_write_mb,
                    current=round(total_disk_mb, 2),
                    reason=f"Cumulative workspace disk budget exceeded: {total_disk_mb:.2f}MB > {self.budget.max_disk_write_mb:.2f}MB.",
                    suggested_action="Avoid writing large generated files or raise max_disk_write_mb on the task budget.",
                )
            return ResourceBudgetDecision(allowed=True, resource_type="disk_write", current=round(total_disk_mb, 2))

    def record_network_request(self, url: str = "", bytes_transferred: int = 0) -> ResourceBudgetDecision:
        """Records network requests and data transfer, checking against request and bandwidth budgets."""
        with self._lock:
            self.network_requests_count += 1
            self.network_bytes_transferred += max(0, bytes_transferred)

            if self.budget.max_network_requests > 0 and self.network_requests_count > self.budget.max_network_requests:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="network_requests",
                    limit=float(self.budget.max_network_requests),
                    current=float(self.network_requests_count),
                    reason=f"Network request budget exceeded: {self.network_requests_count} > {self.budget.max_network_requests} requests.",
                    suggested_action="Cache remote requests or raise max_network_requests on the task budget.",
                )

            total_net_mb = self.network_bytes_transferred / (1024.0 * 1024.0)
            if self.budget.max_network_mb > 0 and total_net_mb > self.budget.max_network_mb:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="network_bandwidth",
                    limit=self.budget.max_network_mb,
                    current=round(total_net_mb, 2),
                    reason=f"Network data transfer budget exceeded: {total_net_mb:.2f}MB > {self.budget.max_network_mb:.2f}MB.",
                    suggested_action="Reduce remote payload size or increase max_network_mb.",
                )
            return ResourceBudgetDecision(allowed=True, resource_type="network_requests", current=float(self.network_requests_count))

    def record_tokens(self, prompt_tokens: int, completion_tokens: int) -> ResourceBudgetDecision:
        """Records prompt and completion tokens, checking against task token ceiling."""
        with self._lock:
            self.prompt_tokens += max(0, prompt_tokens)
            self.completion_tokens += max(0, completion_tokens)
            self.total_tokens = self.prompt_tokens + self.completion_tokens

            if self.budget.max_tokens > 0 and self.total_tokens > self.budget.max_tokens:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="tokens",
                    limit=float(self.budget.max_tokens),
                    current=float(self.total_tokens),
                    reason=f"Task token budget exceeded: {self.total_tokens} > {self.budget.max_tokens} tokens.",
                    suggested_action="Compact context, shorten prompt/outputs, or raise max_tokens on the task budget.",
                )
            return ResourceBudgetDecision(allowed=True, resource_type="tokens", current=float(self.total_tokens))

    def record_process_sample(
        self,
        process_count: int,
        memory_mb: float,
        cpu_percent: float = 0.0,
    ) -> ResourceBudgetDecision:
        """Records a process sample from execution sandbox and verifies against process, memory, and CPU limits."""
        with self._lock:
            self.current_processes = process_count
            if process_count > self.peak_processes:
                self.peak_processes = process_count

            self.current_memory_mb = memory_mb
            if memory_mb > self.peak_memory_mb:
                self.peak_memory_mb = memory_mb

            if cpu_percent > self.peak_cpu_percent:
                self.peak_cpu_percent = cpu_percent

            # Check process count limit
            if self.budget.max_processes > 0 and process_count > self.budget.max_processes:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="processes",
                    limit=float(self.budget.max_processes),
                    current=float(process_count),
                    reason=f"Process limit exceeded: {process_count} active processes > {self.budget.max_processes} max processes.",
                    suggested_action="Ensure subprocesses do not spawn unbounded threads or child processes.",
                )

            # Check memory limit
            if self.budget.max_memory_mb > 0 and memory_mb > self.budget.max_memory_mb:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="memory",
                    limit=self.budget.max_memory_mb,
                    current=round(memory_mb, 2),
                    reason=f"Memory limit exceeded: {memory_mb:.1f}MB resident memory > {self.budget.max_memory_mb:.1f}MB budget.",
                    suggested_action="Optimize memory utilization or increase max_memory_mb in sandbox policy.",
                )

            # Check CPU limit (if non-zero)
            if self.budget.max_cpu_percent > 0 and cpu_percent > self.budget.max_cpu_percent:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="cpu",
                    limit=self.budget.max_cpu_percent,
                    current=round(cpu_percent, 1),
                    reason=f"CPU utilization ceiling exceeded: {cpu_percent:.1f}% > {self.budget.max_cpu_percent:.1f}%.",
                    suggested_action="Throttle parallel execution or optimize CPU-intensive loops.",
                )

            return ResourceBudgetDecision(allowed=True, resource_type="system_sample", current=round(memory_mb, 2))

    def check_runtime(self) -> ResourceBudgetDecision:
        """Evaluates wall-clock elapsed execution time against the task runtime budget."""
        with self._lock:
            elapsed = time.time() - self.start_time
            if self.budget.max_runtime_seconds > 0 and elapsed > self.budget.max_runtime_seconds:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="runtime",
                    limit=self.budget.max_runtime_seconds,
                    current=round(elapsed, 2),
                    reason=f"Execution runtime exceeded: {elapsed:.1f}s > {self.budget.max_runtime_seconds:.1f}s.",
                    suggested_action="Increase max_runtime_seconds on task budget or decompose task into smaller subtasks.",
                )
            return ResourceBudgetDecision(allowed=True, resource_type="runtime", current=round(elapsed, 2))

    def check_budget(self, resource_type: Optional[str] = None) -> ResourceBudgetDecision:
        """Comprehensive check across all resources."""
        with self._lock:
            # 1. Runtime check
            rt_dec = self.check_runtime()
            if not rt_dec.allowed:
                return rt_dec

            # 2. Token check
            if self.budget.max_tokens > 0 and self.total_tokens > self.budget.max_tokens:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="tokens",
                    limit=float(self.budget.max_tokens),
                    current=float(self.total_tokens),
                    reason=f"Task token budget exceeded: {self.total_tokens} > {self.budget.max_tokens}.",
                )

            # 3. Tool call check
            if self.budget.max_tool_calls_total > 0 and self.total_tool_calls > self.budget.max_tool_calls_total:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="tool_calls",
                    limit=float(self.budget.max_tool_calls_total),
                    current=float(self.total_tool_calls),
                    reason=f"Tool call budget exceeded: {self.total_tool_calls} > {self.budget.max_tool_calls_total}.",
                )

            # 4. Disk check
            total_disk_mb = self.cumulative_disk_written_bytes / (1024.0 * 1024.0)
            if self.budget.max_disk_write_mb > 0 and total_disk_mb > self.budget.max_disk_write_mb:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="disk_write",
                    limit=self.budget.max_disk_write_mb,
                    current=round(total_disk_mb, 2),
                    reason=f"Cumulative workspace disk budget exceeded: {total_disk_mb:.2f}MB > {self.budget.max_disk_write_mb:.2f}MB.",
                )

            # 5. Network check
            if self.budget.max_network_requests > 0 and self.network_requests_count > self.budget.max_network_requests:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="network_requests",
                    limit=float(self.budget.max_network_requests),
                    current=float(self.network_requests_count),
                    reason=f"Network request budget exceeded: {self.network_requests_count} > {self.budget.max_network_requests}.",
                )

            # 6. Memory peak check
            if self.budget.max_memory_mb > 0 and self.peak_memory_mb > self.budget.max_memory_mb:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="memory",
                    limit=self.budget.max_memory_mb,
                    current=round(self.peak_memory_mb, 2),
                    reason=f"Peak memory exceeded: {self.peak_memory_mb:.1f}MB > {self.budget.max_memory_mb:.1f}MB.",
                )

            # 7. Process count check
            if self.budget.max_processes > 0 and self.peak_processes > self.budget.max_processes:
                return ResourceBudgetDecision(
                    allowed=False,
                    resource_type="processes",
                    limit=float(self.budget.max_processes),
                    current=float(self.peak_processes),
                    reason=f"Peak process count exceeded: {self.peak_processes} > {self.budget.max_processes}.",
                )

            return ResourceBudgetDecision(allowed=True, reason="All resources within configured budget.")

    def assert_budget(self, resource_type: Optional[str] = None):
        """Raises ResourceBudgetExceededError if limit is breached and action is HALT."""
        decision = self.check_budget(resource_type)
        if not decision.allowed:
            if self.budget.on_budget_exceeded == BudgetAction.HALT or self.budget.on_budget_exceeded == "HALT":
                raise ResourceBudgetExceededError(
                    message=decision.reason,
                    resource_type=decision.resource_type,
                    limit=decision.limit,
                    current=decision.current,
                    suggested_action=decision.suggested_action,
                )

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            elapsed = round(time.time() - self.start_time, 2)
            total_disk_mb = round(self.cumulative_disk_written_bytes / (1024.0 * 1024.0), 2)
            total_net_mb = round(self.network_bytes_transferred / (1024.0 * 1024.0), 2)
            return {
                "elapsed_runtime_seconds": elapsed,
                "current_processes": self.current_processes,
                "peak_processes": self.peak_processes,
                "current_memory_mb": round(self.current_memory_mb, 2),
                "peak_memory_mb": round(self.peak_memory_mb, 2),
                "peak_cpu_percent": round(self.peak_cpu_percent, 1),
                "cumulative_disk_written_mb": total_disk_mb,
                "cumulative_disk_written_bytes": self.cumulative_disk_written_bytes,
                "network_requests_count": self.network_requests_count,
                "network_mb_transferred": total_net_mb,
                "total_tool_calls": self.total_tool_calls,
                "tool_calls_by_name": dict(self.tool_calls_by_name),
                "consecutive_identical_tool_calls": self.consecutive_identical_tool_calls,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "budget": self.budget.to_dict(),
            }
