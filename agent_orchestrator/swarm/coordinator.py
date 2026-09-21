"""
SwarmCoordinator: Central Hub for Swarm Primitives & Multi-Agent Coordination.
Integrates Lifecycle Management, Shared Blackboard, Dynamic Delegation,
Consensus & Conflict Resolution, and Supervisor Watchdogs into a unified runtime.
"""
from datetime import datetime
import threading
from typing import Any, Callable, Dict, List, Optional, Union

from .lifecycle import SwarmPool, SwarmAgentInstance, AgentLifecycleState, ResourceLimits
from .blackboard import SwarmBlackboard, BlackboardEntry
from .delegation import DelegationManager, DelegationResult, HandoffRecord
from .consensus import SwarmConsensusEngine, VotingMechanism, Proposal, ConflictRecord
from .watchdog import SupervisorWatchdog, WatchdogPolicy, HealthStatus, AgentHealthReport


class SwarmCoordinator:
    """
    Unified coordinator exposing all 9 Swarm Primitives to agents and orchestrator:
    1. Discovery (SwarmPool.discover_active)
    2. Spawning (SwarmPool.spawn_subagent)
    3. Delegation (DelegationManager.delegate_subtask)
    4. Handoff (DelegationManager.handoff_to_agent)
    5. Collaboration (SwarmBlackboard + MessageBus)
    6. Result Sharing (SwarmBlackboard.post / read)
    7. Conflict Resolution (SwarmConsensusEngine.resolve_conflict)
    8. Supervision (SupervisorWatchdog.record_action / intervene)
    9. Termination (SwarmPool.terminate_agent with cascading cleanup)
    """

    def __init__(
        self,
        on_event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        agent_registry: Optional[Any] = None,
        message_bus: Optional[Any] = None,
        memory_engine: Optional[Any] = None,
        watchdog_policy: Optional[WatchdogPolicy] = None,
    ):
        self.on_event = on_event_callback or (lambda stage, msg, payload=None: None)
        self.agent_registry = agent_registry
        self.message_bus = message_bus
        self.memory_engine = memory_engine

        # Core Swarm Subsystems
        self.pool = SwarmPool(on_event_callback=self.on_event)
        self.blackboard = SwarmBlackboard(on_event_callback=self.on_event)
        self.delegation = DelegationManager(swarm_pool=self.pool, on_event_callback=self.on_event)
        self.consensus = SwarmConsensusEngine(on_event_callback=self.on_event)
        self.watchdog = SupervisorWatchdog(
            swarm_pool=self.pool,
            policy=watchdog_policy or WatchdogPolicy(),
            on_event_callback=self.on_event,
        )

    # ------------------------------------------------------------------
    # 1 & 2. Discovery & Spawning
    # ------------------------------------------------------------------
    def spawn_subagent(
        self,
        parent_id: Optional[str],
        role: str,
        goal: str,
        capabilities: Optional[List[str]] = None,
        tools: Optional[List[str]] = None,
        resource_limits: Optional[ResourceLimits] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SwarmAgentInstance:
        """Dynamically spawns a new subagent in the pool."""
        return self.pool.spawn_subagent(
            parent_id=parent_id,
            role=role,
            goal=goal,
            capabilities=capabilities,
            tools=tools,
            resource_limits=resource_limits,
            metadata=metadata,
        )

    def discover_agents(
        self,
        capability: Optional[str] = None,
        role: Optional[str] = None,
        state: Optional[AgentLifecycleState] = None,
    ) -> List[SwarmAgentInstance]:
        """Discovers currently active swarm agents."""
        return self.pool.discover_active(capability=capability, role=role, state=state)

    # ------------------------------------------------------------------
    # 3 & 4. Delegation & Stateful Handoff
    # ------------------------------------------------------------------
    def delegate(
        self,
        parent_id: str,
        subtask_objective: str,
        required_capabilities: Optional[List[str]] = None,
        target_role: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        timeout_seconds: float = 120.0,
        executor_fn: Optional[Callable[[SwarmAgentInstance, Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> DelegationResult:
        """Delegates a subtask to a specialized child agent."""
        return self.delegation.delegate_subtask(
            parent_id=parent_id,
            subtask_objective=subtask_objective,
            required_capabilities=required_capabilities,
            target_role=target_role,
            context=context,
            timeout_seconds=timeout_seconds,
            executor_fn=executor_fn,
        )

    def handoff(
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
        """Executes a stateful handoff between two agents."""
        return self.delegation.handoff_to_agent(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            reason=reason,
            working_hypotheses=working_hypotheses,
            active_files=active_files,
            uncommitted_diffs=uncommitted_diffs,
            stack_trace=stack_trace,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # 5 & 6. Result Sharing & Blackboard Collaboration
    # ------------------------------------------------------------------
    def post_finding(
        self,
        topic: str,
        key: str,
        data: Any,
        author_id: str,
        confidence: float = 1.0,
        tags: Optional[List[str]] = None,
    ) -> BlackboardEntry:
        """Publishes an intermediate finding or observation to the shared blackboard."""
        entry = self.blackboard.post(
            topic=topic,
            key=key,
            data=data,
            author_id=author_id,
            confidence=confidence,
            tags=tags,
        )
        # Optional dual-write to message_bus if available
        if self.message_bus and hasattr(self.message_bus, "publish"):
            try:
                from ..runtime.messaging import StructuredMessage, MessageType
                self.message_bus.publish(
                    topic=topic,
                    message=StructuredMessage(
                        sender=author_id,
                        recipient="*",
                        message_type=MessageType.FINDING,
                        content=f"[{key}] {str(data)[:100]}",
                        topic=topic,
                        payload={"key": key, "data": data, "confidence": confidence, "tags": tags or []},
                    ),
                )
            except Exception:
                pass
        return entry

    def read_finding(self, topic: str, key: str) -> Optional[BlackboardEntry]:
        return self.blackboard.read(topic=topic, key=key)

    def query_findings(
        self,
        topic: Optional[str] = None,
        tags: Optional[List[str]] = None,
        min_confidence: float = 0.0,
    ) -> List[BlackboardEntry]:
        return self.blackboard.query(topic=topic, tags=tags, min_confidence=min_confidence)

    def reserve_resource(self, resource_path: str, agent_id: str, lease_seconds: float = 60.0) -> bool:
        return self.blackboard.reserve_resource(resource_path, agent_id, lease_seconds)

    def release_resource(self, resource_path: str, agent_id: str) -> bool:
        return self.blackboard.release_resource(resource_path, agent_id)

    # ------------------------------------------------------------------
    # 7. Consensus & Conflict Resolution
    # ------------------------------------------------------------------
    def propose(
        self,
        proposer_id: str,
        issue: str,
        options: List[str],
        mechanism: Union[VotingMechanism, str] = VotingMechanism.MAJORITY,
        quorum: int = 2,
    ) -> Proposal:
        return self.consensus.create_proposal(proposer_id, issue, options, mechanism, quorum)

    def vote(
        self,
        proposal_id: str,
        voter_id: str,
        option: str,
        confidence: float = 1.0,
        rationale: str = "",
    ) -> bool:
        return self.consensus.cast_vote(proposal_id, voter_id, option, confidence, rationale)

    def tally(self, proposal_id: str) -> Dict[str, Any]:
        return self.consensus.tally_votes(proposal_id)

    def resolve_conflict(
        self,
        agent_a_id: str,
        agent_b_id: str,
        issue: str,
        agent_a_position: str,
        agent_b_position: str,
        arbiter_agent_id: str = "ARCHITECTURE",
        arbiter_decision: Optional[str] = None,
    ) -> ConflictRecord:
        return self.consensus.resolve_conflict(
            agent_a_id=agent_a_id,
            agent_b_id=agent_b_id,
            issue=issue,
            agent_a_position=agent_a_position,
            agent_b_position=agent_b_position,
            arbiter_agent_id=arbiter_agent_id,
            arbiter_decision=arbiter_decision,
        )

    # ------------------------------------------------------------------
    # 8 & 9. Supervision & Termination
    # ------------------------------------------------------------------
    def heartbeat(self, agent_id: str, status_info: Optional[Dict[str, Any]] = None):
        self.watchdog.heartbeat(agent_id, status_info)

    def record_action(
        self,
        agent_id: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> HealthStatus:
        return self.watchdog.record_action(agent_id, tool_name, arguments, error)

    def check_health(self, agent_id: str) -> AgentHealthReport:
        return self.watchdog.check_health(agent_id)

    def intervene(self, agent_id: str, action: str = "WARN") -> Dict[str, Any]:
        return self.watchdog.intervene(agent_id, action)

    def terminate_agent(self, instance_id: str, reason: Optional[str] = None, cascade: bool = True) -> List[str]:
        return self.pool.terminate_agent(instance_id, reason=reason, cascade=cascade)

    def get_status(self) -> Dict[str, Any]:
        """Provides an aggregated summary of the swarm state."""
        return {
            "total_agents": len(self.pool.list_all()),
            "active_agents": len(self.pool.list_active()),
            "active_locks": len(self.blackboard.get_active_locks()),
            "hierarchy": self.pool.get_hierarchy(),
        }
