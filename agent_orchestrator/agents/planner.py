"""
Planner Agent: Generates phased milestones, critical paths, and dependency graphs using ReadFileTool, ListDirectoryTool, and mcp-server-git.
"""
import json
from typing import Any, Dict, List, Optional, Union
from .base import BaseAgent
from ..state import OrchestratorState


from ..contracts import ExecutionPlanContract
from ..runtime.validator import DeliverableValidator


class PlannerAgent(BaseAgent):
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
            name="PLANNER",
            role_description="Responsible for project-planning, dependency-mapping, inspecting existing project files, and creating phased roadmaps.",
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
            req = dict_state.get("user_request") or dict_state.get("goal") or "Plan execution roadmap"
            state_obj = OrchestratorState(user_request=req)
            for k, v in dict_state.items():
                if hasattr(state_obj, k):
                    setattr(state_obj, k, v)
            state = state_obj
        replan_context = ""
        if state.replan_history:
            latest = state.replan_history[-1]
            replan_context = f"\n\n[RE-PLANNING CONTEXT - Iteration {latest.iteration}]\nReason: {latest.trigger_reason}\nFeedback: {latest.feedback_summary}\nRemediation Plan: {json.dumps(latest.remediation_plan, indent=2)}"

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

        project_context = ""
        if hasattr(state, "project_profile") and state.project_profile:
            try:
                from ..runtime.project_discovery import ProjectProfile
                prof = ProjectProfile(**state.project_profile)
                project_context = f"\n\n{prof.to_prompt_context()}"
            except Exception:
                project_context = f"\n\nProject Profile:\n{json.dumps(state.project_profile, indent=2)}"

        env_context = ""
        if hasattr(state, "environment_profile") and state.environment_profile:
            try:
                from ..runtime.environment_probe import EnvironmentProfile
                env_prof = EnvironmentProfile.from_dict(state.environment_profile)
                env_context = f"\n\n{env_prof.to_prompt_context()}"
            except Exception:
                env_context = f"\n\nDynamic Environment:\n{json.dumps(state.environment_profile, indent=2)}"

        prompt = f"""
User Request:
{state.user_request}

Task Understanding:
{json.dumps(state.task_understanding or {}, indent=2)}

Task Decomposition:
{json.dumps(state.task_decomposition or [], indent=2)}
{replan_context}{repo_overview}{project_context}{env_context}

Provide a structured execution plan in JSON format with the following schema:
{{
  "project_title": "string",
  "goal_summary": "string",
  "tasks": [
    {{
      "task_id": "T-01",
      "objective": "Clear action-oriented goal (e.g. 'Implement LRU Cache data structure')",
      "dependencies": [],
      "required_capabilities": ["code-generation", "data-structures"],
      "required_tools": ["filesystem", "terminal"],
      "preferred_skills": ["code-simplification"],
      "inputs": ["src/cache.py"],
      "outputs": ["src/cache.py:LRUCache"],
      "acceptance_tests": ["pytest tests/test_cache.py"],
      "permissions": {{
        "allowed_read_paths": ["*"],
        "allowed_write_paths": ["src/*", "tests/*"]
      }}
    }}
  ],
  "phases": [
    {{
      "phase_number": 1,
      "name": "string",
      "description": "string",
      "deliverables": ["string"],
      "agent_assigned": "string"
    }}
  ],
  "milestones": ["string"],
  "risk_mitigations": [
    {{
      "risk": "string",
      "mitigation": "string"
    }}
  ],
  "success_criteria": ["string"]
}}
"""
        prompt += "\nUse `list_directory` or `read_file` if you need to inspect existing workspace structure, and provide your plan or call `complete_task`."

        system_prompt = self.build_system_prompt(active_skills=active_skills)
        from runtime.tool_policy import ToolPolicyEngine
        executable = ToolPolicyEngine.get_executable_tools(allowed_tools={"read_file", "list_directory", "find_symbol", "complete_task"})
        planner_tools = [getattr(t, "name", str(t)) for t in executable]

        effective_model = kwargs.get("model") or self.model
        loop_result = self.execution_loop.run(
            system_prompt=system_prompt,
            user_prompt=prompt,
            model=effective_model,
            available_tools=planner_tools,
            agent_name=self.name,
            temperature=0.2,
            target_contract=ExecutionPlanContract,
            reasoning_config=kwargs.get("reasoning_config") or getattr(self, "reasoning_config", None),
            **{k: v for k, v in kwargs.items() if k not in ("reasoning_config", "model")},
        )

        final_out = loop_result.get("final_output", {})
        result = (
            final_out.get("deliverables", {})
            if (isinstance(final_out, dict) and "deliverables" in final_out and final_out["deliverables"])
            else final_out
        )
        if not isinstance(result, dict) or "phases" not in result:
            if isinstance(final_out, dict) and "phases" in final_out:
                result = final_out
            elif isinstance(result, dict):
                pass
            else:
                result = {"raw_output": final_out, "goal_summary": str(final_out)}

        val_report = loop_result.get("validation_report") or DeliverableValidator.validate_contract(ExecutionPlanContract, result).to_dict()
        result["validation_report"] = val_report

        state.plan_output = result
        state.add_message(self.name, "PLANNING", json.dumps(result, indent=2), structured_data=result)
        return result
