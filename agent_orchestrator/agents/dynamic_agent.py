"""
DynamicAgent: Generic manifest-driven agent executor.
Scopes tools strictly to the manifest's declared tools, applies custom prompt templates,
and dynamically injects JIT skills and model constraints.
"""
import json
from typing import Any, Dict, List, Optional
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
        supporting tool category aliases like 'filesystem', 'terminal', 'code-search', 'communication',
        as well as tools implied by capabilities.
        """
        if hasattr(self.tool_registry, "get_all_tools"):
            tools_list = self.tool_registry.get_all_tools()
            all_tools = [getattr(t, "name", str(t)) for t in tools_list]
        elif hasattr(self.tool_registry, "get_schemas"):
            schemas = self.tool_registry.get_schemas()
            all_tools = [s.get("function", {}).get("name") for s in schemas if s.get("function", {}).get("name")]
        elif hasattr(self.tool_registry, "_tools"):
            all_tools = list(self.tool_registry._tools.keys())
        else:
            all_tools = ["read_file", "write_file", "list_files", "terminal_execute", "complete_task"]

        from ..capabilities.model import default_capability_registry, expand_tool_names

        declared_or_cap = list(self.declared_tools)
        if not declared_or_cap and self.capabilities:
            declared_or_cap = default_capability_registry.resolve_tools(self.capabilities)

        if not declared_or_cap or "*" in declared_or_cap:
            return all_tools

        expanded = expand_tool_names(declared_or_cap)
        scoped: List[str] = ["complete_task"]
        for t_name in expanded:
            norm = t_name.lower().strip()
            if norm in all_tools:
                scoped.append(norm)
            else:
                matches = [t for t in all_tools if norm in t.lower()]
                scoped.extend(matches)

        valid_tools = [t for t in set(scoped) if t in all_tools or t == "complete_task"]
        return valid_tools or all_tools

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

    def execute(self, state: OrchestratorState, active_skills: Optional[List[str]] = None, **kwargs) -> Dict[str, Any]:
        """
        Executes the dynamic agent using the ReAct loop with scoped tools.
        """
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

        loop_result = self.react_loop.run(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=self.model,
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
