"""
ReAct Tools for Swarm Primitives & Multi-Agent Coordination.
Equips autonomous agents with dynamic discovery, spawning, delegation,
handoff, blackboard sharing, consensus voting, and termination capabilities.
"""
from typing import Any, Callable, Dict, List, Optional
from .coordinator import SwarmCoordinator
from .lifecycle import ResourceLimits, AgentLifecycleState


class SwarmToolRegistry:
    """
    Exposes OpenAI-compatible tool definitions and dispatcher methods
    for agents to interact directly with SwarmCoordinator.
    """

    def __init__(
        self,
        coordinator: SwarmCoordinator,
        current_agent_id: str = "AGENT",
    ):
        self.coordinator = coordinator
        self.current_agent_id = current_agent_id

    def set_current_agent_id(self, agent_id: str):
        self.current_agent_id = agent_id

    # ------------------------------------------------------------------
    # Tool Methods
    # ------------------------------------------------------------------
    def spawn_subagent(
        self,
        role: str,
        goal: str,
        capabilities: Optional[List[str]] = None,
        max_tokens: int = 50_000,
    ) -> Dict[str, Any]:
        """Dynamically spawns a new subagent under the current agent's hierarchy."""
        limits = ResourceLimits(max_tokens=max_tokens)
        subagent = self.coordinator.spawn_subagent(
            parent_id=self.current_agent_id,
            role=role,
            goal=goal,
            capabilities=capabilities or [],
            resource_limits=limits,
        )
        return {
            "status": "SPAWNED",
            "subagent_id": subagent.instance_id,
            "role": subagent.role,
            "state": subagent.state.value,
        }

    def delegate_subtask(
        self,
        subtask_objective: str,
        target_role: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Delegates a subtask to a specialized peer or dynamically spawned child agent."""
        res = self.coordinator.delegate(
            parent_id=self.current_agent_id,
            subtask_objective=subtask_objective,
            target_role=target_role,
            context=context or {},
        )
        return res.to_dict()

    def handoff_to_agent(
        self,
        target_role: str,
        reason: str,
        hypotheses: Optional[List[str]] = None,
        active_files: Optional[List[str]] = None,
        uncommitted_diffs: Optional[Dict[str, str]] = None,
        stack_trace: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes a stateful handoff transferring hypotheses and files to a target agent."""
        # Check if target agent exists in pool, otherwise discover by role
        target_id = target_role
        candidates = self.coordinator.discover_agents(role=target_role)
        if candidates:
            target_id = candidates[0].instance_id

        record = self.coordinator.handoff(
            from_agent_id=self.current_agent_id,
            to_agent_id=target_id,
            reason=reason,
            working_hypotheses=hypotheses or [],
            active_files=active_files or [],
            uncommitted_diffs=uncommitted_diffs or {},
            stack_trace=stack_trace,
        )
        return record.to_dict()

    def discover_swarm_agents(
        self,
        capability: Optional[str] = None,
        role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Discovers active swarm agents matching optional capability or role."""
        agents = self.coordinator.discover_agents(capability=capability, role=role)
        return {
            "count": len(agents),
            "agents": [a.to_dict() for a in agents],
        }

    def post_to_blackboard(
        self,
        topic: str,
        key: str,
        data: Any,
        confidence: float = 1.0,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Publishes an intermediate observation or finding to the shared swarm blackboard."""
        entry = self.coordinator.post_finding(
            topic=topic,
            key=key,
            data=data,
            author_id=self.current_agent_id,
            confidence=confidence,
            tags=tags,
        )
        return {"status": "POSTED", "entry": entry.to_dict()}

    def read_from_blackboard(
        self,
        topic: str,
        key: str,
    ) -> Dict[str, Any]:
        """Reads a specific observation or finding from the shared swarm blackboard."""
        entry = self.coordinator.read_finding(topic=topic, key=key)
        if not entry:
            return {"status": "NOT_FOUND", "topic": topic, "key": key}
        return {"status": "FOUND", "entry": entry.to_dict()}

    def request_consensus(
        self,
        issue: str,
        options: List[str],
        mechanism: str = "MAJORITY",
    ) -> Dict[str, Any]:
        """Initiates a swarm consensus vote on an issue or design choice."""
        prop = self.coordinator.propose(
            proposer_id=self.current_agent_id,
            issue=issue,
            options=options,
            mechanism=mechanism,
        )
        return {"status": "PROPOSED", "proposal": prop.to_dict()}

    def terminate_subagent(
        self,
        subagent_id: str,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Terminates an active subagent and all its child processes."""
        terminated = self.coordinator.terminate_agent(
            instance_id=subagent_id,
            reason=reason or f"Terminated by parent {self.current_agent_id}",
            cascade=True,
        )
        return {"status": "TERMINATED", "terminated_ids": terminated}

    # ------------------------------------------------------------------
    # Tool Schemas
    # ------------------------------------------------------------------
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Returns OpenAI-compatible function schemas for swarm tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "spawn_subagent",
                    "description": "Dynamically spawn a new specialized subagent with a scoped goal and token limit.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "description": "Specialized role (e.g. 'RESEARCHER', 'TESTER', 'REFACTOR')."},
                            "goal": {"type": "string", "description": "Specific sub-goal for this agent."},
                            "capabilities": {"type": "array", "items": {"type": "string"}, "description": "Required capabilities."},
                            "max_tokens": {"type": "integer", "description": "Token limit quota for this subagent."},
                        },
                        "required": ["role", "goal"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delegate_subtask",
                    "description": "Delegate a subtask to a specialized peer or dynamically spawned child agent.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "subtask_objective": {"type": "string", "description": "Objective of the subtask to execute."},
                            "target_role": {"type": "string", "description": "Desired role of the worker agent."},
                            "context": {"type": "object", "description": "Input data and context parameters."},
                        },
                        "required": ["subtask_objective"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "handoff_to_agent",
                    "description": "Execute a stateful handoff to another agent, preserving hypotheses, active files, and diffs.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "target_role": {"type": "string", "description": "Role or instance ID of the recipient agent."},
                            "reason": {"type": "string", "description": "Why handoff is being initiated."},
                            "hypotheses": {"type": "array", "items": {"type": "string"}, "description": "Current working hypotheses."},
                            "active_files": {"type": "array", "items": {"type": "string"}, "description": "Files currently being modified or inspected."},
                        },
                        "required": ["target_role", "reason"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "discover_swarm_agents",
                    "description": "Discover active swarm agents by capability or role.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "capability": {"type": "string", "description": "Optional capability filter (e.g. 'pytest', 'ast')."},
                            "role": {"type": "string", "description": "Optional role filter (e.g. 'CODER')."},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "post_to_blackboard",
                    "description": "Publish an intermediate observation, finding, or deliverable to the shared blackboard.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string", "description": "Topic category (e.g. 'architecture', 'findings')."},
                            "key": {"type": "string", "description": "Lookup key."},
                            "data": {"description": "Data payload or summary."},
                            "confidence": {"type": "number", "description": "Confidence score from 0.0 to 1.0."},
                        },
                        "required": ["topic", "key", "data"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_from_blackboard",
                    "description": "Read a specific observation or finding from the shared blackboard.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string", "description": "Topic category."},
                            "key": {"type": "string", "description": "Lookup key."},
                        },
                        "required": ["topic", "key"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "request_consensus",
                    "description": "Initiate a swarm consensus vote on an issue or design decision.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "issue": {"type": "string", "description": "Description of the decision to make."},
                            "options": {"type": "array", "items": {"type": "string"}, "description": "List of options to vote on."},
                            "mechanism": {"type": "string", "enum": ["MAJORITY", "CONFIDENCE_WEIGHTED", "UNANIMOUS"], "description": "Voting mechanism."},
                        },
                        "required": ["issue", "options"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "terminate_subagent",
                    "description": "Terminate an active subagent and all its child processes.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "subagent_id": {"type": "string", "description": "Instance ID of the subagent to terminate."},
                            "reason": {"type": "string", "description": "Reason for termination."},
                        },
                        "required": ["subagent_id"],
                    },
                },
            },
        ]
