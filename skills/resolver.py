"""
Skill Resolver.
Resolves tasks to executable skills, synthesizes runtime policies,
enforces tool availability across the discovery hierarchy, and constructs execution procedures.
"""
from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from agent_orchestrator.registry.skill_registry import SkillManifest, SkillRegistry
from .compiler import CompiledSkill, ExecutionProcedure, ExecutionStep, SkillCompiler
from .policy import RuntimePolicy, ToolPolicy
from .verifier import SkillVerifier, ToolVerificationStatus

logger = logging.getLogger("skills.resolver")


@dataclass
class ResolvedSkillPlan:
    """
    Complete resolved plan for executing a task with skills.
    Replaces static prompt strings with verified tool bindings,
    runtime policies, and actionable procedural steps.
    """
    task_description: str
    skills: List[CompiledSkill] = field(default_factory=list)
    composite_runtime_policy: RuntimePolicy = field(default_factory=RuntimePolicy)
    composite_tool_policy: ToolPolicy = field(default_factory=ToolPolicy)
    execution_procedure: List[ExecutionStep] = field(default_factory=list)
    tool_bindings: Dict[str, Any] = field(default_factory=dict)
    tool_verification_statuses: Dict[str, ToolVerificationStatus] = field(default_factory=dict)
    missing_tools: List[str] = field(default_factory=list)
    is_executable: bool = True

    def get_legacy_prompt_augmentation(self) -> str:
        """
        Backward-compatibility hook: returns consolidated markdown instructions
        for callers expecting string prompts.
        """
        parts = []
        for s in self.skills:
            parts.append(f"### Active Skill Capability: {s.name} (v{s.version})\n{s.instructions}\n")
        return "\n".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_description": self.task_description,
            "skills": [s.name for s in self.skills],
            "composite_runtime_policy": self.composite_runtime_policy.to_dict(),
            "composite_tool_policy": self.composite_tool_policy.to_dict(),
            "execution_procedure": [step.to_dict() for step in self.execution_procedure],
            "tool_bindings": {
                k: (v.__name__ if callable(v) else str(v)) for k, v in self.tool_bindings.items()
            },
            "tool_verification_statuses": {
                k: v.to_dict() for k, v in self.tool_verification_statuses.items()
            },
            "missing_tools": self.missing_tools,
            "is_executable": self.is_executable,
        }


class SkillResolver:
    """
    Resolves high-level tasks into executable capabilities:
      Task -> Skill Resolver -> Runtime Policy -> Tool Policy -> Execution Procedure
    """

    def __init__(
        self,
        skill_registry: Optional[SkillRegistry] = None,
        builtin_registry: Optional[Any] = None,
        mcp_manager: Optional[Any] = None,
        dispatcher: Optional[Any] = None,
        workspace: Optional[Any] = None,
        matrix_path: Optional[Path] = None,
    ):
        self.skill_registry = skill_registry or SkillRegistry()
        self.builtin_registry = builtin_registry
        self.mcp_manager = mcp_manager
        self.dispatcher = dispatcher
        self.workspace = workspace
        self.compiler = SkillCompiler()
        self.verifier = SkillVerifier(matrix_path=matrix_path)

    def resolve(
        self,
        task_description: str,
        capabilities: Optional[List[str]] = None,
        preferred_skills: Optional[List[str]] = None,
        top_k: int = 3,
    ) -> ResolvedSkillPlan:
        """
        Resolves task into an executable plan:
        1. Discovers relevant skills from SkillRegistry.
        2. Resolves skill dependencies.
        3. Compiles skills into executable operational definitions.
        4. Verifies all required tools against the strict hierarchy:
           MCP -> Dispatcher -> Builtin -> Thin Adapter.
        5. Synthesizes composite RuntimePolicy and ToolPolicy.
        6. Builds the unified ExecutionProcedure.
        """
        clean_task = (task_description or "").strip()
        caps = capabilities or []
        preferred = preferred_skills or []

        # 1. Discover skills
        manifests: List[SkillManifest] = []

        # Explicit preferred skills
        for pref in preferred:
            sk = self.skill_registry.get_skill(pref)
            if sk and sk not in manifests:
                manifests.append(sk)

        # Semantic discovery from task and capabilities
        if not manifests or len(manifests) < top_k:
            query = f"{clean_task} {' '.join(caps)}"
            discovered = self.skill_registry.discover(query=query, top_k=top_k)
            for m, _ in discovered:
                if m not in manifests:
                    manifests.append(m)

        # Fallback to general skill if no skills matched
        if not manifests:
            default_sk = self.skill_registry.get_skill("source-driven-development")
            if default_sk:
                manifests.append(default_sk)

        # 2. Dependency resolution
        resolved_manifests = manifests
        if hasattr(self.skill_registry, "_resolve_dependencies_dag"):
            try:
                resolved_manifests = self.skill_registry._resolve_dependencies_dag(manifests)
            except Exception as e:
                logger.warning(f"DAG dependency resolution encountered warning: {e}; using discovered manifests")
                resolved_manifests = manifests

        # 3. Compile skills
        compiled_skills: List[CompiledSkill] = []
        for m in resolved_manifests:
            compiled = self.compiler.compile(m)
            compiled_skills.append(compiled)

        # 4. Verify all required tools & create thin adapters
        tool_bindings: Dict[str, Any] = {}
        tool_statuses: Dict[str, ToolVerificationStatus] = {}
        missing_tools: List[str] = []
        is_executable = True

        all_required_tools: Set[str] = set()
        for cs in compiled_skills:
            all_required_tools.update(cs.tool_policy.required_tools)

        for req_tool in all_required_tools:
            status = self.verifier.verify_tool(
                tool_name=req_tool,
                builtin_registry=self.builtin_registry,
                mcp_manager=self.mcp_manager,
                dispatcher=self.dispatcher,
                workspace=self.workspace,
            )
            tool_statuses[req_tool] = status

            if status.exists:
                if status.adapter_callable:
                    tool_bindings[req_tool] = status.adapter_callable
                else:
                    tool_bindings[req_tool] = status.resolved_name
            else:
                missing_tools.append(req_tool)
                is_executable = False

        # 5. Synthesize composite policies
        composite_runtime = RuntimePolicy()
        composite_tool = ToolPolicy()

        for cs in compiled_skills:
            composite_runtime = composite_runtime.merge(cs.runtime_policy)
            composite_tool = composite_tool.merge(cs.tool_policy)

        # Include resolved aliases in composite tool policy
        for req_tool, status in tool_statuses.items():
            if status.resolved_name and status.resolved_name != req_tool:
                composite_tool.tool_aliases[req_tool] = status.resolved_name

        # 6. Synthesize combined execution procedure
        combined_steps: List[ExecutionStep] = []
        step_counter = 1
        for cs in compiled_skills:
            for step in cs.procedure.steps:
                combined_steps.append(ExecutionStep(
                    step_number=step_counter,
                    name=f"[{cs.name}] {step.name}",
                    objective=step.objective,
                    suggested_tools=step.suggested_tools,
                    validation_gate=step.validation_gate,
                    required_inputs=step.required_inputs,
                    expected_outputs=step.expected_outputs,
                ))
                step_counter += 1

        return ResolvedSkillPlan(
            task_description=clean_task,
            skills=compiled_skills,
            composite_runtime_policy=composite_runtime,
            composite_tool_policy=composite_tool,
            execution_procedure=combined_steps,
            tool_bindings=tool_bindings,
            tool_verification_statuses=tool_statuses,
            missing_tools=missing_tools,
            is_executable=is_executable,
        )
