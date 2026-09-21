"""
Agent Lifecycle Management & Swarm Agent Pool.
Tracks agent instances, state transitions, resource quotas, parent-child hierarchies,
and cascading termination across autonomous swarm agents.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid


class AgentLifecycleState(str, Enum):
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"
    FAILED = "FAILED"
    KILLED = "KILLED"


@dataclass
class ResourceLimits:
    """Resource constraints for an individual swarm agent instance."""
    max_tokens: int = 100_000
    max_time_seconds: float = 300.0
    max_tool_calls: int = 50
    max_memory_mb: float = 512.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "max_time_seconds": self.max_time_seconds,
            "max_tool_calls": self.max_tool_calls,
            "max_memory_mb": self.max_memory_mb,
        }


@dataclass
class SwarmAgentInstance:
    """
    Stateful runtime encapsulation of an active swarm agent.
    Maintains identity, execution limits, hierarchy, and health indicators.
    """
    instance_id: str
    role: str
    goal: str
    capabilities: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)
    state: AgentLifecycleState = AgentLifecycleState.INITIALIZING
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    tokens_used: int = 0
    tool_calls_count: int = 0
    start_time: float = field(default_factory=time.time)
    last_active_time: float = field(default_factory=time.time)
    agent_instance: Optional[Any] = None
    working_memory: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_active(self) -> bool:
        return self.state in (
            AgentLifecycleState.INITIALIZING,
            AgentLifecycleState.READY,
            AgentLifecycleState.ACTIVE,
            AgentLifecycleState.WAITING,
        )

    def record_usage(self, tokens: int = 0, tool_call: bool = False):
        self.tokens_used += max(0, tokens)
        if tool_call:
            self.tool_calls_count += 1
        self.last_active_time = time.time()

    def exceeds_limits(self) -> Optional[str]:
        if self.tokens_used > self.resource_limits.max_tokens:
            return f"Token ceiling exceeded: {self.tokens_used} > {self.resource_limits.max_tokens}"
        if self.tool_calls_count > self.resource_limits.max_tool_calls:
            return f"Tool calls limit exceeded: {self.tool_calls_count} > {self.resource_limits.max_tool_calls}"
        elapsed = time.time() - self.start_time
        if elapsed > self.resource_limits.max_time_seconds:
            return f"Execution timeout: {elapsed:.1f}s > {self.resource_limits.max_time_seconds}s"
        return None

    def transition(self, new_state: AgentLifecycleState, reason: Optional[str] = None):
        self.state = new_state
        self.last_active_time = time.time()
        if reason:
            self.metadata.setdefault("state_transitions", []).append({
                "from": self.state.value if hasattr(self.state, "value") else str(self.state),
                "to": new_state.value if hasattr(new_state, "value") else str(new_state),
                "reason": reason,
                "timestamp": datetime.now().isoformat(),
            })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "role": self.role,
            "goal": self.goal,
            "capabilities": list(self.capabilities),
            "tools": list(self.tools),
            "parent_id": self.parent_id,
            "children_ids": list(self.children_ids),
            "state": self.state.value if hasattr(self.state, "value") else str(self.state),
            "resource_limits": self.resource_limits.to_dict(),
            "tokens_used": self.tokens_used,
            "tool_calls_count": self.tool_calls_count,
            "start_time": self.start_time,
            "last_active_time": self.last_active_time,
            "metadata": self.metadata,
        }


class SwarmPool:
    """
    Thread-safe Registry and Lifecycle Manager for all active swarm agents.
    Supports spawning, dynamic discovery, hierarchical tracking, and cascading termination.
    """

    def __init__(
        self,
        on_event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    ):
        self._lock = threading.RLock()
        self._instances: Dict[str, SwarmAgentInstance] = {}
        self.on_event = on_event_callback or (lambda stage, msg, payload=None: None)

    def register(self, instance: SwarmAgentInstance) -> SwarmAgentInstance:
        """Registers an agent instance into the swarm pool."""
        with self._lock:
            self._instances[instance.instance_id] = instance
            if instance.parent_id and instance.parent_id in self._instances:
                parent = self._instances[instance.parent_id]
                if instance.instance_id not in parent.children_ids:
                    parent.children_ids.append(instance.instance_id)

        self.on_event(
            "SWARM_POOL",
            f"Registered swarm agent [{instance.instance_id}] (role={instance.role}, state={instance.state.value})",
            instance.to_dict(),
        )
        return instance

    def spawn_subagent(
        self,
        parent_id: Optional[str],
        role: str,
        goal: str,
        capabilities: Optional[List[str]] = None,
        tools: Optional[List[str]] = None,
        resource_limits: Optional[ResourceLimits] = None,
        agent_instance: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SwarmAgentInstance:
        """
        Dynamically spawns a new subagent under the swarm hierarchy.
        """
        instance_id = f"swarm-{role.lower()}-{uuid.uuid4().hex[:6]}"
        limits = resource_limits or ResourceLimits()
        instance = SwarmAgentInstance(
            instance_id=instance_id,
            role=role,
            goal=goal,
            capabilities=capabilities or [],
            tools=tools or [],
            parent_id=parent_id,
            state=AgentLifecycleState.READY,
            resource_limits=limits,
            agent_instance=agent_instance,
            metadata=metadata or {},
        )
        return self.register(instance)

    def get(self, instance_id: str) -> Optional[SwarmAgentInstance]:
        with self._lock:
            return self._instances.get(instance_id)

    def discover_active(
        self,
        capability: Optional[str] = None,
        role: Optional[str] = None,
        state: Optional[AgentLifecycleState] = None,
    ) -> List[SwarmAgentInstance]:
        """
        Discovers active agents matching optional capability, role, or state filters.
        """
        with self._lock:
            results = []
            for inst in self._instances.values():
                if state is not None and inst.state != state:
                    continue
                if state is None and not inst.is_active():
                    continue
                if role is not None and inst.role.upper() != role.upper():
                    continue
                if capability is not None:
                    cap_clean = capability.lower().strip()
                    if not any(cap_clean in c.lower() for c in inst.capabilities):
                        continue
                results.append(inst)
            return results

    def transition_state(
        self,
        instance_id: str,
        new_state: AgentLifecycleState,
        reason: Optional[str] = None,
    ) -> bool:
        with self._lock:
            inst = self._instances.get(instance_id)
            if not inst:
                return False
            inst.transition(new_state, reason=reason)

        self.on_event(
            "SWARM_LIFECYCLE",
            f"Agent [{instance_id}] transitioned to [{new_state.value}]: {reason or 'No reason specified'}",
            {"instance_id": instance_id, "new_state": new_state.value, "reason": reason},
        )
        return True

    def terminate_agent(
        self,
        instance_id: str,
        reason: Optional[str] = None,
        cascade: bool = True,
    ) -> List[str]:
        """
        Terminates an agent and optionally cascades termination to all child subagents.
        Returns the list of terminated agent IDs.
        """
        terminated_ids: List[str] = []
        with self._lock:
            inst = self._instances.get(instance_id)
            if not inst:
                return terminated_ids

            children_to_terminate = list(inst.children_ids) if cascade else []
            inst.transition(AgentLifecycleState.TERMINATED, reason=reason or "Manual termination")
            terminated_ids.append(instance_id)

            if cascade:
                for child_id in children_to_terminate:
                    child_terminated = self.terminate_agent(child_id, reason=f"Cascading termination from parent {instance_id}", cascade=True)
                    terminated_ids.extend(child_terminated)

        self.on_event(
            "SWARM_TERMINATION",
            f"Terminated agent [{instance_id}] (cascaded: {len(terminated_ids) - 1} children)",
            {"terminated_ids": terminated_ids, "reason": reason},
        )
        return terminated_ids

    def list_all(self) -> List[SwarmAgentInstance]:
        with self._lock:
            return list(self._instances.values())

    def list_active(self) -> List[SwarmAgentInstance]:
        with self._lock:
            return [inst for inst in self._instances.values() if inst.is_active()]

    def get_hierarchy(self, root_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Builds a hierarchical tree representation of parent and child subagents.
        """
        with self._lock:
            if root_id:
                root = self._instances.get(root_id)
                if not root:
                    return {}
                return self._build_node_tree(root)

            # Find all top-level roots (agents without parent or whose parent is not in pool)
            roots = [
                inst for inst in self._instances.values()
                if not inst.parent_id or inst.parent_id not in self._instances
            ]
            return {
                "roots": [self._build_node_tree(r) for r in roots],
                "total_agents": len(self._instances),
                "active_agents": len([i for i in self._instances.values() if i.is_active()]),
            }

    def _build_node_tree(self, node: SwarmAgentInstance) -> Dict[str, Any]:
        children = []
        for child_id in node.children_ids:
            child = self._instances.get(child_id)
            if child:
                children.append(self._build_node_tree(child))
        data = node.to_dict()
        data["children"] = children
        return data
