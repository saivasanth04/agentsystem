"""
Agent Lifecycle Manager: Central Runtime for Dynamic Agent Creation, Pausing,
Resumption, Termination, Delegation, and Handoffs.
Integrates with AgentRegistry, SwarmCoordinator, and SQLiteStateStore.
"""
from dataclasses import asdict
from datetime import datetime
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Type, Union
import uuid

from .execution_frame import AgentExecutionFrame
from ..agents.base import BaseAgent


class AgentLifecycleManager:
    """
    Manages the lifecycle of stateful and long-lived agents in the orchestrator runtime:
    - Spawns agents on-demand with specialized capabilities and tools
    - Pauses active agents mid-loop, capturing serializable AgentExecutionFrames
    - Resumes paused agents with injected feedback or peer deliverables
    - Gracefully terminates agents with cascading child cleanup
    - Coordinates synchronous and asynchronous delegation and stateful handoffs
    """

    def __init__(
        self,
        agent_registry: Any,
        swarm_coordinator: Optional[Any] = None,
        state_store: Optional[Any] = None,
        llm: Optional[Any] = None,
        workspace: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
        skill_registry: Optional[Any] = None,
        mcp_client: Optional[Any] = None,
        message_bus: Optional[Any] = None,
        approval_gate: Optional[Any] = None,
        on_event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    ):
        self.agent_registry = agent_registry
        self.swarm_coordinator = swarm_coordinator
        self.state_store = state_store
        self.llm = llm
        self.workspace = workspace
        self.tool_registry = tool_registry
        self.skill_registry = skill_registry
        self.mcp_client = mcp_client
        self.message_bus = message_bus
        from .approval_gate import PolicyBasedApprovalGate
        self.approval_gate = approval_gate or PolicyBasedApprovalGate()
        self.on_event = on_event_callback or (lambda stage, msg, payload=None: None)

        self._lock = threading.RLock()
        self._live_agents: Dict[str, BaseAgent] = {}
        self._saved_frames: Dict[str, AgentExecutionFrame] = {}

    def spawn_agent(
        self,
        role: str,
        goal: str,
        capabilities: Optional[List[str]] = None,
        tools: Optional[List[str]] = None,
        parent_id: Optional[str] = None,
        agent_class: Optional[Type[BaseAgent]] = None,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> BaseAgent:
        """
        Dynamically instantiates and registers a new agent instance in the runtime.
        """
        agent_id = kwargs.get("agent_id") or f"agent-{role.lower()}-{uuid.uuid4().hex[:6]}"
        manifest = None
        if hasattr(self.agent_registry, "get"):
            manifest = self.agent_registry.get(role)

        if manifest and hasattr(self.agent_registry, "create_agent_instance"):
            agent = self.agent_registry.create_agent_instance(
                name_or_manifest=manifest,
                llm=self.llm,
                workspace=self.workspace,
                tool_registry=self.tool_registry,
                skill_registry=self.skill_registry,
                mcp_client=self.mcp_client,
                message_bus=self.message_bus,
                approval_gate=self.approval_gate,
                **kwargs,
            )
        elif agent_class:
            agent = agent_class(
                name=role,
                role_description=goal,
                llm=self.llm,
                workspace=self.workspace,
                tool_registry=self.tool_registry,
                skill_registry=self.skill_registry,
                mcp_client=self.mcp_client,
                message_bus=self.message_bus,
                approval_gate=self.approval_gate,
                **kwargs,
            )
        else:
            # Fallback to DynamicAgent or BaseAgent
            try:
                from ..agents.dynamic_agent import DynamicAgent
                agent = DynamicAgent(
                    name=role,
                    role_description=goal,
                    capabilities=capabilities or [],
                    tools=tools or [],
                    system_prompt_template=system_prompt,
                    llm=self.llm,
                    workspace=self.workspace,
                    tool_registry=self.tool_registry,
                    skill_registry=self.skill_registry,
                    mcp_client=self.mcp_client,
                    message_bus=self.message_bus,
                    approval_gate=self.approval_gate,
                    **kwargs,
                )
            except Exception:
                agent = BaseAgent(
                    name=role,
                    role_description=goal,
                    llm=self.llm,
                    workspace=self.workspace,
                    tool_registry=self.tool_registry,
                    skill_registry=self.skill_registry,
                    mcp_client=self.mcp_client,
                    message_bus=self.message_bus,
                    approval_gate=self.approval_gate,
                    **kwargs,
                )

        # Attach lifecycle attributes
        agent.agent_id = agent_id
        agent.parent_id = parent_id
        agent.lifecycle_state = "READY"
        agent.goal = goal
        agent.capabilities = capabilities or getattr(agent, "capabilities", [])
        agent.tools = tools or getattr(agent, "tools", [])

        with self._lock:
            self._live_agents[agent_id] = agent

        # Register in SwarmCoordinator if present
        if self.swarm_coordinator and hasattr(self.swarm_coordinator, "pool"):
            try:
                from ..swarm.lifecycle import SwarmAgentInstance, AgentLifecycleState
                swarm_inst = SwarmAgentInstance(
                    instance_id=agent_id,
                    role=role,
                    goal=goal,
                    capabilities=agent.capabilities,
                    tools=agent.tools,
                    parent_id=parent_id,
                    state=AgentLifecycleState.READY,
                    agent_instance=agent,
                )
                self.swarm_coordinator.pool.register(swarm_inst)
            except Exception:
                pass

        self.on_event(
            "AGENT_SPAWNED",
            f"Spawned dynamic agent [{agent_id}] (role={role}, goal={goal[:60]})",
            {"agent_id": agent_id, "role": role, "parent_id": parent_id},
        )
        return agent

    def pause_agent(self, agent_id: str, reason: Optional[str] = None) -> Optional[AgentExecutionFrame]:
        """
        Pauses an active agent, saving its current execution frame.
        """
        with self._lock:
            agent = self._live_agents.get(agent_id)
            if not agent:
                return None

            frame = None
            if hasattr(agent, "pause"):
                frame = agent.pause(reason=reason)
            elif hasattr(agent, "current_frame") and agent.current_frame:
                frame = agent.current_frame
                frame.pause_reason = reason or "External pause request"
                frame.paused_at = time.time()
            else:
                frame = AgentExecutionFrame(
                    agent_id=agent_id,
                    role=getattr(agent, "name", "AGENT"),
                    pause_reason=reason or "Paused by lifecycle manager",
                    paused_at=time.time(),
                )

            agent.lifecycle_state = "PAUSED"
            self._saved_frames[agent_id] = frame

        # Sync with SwarmCoordinator if present
        if self.swarm_coordinator and hasattr(self.swarm_coordinator, "pool"):
            try:
                from ..swarm.lifecycle import AgentLifecycleState
                self.swarm_coordinator.pool.transition_state(
                    agent_id,
                    AgentLifecycleState.SUSPENDED,
                    reason=reason or "Paused via lifecycle manager",
                )
            except Exception:
                pass

        self.on_event(
            "AGENT_PAUSED",
            f"Agent [{agent_id}] paused: {reason or 'No reason specified'}",
            frame.to_dict() if frame else {},
        )
        return frame

    def resume_agent(
        self,
        agent_id: str,
        input_updates: Optional[str] = None,
        feedback: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Resumes a paused agent using its saved execution frame.
        """
        with self._lock:
            agent = self._live_agents.get(agent_id)
            frame = self._saved_frames.get(agent_id)
            if not agent:
                return {"status": "ERROR", "error": f"Agent {agent_id} not found."}

            agent.lifecycle_state = "RUNNING"
            user_input = input_updates or feedback or ""

            # Sync with SwarmCoordinator if present
            if self.swarm_coordinator and hasattr(self.swarm_coordinator, "pool"):
                try:
                    from ..swarm.lifecycle import AgentLifecycleState
                    self.swarm_coordinator.pool.transition_state(
                        agent_id,
                        AgentLifecycleState.ACTIVE,
                        reason="Resumed via lifecycle manager",
                    )
                except Exception:
                    pass

            self.on_event(
                "AGENT_RESUMED",
                f"Agent [{agent_id}] resumed with feedback: {user_input[:80] if user_input else 'None'}",
                {"agent_id": agent_id, "has_frame": frame is not None},
            )

            if hasattr(agent, "resume"):
                result = agent.resume(frame=frame, user_input=user_input)
                agent.lifecycle_state = "READY"
                return result
            else:
                agent.lifecycle_state = "READY"
                return {"status": "SUCCESS", "agent_id": agent_id, "resumed": True}

    def terminate_agent(
        self,
        agent_id: str,
        reason: Optional[str] = None,
        cascade: bool = True,
    ) -> List[str]:
        """
        Terminates an agent and optionally cascades termination to child agents.
        """
        terminated_ids: List[str] = []
        with self._lock:
            agent = self._live_agents.get(agent_id)
            if not agent:
                return terminated_ids

            agent.lifecycle_state = "TERMINATED"
            terminated_ids.append(agent_id)

            # Find and terminate child agents
            if cascade:
                children = [
                    c_id for c_id, a in self._live_agents.items()
                    if getattr(a, "parent_id", None) == agent_id
                ]
                for child_id in children:
                    c_term = self.terminate_agent(child_id, reason=f"Cascaded from {agent_id}", cascade=True)
                    terminated_ids.extend(c_term)

            # Clean up live reference
            del self._live_agents[agent_id]
            if agent_id in self._saved_frames:
                del self._saved_frames[agent_id]

        # Sync with SwarmCoordinator
        if self.swarm_coordinator and hasattr(self.swarm_coordinator, "pool"):
            try:
                self.swarm_coordinator.pool.terminate_agent(agent_id, reason=reason, cascade=cascade)
            except Exception:
                pass

        self.on_event(
            "AGENT_TERMINATED",
            f"Terminated agent [{agent_id}] (cascaded: {len(terminated_ids) - 1} children)",
            {"terminated_ids": terminated_ids, "reason": reason},
        )
        return terminated_ids

    def delegate(
        self,
        from_agent_id: str,
        to_agent_id: str,
        subtask_objective: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Delegates a subtask from one agent to another.
        """
        self.on_event(
            "AGENT_DELEGATE",
            f"Agent [{from_agent_id}] delegated subtask to [{to_agent_id}]: {subtask_objective[:80]}",
            {"from_agent_id": from_agent_id, "to_agent_id": to_agent_id, "objective": subtask_objective},
        )
        if self.swarm_coordinator:
            res = self.swarm_coordinator.delegate(
                parent_id=from_agent_id,
                subtask_objective=subtask_objective,
                target_role=to_agent_id,
                context=context or {},
            )
            return res.to_dict()

        target = self.get_agent(to_agent_id)
        if target and hasattr(target, "execute"):
            target_res = target.execute(
                state=context or {},
                task_info={"objective": subtask_objective, "context": context or {}},
            )
            return {"status": "SUCCESS", "output": str(target_res), "agent_id": to_agent_id}

        return {
            "status": "DELEGATED",
            "from_agent_id": from_agent_id,
            "to_agent_id": to_agent_id,
            "subtask_objective": subtask_objective,
        }

    def handoff(
        self,
        from_agent_id: str,
        to_agent_id: str,
        reason: str,
        state_transfer: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Executes a stateful handoff transferring conversational context and active files.
        """
        st = state_transfer or {}
        self.on_event(
            "AGENT_HANDOFF",
            f"Agent [{from_agent_id}] handed off to [{to_agent_id}]: {reason}",
            {"from_agent_id": from_agent_id, "to_agent_id": to_agent_id, "reason": reason},
        )
        if self.swarm_coordinator:
            rec = self.swarm_coordinator.handoff(
                from_agent_id=from_agent_id,
                to_agent_id=to_agent_id,
                reason=reason,
                working_hypotheses=st.get("hypotheses", []),
                active_files=st.get("active_files", []),
                uncommitted_diffs=st.get("diffs", {}),
                stack_trace=st.get("stack_trace"),
            )
            d = rec.to_dict()
            d["status"] = "HANDOFF_COMPLETED"
            return d

        return {
            "status": "HANDOFF_COMPLETED",
            "from_agent_id": from_agent_id,
            "to_agent_id": to_agent_id,
            "reason": reason,
            "state_transfer": st,
        }

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        with self._lock:
            return self._live_agents.get(agent_id)

    def get_frame(self, agent_id: str) -> Optional[AgentExecutionFrame]:
        with self._lock:
            return self._saved_frames.get(agent_id)

    def list_active_agents(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "agent_id": a_id,
                    "name": getattr(a, "name", "AGENT"),
                    "role_description": getattr(a, "role_description", ""),
                    "lifecycle_state": getattr(a, "lifecycle_state", "READY"),
                    "parent_id": getattr(a, "parent_id", None),
                }
                for a_id, a in self._live_agents.items()
            ]
