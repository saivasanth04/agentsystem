"""
Specification Agent: Defines API contracts, data schemas, and Given-When-Then criteria using openapi-spec-design and acceptance-criteria-generation skills.
"""
import json
from typing import Any, Dict, List, Optional, Union
from .base import BaseAgent
from ..state import OrchestratorState


class SpecificationAgent(BaseAgent):
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
            name="SPECIFICATION",
            role_description="Responsible for requirements analysis, acceptance-criteria generation, schema-compliant API contracts, and edge cases.",
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
            req = dict_state.get("user_request") or dict_state.get("goal") or "Define specifications"
            state_obj = OrchestratorState(user_request=req)
            for k, v in dict_state.items():
                if hasattr(state_obj, k):
                    setattr(state_obj, k, v)
            state = state_obj
        replan_context = ""
        if state.replan_history:
            latest = state.replan_history[-1]
            replan_context = f"\n\n[RE-PLANNING CONTEXT - Iteration {latest.iteration}]\nIssues to fix in specification:\n{latest.feedback_summary}\nAction items: {json.dumps(latest.remediation_plan)}"

        prompt = f"""
User Request:
{state.user_request}

Execution Plan:
{json.dumps(state.plan_output or {}, indent=2)}
{replan_context}

Provide a detailed specification in JSON format with the following schema:
{{
  "feature_name": "string",
  "overview": "string",
  "functional_requirements": [
    {{
      "id": "FR-1",
      "description": "string",
      "input_contract": "string",
      "output_contract": "string"
    }}
  ],
  "non_functional_requirements": [
    {{
      "id": "NFR-1",
      "category": "performance | security | reliability | usability",
      "target": "string"
    }}
  ],
  "edge_cases": [
    {{
      "scenario": "string",
      "expected_behavior": "string"
    }}
  ],
  "acceptance_criteria": [
    "Given-When-Then criteria..."
  ]
}}
"""
        prompt += "\nUse tools if you need to inspect existing models or schemas, and call `complete_task` when finished."

        system_prompt = self.build_system_prompt(active_skills=active_skills)
        raw_tools = self.tool_registry.get_tools_for_agent(self.name) if hasattr(self.tool_registry, "get_tools_for_agent") else []
        spec_tools = [getattr(t, "name", str(t)) for t in raw_tools]

        from ..contracts import SpecificationContract
        from ..runtime.validator import DeliverableValidator

        effective_model = kwargs.get("model") or self.model
        loop_result = self.react_loop.run(
            system_prompt=system_prompt,
            user_prompt=prompt,
            model=effective_model,
            available_tools=spec_tools,
            agent_name=self.name,
            temperature=0.2,
            target_contract=SpecificationContract,
            reasoning_config=kwargs.get("reasoning_config") or getattr(self, "reasoning_config", None),
            **{k: v for k, v in kwargs.items() if k not in ("reasoning_config", "model")},
        )

        final_out = loop_result.get("final_output", {})
        result = (
            final_out.get("deliverables", {})
            if (isinstance(final_out, dict) and "deliverables" in final_out and final_out["deliverables"])
            else final_out
        )
        if not isinstance(result, dict) or "functional_requirements" not in result:
            if isinstance(final_out, dict) and "functional_requirements" in final_out:
                result = final_out
            elif isinstance(result, dict):
                pass
            else:
                result = {"raw_output": final_out, "feature_name": state.user_request}

        val_report = loop_result.get("validation_report") or DeliverableValidator.validate_contract(SpecificationContract, result).to_dict()
        result["validation_report"] = val_report

        state.specification_output = result
        state.add_message(self.name, "SPECIFICATION", json.dumps(result, indent=2), structured_data=result)
        return result
