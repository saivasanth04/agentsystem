"""
Dynamic Delegation & Stateful Handoff Protocols.
Enables agents to delegate subtasks to specialized peers/subagents, track outcomes,
and execute stateful conversational handoffs preserving hypotheses, active files, and diffs.
"""
from dataclasses import dataclass, field
from datetime import datetime
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

from .lifecycle import SwarmPool, SwarmAgentInstance, AgentLifecycleState


@dataclass
class DelegationRequest:
    """Specification for a dynamically delegated subtask."""
    delegation_id: str
    parent_id: str
    subtask_objective: str
    required_capabilities: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 120.0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delegation_id": self.delegation_id,
            "parent_id": self.parent_id,
            "subtask_objective": self.subtask_objective,
            "required_capabilities": list(self.required_capabilities),
            "context": self.context,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at,
        }


@dataclass
class DelegationResult:
    """Outcome and deliverables from a delegated subtask."""
    delegation_id: str
    child_agent_id: str
    status: str  # "SUCCESS", "FAILED", "TIMED_OUT"
    output: str
    artifacts: List[str] = field(default_factory=list)
    token_usage: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    completed_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delegation_id": self.delegation_id,
            "child_agent_id": self.child_agent_id,
            "status": self.status,
            "output": self.output,
            "artifacts": list(self.artifacts),
            "token_usage": self.token_usage,
            "metadata": self.metadata,
            "completed_at": self.completed_at,
        }


@dataclass
class HandoffRecord:
    """
    Structured handoff transfer record between two agents.
    Carries cognitive state, active files, uncommitted diffs, and hypotheses.
    """
    handoff_id: str
    from_agent_id: str
    to_agent_id: str
    reason: str
    working_hypotheses: List[str] = field(default_factory=list)
    active_files: List[str] = field(default_factory=list)
    uncommitted_diffs: Dict[str, str] = field(default_factory=dict)
    stack_trace: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    accepted: bool = False
    acceptance_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "from_agent_id": self.from_agent_id,
            "to_agent_id": self.to_agent_id,
            "reason": self.reason,
            "working_hypotheses": list(self.working_hypotheses),
            "active_files": list(self.active_files),
            "uncommitted_diffs": self.uncommitted_diffs,
            "stack_trace": self.stack_trace,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "accepted": self.accepted,
            "acceptance_reason": self.acceptance_reason,
        }


