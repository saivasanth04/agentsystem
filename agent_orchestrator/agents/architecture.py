"""
Architecture Agent: Defines component boundaries, class structures, folder layouts, and design patterns.
"""
import json
from typing import Any, Dict, List, Optional, Union
from .base import BaseAgent
from ..state import OrchestratorState


class ArchitectureAgent(BaseAgent):
    def __init__(
        self,
        model: str = None,
        llm=None,
        workspace=None,
        tool_registry=None,
        skill_registry=None,
        mcp_client=None,
        message_bus=None,
        approval_gate=None,
        **kwargs: Any,
    ):
        super().__init__(
            name="ARCHITECTURE",
            role_description="Responsible for system design, component topology, folder layouts, and design patterns selection.",
            model=model,
            llm=llm,
            workspace=workspace,
            tool_registry=tool_registry,
            skill_registry=skill_registry,
            mcp_client=mcp_client,
            message_bus=message_bus,
            approval_gate=approval_gate,
            **kwargs,
        )

    def execute(self, state: Union[OrchestratorState, Dict[str, Any]], active_skills: Optional[List[str]] = None, **kwargs) -> Dict[str, Any]:
        if isinstance(state, dict) or state is None:
            dict_state = state or {}
            req = dict_state.get("user_request") or dict_state.get("goal") or "Design system architecture"
            state_obj = OrchestratorState(user_request=req)
            for k, v in dict_state.items():
                if hasattr(state_obj, k):
                    setattr(state_obj, k, v)
            state = state_obj
        replan_context = ""
        if state.replan_history:
            latest = state.replan_history[-1]
            replan_context = f"\n\n[RE-PLANNING CONTEXT - Iteration {latest.iteration}]\nArchitecture adjustments required:\n{latest.feedback_summary}\nAction items: {json.dumps(latest.remediation_plan)}"

        repo_overview = ""
        if self.tool_registry and hasattr(self.tool_registry, "arch_analyzer"):
            try:
                arch_summary = self.tool_registry.arch_analyzer.analyze()
                repo_overview = f"\n\nExisting Codebase Architecture:\n{arch_summary.to_markdown()}"
            except Exception:
                pass
        if not repo_overview and self.tool_registry and hasattr(self.tool_registry, "code_graph"):
            try:
                from ..codebase.repo_map import PageRankRepoMap
                pr_map = PageRankRepoMap(self.tool_registry.code_graph)
                repo_overview = f"\n\nExisting Codebase Map (Top Central Interfaces):\n{pr_map.generate_repo_map(max_tokens=1500)}"
            except Exception:
                pass

        prompt = f"""
User Request:
{state.user_request}

Execution Plan:
{json.dumps(state.plan_output or {}, indent=2)}

Specification:
{json.dumps(state.specification_output or {}, indent=2)}
{replan_context}{repo_overview}

Provide the architecture design in JSON format with the following schema:
{{
  "system_title": "string",
  "design_patterns": ["string"],
  "component_structure": [
    {{
      "module_name": "string",
      "purpose": "string",
      "classes_or_functions": [
        {{
          "name": "string",
          "type": "class | function | dataclass",
          "description": "string",
          "methods_or_params": ["string"]
        }}
      ]
    }}
  ],
  "file_layout": [
    {{
      "filepath": "string (e.g. cache/core.py)",
      "description": "string"
    }}
  ],
  "data_flow_description": "string"
}}
"""
        prompt += "\nUse `list_directory` or `read_file` to inspect existing project folder structure, and call `complete_task` when finished."

        system_prompt = self.build_system_prompt(active_skills=active_skills)
        raw_tools = self.tool_registry.get_tools_for_agent(self.name) if hasattr(self.tool_registry, "get_tools_for_agent") else []
        arch_tools = [getattr(t, "name", str(t)) for t in raw_tools]

        from ..contracts import ArchitectureContract
        from ..runtime.validator import DeliverableValidator

        effective_model = kwargs.get("model") or self.model
        loop_result = self.react_loop.run(
            system_prompt=system_prompt,
            user_prompt=prompt,
            model=effective_model,
            available_tools=arch_tools,
            agent_name=self.name,
            temperature=0.2,
            target_contract=ArchitectureContract,
            reasoning_config=kwargs.get("reasoning_config") or getattr(self, "reasoning_config", None),
            **{k: v for k, v in kwargs.items() if k not in ("reasoning_config", "model")},
        )

        final_out = loop_result.get("final_output", {})
        result = (
            final_out.get("deliverables", {})
            if (isinstance(final_out, dict) and "deliverables" in final_out and final_out["deliverables"])
            else final_out
        )
        if not isinstance(result, dict) or "component_structure" not in result:
            if isinstance(final_out, dict) and "component_structure" in final_out:
                result = final_out
            elif isinstance(result, dict):
                pass
            else:
                result = {"raw_output": final_out, "system_title": state.user_request}

        val_report = loop_result.get("validation_report") or DeliverableValidator.validate_contract(ArchitectureContract, result).to_dict()
        result["validation_report"] = val_report

        state.architecture_output = result
        state.add_message(self.name, "ARCHITECTURE", json.dumps(result, indent=2), structured_data=result)
        return result
