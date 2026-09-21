"""
Specification Agent: Defines API contracts, data schemas, and Given-When-Then criteria using openapi-spec-design and acceptance-criteria-generation skills.
"""
import json
from typing import Any, Dict, List, Optional
from .base import BaseAgent
from ..state import OrchestratorState


class SpecificationAgent(BaseAgent):
    def __init__(self, model: str = None, llm=None, workspace=None, tool_registry=None, skill_registry=None, mcp_client=None):
        super().__init__(
            name="SPECIFICATION",
            role_description="Responsible for requirements analysis, acceptance-criteria generation, schema-compliant API contracts, and edge cases.",
            model=model,
            llm=llm,
            workspace=workspace,
            tool_registry=tool_registry,
            skill_registry=skill_registry,
            mcp_client=mcp_client,
        )

    def execute(self, state: OrchestratorState, active_skills: Optional[List[str]] = None, **kwargs) -> Dict[str, Any]:
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
        spec_tools = [t.name for t in self.tool_registry.get_tools_for_agent(self.name)]

        from ..contracts import SpecificationContract
        from ..runtime.validator import DeliverableValidator

        loop_result = self.react_loop.run(
            system_prompt=system_prompt,
            user_prompt=prompt,
            model=self.model,
            available_tools=spec_tools,
            agent_name=self.name,
            temperature=0.2,
            target_contract=SpecificationContract,
            reasoning_config=kwargs.get("reasoning_config") or getattr(self, "reasoning_config", None),
            **{k: v for k, v in kwargs.items() if k != "reasoning_config"},
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
