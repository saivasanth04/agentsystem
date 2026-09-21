"""
Production-Grade Telemetry & Execution Observability Engine (Issue #52).
Captures, aggregates, and attributes runtime telemetry across all 10 canonical dimensions:
1. Agent Latency (per-agent duration, p50/p95 percentiles, active vs idle overhead)
2. LLM Latency (round-trip timing for provider calls, total inference wait time)
3. Tool Latency (per-tool aggregation, min/max/mean, tool vs LLM duration ratio)
4. Token Usage (prompt/completion/cached tokens partitioned by agent and by model)
5. Failure Rate (task failure rate, tool failure rate, verification stage failure rate)
6. Retry Count (aggregate retries across tasks, retry rate, failure cause attribution)
7. Retrieval Count (CBM graph queries, symbol lookups, memory queries, file searches)
8. Files Accessed (distinct files read vs written vs deleted, working set size)
9. Model Used (model tracking per task attempt, session model distribution)
10. Cost Attribution (breakdown by agent, by model, tool compute vs LLM, productive vs wasted)
"""
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
from pathlib import Path
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Union


@dataclass
class LatencyMetric:
    """Statistical summary of execution latencies."""
    count: int = 0
    total_seconds: float = 0.0
    min_seconds: float = 0.0
    max_seconds: float = 0.0
    mean_seconds: float = 0.0
    p50_seconds: float = 0.0
    p95_seconds: float = 0.0
    _raw_durations: List[float] = field(default_factory=list, repr=False)

    def record(self, duration: float) -> None:
        """Records a duration in seconds and updates statistical summary."""
        dur = max(0.0, float(duration))
        self._raw_durations.append(dur)
        self.count += 1
        self.total_seconds = round(self.total_seconds + dur, 4)
        if self.count == 1:
            self.min_seconds = round(dur, 4)
            self.max_seconds = round(dur, 4)
        else:
            self.min_seconds = round(min(self.min_seconds, dur), 4)
            self.max_seconds = round(max(self.max_seconds, dur), 4)
        self.mean_seconds = round(self.total_seconds / self.count, 4)

        # Percentiles
        sorted_d = sorted(self._raw_durations)
        n = len(sorted_d)
        if n == 1:
            self.p50_seconds = sorted_d[0]
            self.p95_seconds = sorted_d[0]
        else:
            idx_p50 = int(math.ceil(0.50 * n)) - 1
            idx_p95 = int(math.ceil(0.95 * n)) - 1
            self.p50_seconds = round(sorted_d[max(0, idx_p50)], 4)
            self.p95_seconds = round(sorted_d[max(0, idx_p95)], 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "total_seconds": round(self.total_seconds, 4),
            "min_seconds": round(self.min_seconds, 4),
            "max_seconds": round(self.max_seconds, 4),
            "mean_seconds": round(self.mean_seconds, 4),
            "p50_seconds": round(self.p50_seconds, 4),
            "p95_seconds": round(self.p95_seconds, 4),
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "LatencyMetric":
        if not d:
            return cls()
        metric = cls(
            count=int(d.get("count", 0)),
            total_seconds=float(d.get("total_seconds", 0.0)),
            min_seconds=float(d.get("min_seconds", 0.0)),
            max_seconds=float(d.get("max_seconds", 0.0)),
            mean_seconds=float(d.get("mean_seconds", 0.0)),
            p50_seconds=float(d.get("p50_seconds", 0.0)),
            p95_seconds=float(d.get("p95_seconds", 0.0)),
        )
        return metric


@dataclass
class AgentTelemetry:
    """Telemetry metrics partitioned by agent role."""
    agent_name: str
    latency: LatencyMetric = field(default_factory=LatencyMetric)
    tasks_assigned: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "latency": self.latency.to_dict(),
            "tasks_assigned": self.tasks_assigned,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "retries": self.retries,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AgentTelemetry":
        return cls(
            agent_name=d.get("agent_name", "UNKNOWN"),
            latency=LatencyMetric.from_dict(d.get("latency")),
            tasks_assigned=int(d.get("tasks_assigned", 0)),
            tasks_completed=int(d.get("tasks_completed", 0)),
            tasks_failed=int(d.get("tasks_failed", 0)),
            retries=int(d.get("retries", 0)),
            prompt_tokens=int(d.get("prompt_tokens", 0)),
            completion_tokens=int(d.get("completion_tokens", 0)),
            total_tokens=int(d.get("total_tokens", 0)),
            cost_usd=float(d.get("cost_usd", 0.0)),
        )


@dataclass
class LLMTelemetry:
    """Telemetry metrics for LLM provider invocations."""
    call_count: int = 0
    latency: LatencyMetric = field(default_factory=LatencyMetric)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    models_used: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def record_call(
        self,
        model: str,
        latency_seconds: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        self.call_count += 1
        self.latency.record(latency_seconds)
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.cached_tokens += cached_tokens
        self.total_tokens += (prompt_tokens + completion_tokens)
        self.cost_usd = round(self.cost_usd + cost_usd, 6)

        m_key = model or "default"
        if m_key not in self.models_used:
            self.models_used[m_key] = {
                "calls": 0,
                "latency_seconds": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
            }
        m_entry = self.models_used[m_key]
        m_entry["calls"] += 1
        m_entry["latency_seconds"] = round(m_entry["latency_seconds"] + latency_seconds, 4)
        m_entry["prompt_tokens"] += prompt_tokens
        m_entry["completion_tokens"] += completion_tokens
        m_entry["cached_tokens"] += cached_tokens
        m_entry["total_tokens"] += (prompt_tokens + completion_tokens)
        m_entry["cost_usd"] = round(m_entry["cost_usd"] + cost_usd, 6)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_count": self.call_count,
            "latency": self.latency.to_dict(),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "models_used": self.models_used,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "LLMTelemetry":
        if not d:
            return cls()
        return cls(
            call_count=int(d.get("call_count", 0)),
            latency=LatencyMetric.from_dict(d.get("latency")),
            prompt_tokens=int(d.get("prompt_tokens", 0)),
            completion_tokens=int(d.get("completion_tokens", 0)),
            cached_tokens=int(d.get("cached_tokens", 0)),
            total_tokens=int(d.get("total_tokens", 0)),
            cost_usd=float(d.get("cost_usd", 0.0)),
            models_used=dict(d.get("models_used") or {}),
        )


@dataclass
class ToolTelemetry:
    """Telemetry metrics per environment tool."""
    tool_name: str
    call_count: int = 0
    error_count: int = 0
    failure_rate: float = 0.0
    latency: LatencyMetric = field(default_factory=LatencyMetric)
    cost_usd: float = 0.0

    def record_call(self, duration_seconds: float, is_error: bool = False, cost_usd: float = 0.0) -> None:
        self.call_count += 1
        if is_error:
            self.error_count += 1
        self.failure_rate = round(self.error_count / self.call_count, 4) if self.call_count > 0 else 0.0
        self.latency.record(duration_seconds)
        self.cost_usd = round(self.cost_usd + cost_usd, 6)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "call_count": self.call_count,
            "error_count": self.error_count,
            "failure_rate": self.failure_rate,
            "latency": self.latency.to_dict(),
            "cost_usd": round(self.cost_usd, 6),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ToolTelemetry":
        return cls(
            tool_name=d.get("tool_name", "UNKNOWN"),
            call_count=int(d.get("call_count", 0)),
            error_count=int(d.get("error_count", 0)),
            failure_rate=float(d.get("failure_rate", 0.0)),
            latency=LatencyMetric.from_dict(d.get("latency")),
            cost_usd=float(d.get("cost_usd", 0.0)),
        )


@dataclass
class FileAccessTelemetry:
    """Tracks session-wide file access and working-set footprints."""
    files_read: Set[str] = field(default_factory=set)
    files_written: Set[str] = field(default_factory=set)
    files_deleted: Set[str] = field(default_factory=set)
    total_mutations: int = 0

    def record_access(self, filepath: str, access_type: str = "READ") -> None:
        clean = filepath.replace("\\", "/").strip("/")
        if not clean:
            return
        act = access_type.upper()
        if act == "READ":
            self.files_read.add(clean)
        elif act in ("WRITE", "CREATED", "MODIFIED"):
            self.files_written.add(clean)
            self.total_mutations += 1
        elif act == "DELETED":
            self.files_deleted.add(clean)
            self.total_mutations += 1

    @property
    def total_distinct_files(self) -> int:
        return len(self.files_read | self.files_written | self.files_deleted)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files_read": sorted(list(self.files_read)),
            "files_written": sorted(list(self.files_written)),
            "files_deleted": sorted(list(self.files_deleted)),
            "total_distinct_files": self.total_distinct_files,
            "total_mutations": self.total_mutations,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "FileAccessTelemetry":
        if not d:
            return cls()
        return cls(
            files_read=set(d.get("files_read") or []),
            files_written=set(d.get("files_written") or []),
            files_deleted=set(d.get("files_deleted") or []),
            total_mutations=int(d.get("total_mutations", 0)),
        )


@dataclass
class TelemetrySnapshot:
    """
    Immutable, serializable session telemetry ledger capturing all 10 canonical dimensions.
    """
    session_id: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    agent_latencies: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    llm_latency: Dict[str, Any] = field(default_factory=dict)
    tool_latencies: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    token_usage: Dict[str, Any] = field(default_factory=dict)
    failure_rates: Dict[str, float] = field(default_factory=dict)
    retry_metrics: Dict[str, Any] = field(default_factory=dict)
    retrieval_metrics: Dict[str, Any] = field(default_factory=dict)
    file_access: Dict[str, Any] = field(default_factory=dict)
    model_distribution: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    cost_breakdown: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "agent_latencies": self.agent_latencies,
            "llm_latency": self.llm_latency,
            "tool_latencies": self.tool_latencies,
            "token_usage": self.token_usage,
            "failure_rates": self.failure_rates,
            "retry_metrics": self.retry_metrics,
            "retrieval_metrics": self.retrieval_metrics,
            "file_access": self.file_access,
            "model_distribution": self.model_distribution,
            "cost_breakdown": self.cost_breakdown,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TelemetrySnapshot":
        return cls(
            session_id=d.get("session_id", "default"),
            created_at=d.get("created_at", datetime.now().isoformat()),
            agent_latencies=dict(d.get("agent_latencies") or {}),
            llm_latency=dict(d.get("llm_latency") or {}),
            tool_latencies=dict(d.get("tool_latencies") or {}),
            token_usage=dict(d.get("token_usage") or {}),
            failure_rates=dict(d.get("failure_rates") or {}),
            retry_metrics=dict(d.get("retry_metrics") or {}),
            retrieval_metrics=dict(d.get("retrieval_metrics") or {}),
            file_access=dict(d.get("file_access") or {}),
            model_distribution=dict(d.get("model_distribution") or {}),
            cost_breakdown=dict(d.get("cost_breakdown") or {}),
        )


class TelemetryEngine:
    """
    Central Thread-Safe Telemetry & Observability Engine.
    Subscribes to the EventBus to capture execution events, and provides
    programmatic instrumentation APIs for high-resolution timing and accounting.
    """

    def __init__(self, session_id: Optional[str] = None, event_bus: Optional[Any] = None):
        self.session_id = session_id or "default-session"
        self._lock = threading.RLock()
        self.event_bus = event_bus

        # 1. Agent metrics
        self._agents: Dict[str, AgentTelemetry] = {}
        # 2. LLM metrics
        self._llm = LLMTelemetry()
        # 3. Tool metrics
        self._tools: Dict[str, ToolTelemetry] = {}
        # 4. File access footprint
        self._file_access = FileAccessTelemetry()
        # 5. Task & verification outcomes
        self._tasks_total: int = 0
        self._tasks_passed: int = 0
        self._tasks_failed: int = 0
        self._task_retries: int = 0
        self._retried_task_ids: Set[str] = set()
        self._replan_iterations: int = 0
        self._verification_stages_total: int = 0
        self._verification_stages_failed: int = 0
        # 6. Retrievals
        self._retrieval_counts: Dict[str, int] = {
            "cbm_subgraph": 0,
            "symbol_lookup": 0,
            "memory_query": 0,
            "file_search": 0,
            "skill_load": 0,
            "total": 0,
        }
        # 7. Financial breakdown
        self._productive_cost_usd: float = 0.0
        self._wasted_cost_usd: float = 0.0

        if self.event_bus:
            self._attach_event_bus(self.event_bus)

    def set_session(self, session_id: str) -> None:
        with self._lock:
            self.session_id = session_id

    def _attach_event_bus(self, event_bus: Any) -> None:
        """Subscribes to EventBus for automatic lifecycle event telemetry capture."""
        try:
            event_bus.subscribe("*", self._handle_event_bus_event)
        except Exception:
            pass

    def _handle_event_bus_event(self, event: Any) -> None:
        """Processes events dispatched by the EventBus."""
        with self._lock:
            e_type = getattr(event, "event_type", None)
            e_type_val = e_type.value if hasattr(e_type, "value") else str(e_type)
            payload = getattr(event, "payload", {}) or {}

            if e_type_val == "TOOL_COMPLETED":
                t_name = payload.get("tool_name") or "UNKNOWN"
                dur = float(payload.get("duration_seconds", 0.0))
                is_err = (payload.get("status") == "ERROR")
                self.record_tool_call(tool_name=t_name, duration=dur, is_error=is_err)

            elif e_type_val == "FILE_CHANGED":
                fp = payload.get("filepath")
                c_type = payload.get("change_type", "MODIFIED")
                if fp:
                    self.record_file_access(filepath=fp, access_type=c_type)

            elif e_type_val == "TEST_STARTED":
                self._verification_stages_total += 1

            elif e_type_val == "TEST_FAILED":
                self._verification_stages_failed += 1

            elif e_type_val == "REPLAN_STARTED":
                self._replan_iterations += 1

    # ==========================================
    # PROGRAMMATIC INSTRUMENTATION METHODS
    # ==========================================

    def record_agent_execution(
        self,
        agent_name: str,
        duration_seconds: float,
        status: str = "COMPLETED",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Records agent task execution duration, token usage, and status."""
        with self._lock:
            a_key = agent_name or "UNKNOWN"
            if a_key not in self._agents:
                self._agents[a_key] = AgentTelemetry(agent_name=a_key)
            agent = self._agents[a_key]
            agent.latency.record(duration_seconds)
            agent.tasks_assigned += 1
            if status == "COMPLETED":
                agent.tasks_completed += 1
            else:
                agent.tasks_failed += 1
            agent.prompt_tokens += prompt_tokens
            agent.completion_tokens += completion_tokens
            agent.total_tokens += (prompt_tokens + completion_tokens)
            agent.cost_usd = round(agent.cost_usd + cost_usd, 6)

    def record_llm_call(
        self,
        model: str,
        latency_seconds: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
        cost_usd: float = 0.0,
        agent_name: Optional[str] = None,
    ) -> None:
        """Records round-trip latency, token counts, and compute cost for an LLM provider call."""
        with self._lock:
            self._llm.record_call(
                model=model,
                latency_seconds=latency_seconds,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                cost_usd=cost_usd,
            )
            if agent_name:
                a_key = agent_name or "UNKNOWN"
                if a_key not in self._agents:
                    self._agents[a_key] = AgentTelemetry(agent_name=a_key)
                agent = self._agents[a_key]
                agent.prompt_tokens += prompt_tokens
                agent.completion_tokens += completion_tokens
                agent.total_tokens += (prompt_tokens + completion_tokens)
                agent.cost_usd = round(agent.cost_usd + cost_usd, 6)

    def record_tool_call(
        self,
        tool_name: str,
        duration: float,
        is_error: bool = False,
        cost_usd: float = 0.0,
    ) -> None:
        """Records tool execution duration, outcome, and compute cost."""
        with self._lock:
            t_key = tool_name or "UNKNOWN"
            if t_key not in self._tools:
                self._tools[t_key] = ToolTelemetry(tool_name=t_key)
            self._tools[t_key].record_call(duration_seconds=duration, is_error=is_error, cost_usd=cost_usd)

    def record_file_access(self, filepath: str, access_type: str = "READ") -> None:
        """Records file access event (READ, WRITE, CREATED, MODIFIED, DELETED)."""
        with self._lock:
            self._file_access.record_access(filepath=filepath, access_type=access_type)

    def record_retrieval(self, retrieval_type: str = "cbm_subgraph", count: int = 1) -> None:
        """Records context/knowledge retrieval operations (CBM, symbols, memory, search)."""
        with self._lock:
            r_key = retrieval_type.lower()
            if r_key not in self._retrieval_counts:
                self._retrieval_counts[r_key] = 0
            self._retrieval_counts[r_key] += count
            self._retrieval_counts["total"] += count

    def record_retry(self, task_id: str, retry_number: int, agent_name: Optional[str] = None) -> None:
        """Records a task execution retry."""
        with self._lock:
            self._task_retries += 1
            self._retried_task_ids.add(task_id)
            if agent_name and agent_name in self._agents:
                self._agents[agent_name].retries += 1

    def record_replan_iteration(self, iteration: int = 1, reason: str = "") -> None:
        """Records a dynamic replan cycle."""
        with self._lock:
            if iteration > 0:
                self._replan_iterations += iteration

    def record_task_outcome(
        self,
        task_id: str,
        passed: bool,
        cost_usd: float = 0.0,
        is_retry: bool = False,
    ) -> None:
        """Records task acceptance gate verdict and partitions productive vs wasted cost."""
        with self._lock:
            self._tasks_total += 1
            if passed:
                self._tasks_passed += 1
                self._productive_cost_usd = round(self._productive_cost_usd + cost_usd, 6)
            else:
                self._tasks_failed += 1
                self._wasted_cost_usd = round(self._wasted_cost_usd + cost_usd, 6)

    def record_verification_stage(self, stage: str, passed: bool) -> None:
        """Records verification stage outcome."""
        with self._lock:
            self._verification_stages_total += 1
            if not passed:
                self._verification_stages_failed += 1

    # ==========================================
    # SNAPSHOT GENERATION
    # ==========================================

    def get_snapshot(self) -> TelemetrySnapshot:
        """Generates a comprehensive, immutable TelemetrySnapshot covering all 10 dimensions."""
        with self._lock:
            # 1. Agent Latencies
            agent_dict = {name: a.to_dict() for name, a in self._agents.items()}

            # 2. LLM Latency
            llm_dict = self._llm.to_dict()

            # 3. Tool Latencies
            tool_dict = {name: t.to_dict() for name, t in self._tools.items()}

            # 4. Token Usage Breakdown
            tokens_dict = {
                "total_prompt_tokens": self._llm.prompt_tokens,
                "total_completion_tokens": self._llm.completion_tokens,
                "total_cached_tokens": self._llm.cached_tokens,
                "total_tokens": self._llm.total_tokens,
                "by_agent": {
                    name: {
                        "prompt_tokens": a.prompt_tokens,
                        "completion_tokens": a.completion_tokens,
                        "total_tokens": a.total_tokens,
                    }
                    for name, a in self._agents.items()
                },
                "by_model": {
                    m: {
                        "prompt_tokens": data["prompt_tokens"],
                        "completion_tokens": data["completion_tokens"],
                        "cached_tokens": data["cached_tokens"],
                        "total_tokens": data["total_tokens"],
                    }
                    for m, data in self._llm.models_used.items()
                },
            }

            # 5. Failure Rates
            total_tool_calls = sum(t.call_count for t in self._tools.values())
            total_tool_errors = sum(t.error_count for t in self._tools.values())
            tool_failure_rate = round(total_tool_errors / total_tool_calls, 4) if total_tool_calls > 0 else 0.0

            eff_tasks = max(self._tasks_total, len(self._retried_task_ids) + self._tasks_passed)
            task_failure_rate = round(self._tasks_failed / eff_tasks, 4) if eff_tasks > 0 else 0.0

            verif_failure_rate = (
                round(self._verification_stages_failed / self._verification_stages_total, 4)
                if self._verification_stages_total > 0 else 0.0
            )

            failure_rates = {
                "task_failure_rate": task_failure_rate,
                "tool_failure_rate": tool_failure_rate,
                "verification_failure_rate": verif_failure_rate,
                "total_tool_calls": total_tool_calls,
                "total_tool_errors": total_tool_errors,
            }

            # 6. Retry Metrics
            retry_rate = round(len(self._retried_task_ids) / max(1, eff_tasks), 4)
            retry_metrics = {
                "total_retries": self._task_retries,
                "retried_tasks_count": len(self._retried_task_ids),
                "retry_rate": retry_rate,
                "replan_iterations": self._replan_iterations,
            }

            # 7. Retrieval Metrics
            retrieval_metrics = dict(self._retrieval_counts)

            # 8. Files Accessed
            file_access_dict = self._file_access.to_dict()

            # 9. Model Distribution
            model_dist = dict(self._llm.models_used)

            # 10. Cost Attribution
            total_tool_cost = sum(t.cost_usd for t in self._tools.values())
            total_cost = round(self._llm.cost_usd + total_tool_cost, 6)

            cost_breakdown = {
                "total_cost_usd": total_cost,
                "llm_inference_cost_usd": round(self._llm.cost_usd, 6),
                "tool_compute_cost_usd": round(total_tool_cost, 6),
                "cost_by_agent": {name: round(a.cost_usd, 6) for name, a in self._agents.items()},
                "cost_by_model": {m: round(data["cost_usd"], 6) for m, data in self._llm.models_used.items()},
                "productive_cost_usd": round(self._productive_cost_usd, 6),
                "wasted_cost_usd": round(self._wasted_cost_usd, 6),
            }

            return TelemetrySnapshot(
                session_id=self.session_id,
                agent_latencies=agent_dict,
                llm_latency=llm_dict.get("latency", {}),
                tool_latencies=tool_dict,
                token_usage=tokens_dict,
                failure_rates=failure_rates,
                retry_metrics=retry_metrics,
                retrieval_metrics=retrieval_metrics,
                file_access=file_access_dict,
                model_distribution=model_dist,
                cost_breakdown=cost_breakdown,
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convenience method returning serialized snapshot dictionary."""
        return self.get_snapshot().to_dict()
