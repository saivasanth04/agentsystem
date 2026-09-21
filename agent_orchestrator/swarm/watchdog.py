"""
Supervisor Watchdog & Swarm Health Sentinel.
Monitors heartbeats, detects reasoning/tool call loops, prevents token runaway,
and triggers circuit breaker interventions (warnings, suspensions, or cascading terminations).
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .lifecycle import SwarmPool, AgentLifecycleState


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    LOOPING = "LOOPING"
    RUNAWAY = "RUNAWAY"
    UNRESPONSIVE = "UNRESPONSIVE"
    CRITICAL = "CRITICAL"


@dataclass
class WatchdogPolicy:
    """Configurable thresholds for supervisor watchdog monitoring."""
    heartbeat_timeout_seconds: float = 60.0
    max_consecutive_errors: int = 5
    max_token_ceiling: int = 150_000
    loop_repetition_threshold: int = 3  # Identical tool + args consecutively


@dataclass
class AgentHealthReport:
    agent_id: str
    status: HealthStatus
    last_heartbeat: float
    consecutive_errors: int = 0
    recent_action_hashes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    circuit_broken: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "last_heartbeat": self.last_heartbeat,
            "consecutive_errors": self.consecutive_errors,
            "warnings": list(self.warnings),
            "circuit_broken": self.circuit_broken,
        }


class SupervisorWatchdog:
    """
    Supervises swarm agents, detecting infinite loops, reasoning degradation,
    and token/execution runaway.
    """

    def __init__(
        self,
        swarm_pool: SwarmPool,
        policy: Optional[WatchdogPolicy] = None,
        on_event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    ):
        self.swarm_pool = swarm_pool
        self.policy = policy or WatchdogPolicy()
        self._lock = threading.RLock()
        self._health_reports: Dict[str, AgentHealthReport] = {}
        self.on_event = on_event_callback or (lambda stage, msg, payload=None: None)

    def heartbeat(self, agent_id: str, status_info: Optional[Dict[str, Any]] = None):
        """Records an alive heartbeat from an agent."""
        with self._lock:
            report = self._get_or_create_report(agent_id)
            report.last_heartbeat = time.time()
            if report.status == HealthStatus.UNRESPONSIVE:
                report.status = HealthStatus.HEALTHY

    def record_action(
        self,
        agent_id: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> HealthStatus:
        """
        Records an agent action (tool invocation or step) to detect loops or consecutive errors.
        """
        arg_str = json.dumps(arguments or {}, sort_keys=True)
        action_hash = hashlib.md5(f"{tool_name}:{arg_str}".encode("utf-8")).hexdigest()

        with self._lock:
            report = self._get_or_create_report(agent_id)
            report.last_heartbeat = time.time()

            # 1. Error tracking
            if error:
                report.consecutive_errors += 1
                if report.consecutive_errors >= self.policy.max_consecutive_errors:
                    report.status = HealthStatus.CRITICAL
                    report.warnings.append(f"Exceeded max consecutive errors ({report.consecutive_errors})")
                else:
                    report.status = HealthStatus.DEGRADED
            else:
                report.consecutive_errors = 0

            # 2. Loop detection (identical actions repeated)
            report.recent_action_hashes.append(action_hash)
            if len(report.recent_action_hashes) > 10:
                report.recent_action_hashes = report.recent_action_hashes[-10:]

            # Check if last N actions are identical
            n = self.policy.loop_repetition_threshold
            if len(report.recent_action_hashes) >= n:
                recent_window = report.recent_action_hashes[-n:]
                if len(set(recent_window)) == 1:
                    if report.status != HealthStatus.CRITICAL:
                        report.status = HealthStatus.LOOPING
                    msg = f"Loop detected: tool [{tool_name}] called {n} times with identical arguments"
                    if msg not in report.warnings:
                        report.warnings.append(msg)

            # 3. Check agent pool limits
            agent_inst = self.swarm_pool.get(agent_id)
            if agent_inst:
                limit_err = agent_inst.exceeds_limits()
                if limit_err:
                    report.status = HealthStatus.RUNAWAY
                    if limit_err not in report.warnings:
                        report.warnings.append(limit_err)

            return report.status

    def check_health(self, agent_id: str) -> AgentHealthReport:
        """Evaluates and returns the current health status of an agent."""
        with self._lock:
            report = self._get_or_create_report(agent_id)
            elapsed_hb = time.time() - report.last_heartbeat

            if elapsed_hb > self.policy.heartbeat_timeout_seconds:
                report.status = HealthStatus.UNRESPONSIVE
                warn = f"Heartbeat timed out: {elapsed_hb:.1f}s > {self.policy.heartbeat_timeout_seconds}s"
                if warn not in report.warnings:
                    report.warnings.append(warn)

            return report

    def intervene(
        self,
        agent_id: str,
        action: str = "WARN",
    ) -> Dict[str, Any]:
        """
        Executes a supervisor intervention on an agent:
        - "WARN": Records a warning and alerts the agent
        - "PAUSE": Transitions agent to SUSPENDED
        - "TERMINATE": Kills agent and cascades to child subagents
        """
        act_clean = action.upper().strip()
        with self._lock:
            report = self._get_or_create_report(agent_id)

            if act_clean == "WARN":
                self.on_event("SUPERVISOR_WARN", f"Supervisor issued warning to [{agent_id}]", report.to_dict())
                return {"action": "WARN", "agent_id": agent_id, "status": report.status.value}

            elif act_clean == "PAUSE":
                self.swarm_pool.transition_state(
                    agent_id,
                    AgentLifecycleState.SUSPENDED,
                    reason="Supervisor circuit breaker: PAUSE",
                )
                report.circuit_broken = True
                self.on_event("SUPERVISOR_PAUSE", f"Supervisor suspended agent [{agent_id}]", report.to_dict())
                return {"action": "PAUSE", "agent_id": agent_id, "status": "SUSPENDED"}

            elif act_clean == "TERMINATE":
                terminated = self.swarm_pool.terminate_agent(
                    agent_id,
                    reason="Supervisor circuit breaker: TERMINATE (loop/runaway)",
                    cascade=True,
                )
                report.circuit_broken = True
                self.on_event("SUPERVISOR_TERMINATE", f"Supervisor terminated [{agent_id}] and {len(terminated)-1} subagents", {"terminated": terminated})
                return {"action": "TERMINATE", "agent_id": agent_id, "terminated_ids": terminated}

            return {"action": "UNKNOWN", "agent_id": agent_id}

    def _get_or_create_report(self, agent_id: str) -> AgentHealthReport:
        if agent_id not in self._health_reports:
            self._health_reports[agent_id] = AgentHealthReport(
                agent_id=agent_id,
                status=HealthStatus.HEALTHY,
                last_heartbeat=time.time(),
            )
        return self._health_reports[agent_id]
