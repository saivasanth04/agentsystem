"""
Skill Runtime Engine.
Executes capability-driven skill procedures under active RuntimePolicy and ToolPolicy constraints.
Seamlessly integrates with SkillRegistry, AgentRegistry, MCPManager, UnifiedToolDispatcher,
WorkspaceManager, LangGraph StateGraph, and LiteLLM Gateway.
"""
from dataclasses import dataclass, field
import logging
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional, Union

from agent_orchestrator.registry.skill_registry import SkillRegistry
from agent_orchestrator.registry.agent_registry import AgentRegistry
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.llm import LLMClient

from .compiler import ExecutionStep, SkillCompiler
from .policy import RuntimePolicy, ToolPolicy
from .resolver import ResolvedSkillPlan, SkillResolver
from .verifier import SkillVerifier

logger = logging.getLogger("skills.runtime")


@dataclass
class StepExecutionResult:
    """Outcome of an individual procedural step execution."""
    step_number: int
    step_name: str
    status: str  # 'COMPLETED' | 'FAILED' | 'SKIPPED'
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    output: Any = None
    error: Optional[str] = None
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_number": self.step_number,
            "step_name": self.step_name,
            "status": self.status,
            "tool_calls": self.tool_calls,
            "output": self.output,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class RuntimeExecutionReport:
    """Comprehensive report for a completed skill execution procedure."""
    task_description: str
    status: str  # 'SUCCESS' | 'PARTIAL' | 'FAILED'
    active_skills: List[str]
    step_results: List[StepExecutionResult]
    total_tool_calls: int
    total_duration_seconds: float
    violations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_description": self.task_description,
            "status": self.status,
            "active_skills": self.active_skills,
            "step_results": [s.to_dict() for s in self.step_results],
            "total_tool_calls": self.total_tool_calls,
            "total_duration_seconds": self.total_duration_seconds,
            "violations": self.violations,
        }


