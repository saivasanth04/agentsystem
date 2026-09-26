"""
DynamicAgent: Generic manifest-driven agent executor.
Scopes tools strictly to the manifest's declared tools, applies custom prompt templates,
and dynamically injects JIT skills and model constraints.
"""
import json
from typing import Any, Dict, List, Optional, Union
from .base import BaseAgent
from ..llm import LLMClient
from ..state import OrchestratorState
from ..tools.workspace import WorkspaceManager
from ..tools.mcp_client import MCPClientAdapter
from ..registry.skill_registry import SkillManager


class DynamicAgent(BaseAgent):
    """
    A dynamically instantiated agent whose behavior, scoped tools, skills,
    and prompt guidelines are driven by an AgentManifest.
    """

    def __init__(
        self,
        name: str,
        role_description: str,
        capabilities: Optional[List[str]] = None,
        tools: Optional[List[str]] = None,
        skills: Optional[List[str]] = None,
        model_requirements: Optional[Dict[str, Any]] = None,
        system_prompt_template: Optional[str] = None,
        model: Optional[str] = None,
        llm: Optional[LLMClient] = None,
        workspace: Optional[WorkspaceManager] = None,
        tool_registry: Optional[Any] = None,
        skill_registry: Optional[SkillManager] = None,
        mcp_client: Optional[MCPClientAdapter] = None,
        message_bus: Optional[Any] = None,
        approval_gate: Optional[Any] = None,
        **kwargs: Any,
    ):
        reqs = model_requirements or {}
        effective_model = model or reqs.get("preferred_model") or reqs.get("model")
        super().__init__(
            name=name,
            role_description=role_description,
            model=effective_model,
            llm=llm,
            workspace=workspace,
            tool_registry=tool_registry,
            skill_registry=skill_registry,
            mcp_client=mcp_client,
            message_bus=message_bus,
            approval_gate=approval_gate,
            **kwargs,
        )
        self.capabilities = capabilities or []
        self.declared_tools = tools or []
        self.declared_skills = skills or []
        self.model_requirements = reqs
        self.system_prompt_template = system_prompt_template

    def _resolve_scoped_tools(self) -> List[str]:
        """
        Filters available tools down to the declared tools in the manifest,
        delegating directly to ToolPolicyEngine to enforce the strict EXECUTABLE lifecycle invariant.
        """
        from runtime.tool_policy import ToolPolicyEngine
        allowed = set(self.declared_tools) if self.declared_tools and "*" not in self.declared_tools else None
        executable_tools = ToolPolicyEngine.get_executable_tools(allowed_tools=allowed)
        tools = [getattr(t, "name", str(t)) for t in executable_tools]
        if "complete_task" not in tools:
            tools.append("complete_task")
        return tools

    def build_system_prompt(self, active_skills: Optional[List[str]] = None) -> str:
        """
        Constructs system prompt incorporating capabilities, tool boundaries,
        and dynamically loaded JIT skills.
        """
        from ..capabilities.model import default_capability_registry

        effective_skills = list(self.declared_skills or [])
        if self.capabilities:
            cap_skills = default_capability_registry.resolve_skills(self.capabilities)
            for cs in cap_skills:
                if cs not in effective_skills:
                    effective_skills.append(cs)

        all_skills = list(set(effective_skills + (active_skills or [])))
        base_prompt = super().build_system_prompt(active_skills=all_skills)

        prompt_additions = [
            f"\nSpecialized Capabilities: {', '.join(self.capabilities) if self.capabilities else 'General software engineering'}",
            f"Allowed Toolset Scope: {', '.join(self.declared_tools) if self.declared_tools else 'Standard tools'}",
        ]

        if self.system_prompt_template:
            prompt_additions.append(f"\nOperational Guidelines:\n{self.system_prompt_template}")

        return base_prompt + "\n" + "\n".join(prompt_additions)

    def execute(self, state: Union[OrchestratorState, Dict[str, Any]], active_skills: Optional[List[str]] = None, **kwargs) -> Dict[str, Any]:
        """
        Executes the dynamic agent using the ReAct loop with scoped tools.
        """
        if isinstance(state, dict) or state is None:
            dict_state = state or {}
            req = dict_state.get("user_request") or dict_state.get("goal") or "Execute dynamic task"
            state_obj = OrchestratorState(user_request=req)
            for k, v in dict_state.items():
                if hasattr(state_obj, k):
                    setattr(state_obj, k, v)
            state = state_obj
        task_info = kwargs.get("task_info") or {}
        if hasattr(self.tool_registry, "set_orchestrator_state"):
            self.tool_registry.set_orchestrator_state(state)
        scoped_tools = self._resolve_scoped_tools()


        user_prompt = f"""
Task Request:
{state.user_request}

Assigned Subtask & Context:
{json.dumps(task_info, indent=2) if task_info else state.user_request}

Instructions:
1. Review the subtask requirements, available context, and files.
2. Use your permitted tools ({', '.join(scoped_tools)}) to accomplish the goal.
3. When you have completed the subtask, call `complete_task` with your structured output and summary.
"""
        system_prompt = self.build_system_prompt(active_skills=active_skills)

        temperature = self.model_requirements.get("temperature", 0.2)
        state_store = kwargs.get("state_store")
        session_id = kwargs.get("session_id")
        task_id = kwargs.get("task_id")
        checkpoint_manager = kwargs.get("checkpoint_manager")
        workspace = kwargs.get("workspace") or self.workspace
        initial_messages = kwargs.get("initial_messages")
        initial_observations = kwargs.get("initial_observations")
        start_turn = kwargs.get("start_turn", 0)
        permissions = kwargs.get("permissions")
        max_turns = kwargs.get("max_turns")
        timeout_seconds = kwargs.get("timeout_seconds")

        effective_model = kwargs.get("model") or self.model
        loop_result = self.execution_loop.run(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=effective_model,
            available_tools=scoped_tools,
            agent_name=self.name,
            temperature=temperature,
            permissions=permissions,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            state_store=state_store,
            session_id=session_id,
            task_id=task_id,
            checkpoint_manager=checkpoint_manager,
            workspace=workspace,
            initial_messages=initial_messages,
            initial_observations=initial_observations,
            start_turn=start_turn,
            approval_gate=kwargs.get("approval_gate") or self.approval_gate,
            task_info=task_info,
            **{k: v for k, v in kwargs.items() if k not in (
                "state_store", "session_id", "task_id", "checkpoint_manager", "workspace",
                "initial_messages", "initial_observations", "start_turn", "approval_gate",
                "permissions", "max_turns", "timeout_seconds", "task_info", "model",
            )},
        )

        final_out = loop_result.get("final_output", {})
        if not isinstance(final_out, dict):
            final_out = {"summary": str(final_out), "status": "COMPLETED"}

        final_out["agent_name"] = self.name
        final_out["scoped_tools"] = scoped_tools
        final_out["capabilities"] = self.capabilities
        final_out["observations"] = loop_result.get("observations", [])
        final_out["token_usage"] = loop_result.get("token_usage")
        final_out["tools_used"] = loop_result.get("tools_used", [])
        final_out["errors"] = loop_result.get("errors", [])
        final_out["history_events"] = loop_result.get("history_events", [])
        final_out["messages"] = loop_result.get("messages", [])
        return final_out