class DelegationManager:
    """
    Coordinates task delegation and stateful peer handoffs across swarm agents.
    Integrates with SwarmPool for subagent lifecycle and agent discovery.
    """

    def __init__(
        self,
        swarm_pool: SwarmPool,
        on_event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    ):
        self.swarm_pool = swarm_pool
        self._lock = threading.RLock()
        self._delegations: Dict[str, DelegationResult] = {}
        self._handoffs: Dict[str, HandoffRecord] = {}
        self.on_event = on_event_callback or (lambda stage, msg, payload=None: None)

    def delegate_subtask(
        self,
        parent_id: str,
        subtask_objective: str,
        required_capabilities: Optional[List[str]] = None,
        target_role: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        timeout_seconds: float = 120.0,
        executor_fn: Optional[Callable[[SwarmAgentInstance, Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> DelegationResult:
        """
        Delegates a subtask to an existing or dynamically spawned child agent.
        Executes the subtask synchronously or via executor_fn, tracks tokens, and records outcome.
        """
        delegation_id = f"del-{uuid.uuid4().hex[:8]}"
        req_caps = required_capabilities or []
        role = target_role or "SPECIALIST"

        # 1. Discover or spawn child agent
        child_agent: Optional[SwarmAgentInstance] = None
        candidates = self.swarm_pool.discover_active(role=role, state=AgentLifecycleState.READY)
        if candidates:
            child_agent = candidates[0]
            self.swarm_pool.transition_state(
                child_agent.instance_id,
                AgentLifecycleState.ACTIVE,
                reason=f"Assigned delegated subtask {delegation_id}",
            )
        else:
            child_agent = self.swarm_pool.spawn_subagent(
                parent_id=parent_id,
                role=role,
                goal=subtask_objective,
                capabilities=req_caps,
            )
            self.swarm_pool.transition_state(
                child_agent.instance_id,
                AgentLifecycleState.ACTIVE,
                reason=f"Spawned for delegated subtask {delegation_id}",
            )

        self.on_event(
            "SWARM_DELEGATION",
            f"Parent [{parent_id}] delegated to [{child_agent.instance_id}]: {subtask_objective[:80]}",
            {"delegation_id": delegation_id, "child_id": child_agent.instance_id},
        )

        # 2. Execute delegated work
        start_t = time.time()
        output_text = ""
        artifacts: List[str] = []
        tokens = 0
        status = "SUCCESS"

        try:
            if executor_fn:
                res = executor_fn(child_agent, context or {})
                output_text = res.get("output", "")
                artifacts = res.get("artifacts", [])
                tokens = res.get("tokens_used", 0)
                status = res.get("status", "SUCCESS")
            else:
                output_text = f"Delegated task completed by {child_agent.role}: {subtask_objective}"
        except Exception as e:
            status = "FAILED"
            output_text = f"Execution failed: {e}"

        elapsed = time.time() - start_t
        if elapsed > timeout_seconds:
            status = "TIMED_OUT"

        child_agent.record_usage(tokens=tokens, tool_call=True)
        self.swarm_pool.transition_state(
            child_agent.instance_id,
            AgentLifecycleState.READY,
            reason=f"Finished delegation {delegation_id}",
        )

        result = DelegationResult(
            delegation_id=delegation_id,
            child_agent_id=child_agent.instance_id,
            status=status,
            output=output_text,
            artifacts=artifacts,
            token_usage=tokens,
            metadata={"elapsed_seconds": elapsed, "parent_id": parent_id},
        )

        with self._lock:
            self._delegations[delegation_id] = result

        return result

    def handoff_to_agent(
        self,
        from_agent_id: str,
        to_agent_id: str,
        reason: str,
        working_hypotheses: Optional[List[str]] = None,
        active_files: Optional[List[str]] = None,
        uncommitted_diffs: Optional[Dict[str, str]] = None,
        stack_trace: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> HandoffRecord:
        """
        Initiates a stateful handoff between two agents.
        Transfers execution hypotheses, file references, diffs, and context.
        """
        handoff_id = f"ho-{uuid.uuid4().hex[:8]}"
        record = HandoffRecord(
            handoff_id=handoff_id,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            reason=reason,
            working_hypotheses=working_hypotheses or [],
            active_files=active_files or [],
            uncommitted_diffs=uncommitted_diffs or {},
            stack_trace=stack_trace,
            metadata=metadata or {},
        )

        with self._lock:
            self._handoffs[handoff_id] = record

        # Automatically acknowledge if target agent exists and is ready
        target = self.swarm_pool.get(to_agent_id)
        if target and target.is_active():
            record.accepted = True
            record.acceptance_reason = f"Agent {to_agent_id} accepted handoff for reason: {reason}"
            self.swarm_pool.transition_state(
                to_agent_id,
                AgentLifecycleState.ACTIVE,
                reason=f"Accepted handoff {handoff_id} from {from_agent_id}",
            )

        self.on_event(
            "SWARM_HANDOFF",
            f"Handoff [{handoff_id}] from [{from_agent_id}] to [{to_agent_id}]: {reason}",
            record.to_dict(),
        )
        return record

    def accept_handoff(self, handoff_id: str, agent_id: str, reason: Optional[str] = None) -> bool:
        """Explicitly accepts a handoff by the target agent."""
        with self._lock:
            record = self._handoffs.get(handoff_id)
            if not record or record.to_agent_id != agent_id:
                return False
            record.accepted = True
            record.acceptance_reason = reason or f"Explicitly accepted by {agent_id}"
            return True

    def get_delegation(self, delegation_id: str) -> Optional[DelegationResult]:
        with self._lock:
            return self._delegations.get(delegation_id)

    def get_handoff(self, handoff_id: str) -> Optional[HandoffRecord]:
        with self._lock:
            return self._handoffs.get(handoff_id)

    def list_delegations(self, parent_id: Optional[str] = None) -> List[DelegationResult]:
        with self._lock:
            results = list(self._delegations.values())
            if parent_id:
                results = [d for d in results if d.metadata.get("parent_id") == parent_id]
            return results

    def list_handoffs(self, from_agent: Optional[str] = None, to_agent: Optional[str] = None) -> List[HandoffRecord]:
        with self._lock:
            results = list(self._handoffs.values())
            if from_agent:
                results = [h for h in results if h.from_agent_id == from_agent]
            if to_agent:
                results = [h for h in results if h.to_agent_id == to_agent]
            return results