class SkillRuntime:
    """
    Unified Runtime Engine replacing passive markdown prompts with active,
    policy-enforced capability execution.
    """

    def __init__(
        self,
        skill_registry: Optional[SkillRegistry] = None,
        agent_registry: Optional[AgentRegistry] = None,
        mcp_manager: Optional[MCPManager] = None,
        tool_dispatcher: Optional[UnifiedToolDispatcher] = None,
        workspace_manager: Optional[WorkspaceManager] = None,
        llm_client: Optional[LLMClient] = None,
        on_event: Optional[Callable[[str, str], None]] = None,
    ):
        self.workspace = workspace_manager or WorkspaceManager()
        self.skill_registry = skill_registry or SkillRegistry()
        self.agent_registry = agent_registry or AgentRegistry()

        # Ensure AgentRegistry is populated with manifests and core personas
        if not self.agent_registry.get("CODER"):
            manifests_dir = Path(__file__).resolve().parents[1] / "agent_orchestrator" / "registry" / "manifests"
            if manifests_dir.exists():
                self.agent_registry.load_from_directory(manifests_dir)
            from agent_orchestrator.registry.agent_registry import AgentManifest
            core_personas = [
                AgentManifest(name="CODER", role_description="Code generator and refactoring specialist", capabilities=["code-generation", "refactoring"], tools=["filesystem", "terminal"]),
                AgentManifest(name="TESTER", role_description="Test engineer and quality assurance specialist", capabilities=["testing", "verification"], tools=["filesystem", "terminal"]),
                AgentManifest(name="REVIEWER", role_description="Code review and architecture verification specialist", capabilities=["code-review"], tools=["filesystem", "git"]),
                AgentManifest(name="PLANNER", role_description="Strategic roadmap and execution planner", capabilities=["planning"], tools=["filesystem"]),
                AgentManifest(name="SPECIFICATION", role_description="Requirements and API designer", capabilities=["spec-writing"], tools=["filesystem"]),
                AgentManifest(name="ARCHITECTURE", role_description="System modular architecture designer", capabilities=["software-architecture"], tools=["filesystem"]),
            ]
            for persona in core_personas:
                if not self.agent_registry.get(persona.name):
                    self.agent_registry.register(persona)

        self.mcp_manager = mcp_manager or MCPManager(workspace_dir=self.workspace.root_dir)

        builtin = BuiltinToolRegistry(
            workspace=self.workspace,
            skill_registry=self.skill_registry,
            agent_registry=self.agent_registry,
        )
        self.dispatcher = tool_dispatcher or UnifiedToolDispatcher(
            builtin_registry=builtin,
            mcp_manager=self.mcp_manager,
        )

        self.llm = llm_client or LLMClient()
        self.on_event = on_event or (lambda event_type, msg: logger.info(f"[{event_type}] {msg}"))

        self.resolver = SkillResolver(
            skill_registry=self.skill_registry,
            builtin_registry=builtin,
            mcp_manager=self.mcp_manager,
            dispatcher=self.dispatcher,
            workspace=self.workspace,
        )
        self.verifier = SkillVerifier()

    def dispatch_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        plan: Optional[ResolvedSkillPlan] = None,
        agent_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Dispatches a tool call enforcing ToolPolicy and resolving aliases.
        """
        arguments = arguments or {}
        policy = plan.composite_tool_policy if plan else ToolPolicy()

        # 1. Enforce allowed tools policy
        if not policy.is_tool_allowed(tool_name):
            msg = f"Tool '{tool_name}' is not allowed by active ToolPolicy."
            logger.warning(msg)
            return {"error": msg, "success": False, "is_error": True}

        # 2. Check for thin adapter in plan tool_bindings
        if plan and tool_name in plan.tool_bindings:
            binding = plan.tool_bindings[tool_name]
            if callable(binding):
                try:
                    return binding(**arguments)
                except Exception as e:
                    return {"error": f"Adapter execution error: {e}", "success": False, "is_error": True}

        # 3. Resolve canonical aliases (e.g. 'filesystem.read' -> 'read_file')
        resolved_name = policy.resolve_tool_name(tool_name)

        # 4. Dispatch via UnifiedToolDispatcher
        return self.dispatcher.call_tool(
            name=resolved_name,
            arguments=arguments,
            agent_role=agent_role,
        )

    def execute_plan(
        self,
        plan: ResolvedSkillPlan,
        task_context: Optional[Dict[str, Any]] = None,
        agent_role: Optional[str] = None,
    ) -> RuntimeExecutionReport:
        """
        Executes a resolved skill plan step by step under policy enforcement.
        """
        start_time = time.time()
        task_context = task_context or {}
        runtime_policy = plan.composite_runtime_policy
        eff_role = agent_role or runtime_policy.agent_affinity or "CODER"

        self.on_event(
            "SKILL_RUNTIME_START",
            f"Executing plan for: '{plan.task_description}' with skills: {[s.name for s in plan.skills]}"
        )

        step_results: List[StepExecutionResult] = []
        violations: List[str] = []
        total_tool_calls = 0

        # Execute procedure steps
        for step in plan.execution_procedure:
            # Check timeout policy
            elapsed = time.time() - start_time
            if elapsed > runtime_policy.timeout_seconds:
                violations.append(f"Timeout of {runtime_policy.timeout_seconds}s exceeded at step {step.step_number}")
                step_results.append(StepExecutionResult(
                    step_number=step.step_number,
                    step_name=step.name,
                    status="FAILED",
                    error="Timeout exceeded",
                ))
                break

            step_start = time.time()
            self.on_event("STEP_START", f"Step {step.step_number}: {step.name}")

            step_tool_calls: List[Dict[str, Any]] = []
            step_error: Optional[str] = None

            # Execute suggested tools for this step
            for tool in step.suggested_tools:
                if not plan.composite_tool_policy.is_tool_allowed(tool):
                    continue

                total_tool_calls += 1
                if total_tool_calls > runtime_policy.max_turns:
                    violations.append(f"Max turn limit ({runtime_policy.max_turns}) exceeded.")
                    break

                # Prepare standard parameters if applicable
                args: Dict[str, Any] = {}
                resolved_target = plan.composite_tool_policy.resolve_tool_name(tool)

                if resolved_target in ("read_file", "filesystem.read"):
                    target_file = task_context.get("target_file") or "README.md"
                    args["path"] = target_file
                elif resolved_target in ("list_directory", "filesystem.list"):
                    args["path"] = ""
                elif resolved_target in ("git_status", "git.status"):
                    args = {}
                elif resolved_target in ("ast_syntax_check",):
                    args["code"] = task_context.get("code", "")
                    if not args["code"] and task_context.get("target_file"):
                        args["filepath"] = task_context.get("target_file")

                # Dispatch
                res = self.dispatch_tool(
                    tool_name=tool,
                    arguments=args,
                    plan=plan,
                    agent_role=eff_role,
                )
                step_tool_calls.append({"tool": tool, "args": args, "result": res})

            step_dur = round(time.time() - step_start, 4)
            status = "COMPLETED" if not step_error else "FAILED"
            step_results.append(StepExecutionResult(
                step_number=step.step_number,
                step_name=step.name,
                status=status,
                tool_calls=step_tool_calls,
                duration_seconds=step_dur,
                error=step_error,
            ))
            self.on_event("STEP_END", f"Step {step.step_number} finished with status {status}")

        total_dur = round(time.time() - start_time, 4)
        overall_status = "SUCCESS" if all(s.status == "COMPLETED" for s in step_results) else "PARTIAL"
        if violations and not any(s.status == "COMPLETED" for s in step_results):
            overall_status = "FAILED"

        report = RuntimeExecutionReport(
            task_description=plan.task_description,
            status=overall_status,
            active_skills=[s.name for s in plan.skills],
            step_results=step_results,
            total_tool_calls=total_tool_calls,
            total_duration_seconds=total_dur,
            violations=violations,
        )
        self.on_event("SKILL_RUNTIME_COMPLETE", f"Execution complete: {overall_status} in {total_dur}s")
        return report

    def create_langgraph_node(self) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        """
        Creates a LangGraph-compatible node function for inclusion in StateGraph.
        Can be used directly in orchestrator workflows.
        """
        def skill_runtime_node(state: Dict[str, Any]) -> Dict[str, Any]:
            objective = state.get("user_request") or state.get("objective") or "Execute task"
            capabilities = state.get("required_capabilities") or []
            preferred = state.get("preferred_skills") or []

            # 1. Resolve skills and construct plan
            plan = self.resolver.resolve(
                task_description=objective,
                capabilities=capabilities,
                preferred_skills=preferred,
            )

            # 2. Execute plan under runtime policies
            report = self.execute_plan(plan, task_context=state)

            # 3. Update state
            new_state = dict(state)
            new_state["skill_execution_report"] = report.to_dict()
            new_state["active_skills"] = report.active_skills
            new_state["skill_procedure_steps"] = [s.to_dict() for s in plan.execution_procedure]
            new_state["skill_tool_policy"] = plan.composite_tool_policy.to_dict()
            new_state["skill_runtime_policy"] = plan.composite_runtime_policy.to_dict()
            return new_state

        return skill_runtime_node
