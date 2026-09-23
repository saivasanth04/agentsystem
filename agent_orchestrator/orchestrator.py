"""
Task-Orchestrator: LangGraph-powered Intelligent Multi-Agent Software Engineering Runtime.
Dynamically decomposes tasks into capability-driven action DAGs, discovers optimal specialized
agent personas from AgentRegistry, injects JIT skills, scopes tools, and adapts execution in real-time.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional, TypedDict, Union
import uuid
from langgraph.graph import StateGraph, START, END

from .config import config, OrchestratorConfig
from .llm import LLMClient, default_llm
from .state import OrchestratorState, ReviewVerdict, TaskStatus, AgentMessage, ReplanRecord
from .tools.workspace import WorkspaceManager
from .tools.builtin_tools import BuiltinToolRegistry
from .tools.mcp_client import MCPClientAdapter
from .tools.unified_dispatcher import UnifiedToolDispatcher
from .mcp.manager import MCPManager
from .registry.skill_registry import SkillRegistry
from .registry.agent_registry import AgentRegistry, AgentDefinition, AgentManifest
from .runtime.diagnostics import FailureDiagnostician
from .runtime.replan_engine import EpistemicReplanner, ReplanResult
from .memory.working_memory import WorkingMemory
from .runtime.task_graph import TaskDAG, ExecutableTask, TaskState, TaskPermissions, RetryPolicy, TokenUsage, TaskAttemptRecord, ObservationRecord
from .runtime.verification import TaskVerificationGate, VerificationResult
from .runtime.dag_scheduler import ConcurrentDAGScheduler
from .runtime.analysis import ParallelDomainAnalyzer, DomainAnalysisMatrix
from .runtime.messaging import MessageBus, MessageType, StructuredMessage
from .persistence import SQLiteStateStore, WorkspaceCheckpointManager, WorkspaceTransactionManager, SessionRecoveryEngine, SessionRecoveryReport
from .agents.planner import PlannerAgent

from .agents.specification import SpecificationAgent
from .agents.architecture import ArchitectureAgent
from .agents.coder import CoderAgent
from .agents.tester import TesterAgent
from .agents.reviewer import ReviewerAgent
from .agents.dynamic_agent import DynamicAgent
from .agents.base import BaseAgent




# LangGraph State Schema
class OrchestratorGraphState(TypedDict):
    user_request: str
    project_profile: Optional[Dict[str, Any]]
    environment_profile: Optional[Dict[str, Any]]
    task_understanding: Optional[Dict[str, Any]]
    task_decomposition: Optional[List[Dict[str, Any]]]
    subtasks: List[Dict[str, Any]]
    current_subtask_index: int
    completed_subtasks: List[Dict[str, Any]]
    step_results: Dict[str, Any]
    plan_output: Optional[Dict[str, Any]]
    specification_output: Optional[Dict[str, Any]]
    architecture_output: Optional[Dict[str, Any]]
    code_output: Optional[Dict[str, Any]]
    test_output: Optional[Dict[str, Any]]
    review_output: Optional[Dict[str, Any]]
    verdict: str
    iteration: int
    max_iterations: int
    remediation_plan: List[str]
    replan_history: List[Dict[str, Any]]
    target_agent_for_fix: str
    status: str
    messages: List[Dict[str, Any]]
    baseline_test_info: Optional[Dict[str, Any]]
    rollback_executed: Optional[bool]
    last_rollback: Optional[Dict[str, Any]]
    execution_snapshot: Optional[Dict[str, Any]]


class TaskOrchestrator:
    """
    Intelligent Dynamic Multi-Agent Orchestrator:
    START -> Understand -> Dynamic Capability Decompose -> Adaptive Subtask Loop [Select Agent -> JIT Skills -> Execute] -> Reviewer -> (PASS -> END | FAIL -> Re-plan & Inject Remediation Steps)
    """

    def __init__(
        self,
        cfg: Optional[OrchestratorConfig] = None,
        llm: Optional[LLMClient] = None,
        workspace: Optional[WorkspaceManager] = None,
        state_store: Optional[SQLiteStateStore] = None,
        checkpoint_manager: Optional[WorkspaceCheckpointManager] = None,
        on_event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        approval_gate: Optional[Any] = None,
        **kwargs: Any,
    ):
        self.cfg = cfg or kwargs.get("config") or config
        self.llm = llm or kwargs.get("llm_client") or default_llm
        self.workspace = workspace or kwargs.get("ws") or WorkspaceManager(self.cfg.workspace_dir)

        # Structured Logging (Issue #53)
        from .logging import (
            StructuredLogger,
            get_logger,
            configure_logging,
            add_session_file_sink,
            remove_session_file_sink,
            LogContext,
            set_log_context,
        )
        self.logger = kwargs.get("logger") or get_logger("orchestrator")
        self._user_on_event = on_event_callback
        self.on_event = self._on_event_bridge
        self.active_session_id = f"sess-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        self.active_execution_id = self.active_session_id

        from .runtime.approval_gate import PolicyBasedApprovalGate
        self.approval_gate = approval_gate or PolicyBasedApprovalGate()

        # Budget & Real-Time Ledger Tracking Engine (Issue #35)
        from .cost.budget_tracker import budget_tracker, BudgetSpec, BudgetTracker
        self.budget_tracker = kwargs.get("budget_tracker") or budget_tracker
        custom_spec = kwargs.get("budget_spec")
        if custom_spec is not None:
            self.budget_spec = custom_spec
        elif self.cfg:
            self.budget_spec = BudgetSpec(
                max_session_cost_usd=float(getattr(self.cfg, "max_session_cost_usd", 0.0) or 0.0),
                max_cost_usd_per_task=float(getattr(self.cfg, "max_task_cost_usd", 0.0) or getattr(self.cfg, "max_cost_usd_per_task", 0.0) or 0.0),
                max_tokens_per_task=int(getattr(self.cfg, "max_task_tokens", 0) or getattr(self.cfg, "max_tokens_per_task", 0) or 0),
                max_session_tokens=int(getattr(self.cfg, "max_session_tokens", 0) or 0),
            )
        else:
            self.budget_spec = BudgetSpec()

        self.budget_tracker.set_spec(self.budget_spec)
        budget_tracker.set_spec(self.budget_spec)

        # Persistence & Checkpoint Managers
        self.state_store = state_store or SQLiteStateStore(workspace_dir=self.workspace.root_dir)
        from .persistence.artifact_store import ArtifactStore
        self.artifact_store = kwargs.get("artifact_store") or ArtifactStore(
            storage_root=os.path.join(self.workspace.root_dir, ".orchestrator", "artifacts"),
            state_store=self.state_store,
        )
        self.checkpoint_manager = checkpoint_manager or WorkspaceCheckpointManager(
            workspace_dir=self.workspace.root_dir,
            artifact_store=self.artifact_store,
        )
        if self.checkpoint_manager and not getattr(self.checkpoint_manager, "artifact_store", None):
            self.checkpoint_manager.artifact_store = self.artifact_store

        self.transaction_manager = WorkspaceTransactionManager(
            workspace=self.workspace,
            checkpoint_manager=self.checkpoint_manager,
        )

        # Registries, Message Bus, MCP Manager, and Unified Tools
        self.message_bus = MessageBus(
            on_event_callback=self.on_event,
            state_store=self.state_store,
            session_id=self.active_session_id,
        )
        # Cancellation infrastructure (Issue #90)
        from .runtime.cancellation import CancellationSource
        self.cancellation_source = CancellationSource()
        self.cancellation_token = self.cancellation_source.token

        self.skill_registry = SkillRegistry()
        self.agent_registry = AgentRegistry()
        self.tool_registry = BuiltinToolRegistry(
            workspace=self.workspace,
            skill_registry=self.skill_registry,
            message_bus=self.message_bus,
            agent_registry=self.agent_registry,
            llm=self.llm,
            checkpoint_manager=self.checkpoint_manager,
        )
        if hasattr(self.tool_registry, "git_server"):
            self.transaction_manager.git_server = self.tool_registry.git_server

        self.mcp_manager = MCPManager(workspace_dir=self.workspace.root_dir)
        self.mcp_client = MCPClientAdapter(workspace_dir=self.workspace.root_dir, mcp_manager=self.mcp_manager)
        self.unified_dispatcher = UnifiedToolDispatcher(builtin_registry=self.tool_registry, mcp_manager=self.mcp_manager)
        self.diagnostician = FailureDiagnostician(self.llm)
        self.replan_engine = EpistemicReplanner(on_event=self.on_event)
        from .memory.manager import AgentMemoryEngine
        self.memory_engine = kwargs.get("memory_engine") or AgentMemoryEngine(
            workspace_dir=self.workspace.root_dir if hasattr(self.workspace, "root_dir") else None,
        )
        self.working_memory = self.memory_engine.working

        from .swarm.coordinator import SwarmCoordinator
        self.swarm_coordinator = kwargs.get("swarm_coordinator") or SwarmCoordinator(
            on_event_callback=self.on_event,
            agent_registry=self.agent_registry,
            message_bus=self.message_bus,
            memory_engine=self.memory_engine,
        )
        if hasattr(self.tool_registry, "swarm_coordinator") and not self.tool_registry.swarm_coordinator:
            self.tool_registry.swarm_coordinator = self.swarm_coordinator
            from .tools.swarm_tools import SwarmToolRegistry
            self.tool_registry.swarm_tools = SwarmToolRegistry(coordinator=self.swarm_coordinator)

        from .runtime.lifecycle_manager import AgentLifecycleManager
        self.lifecycle_manager = kwargs.get("lifecycle_manager") or AgentLifecycleManager(
            agent_registry=self.agent_registry,
            swarm_coordinator=self.swarm_coordinator,
            state_store=self.state_store,
            llm=self.llm,
            workspace=self.workspace,
            tool_registry=self.unified_dispatcher,
            skill_registry=self.skill_registry,
            mcp_client=self.mcp_client,
            message_bus=self.message_bus,
            approval_gate=self.approval_gate,
            on_event_callback=self.on_event,
        )

        from .runtime.event_bus import EventBus, EventType, ExecutionEvent
        self.event_bus = kwargs.get("event_bus") or EventBus(
            on_event_callback=self.on_event,
            state_store=self.state_store,
            session_id=self.active_session_id,
        )
        if hasattr(self.workspace, "set_event_bus"):
            self.workspace.set_event_bus(self.event_bus)

        from .telemetry.telemetry_engine import TelemetryEngine
        self.telemetry_engine = kwargs.get("telemetry_engine") or TelemetryEngine(
            session_id=self.active_session_id,
            event_bus=self.event_bus,
        )
        if hasattr(self.workspace, "set_telemetry_engine"):
            self.workspace.set_telemetry_engine(self.telemetry_engine)

        from .tracing import get_tracer
        self.tracer = kwargs.get("tracer") or get_tracer("orchestrator")
        self.active_trace_id = None

        self.verification_gate = TaskVerificationGate(
            workspace=self.workspace,
            tool_dispatcher=self.unified_dispatcher,
            event_bus=self.event_bus,
        )
        self.dag_scheduler = ConcurrentDAGScheduler(
            max_workers=4,
            on_event_callback=self.on_event,
            event_bus=self.event_bus,
            telemetry_engine=self.telemetry_engine,
        )
        self.domain_analyzer = ParallelDomainAnalyzer(llm=self.llm, workspace=self.workspace, on_event_callback=self.on_event)

        from .reproducibility.recorder import ReproducibilityRecorder
        self.reproducibility_recorder = kwargs.get("reproducibility_recorder") or ReproducibilityRecorder(
            default_seed=getattr(self.cfg, "seed", None)
        )
        self.current_snapshot = None

        self._register_default_agents()
        self.graph = self._build_langgraph_workflow()



    def _on_event_bridge(self, stage: str, message: str, payload: Optional[Dict[str, Any]] = None):
        """Dispatches event to StructuredLogger and forwards to user callback if configured."""
        if hasattr(self, "logger") and self.logger:
            try:
                self.logger.log_event(
                    stage=stage,
                    message=message,
                    payload=payload,
                    session_id=getattr(self, "active_session_id", None),
                )
            except Exception:
                pass
        if getattr(self, "_user_on_event", None):
            try:
                self._user_on_event(stage, message, payload)
            except Exception:
                pass

    def _default_event_logger(self, stage: str, message: str, payload: Optional[Dict[str, Any]] = None):
        """Legacy default event logger delegating to StructuredLogger."""
        self._on_event_bridge(stage, message, payload)

    def _register_default_agents(self):
        # 1. Register Core Framework Agents with Declarative Manifests
        self.agent_registry.register(AgentDefinition(
            name="PLANNER",
            role_description="Creates structured execution roadmaps, milestone decompositions, and risk mitigations.",
            capabilities=["planning", "roadmapping", "task-decomposition", "risk-analysis"],
            tools=["filesystem", "code-search", "communication"],
            skills=["planning-and-task-breakdown"],
            agent_class=PlannerAgent,
            model=self.cfg.planner_model,
        ))
        self.agent_registry.register(AgentDefinition(
            name="SPECIFICATION",
            role_description="Formulates requirements, API contracts, acceptance criteria, and schema boundaries.",
            capabilities=["spec-writing", "api-contracts", "requirements-analysis", "acceptance-criteria"],
            tools=["filesystem", "communication"],
            skills=["spec-driven-development", "api-and-interface-design"],
            agent_class=SpecificationAgent,
            model=self.cfg.spec_model,
        ))
        self.agent_registry.register(AgentDefinition(
            name="ARCHITECTURE",
            role_description="Designs modular system topology, component layouts, interfaces, and seam boundaries.",
            capabilities=["software-architecture", "system-design", "component-hierarchy", "seam-design"],
            tools=["filesystem", "code-search", "communication"],
            skills=["codebase-design", "software-architecture"],
            agent_class=ArchitectureAgent,
            model=self.cfg.arch_model,
        ))
        self.agent_registry.register(AgentDefinition(
            name="CODER",
            role_description="Generates, inspects, and refactors working production code and modules.",
            capabilities=["code-generation", "refactoring", "frontend", "backend", "general-coding"],
            tools=["filesystem", "terminal", "communication"],
            skills=["code-simplification"],
            agent_class=CoderAgent,
            model=self.cfg.coder_model,
        ))
        self.agent_registry.register(AgentDefinition(
            name="TESTER",
            role_description="Generates unit tests, executes test suites in terminal/sandbox, and verifies boundaries.",
            capabilities=["testing", "tdd", "unit-tests", "integration-tests", "terminal-execution", "communication"],
            tools=["filesystem", "terminal", "communication"],
            skills=["test-driven-development"],
            agent_class=TesterAgent,
            model=self.cfg.tester_model,
        ))
        self.agent_registry.register(AgentDefinition(
            name="REVIEWER",
            role_description="Conducts deep multi-axis quality review, security auditing, and PASS/FAIL evaluation.",
            capabilities=["code-review", "quality-audit", "standards-compliance", "rubric-scoring"],
            tools=["filesystem", "code-search", "communication"],
            skills=["code-review-and-quality", "doubt-driven-development"],
            agent_class=ReviewerAgent,
            model=self.cfg.reviewer_model,
        ))

        # 2. Discover and load specialized agent manifests from registry directory & workspace
        from pathlib import Path
        builtin_manifests_dir = Path(__file__).parent / "registry" / "manifests"
        if builtin_manifests_dir.exists():
            self.agent_registry.load_from_directory(builtin_manifests_dir)

        workspace_agents_dir = self.workspace.root_dir / "agents"
        if workspace_agents_dir.exists():
            self.agent_registry.load_from_directory(workspace_agents_dir)

    def select_agent(self, agent_name_or_manifest: Union[str, Any]) -> BaseAgent:
        return self.agent_registry.create_agent_instance(
            name_or_manifest=agent_name_or_manifest,
            llm=self.llm,
            workspace=self.workspace,
            tool_registry=self.unified_dispatcher,
            skill_registry=self.skill_registry,
            mcp_client=self.mcp_client,
            message_bus=self.message_bus,
            approval_gate=self.approval_gate,
        )

    def close(self):
        """Clean up and close active MCP sessions."""
        if hasattr(self, "mcp_manager") and self.mcp_manager:
            self.mcp_manager.close()

    # ==========================================
    # DYNAMIC AGENT LIFECYCLE APIS (Issue #68)
    # ==========================================
    def spawn_agent(
        self,
        role: str,
        goal: str,
        capabilities: Optional[List[str]] = None,
        tools: Optional[List[str]] = None,
        parent_id: Optional[str] = None,
        **kwargs: Any,
    ) -> BaseAgent:
        """Dynamically spawns, registers, and tracks a new specialized agent."""
        return self.lifecycle_manager.spawn_agent(
            role=role,
            goal=goal,
            capabilities=capabilities,
            tools=tools,
            parent_id=parent_id,
            **kwargs,
        )

    def pause_agent(self, agent_id: str, reason: Optional[str] = None) -> Optional[Any]:
        """Pauses an active agent and captures its execution frame."""
        return self.lifecycle_manager.pause_agent(agent_id, reason=reason)

    def resume_agent(
        self,
        agent_id: str,
        input_updates: Optional[str] = None,
        feedback: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resumes a paused agent from its execution frame with optional input/feedback."""
        return self.lifecycle_manager.resume_agent(agent_id, input_updates=input_updates, feedback=feedback)

    def terminate_agent(
        self,
        agent_id: str,
        reason: Optional[str] = None,
        cascade: bool = True,
    ) -> List[str]:
        """Terminates an agent and optionally cascades termination to child subagents."""
        return self.lifecycle_manager.terminate_agent(agent_id, reason=reason, cascade=cascade)

    def delegate(
        self,
        from_agent_id: str,
        to_agent_id: str,
        subtask_objective: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Delegates a subtask from one agent to another with result tracking."""
        return self.lifecycle_manager.delegate(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            subtask_objective=subtask_objective,
            context=context,
        )

    def handoff(
        self,
        from_agent_id: str,
        to_agent_id: str,
        reason: str,
        state_transfer: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Executes a stateful handoff transferring context, diffs, and hypotheses."""
        return self.lifecycle_manager.handoff(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            reason=reason,
            state_transfer=state_transfer,
        )

    def list_live_agents(self) -> List[Dict[str, Any]]:
        """Lists all active and registered live agents."""
        return self.lifecycle_manager.list_active_agents()

    def health_check_tools(self, tool_name: Optional[str] = None) -> Dict[str, Any]:
        """Runs health diagnostics across registered tools and servers."""
        if hasattr(self.unified_dispatcher, "health_check"):
            return self.unified_dispatcher.health_check(name=tool_name)
        elif hasattr(self.tool_registry, "health_check"):
            return self.tool_registry.health_check(name=tool_name)
        return {}

    # ==========================================
    # LANGGRAPH STATEGRAPH CONSTRUCTION
    # ==========================================
    def _build_langgraph_workflow(self):
        workflow = StateGraph(OrchestratorGraphState)

        # 1. Add Workflow Nodes
        workflow.add_node("discovery_node", self._node_discovery)
        workflow.add_node("understand_node", self._node_understand)
        workflow.add_node("decompose_node", self._node_decompose)
        workflow.add_node("execute_subtask_node", self._node_execute_subtask)
        workflow.add_node("reviewer_node", self._node_reviewer)
        workflow.add_node("replan_node", self._node_replan)

        # 2. Add Graph Edges
        workflow.add_edge(START, "discovery_node")
        workflow.add_edge("discovery_node", "understand_node")
        workflow.add_edge("understand_node", "decompose_node")
        workflow.add_edge("decompose_node", "execute_subtask_node")

        # 3. Dynamic Loop Edge for Subtask Execution
        workflow.add_conditional_edges(
            "execute_subtask_node",
            self._routing_condition_after_subtask,
            {
                "continue_subtasks": "execute_subtask_node",
                "proceed_to_review": "reviewer_node",
            },
        )

        # 4. Conditional Edge after Reviewer
        workflow.add_conditional_edges(
            "reviewer_node",
            self._routing_condition_after_review,
            {
                "pass": END,
                "replan": "replan_node",
                "max_iterations_reached": END,
            },
        )

        # 5. Dynamic Loop from Re-plan node back to Subtask Executor
        workflow.add_edge("replan_node", "execute_subtask_node")

        return workflow.compile()

    # ==========================================
    # LANGGRAPH NODE STEP IMPLEMENTATIONS
    # ==========================================
    def _persist_current_state(self, state: Dict[str, Any]):
        """Snapshots the active graph state to SQLite persistence store."""
        try:
            ostate = self._graph_to_state(state)  # type: ignore
            self.state_store.save_session(self.active_session_id, ostate)
        except Exception as e:
            self.on_event("PERSIST ERROR", f"Failed to persist state snapshot: {e}")

    def _node_discovery(self, state: OrchestratorGraphState) -> Dict[str, Any]:
        self.on_event("0. DISCOVERY", "Executing deterministic pre-flight project discovery across 10 dimensions...")
        from .runtime.project_discovery import ProjectDiscoveryEngine
        from .runtime.environment_probe import EnvironmentProbeEngine

        profile = ProjectDiscoveryEngine.discover(self.workspace)
        env_profile = EnvironmentProbeEngine.probe(
            workspace_or_path=self.workspace,
            required_env_vars=profile.required_env_vars,
            declared_deps=profile.key_dependencies,
        )

        self.on_event(
            "0. DISCOVERY",
            f"Discovered project profile: Language={profile.primary_language}, Framework={profile.framework or 'None'}, "
            f"PackageManager={profile.package_manager}, Entrypoints={len(profile.entrypoints)}, "
            f"Tests={profile.test_framework} ({profile.total_test_files} files), "
            f"CI={len(profile.ci_workflows)}, Docker={'Yes' if profile.has_docker else 'No'}, "
            f"Monorepo={'Yes' if profile.is_monorepo else 'No'}"
        )

        self.on_event(
            "0. DISCOVERY",
            f"Environment Awareness: OS={env_profile.os.system.capitalize()} ({env_profile.os.architecture}, Shell: {env_profile.os.default_shell}), "
            f"Python={env_profile.python.version} ({'venv' if env_profile.python.is_virtualenv else 'system'}), "
            f"Node={'v' + env_profile.node.version if env_profile.node.is_installed else 'None'}, "
            f"Docker={'Running' if env_profile.docker.is_daemon_running else ('Stopped' if env_profile.docker.is_installed else 'No')}, "
            f"EnvVars={len(env_profile.environment_variables.present)} present/{len(env_profile.environment_variables.missing)} missing"
        )

        if hasattr(self, "memory_engine") and self.memory_engine:
            try:
                self.memory_engine.seed_from_discovery(
                    project_profile=profile.to_dict(),
                    env_profile=env_profile.to_dict(),
                )
            except Exception:
                pass
        elif hasattr(self, "working_memory") and self.working_memory:
            try:
                self.working_memory.set("project_profile", profile.to_dict())
                self.working_memory.set("environment_profile", env_profile.to_dict())
            except Exception:
                pass

        # Reproducibility Snapshot (Issue #93)
        execution_snapshot_dict = None
        try:
            from .reproducibility.recorder import ReproducibilityRecorder
            if not hasattr(self, "reproducibility_recorder") or not self.reproducibility_recorder:
                self.reproducibility_recorder = ReproducibilityRecorder(default_seed=getattr(self.cfg, "seed", None))
            
            snap = self.reproducibility_recorder.build_snapshot(
                session_id=self.active_session_id,
                workspace_path=self.workspace.root_dir,
                skill_registry=self.skill_registry,
                tool_registry=self.tool_registry,
                mcp_manager=self.mcp_manager,
                seed=getattr(self.cfg, "seed", None),
            )
            self.current_snapshot = snap
            execution_snapshot_dict = snap.to_dict()
            self.on_event(
                "0. DISCOVERY",
                f"Reproducibility Snapshot: ID={snap.snapshot_id}, "
                f"Fingerprint={snap.manifest_hash[:12]}..., GitDirty={snap.git_snapshot.is_dirty if snap.git_snapshot else False}, "
                f"Skills={len(snap.skills)}, Tools={len(snap.tools)}"
            )
            if hasattr(self, "artifact_store") and self.artifact_store and hasattr(self.artifact_store, "put"):
                from .persistence.artifact_store import ArtifactCategory
                self.artifact_store.put(
                    category=ArtifactCategory.REPORTS,
                    name=f"reproducibility_{self.active_session_id}.json",
                    content=snap.to_dict(),
                    session_id=self.active_session_id,
                    metadata={"snapshot_id": snap.snapshot_id, "manifest_hash": snap.manifest_hash},
                )
        except Exception as e:
            self.on_event("0. DISCOVERY", f"Notice: Snapshot recording skipped: {e}")

        updates = {
            "project_profile": profile.to_dict(),
            "environment_profile": env_profile.to_dict(),
            "execution_snapshot": execution_snapshot_dict,
            "status": "IN_PROGRESS",
        }
        self._persist_current_state({**state, **updates})
        return updates

    def _node_understand(self, state: OrchestratorGraphState) -> Dict[str, Any]:
        self.on_event("1. UNDERSTAND", "Executing concurrent multi-domain analysis across Backend, Frontend, Database, and Security...")
        matrix = self.domain_analyzer.analyze_in_parallel(state["user_request"], model=self.cfg.default_model)

        # Baseline Test Suite Discovery & Pre-flight Execution
        from .runtime.test_detector import ExistingTestDetector
        baseline_info = None
        try:
            test_ctx = ExistingTestDetector.scan(self.workspace)
            if test_ctx.has_existing_tests:
                self.on_event("TEST BASELINE", f"Discovered existing {test_ctx.test_framework} test suite ({test_ctx.total_test_files} files). Running pre-flight baseline...")
                baseline_res = ExistingTestDetector.run_baseline(self.workspace, sandbox=getattr(self.tool_registry, "sandbox", None))
                baseline_info = {
                    "has_existing_tests": True,
                    "test_framework": test_ctx.test_framework,
                    "total_test_files": test_ctx.total_test_files,
                    "authoritative_ci_command": test_ctx.authoritative_ci_command,
                    "baseline_execution": baseline_res,
                }
                status_str = "PASSED" if baseline_res.get("passed") else "FAILED"
                self.on_event("TEST BASELINE", f"Baseline test suite execution {status_str} (Exit code: {baseline_res.get('exit_code')}).")
        except Exception as e:
            self.on_event("TEST BASELINE", f"Notice: Baseline test discovery/execution skipped: {e}")

        updates = {
            "task_understanding": matrix.to_dict(),
            "status": "IN_PROGRESS",
            "baseline_test_info": baseline_info,
        }
        self._persist_current_state({**state, **updates})
        return updates


    def _node_decompose(self, state: OrchestratorGraphState) -> Dict[str, Any]:
        self.on_event("2. DECOMPOSE", "Dynamically determining required capabilities and synthesizing execution DAG...")
        
        # Ingest real environment context via Retrieval Hierarchy (Levels 4 & 5: Architecture & PageRank Repo Map)
        from .context.retrieval_hierarchy import RetrievalHierarchyEngine, HierarchyBudgetConfig, RetrievalLevel
        hierarchy_engine = RetrievalHierarchyEngine(
            workspace=self.workspace,
            code_graph=getattr(self.tool_registry, "code_graph", None),
            semantic_index=getattr(self.tool_registry, "semantic_index", None),
            arch_analyzer=getattr(self.tool_registry, "arch_analyzer", None),
            cbm=getattr(self.tool_registry, "cbm", None),
            fallback_engine=getattr(self.tool_registry, "fallback_engine", None),
        )
        hierarchy_bundle = hierarchy_engine.retrieve_hierarchy(
            query=state["user_request"],
            budget_config=HierarchyBudgetConfig(total_budget=3000),
            include_levels=[RetrievalLevel.LEVEL_4_ARCHITECTURE_KNOWLEDGE, RetrievalLevel.LEVEL_5_BROADER_REPOSITORY],
        )
        hierarchical_repo_context = hierarchy_bundle.to_markdown()

        available_agents = [
            {"name": a.name, "description": a.role_description, "capabilities": a.capabilities, "tools": a.tools}
            for a in self.agent_registry.list_agents()
        ]

        # Use rich ProjectProfile and EnvironmentProfile context
        from .runtime.project_discovery import ProjectProfile
        from .runtime.environment_probe import EnvironmentProfile

        profile_dict = state.get("project_profile")
        if profile_dict:
            try:
                profile_obj = ProjectProfile(**profile_dict)
                project_context = profile_obj.to_prompt_context()
                if hierarchical_repo_context:
                    project_context += f"\n\n{hierarchical_repo_context}"
            except Exception:
                project_context = json.dumps(profile_dict, indent=2)
        else:
            project_context = hierarchical_repo_context or "- Codebase Outline: Initializing"

        env_dict = state.get("environment_profile")
        env_context = ""
        if env_dict:
            try:
                env_obj = EnvironmentProfile.from_dict(env_dict)
                env_context = f"\n\n{env_obj.to_prompt_context()}"
            except Exception:
                env_context = f"\n\nDynamic Environment:\n{json.dumps(env_dict, indent=2)}"

        memory_context = ""
        if hasattr(self, "memory_engine") and self.memory_engine:
            try:
                primed = self.memory_engine.prime_context_for_task({"query": state["user_request"]})
                mem_parts = []
                if primed.get("episodic_experience"):
                    mem_parts.append(f"Past Experiences:\n{primed['episodic_experience']}")
                if primed.get("semantic_knowledge"):
                    mem_parts.append(f"Semantic Knowledge:\n{primed['semantic_knowledge']}")
                if mem_parts:
                    memory_context = f"\n\nHistorical Memory Context:\n" + "\n".join(mem_parts)
            except Exception:
                pass

        prompt = f"""
Analyze the user request and task understanding:
Task Goal: "{state['user_request']}"
Task Understanding: {json.dumps(state.get('task_understanding') or {}, indent=2)}

Project Discovery Context (Ground Truth):
{project_context}{env_context}{memory_context}

Available Specialized Agent Manifests in Registry:
{json.dumps(available_agents, indent=2)}


INSTRUCTIONS:
Do NOT produce an unexecutable abstract waterfall.
Produce a structured, capability-driven DAG of concrete Executable Tasks.
Each task must have explicit inputs, expected outputs, tools, capabilities, and automated acceptance tests.

Return a JSON array of executable tasks:
[
  {{
    "task_id": "T-01",
    "objective": "Clear action-oriented goal (e.g. 'Implement LRUCache in cache.py')",
    "dependencies": [],
    "required_capabilities": ["code-generation", "data-structures"],
    "required_tools": ["filesystem", "terminal"],
    "preferred_skills": ["code-simplification"],
    "inputs": ["src/cache.py"],
    "outputs": ["src/cache.py:LRUCache"],
    "acceptance_tests": ["pytest tests/test_cache.py"],
    "permissions": {{
      "allowed_read_paths": ["*"],
      "allowed_write_paths": ["src/*", "tests/*"],
      "allowed_commands": []
    }},
    "timeout_seconds": 180,
    "max_turns": 15
  }}
]
Respond ONLY with the JSON array of tasks.
"""
        messages = [{"role": "system", "content": "You are the Intelligent Task Decomposer & Capability DAG Planner."}, {"role": "user", "content": prompt}]
        result = self.llm.chat_json(messages, model=self.cfg.default_model, temperature=0.2)
        raw_tasks = result if isinstance(result, list) else (result.get("tasks") or result.get("subtasks") or result.get("steps") or [])
        if not raw_tasks:
            # Fallback sane default capability sequence
            raw_tasks = [
                {
                    "task_id": "T-01",
                    "objective": "Implement Core Deliverables",
                    "description": state["user_request"],
                    "required_capabilities": ["code-generation", "refactoring"],
                    "required_tools": ["filesystem", "terminal"],
                    "preferred_skills": ["code-simplification"],
                    "inputs": [],
                    "outputs": [],
                    "acceptance_tests": [],
                    "dependencies": [],
                },
                {
                    "task_id": "T-02",
                    "objective": "Automated Verification & Tests",
                    "description": "Verify deliverables and execute tests",
                    "required_capabilities": ["testing", "unit-tests"],
                    "required_tools": ["filesystem", "terminal"],
                    "preferred_skills": ["test-driven-development"],
                    "inputs": [],
                    "outputs": [],
                    "acceptance_tests": [],
                    "dependencies": ["T-01"],
                }
            ]

        # Build and validate TaskDAG
        task_dag = TaskDAG.from_list(raw_tasks)
        if task_dag.detect_cycles():
            self.on_event("DAG ERROR", "Cycle detected in synthesized plan! Cyclic plans cannot execute safely.")
            raise ValueError("Cyclic dependency detected in synthesized task DAG. Plan execution aborted.")

        # Gate 2: Semantic Plan Validation against Architecture & Specification
        spec_data = state.get("specification_output")
        arch_data = state.get("architecture_output")
        if spec_data or arch_data:
            try:
                from .runtime.semantic_plan_validator import SemanticPlanValidator
                plan_audit = SemanticPlanValidator.verify_plan_against_spec_and_arch(
                    spec=spec_data,
                    arch=arch_data,
                    tasks=task_dag.list_tasks(),
                )
                self.on_event("PLAN_SEMANTIC_ALIGNMENT", f"{plan_audit.summary()}")
                if hasattr(self, "event_bus") and self.event_bus:
                    try:
                        self.event_bus.publish(
                            "PLAN_SEMANTIC_ALIGNMENT",
                            payload=plan_audit.to_dict(),
                        )
                    except Exception:
                        pass

                if not plan_audit.is_aligned and plan_audit.missing_items:
                    self.on_event("COMPENSATORY TASK INJECTION", f"Plan omitted {len(plan_audit.missing_items)} components/requirements. Auto-injecting compensatory tasks...")
                    comp_tasks = SemanticPlanValidator.generate_compensatory_tasks(plan_audit.missing_items)
                    for ct in comp_tasks:
                        from .runtime.task_graph import ExecutableTask
                        task_dag.add_task(ExecutableTask.from_dict(ct))
            except Exception as audit_err:
                self.on_event("PLAN_AUDIT_ERROR", f"Notice: Semantic plan validation error: {audit_err}")

        # Propagate execution_id to all tasks in the DAG
        for t in task_dag.list_tasks():
            if not t.execution_id:
                t.execution_id = getattr(self, "active_execution_id", self.active_session_id)

        subtasks_list = task_dag.to_list()
        self.on_event("DYNAMIC CAPABILITY PLAN", f"Synthesized {len(subtasks_list)} executable DAG tasks: {[s.get('task_id') + ': ' + s.get('objective', '') for s in subtasks_list]}")
        for s in subtasks_list:
            if hasattr(self, "event_bus") and self.event_bus:
                try:
                    self.event_bus.publish(
                        "TASK_CREATED",
                        task_id=s.get("task_id"),
                        payload=s,
                    )
                except Exception:
                    pass
        updates = {
            "task_decomposition": subtasks_list,
            "subtasks": subtasks_list,
            "current_subtask_index": 0,
            "completed_subtasks": [],
            "step_results": {},
        }
        self._persist_current_state({**state, **updates})
        return updates

    def _node_execute_subtask(self, state: OrchestratorGraphState) -> Dict[str, Any]:
        subtasks_data = state.get("subtasks") or state.get("task_decomposition") or []
        task_dag = TaskDAG.from_list(subtasks_data)

        # Iteration baseline checkpoint before changes are applied
        current_iter = state.get("iteration", 0)
        iter_baseline = f"ckpt-iter-{current_iter}-baseline"
        if iter_baseline not in self.checkpoint_manager.list_snapshots():
            try:
                self.checkpoint_manager.create_snapshot(
                    checkpoint_id=iter_baseline,
                    workspace=self.workspace,
                    stage="ITERATION_BASELINE",
                    metadata={"iteration": current_iter, "session_id": self.active_session_id},
                )
            except Exception:
                pass

        # Check session budget before executing wave (Issue #35)
        from .cost.budget_tracker import budget_tracker, BudgetExceededError
        try:
            budget_tracker.assert_budget()
        except BudgetExceededError as bee:
            self.on_event("BUDGET EXCEEDED", f"Session budget limit reached: {str(bee)}")
            return {
                "status": "STOPPED",
                "errors": [str(bee)],
            }

        wave_updates = self.dag_scheduler.execute_ready_wave(
            task_dag=task_dag,
            orchestrator=self,
            state_data=state,
        )

        ostate = self._graph_to_state(state)
        updates: Dict[str, Any] = {
            "subtasks": wave_updates["subtasks"],
            "task_decomposition": wave_updates["task_decomposition"],
            "current_subtask_index": wave_updates["current_subtask_index"],
            "completed_subtasks": wave_updates["completed_subtasks"],
            "step_results": wave_updates["step_results"],
            "messages": [m.__dict__ if hasattr(m, "__dict__") else m for m in ostate.messages],
        }

        # Map known outputs for backward compatibility and persist into ArtifactStore
        for name, res in wave_updates["step_results"].items():
            name_lower = name.lower()
            if "plan" in name_lower:
                updates["plan_output"] = res
                if self.artifact_store:
                    try:
                        self.artifact_store.put("plans", f"plan_{name}.json", res, session_id=self.active_session_id)
                    except Exception:
                        pass
            elif "spec" in name_lower or "contract" in name_lower:
                updates["specification_output"] = res
                if self.artifact_store:
                    try:
                        self.artifact_store.put("specifications", f"spec_{name}.json", res, session_id=self.active_session_id)
                    except Exception:
                        pass
            elif "arch" in name_lower or "topology" in name_lower:
                updates["architecture_output"] = res
                if self.artifact_store:
                    try:
                        self.artifact_store.put("specifications", f"arch_{name}.json", res, session_id=self.active_session_id)
                    except Exception:
                        pass
                spec_cand = updates.get("specification_output") or state.get("specification_output")
                if spec_cand:
                    try:
                        from .runtime.semantic_plan_validator import SemanticPlanValidator
                        arch_audit = SemanticPlanValidator.verify_architecture_against_spec(spec_cand, res)
                        self.on_event("ARCH_SEMANTIC_ALIGNMENT", f"{arch_audit.summary()}")
                        if hasattr(self, "event_bus") and self.event_bus:
                            try:
                                self.event_bus.publish("ARCH_SEMANTIC_ALIGNMENT", payload=arch_audit.to_dict())
                            except Exception:
                                pass
                    except Exception as audit_err:
                        self.on_event("ARCH_AUDIT_ERROR", f"Notice: Architecture semantic validation error: {audit_err}")
            elif "test" in name_lower or "verify" in name_lower:
                updates["test_output"] = res
                if not state.get("baseline_test_info"):
                    updates["baseline_test_info"] = res
                if self.artifact_store:
                    try:
                        self.artifact_store.put("test_results", f"test_result_{name}.json", res, session_id=self.active_session_id)
                    except Exception:
                        pass
            elif "code" in name_lower:
                updates["code_output"] = res
                if self.artifact_store:
                    try:
                        if isinstance(res, dict) and res.get("change_manifest"):
                            cm = res["change_manifest"]
                            all_diffs = []
                            for fc in cm.get("file_changes", []):
                                if fc.get("diff"):
                                    all_diffs.append(fc["diff"])
                            if all_diffs:
                                self.artifact_store.put("patches", f"patch_{name}.patch", "\n".join(all_diffs), session_id=self.active_session_id)
                        self.artifact_store.put("reports", f"code_summary_{name}.json", res, session_id=self.active_session_id)
                    except Exception:
                        pass

        self._persist_current_state({**state, **updates})
        return updates



    def _node_reviewer(self, state: OrchestratorGraphState) -> Dict[str, Any]:
        discovered_reviewers = self.agent_registry.discover(
            query="code review quality audit security",
            capabilities=["code-review", "quality-audit"],
            top_k=1,
        )
        reviewer_manifest = discovered_reviewers[0][0] if discovered_reviewers else "REVIEWER"
        agent = self.select_agent(reviewer_manifest)

        skills = [m.name for m, _ in self.skill_registry.discover(query="code review quality standards", top_k=2)]
        self.on_event("EXECUTE AGENT", f"Dispatching Quality Audit [{agent.name}]")

        ostate = self._graph_to_state(state)
        res = agent.execute(ostate, active_skills=skills)
        verdict = res.get("verdict", "FAIL").upper()
        ev_info = res.get("evidence", {})
        if ev_info and isinstance(ev_info, dict):
            b_str = "PASS" if ev_info.get("build", {}).get("success") else "FAIL"
            t_data = ev_info.get("tests", {})
            t_str = f"{t_data.get('passed', 0)}/{t_data.get('total', 0)} passed" if t_data.get("total") else "clean"
            l_str = f"{ev_info.get('lint', {}).get('errors', 0)} errors"
            ac_data = ev_info.get("acceptance_criteria", {})
            ac_str = f"{sum(1 for v in ac_data.values() if v == 'verified')}/{len(ac_data)} ACs verified" if ac_data else "N/A"
            self.on_event("5. CHECK RESULT", f"Reviewer Verdict: {verdict} | Build: {b_str} | Tests: {t_str} | Lint: {l_str} | {ac_str}")
        else:
            self.on_event("5. CHECK RESULT", f"Reviewer Verdict: {verdict} (Score: {res.get('score_out_of_100', 'N/A')}/100)")
        updates = {
            "review_output": res,
            "verdict": verdict,
            "messages": [m.__dict__ if hasattr(m, "__dict__") else m for m in ostate.messages],
        }

        # Gate 3 & 4: Semantic Implementation & Test Conformance Verification
        arch_data = state.get("architecture_output")
        spec_data = state.get("specification_output")
        semantic_audits = {}
        if arch_data:
            try:
                from .runtime.semantic_plan_validator import SemanticPlanValidator
                impl_audit = SemanticPlanValidator.verify_implementation_against_arch(arch_data, self.workspace)
                semantic_audits["implementation_alignment"] = impl_audit.to_dict()
                self.on_event("IMPL_SEMANTIC_ALIGNMENT", f"{impl_audit.summary()}")
            except Exception as e:
                self.on_event("IMPL_AUDIT_ERROR", f"Notice: Implementation audit error: {e}")

        if spec_data:
            try:
                from .runtime.semantic_plan_validator import SemanticPlanValidator
                test_audit = SemanticPlanValidator.verify_tests_against_acceptance_criteria(
                    spec=spec_data,
                    workspace=self.workspace,
                    test_results=state.get("test_output"),
                )
                semantic_audits["test_alignment"] = test_audit.to_dict()
                self.on_event("TEST_SEMANTIC_ALIGNMENT", f"{test_audit.summary()}")
            except Exception as e:
                self.on_event("TEST_AUDIT_ERROR", f"Notice: Test audit error: {e}")

        if semantic_audits:
            updates["semantic_alignment"] = semantic_audits
        if self.artifact_store:
            try:
                self.artifact_store.put(
                    category="reports",
                    name="review_output.json",
                    content=res,
                    session_id=self.active_session_id,
                    artifact_type="REVIEW_REPORT",
                )
            except Exception:
                pass
        self._persist_current_state({**state, **updates})
        return updates

    def _node_replan(self, state: OrchestratorGraphState) -> Dict[str, Any]:
        current_iter = state.get("iteration", 0) + 1
        self.on_event("6. RE-PLAN", f"Triggering Dynamic DAG Re-plan (Iteration {current_iter}/{state.get('max_iterations', 3)})...")

        # Check session budget before continuing replan iteration (Issue #35)
        from .cost.budget_tracker import budget_tracker, BudgetExceededError
        try:
            budget_tracker.assert_budget()
        except BudgetExceededError as bee:
            self.on_event("BUDGET EXCEEDED", f"Session budget reached during replan: {str(bee)}")
            return {
                "iteration": current_iter,
                "status": "STOPPED",
                "errors": [str(bee)],
            }

        test_info = state.get("test_output") or {}
        review_info = state.get("review_output") or {}
        baseline_test = state.get("baseline_test_info")
        prev_iter = state.get("iteration", 0)
        baseline_ckpt = f"ckpt-iter-{prev_iter}-baseline"

        # Ingest empirical failure signals across active DAG tasks
        subtasks_data = state.get("subtasks") or state.get("task_decomposition") or []
        task_dag = TaskDAG.from_list(subtasks_data)
        failed_tasks = task_dag.get_failed_tasks()

        stderr_parts: List[str] = []
        if test_info.get("stderr"):
            stderr_parts.append(str(test_info["stderr"]))
        for ft in failed_tasks:
            if ft.error_message:
                stderr_parts.append(f"Task [{ft.task_id}] Error: {ft.error_message}")
            for attempt in getattr(ft, "attempts", []):
                if getattr(attempt, "errors", None):
                    stderr_parts.extend(attempt.errors)
                v_res = getattr(attempt, "verification_result", {})
                if isinstance(v_res, dict) and v_res.get("stderr"):
                    stderr_parts.append(v_res["stderr"])
        combined_stderr = "\n".join(stderr_parts)

        stdout_parts: List[str] = []
        if test_info.get("stdout"):
            stdout_parts.append(str(test_info["stdout"]))
        for ft in failed_tasks:
            for attempt in getattr(ft, "attempts", []):
                v_res = getattr(attempt, "verification_result", {})
                if isinstance(v_res, dict) and v_res.get("stdout"):
                    stdout_parts.append(v_res["stdout"])
        combined_stdout = "\n".join(stdout_parts)

        verification_reports: List[Dict[str, Any]] = []
        if isinstance(review_info.get("ground_truth_report"), dict):
            verification_reports.append(review_info["ground_truth_report"])
        for ft in failed_tasks:
            for attempt in getattr(ft, "attempts", []):
                if getattr(attempt, "verification_result", None) and isinstance(attempt.verification_result, dict):
                    verification_reports.append(attempt.verification_result)

        # Run Root-cause Failure Diagnostician with regression detection using REASONING model tier
        from .routing.model_router import model_router, ModelTier
        diag_model = model_router.get_tier_model(ModelTier.REASONING)
        diag = self.diagnostician.diagnose_failure(
            task_title="Quality Audit & Verification",
            execution_stderr=combined_stderr,
            execution_stdout=combined_stdout,
            codebase_context=self.tool_registry.code_graph.get_codebase_map(),
            model=diag_model,
            baseline_test_info=baseline_test,
            current_test_info=test_info,
            review_info=review_info,
            default_rollback_target=baseline_ckpt,
            failed_tasks=failed_tasks,
            verification_reports=verification_reports,
            workspace_dir=str(self.workspace.root_dir),
        )

        if diag.attribution_override:
            self.on_event("ADVERSARIAL ATTRIBUTION VETO", f"{diag.attribution_override.get('rationale')}")

        target_agent = diag.target_agent.upper()
        self.on_event("6. RE-PLAN", f"Diagnosed Root Cause: {diag.root_cause_summary} -> Routing fix to: {target_agent}")
        if hasattr(self, "event_bus") and self.event_bus:
            try:
                self.event_bus.publish(
                    "REPLAN_STARTED",
                    payload={"iteration": current_iter, "root_cause": diag.root_cause_summary, "target_agent": target_agent},
                )
            except Exception:
                pass

        # Check for rollback requirement
        rollback_executed = False
        rollback_details = None

        if diag.should_rollback:
            target_ckpt = diag.rollback_target or baseline_ckpt
            snapshots = self.checkpoint_manager.list_snapshots()
            if target_ckpt not in snapshots:
                target_ckpt = baseline_ckpt if baseline_ckpt in snapshots else (snapshots[-1] if snapshots else None)

            if target_ckpt:
                try:
                    res = self.checkpoint_manager.rollback_to_checkpoint(target_ckpt, self.workspace)
                    if hasattr(self.workspace, "clear_change_manifest"):
                        self.workspace.clear_change_manifest()
                    rollback_executed = True
                    rollback_details = {
                        "checkpoint_id": target_ckpt,
                        "reasons": diag.reasons_for_rollback,
                        "restored_files": res.get("restored_files", []),
                        "deleted_files": res.get("deleted_files", []),
                        "timestamp": datetime.now().isoformat(),
                    }
                    try:
                        self.state_store.save_rollback_event(
                            session_id=self.active_session_id,
                            task_id=f"iter-{current_iter}",
                            checkpoint_id=target_ckpt,
                            trigger_reason=f"Regression: {'; '.join(diag.reasons_for_rollback)}",
                            restored_files=res.get("restored_files", []),
                            deleted_files=res.get("deleted_files", []),
                        )
                    except Exception:
                        pass
                    self.on_event(
                        "TRANSACTION ROLLBACK",
                        f"Regression detected! Rolled back workspace to '{target_ckpt}' (Restored: {len(res.get('restored_files', []))} files, Deleted: {len(res.get('deleted_files', []))} files)",
                    )
                except Exception as e:
                    self.on_event("ROLLBACK WARNING", f"Failed to execute rollback to '{target_ckpt}': {e}")

        replan_records = list(state.get("replan_history") or [])
        replan_records.append({
            "iteration": current_iter,
            "trigger_reason": f"Failure at Review on iteration {current_iter - 1}",
            "feedback_summary": review_info.get("summary", "Defects found"),
            "remediation_plan": diag.suggested_remediation,
            "rollback_executed": rollback_executed,
            "rollback_details": rollback_details,
            "timestamp": datetime.now().isoformat(),
        })

        # Circuit Breaker for CONTRACT_MISMATCH and INFRASTRUCTURE_ERROR
        if diag.failure_type in ("CONTRACT_MISMATCH", "INFRASTRUCTURE_ERROR"):
            self.on_event(
                "CIRCUIT BREAKER",
                f"Infrastructure/Contract failure detected: {diag.root_cause_summary}. Halting workspace code remediation to prevent infinite loop.",
            )
            # Reconcile contract at source & rerun original task
            subtasks_data = state.get("subtasks") or state.get("task_decomposition") or []
            task_dag = TaskDAG.from_list(subtasks_data)
            failed_tasks = task_dag.get_failed_tasks()

            can_reconcile = False
            for ft in failed_tasks:
                if ft.retry_policy.current_retry < ft.retry_policy.max_retries:
                    ft.retry_policy.current_retry += 1
                    ft.state = TaskState.READY
                    can_reconcile = True

            if can_reconcile:
                self.on_event(
                    "CONTRACT RECONCILIATION",
                    f"Reconciled contract at source for Task(s): {[t.task_id for t in failed_tasks]}. Re-queueing original task for execution.",
                )
                updated_subtasks = task_dag.to_list()
                updates = {
                    "iteration": current_iter,
                    "subtasks": updated_subtasks,
                    "task_decomposition": updated_subtasks,
                    "current_subtask_index": 0,
                    "remediation_plan": diag.suggested_remediation,
                    "replan_history": replan_records,
                    "target_agent_for_fix": "SYSTEM",
                    "rollback_executed": False,
                    "last_rollback": None,
                }
                self._persist_current_state({**state, **updates})
                return updates
            else:
                self.on_event(
                    "CIRCUIT BREAKER HALT",
                    f"Contract/Infrastructure failure cannot be recovered automatically or max retries exceeded. Halting workflow.",
                )
                updated_subtasks = task_dag.to_list()
                updates = {
                    "iteration": current_iter,
                    "subtasks": updated_subtasks,
                    "task_decomposition": updated_subtasks,
                    "current_subtask_index": len(updated_subtasks),
                    "remediation_plan": diag.suggested_remediation,
                    "replan_history": replan_records,
                    "target_agent_for_fix": "SYSTEM",
                    "status": TaskStatus.STOPPED.value,
                    "rollback_executed": False,
                    "last_rollback": None,
                }
                self._persist_current_state({**state, **updates})
                return updates

        # Load existing DAG and execute epistemic replanning
        subtasks_data = state.get("subtasks") or state.get("task_decomposition") or []
        task_dag = TaskDAG.from_list(subtasks_data)

        wm = getattr(self, "working_memory", None)
        replan_res = self.replan_engine.replan(
            task_dag=task_dag,
            diagnostic=diag,
            working_memory=wm,
            iteration=current_iter,
            rollback_executed=rollback_executed,
            rollback_details=rollback_details,
            trigger_reason=f"Failure at Review on iteration {current_iter - 1}",
            feedback_summary=review_info.get("summary") or diag.root_cause_summary,
        )
        updated_subtasks = task_dag.to_list()

        # Update replan audit history with full epistemic analysis
        if replan_records:
            replan_records.pop()
        replan_records.append(replan_res.replan_record)

        if replan_res.pruned_task_ids:
            self.on_event(
                "TRANSITIVE TASK INVALIDATION",
                f"Epistemic replanning pruned {len(replan_res.pruned_task_ids)} stale/invalid task(s): {replan_res.pruned_task_ids}",
            )

        self.on_event(
            "DYNAMIC RE-PLAN INJECTION",
            f"Epistemic replanning injected {len(replan_res.injected_tasks)} task(s) into TaskDAG. Parallel groups: {replan_res.parallel_groups}",
        )
        if hasattr(self, "event_bus") and self.event_bus:
            try:
                self.event_bus.publish(
                    "REPLAN_COMPLETED",
                    payload={
                        "iteration": current_iter,
                        "injected_tasks": [t.task_id for t in replan_res.injected_tasks],
                        "pruned_tasks": replan_res.pruned_task_ids,
                        "parallel_groups": replan_res.parallel_groups,
                    },
                )
            except Exception:
                pass

        updates = {
            "iteration": current_iter,
            "subtasks": updated_subtasks,
            "task_decomposition": updated_subtasks,
            "current_subtask_index": 0,
            "remediation_plan": diag.suggested_remediation,
            "replan_history": replan_records,
            "target_agent_for_fix": target_agent,
            "rollback_executed": rollback_executed,
            "last_rollback": rollback_details,
        }
        self._persist_current_state({**state, **updates})
        return updates

    # ==========================================
    # CONDITIONAL EDGE ROUTING
    # ==========================================
    def _routing_condition_after_subtask(self, state: OrchestratorGraphState) -> str:
        subtasks_data = state.get("subtasks") or state.get("task_decomposition") or []
        task_dag = TaskDAG.from_list(subtasks_data)

        if task_dag.is_all_completed():
            return "proceed_to_review"

        ready_tasks = task_dag.get_ready_tasks()
        if ready_tasks:
            return "continue_subtasks"

        # If has failures and no ready tasks left, proceed to review/replan
        return "proceed_to_review"

    def _routing_condition_after_review(self, state: OrchestratorGraphState) -> str:
        verdict = state.get("verdict", "FAIL").upper()
        iteration = state.get("iteration", 0)
        max_iter = state.get("max_iterations", self.cfg.max_replan_iterations)

        if "PASS" in verdict:
            self.on_event("SUCCESS", f"Task workflow successfully PASSED on iteration {iteration}!")
            return "pass"
        if iteration >= max_iter:
            self.on_event("MAX_ITERATIONS", f"Reached maximum re-plan iterations ({max_iter}). Halting workflow.")
            return "max_iterations_reached"
        return "replan"

    # Public helper methods for unit testing / direct invocation
    def understand_task(self, state: OrchestratorState) -> Dict[str, Any]:
        res = self._node_understand({"user_request": state.user_request, "task_understanding": None})
        state.task_understanding = res.get("task_understanding")
        return state.task_understanding or {}

    def decompose_task(self, state: OrchestratorState) -> List[Dict[str, Any]]:
        res = self._node_decompose({
            "user_request": state.user_request,
            "task_understanding": state.task_understanding,
        })
        state.task_decomposition = res.get("task_decomposition")
        if state.task_decomposition:
            state.task_dag = TaskDAG.from_list(state.task_decomposition)
        return state.task_decomposition or []


    @staticmethod
    def resolve_terminal_status(
        verdict: Union[ReviewVerdict, str],
        task_dag: Optional[TaskDAG] = None,
        current_iteration: int = 0,
        max_iterations: int = 3,
        has_failures: bool = False,
    ) -> TaskStatus:
        """
        Deterministically resolves the final workflow TaskStatus from the review verdict,
        task DAG completion state, and iteration limits.

        Production Rules:
        1. COMPLETED: ONLY when verdict is PASS (verified successful).
        2. UNDECIDED: MUST NEVER BE COMPLETED.
           - If iteration >= max_iterations: STOPPED
           - If tasks remain uncompleted/in-progress: INCOMPLETE
           - If tasks finished but unverified: NEED_VERIFICATION
        3. FAIL:
           - If iteration >= max_iterations: STOPPED
           - Otherwise: FAILED
        """
        if isinstance(verdict, str):
            v_upper = verdict.upper()
            if "PASS" in v_upper:
                verdict = ReviewVerdict.PASS
            elif "FAIL" in v_upper:
                verdict = ReviewVerdict.FAIL
            else:
                verdict = ReviewVerdict.UNDECIDED

        # 1. Verified Success: ONLY when verdict is PASS
        if verdict == ReviewVerdict.PASS:
            return TaskStatus.COMPLETED

        # 2. Iteration exhaustion halts workflow
        if current_iteration >= max_iterations:
            return TaskStatus.STOPPED

        # 3. Definite Failure
        if verdict == ReviewVerdict.FAIL:
            return TaskStatus.FAILED

        # 4. UNDECIDED: MUST NEVER BE COMPLETED
        if task_dag and not task_dag.is_all_completed():
            return TaskStatus.INCOMPLETE

        # All tasks ran or no DAG, but verification was inconclusive or never conducted
        return TaskStatus.NEED_VERIFICATION

    # ==========================================
    # CONVERTER HELPER
    # ==========================================
    def _graph_to_state(self, state: OrchestratorGraphState) -> OrchestratorState:
        ostate = OrchestratorState(user_request=state["user_request"])
        ostate.project_profile = state.get("project_profile")
        ostate.environment_profile = state.get("environment_profile")
        ostate.task_understanding = state.get("task_understanding")
        ostate.task_decomposition = state.get("task_decomposition") or state.get("subtasks")
        if ostate.task_decomposition:
            ostate.task_dag = TaskDAG.from_list(ostate.task_decomposition)
        ostate.plan_output = state.get("plan_output")
        ostate.specification_output = state.get("specification_output")
        ostate.architecture_output = state.get("architecture_output")
        ostate.code_output = state.get("code_output")
        ostate.test_output = state.get("test_output")
        ostate.review_output = state.get("review_output")
        ostate.baseline_test_output = state.get("baseline_test_info")
        ostate.artifact_store = getattr(self, "artifact_store", None)
        ostate.event_bus = getattr(self, "event_bus", None)


        ostate.current_iteration = state.get("iteration", 0)
        verdict_str = state.get("verdict", "UNDECIDED").upper()
        if "PASS" in verdict_str:
            ostate.verdict = ReviewVerdict.PASS
        elif "FAIL" in verdict_str:
            ostate.verdict = ReviewVerdict.FAIL
        else:
            ostate.verdict = ReviewVerdict.UNDECIDED

        # Restore messages
        raw_msgs = state.get("messages") or []
        ostate.messages = [
            m if isinstance(m, AgentMessage) else AgentMessage(
                agent_name=m.get("agent_name", "UNKNOWN"),
                stage=m.get("stage", "UNKNOWN"),
                content=m.get("content", ""),
                structured_data=m.get("structured_data"),
                timestamp=m.get("timestamp", datetime.now().isoformat()),
            )
            for m in raw_msgs
        ]

        # Restore replan history
        raw_replan = state.get("replan_history") or []
        ostate.replan_history = [
            r if isinstance(r, ReplanRecord) else ReplanRecord(
                iteration=r.get("iteration", 0),
                trigger_reason=r.get("trigger_reason", ""),
                feedback_summary=r.get("feedback_summary", ""),
                remediation_plan=r.get("remediation_plan", []),
                rollback_executed=r.get("rollback_executed", False),
                rollback_details=r.get("rollback_details"),
                timestamp=r.get("timestamp", datetime.now().isoformat()),
                invalid_assumptions=r.get("invalid_assumptions", []),
                missing_evidence=r.get("missing_evidence", []),
                invalid_task_ids=r.get("invalid_task_ids", []),
                pruned_task_ids=r.get("pruned_task_ids", []),
                injected_task_ids=r.get("injected_task_ids", []),
                parallel_groups=r.get("parallel_groups", []),
            )
            for r in raw_replan
        ]
        ostate.rollback_history = [
            r.get("rollback_details")
            for r in raw_replan
            if isinstance(r, dict) and r.get("rollback_executed") and r.get("rollback_details")
        ]

        # Aggregate runtime telemetry across tasks in DAG
        if ostate.task_dag:
            tot_tokens = TokenUsage()
            tot_cost = 0.0
            tot_duration = 0.0
            for t in ostate.task_dag.list_tasks():
                tot_tokens.add(t.token_usage)
                tot_cost += t.cost
                tot_duration += t.duration_seconds
            ostate.total_token_usage = tot_tokens
            ostate.total_cost_usd = tot_cost
            ostate.total_duration_seconds = tot_duration

        if hasattr(self, "telemetry_engine") and self.telemetry_engine:
            snapshot = self.telemetry_engine.get_snapshot()
            ostate.telemetry = snapshot
            ostate.telemetry_engine = self.telemetry_engine
            if hasattr(self, "state_store") and self.state_store and hasattr(self.state_store, "save_telemetry_snapshot"):
                try:
                    self.state_store.save_telemetry_snapshot(self.active_session_id, snapshot)
                except Exception:
                    pass
            if hasattr(self, "artifact_store") and self.artifact_store and hasattr(self.artifact_store, "put"):
                try:
                    from .persistence.artifact_store import ArtifactCategory
                    self.artifact_store.put(
                        category=ArtifactCategory.REPORTS,
                        name="telemetry_report",
                        content=snapshot.to_dict(),
                        session_id=self.active_session_id,
                        metadata={"type": "session_telemetry_report"},
                    )
                    log_dir = getattr(self.cfg, "log_dir", None) or (Path(self.workspace.root_dir) / ".orchestrator" / "logs")
                    session_log_path = Path(log_dir) / f"session_{self.active_session_id}.jsonl"
                    if session_log_path.exists():
                        try:
                            with open(session_log_path, "r", encoding="utf-8") as lf:
                                log_content = lf.read()
                            if log_content:
                                self.artifact_store.put(
                                    category=ArtifactCategory.LOGS,
                                    name=f"session_{self.active_session_id}.jsonl",
                                    content=log_content,
                                    session_id=self.active_session_id,
                                    mime_type="application/x-ndjson",
                                    metadata={"type": "session_structured_log"},
                                )
                        except Exception:
                            pass
                except Exception:
                    pass

        if hasattr(self, "tracer") and self.tracer:
            trace_id = getattr(self, "active_trace_id", None) or self.active_session_id
            trace_dict = self.tracer.export_trace_dict(trace_id)
            if trace_dict:
                ostate.trace_tree = trace_dict
                ostate.tracer = self.tracer
                if hasattr(self, "state_store") and self.state_store and hasattr(self.state_store, "save_trace"):
                    try:
                        self.state_store.save_trace(trace_id, trace_dict, session_id=self.active_session_id)
                    except Exception:
                        pass
                if hasattr(self, "artifact_store") and self.artifact_store and hasattr(self.artifact_store, "put"):
                    try:
                        from .persistence.artifact_store import ArtifactCategory
                        self.artifact_store.put(
                            category=ArtifactCategory.REPORTS,
                            name=f"session_{self.active_session_id}_trace.json",
                            content=trace_dict,
                            session_id=self.active_session_id,
                            metadata={"type": "distributed_trace", "trace_id": trace_id},
                        )
                    except Exception:
                        pass

        snap_dict = state.get("execution_snapshot")
        if snap_dict:
            try:
                from .reproducibility.manifest import ExecutionSnapshot
                if isinstance(snap_dict, ExecutionSnapshot):
                    ostate.execution_snapshot = snap_dict
                elif isinstance(snap_dict, dict):
                    ostate.execution_snapshot = ExecutionSnapshot.from_dict(snap_dict)
            except Exception:
                ostate.execution_snapshot = snap_dict
        elif getattr(self, "current_snapshot", None):
            ostate.execution_snapshot = self.current_snapshot

        return ostate

    def get_execution_snapshot(self, session_id: Optional[str] = None) -> Optional[Any]:
        """Retrieves reproducibility snapshot for current or specified session."""
        target_id = session_id or getattr(self, "active_session_id", None)
        if target_id and self.state_store:
            snap = self.state_store.get_execution_snapshot(target_id)
            if snap:
                return snap
        return getattr(self, "current_snapshot", None)

    def compare_session_reproducibility(self, session_a: str, session_b: str) -> Dict[str, Any]:
        """Compares reproducibility snapshots between two sessions."""
        from .reproducibility.manifest import compare_snapshots
        snap_a = self.get_execution_snapshot(session_a)
        snap_b = self.get_execution_snapshot(session_b)
        if not snap_a or not snap_b:
            return {
                "match": False,
                "error": f"One or both snapshots missing: session_a={bool(snap_a)}, session_b={bool(snap_b)}",
                "differences": ["Missing snapshot for comparison"],
            }
        return compare_snapshots(snap_a, snap_b)

    def get_file_attribution(self, filepath: str, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Agent Blame: returns complete attribution history for a file."""
        if not self.state_store:
            return []
        target_session = session_id or getattr(self, "active_session_id", None)
        return self.state_store.get_file_attribution(filepath, session_id=target_session)

    def get_changes_by_model(self, model_name: str, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns all changes authored by a specific model."""
        if not self.state_store:
            return []
        target_session = session_id or getattr(self, "active_session_id", None)
        return self.state_store.get_changes_by_model(model_name, session_id=target_session)

    def get_changes_by_prompt(self, prompt_hash: str, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns all changes authored under a specific prompt template hash."""
        if not self.state_store:
            return []
        target_session = session_id or getattr(self, "active_session_id", None)
        return self.state_store.get_changes_by_prompt(prompt_hash, session_id=target_session)

    def get_changes_by_agent(self, agent_name: str, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns all changes authored by a specific agent persona."""
        if not self.state_store:
            return []
        target_session = session_id or getattr(self, "active_session_id", None)
        return self.state_store.get_changes_by_agent(agent_name, session_id=target_session)

    # ==========================================
    # RUN & RESUME ENTRYPOINTS
    # ==========================================
    def execute(self, user_request: str, session_id: Optional[str] = None) -> OrchestratorState:
        """
        Executes the LangGraph StateGraph orchestration workflow for the given user request.
        Alias for run().
        """
        return self.run(user_request, session_id=session_id)

    def run(self, user_request: str, session_id: Optional[str] = None) -> OrchestratorState:
        """
        Executes the LangGraph StateGraph workflow for the given user request.
        Persists runtime state, DAG progress, and message logs to SQLite.
        """
        self.active_session_id = session_id or getattr(self, "active_session_id", None) or f"sess-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        self.active_trace_id = self.active_session_id
        if hasattr(self, "tracer") and self.tracer:
            self.tracer.start_trace(self.active_trace_id, metadata={"session_id": self.active_session_id, "user_request": user_request})
        self.message_bus.set_session(self.active_session_id, self.state_store)
        if hasattr(self, "event_bus") and self.event_bus:
            self.event_bus.set_session(self.active_session_id, self.state_store)
        if hasattr(self, "telemetry_engine") and self.telemetry_engine:
            self.telemetry_engine.set_session(self.active_session_id)

        initial_state: OrchestratorGraphState = {
            "user_request": user_request,
            "project_profile": None,
            "environment_profile": None,
            "task_understanding": None,
            "task_decomposition": None,
            "subtasks": [],
            "current_subtask_index": 0,
            "completed_subtasks": [],
            "step_results": {},
            "plan_output": None,
            "specification_output": None,
            "architecture_output": None,
            "code_output": None,
            "test_output": None,
            "review_output": None,
            "verdict": "UNDECIDED",
            "iteration": 0,
            "max_iterations": self.cfg.max_replan_iterations,
            "remediation_plan": [],
            "replan_history": [],
            "target_agent_for_fix": "CODER",
            "status": "PENDING",
            "messages": [],
            "baseline_test_info": None,
            "rollback_executed": False,
            "last_rollback": None,
            "execution_snapshot": None,
        }

        session_log_handler = None
        if getattr(self.cfg, "log_to_file", True):
            try:
                from .logging import add_session_file_sink
                log_dir = getattr(self.cfg, "log_dir", None) or (Path(self.workspace.root_dir) / ".orchestrator" / "logs")
                session_log_handler = add_session_file_sink(self.active_session_id, log_dir=log_dir)
            except Exception:
                pass

        from .logging import LogContext, remove_session_file_sink
        with LogContext(session_id=self.active_session_id):
            try:
                self.on_event("START", f"Starting LangGraph Intelligent Orchestration for: '{user_request}' (Session: {self.active_session_id})")
                if hasattr(self, "event_bus") and self.event_bus:
                    try:
                        self.event_bus.publish(
                            "WORKFLOW_STARTED",
                            payload={"user_request": user_request, "session_id": self.active_session_id},
                        )
                    except Exception:
                        pass

                final_graph_state = self.graph.invoke(initial_state)

                # Convert final state
                final_state = self._graph_to_state(final_graph_state)
                final_state.status = self.resolve_terminal_status(
                    verdict=final_state.verdict,
                    task_dag=final_state.task_dag,
                    current_iteration=final_state.current_iteration,
                    max_iterations=final_state.max_iterations,
                )

                if hasattr(self, "event_bus") and self.event_bus:
                    try:
                        self.event_bus.publish(
                            "WORKFLOW_COMPLETED",
                            payload={
                                "verdict": final_state.verdict.value if hasattr(final_state.verdict, "value") else str(final_state.verdict),
                                "status": final_state.status.value if hasattr(final_state.status, "value") else str(final_state.status),
                                "iteration": final_state.current_iteration,
                            },
                        )
                    except Exception:
                        pass

                self.state_store.save_session(self.active_session_id, final_state)
                return final_state
            finally:
                if session_log_handler:
                    try:
                        remove_session_file_sink(session_log_handler)
                    except Exception:
                        pass

    def diagnose_session(self, session_id: str) -> SessionRecoveryReport:
        """
        Diagnoses an interrupted or crashed session, summarizing completed tasks,
        file changes, last tool invocations, and suggested resume points.
        """
        return SessionRecoveryEngine.diagnose_session(session_id, self.state_store)

    def list_recoverable_sessions(self) -> List[Dict[str, Any]]:
        """
        Lists all recorded orchestration sessions eligible for resumption.
        """
        return SessionRecoveryEngine.list_recoverable_sessions(self.state_store)

    def resume(self, target_id: str) -> Union[OrchestratorState, Dict[str, Any]]:
        """
        Resumes an in-progress or interrupted orchestration session or individual task.
        If target_id is a task_id, resumes the specific task from its latest step checkpoint.
        If target_id is a session_id, resumes the entire DAG workflow from the last wave.
        """
        session_for_task = self.state_store.find_session_for_task(target_id)
        is_direct_session = bool(self.state_store.load_session(target_id))

        if session_for_task and not is_direct_session:
            return self.resume_task(task_id=target_id, session_id=session_for_task)

        return self.resume_workflow(session_id=target_id)

    def resume_workflow(self, session_id: str) -> OrchestratorState:
        """
        Durable workflow resumption: reconciles crashed/interrupted task states,
        rebuilds graph state from SQLite, cleans stale sandboxes, and resumes DAG execution.
        """
        self.active_session_id = session_id
        self.message_bus.set_session(session_id, self.state_store)
        self.message_bus.load_history_from_store(session_id)
        if hasattr(self, "event_bus") and self.event_bus:
            self.event_bus.set_session(session_id, self.state_store)
        if hasattr(self, "telemetry_engine") and self.telemetry_engine:
            self.telemetry_engine.set_session(session_id)

        loaded_state = self.state_store.load_session(session_id)
        if not loaded_state:
            raise ValueError(f"Session '{session_id}' not found in SQLite store.")

        # Cleanup any orphaned sandboxes from prior crash
        SessionRecoveryEngine.cleanup_stale_sandboxes(
            workspace_dir=str(self.workspace.root_dir),
            max_age_seconds=1800,
        )

        session_log_handler = None
        if getattr(self.cfg, "log_to_file", True):
            try:
                from .logging import add_session_file_sink
                log_dir = getattr(self.cfg, "log_dir", None) or (Path(self.workspace.root_dir) / ".orchestrator" / "logs")
                session_log_handler = add_session_file_sink(session_id, log_dir=log_dir)
            except Exception:
                pass

        from .logging import LogContext, remove_session_file_sink
        with LogContext(session_id=session_id):
            try:
                # 1. Run crash diagnosis
                diag_report = self.diagnose_session(session_id)
                self.on_event("RESUME WORKFLOW", f"Resuming Session [{session_id}]: '{loaded_state.user_request}'")
                self.on_event("RESUME DIAGNOSIS", diag_report.summary())

                if not loaded_state.task_dag or not loaded_state.task_dag.list_tasks():
                    return self.run(loaded_state.user_request, session_id=session_id)

                # 2. Reconstruct graph state and reconcile DAG
                graph_state = SessionRecoveryEngine.reconstruct_graph_state(
                    session_id=session_id,
                    state_store=self.state_store,
                    workspace=self.workspace,
                )
                task_dag = TaskDAG.from_list(graph_state["subtasks"])

                # If already all completed and reviewed pass, return directly
                if task_dag.is_all_completed() and loaded_state.verdict == ReviewVerdict.PASS:
                    self.on_event("RESUME", f"Session [{session_id}] was already completed and verified.")
                    return loaded_state

                # 3. Execute remaining waves in the DAG
                while not task_dag.is_all_completed():
                    ready_tasks = task_dag.get_ready_tasks()
                    if not ready_tasks:
                        if task_dag.has_failures():
                            replan_updates = self._node_replan(graph_state)
                            graph_state.update(replan_updates)
                            task_dag = TaskDAG.from_list(graph_state["subtasks"])
                            self._persist_current_state(graph_state)
                            continue
                        else:
                            break

                    wave_updates = self.dag_scheduler.execute_ready_wave(
                        task_dag=task_dag,
                        orchestrator=self,
                        state_data=graph_state,
                    )
                    graph_state["step_results"].update(wave_updates.get("step_results", {}))
                    graph_state["subtasks"] = wave_updates.get("subtasks", [])
                    graph_state["task_decomposition"] = wave_updates.get("task_decomposition", [])
                    graph_state["completed_subtasks"] = wave_updates.get("completed_subtasks", [])
                    self._persist_current_state(graph_state)

                # 4. Run review when all tasks completed
                if task_dag.is_all_completed():
                    rev_updates = self._node_reviewer(graph_state)
                    graph_state.update(rev_updates)
                    self._persist_current_state(graph_state)

                final_state = self._graph_to_state(graph_state)
                final_state.status = self.resolve_terminal_status(
                    verdict=final_state.verdict,
                    task_dag=final_state.task_dag,
                    current_iteration=final_state.current_iteration,
                    max_iterations=final_state.max_iterations,
                )
                self.state_store.save_session(session_id, final_state)
                return final_state
            finally:
                if session_log_handler:
                    try:
                        remove_session_file_sink(session_log_handler)
                    except Exception:
                        pass

    def resume_task(self, task_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Resumes an individual task from its most recent step-level checkpoint.
        Re-hydrates conversational history, prior tool observations, and sandboxed files.
        """
        target_session = session_id or self.state_store.find_session_for_task(task_id) or self.active_session_id
        self.active_session_id = target_session
        self.message_bus.set_session(target_session, self.state_store)

        task = self.state_store.load_single_task(target_session, task_id)
        if not task:
            # Create stub task if registered in transcripts
            task = ExecutableTask(task_id=task_id, objective=f"Task {task_id}")

        latest_transcript = self.state_store.load_latest_task_transcript(target_session, task_id)
        resumed_turn = latest_transcript["turn"] if latest_transcript else 0
        initial_messages = latest_transcript["messages"] if latest_transcript else None
        initial_observations = latest_transcript["observations"] if latest_transcript else []

        self.on_event(
            "RESUME TASK",
            f"Resuming Task [{task_id}] (Session: {target_session}) from turn {resumed_turn} with {len(initial_observations)} prior observation(s)."
        )

        # Discover or select agent
        if task.owner_agent:
            agent = self.select_agent(task.owner_agent)
        else:
            discovered = self.agent_registry.discover(
                query=f"{task.objective} {' '.join(task.inputs)} {' '.join(task.outputs)}",
                capabilities=task.required_capabilities,
                tools=task.required_tools,
                top_k=1,
            )
            agent = self.select_agent(discovered[0][0]) if discovered else self.select_agent("CODER")
            task.owner_agent = agent.name

        # Discover active skills
        skill_query = f"{task.objective} {' '.join(task.required_capabilities)} {' '.join(task.preferred_skills)}"
        discovered_skills = self.skill_registry.discover(query=skill_query, top_k=3)
        active_skills = [m.name for m, _ in discovered_skills]

        clean_id = task_id.replace(":", "_").replace("/", "_").replace("\\", "_")
        from .tools.workspace import SandboxedWorkspace
        sandbox = SandboxedWorkspace(main_workspace=self.workspace, sandbox_id=clean_id)

        loaded_session = self.state_store.load_session(target_session)
        ostate = loaded_session if loaded_session else OrchestratorState(user_request=f"Task {task_id}")

        task_context = {
            "task": task.to_dict(),
            "subtask": task.to_dict(),
            "task_id": task_id,
            "objective": task.objective,
            "inputs": task.inputs,
            "outputs": task.outputs,
            "acceptance_tests": task.acceptance_tests,
            "dependencies": task.dependencies,
            "permissions": task.permissions.to_dict() if hasattr(task.permissions, "to_dict") else task.permissions,
        }

        attempt_start = time.time()
        attempt_num = len(task.attempts) + 1
        res: Dict[str, Any] = {}
        exec_error = None

        try:
            try:
                res = agent.execute(
                    ostate,
                    active_skills=active_skills,
                    task_info=task_context,
                    permissions=task.permissions,
                    max_turns=task.max_turns,
                    timeout_seconds=task.timeout_seconds,
                    session_id=target_session,
                    task_id=task_id,
                    state_store=self.state_store,
                    checkpoint_manager=self.checkpoint_manager,
                    workspace=sandbox,
                    initial_messages=initial_messages,
                    initial_observations=initial_observations,
                    start_turn=resumed_turn,
                )
            except Exception as e:
                exec_error = str(e)
                res = {"error": str(e), "success": False}

            # Verification
            v_res = self.verification_gate.verify_task(task, workspace=sandbox)
            attempt_duration = round(time.time() - attempt_start, 4)
            merged: List[str] = []

            if v_res.passed:
                merged = sandbox.merge_into_main()
                task.state = TaskState.COMPLETED
                task.result_data = res
                task.artifacts = [{
                    "verified_outputs": v_res.verified_outputs,
                    "merged_files": merged,
                    "stdout": v_res.stdout,
                }]
                self.on_event("RESUME TASK COMPLETE", f"Task [{task_id}] PASSED verification. Merged {len(merged)} file(s).")
            else:
                task.state = TaskState.FAILED
                task.error_message = "; ".join(v_res.failure_reasons)
                self.on_event("RESUME TASK FAILED", f"Task [{task_id}] failed verification: {task.error_message}")

            # Record attempt
            attempt_rec = TaskAttemptRecord(
                attempt_number=attempt_num,
                agent_name=agent.name,
                started_at=datetime.now().isoformat(),
                completed_at=datetime.now().isoformat(),
                status=task.state.value,
                tools_used=res.get("tools_used", []),
                skills_used=active_skills,
                observations=res.get("observations", initial_observations),
                errors=[exec_error] if exec_error else v_res.failure_reasons,
                verification_result={"passed": v_res.passed, "verified_outputs": v_res.verified_outputs, "failure_reasons": v_res.failure_reasons},
                duration_seconds=attempt_duration,
            )
            task.record_attempt(attempt_rec)
            self.state_store.save_task(target_session, task)

        finally:
            sandbox.cleanup()

        return {
            "task_id": task_id,
            "session_id": target_session,
            "resumed_from_turn": resumed_turn,
            "status": task.state.value,
            "result": res,
            "verified": v_res.passed,
            "merged_files": merged if v_res.passed else [],
        }

    def rollback_task(self, task_id: str, checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Rolls back the workspace to a specified checkpoint_id or task pre-execution snapshot.
        """
        target_ckpt = checkpoint_id or f"ckpt-{task_id}-pre_execution-1"
        return self.checkpoint_manager.rollback_to_checkpoint(target_ckpt, self.workspace)

    def cancel(self, reason: str = "Execution stopped by user") -> bool:
        """
        Cancels all active tasks, agent loops, and subprocesses for this orchestrator session.
        """
        self.logger.warning(f"Orchestrator cancellation requested: {reason}")
        self.on_event("ORCHESTRATOR_CANCELLED", f"Session execution cancelled: {reason}")
        return self.cancellation_source.cancel(reason)

    def is_cancelled(self) -> bool:
        """Checks if orchestrator cancellation has been requested."""
        return self.cancellation_source.is_cancelled
