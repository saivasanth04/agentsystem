"""
Production IDE Coordinator.
The central production IDE engine uniting the verification lifecycle:
Edit -> Build -> Lint -> Tests -> Security -> Review -> PASS
and the closed-loop failure repair lifecycle:
Failure -> Observation -> Repository Brain -> Context Compiler -> Replanner -> Repair

Integrates with:
- SkillRegistry
- AgentRegistry
- MCPManager
- UnifiedToolDispatcher
- WorkspaceManager
- LangGraph (StateGraph compilation)
- LiteLLM Gateway
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

# Core architectural systems
from agent_orchestrator.registry.skill_registry import SkillRegistry
from agent_orchestrator.registry.agent_registry import AgentRegistry
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.llm import LLMClient

# IDE Pipelines
from .verification_pipeline import (
    IDEVerificationPipeline,
    VerificationStage,
    StageOutcome,
    VerificationReport,
    VerificationGate,
)
from .repair_pipeline import IDERepairPipeline, RepairResult

# Reused existing components
from agent_orchestrator.agents.tester import TesterAgent
from agent_orchestrator.agents.reviewer import ReviewerAgent
from agent_orchestrator.runtime.replan_engine import EpistemicReplanner
from repository.repository_brain import RepositoryBrain
from context.compiler import ContextCompiler
from runtime.observation_engine import ObservationEngine
from runtime.agent_loop import AgentExecutionLoop

logger = logging.getLogger("ide.production_ide")


class ProductionIDE:
    """
    Production IDE Engine coordinating multi-stage verification and autonomous repair.
    """

    def __init__(
        self,
        workspace: Optional[WorkspaceManager] = None,
        skill_registry: Optional[SkillRegistry] = None,
        agent_registry: Optional[AgentRegistry] = None,
        mcp_manager: Optional[MCPManager] = None,
        tool_dispatcher: Optional[UnifiedToolDispatcher] = None,
        llm_client: Optional[LLMClient] = None,
        repository_brain: Optional[RepositoryBrain] = None,
        task_orchestrator: Optional[Any] = None,
        execution_loop: Optional[AgentExecutionLoop] = None,
        verification_pipeline: Optional[IDEVerificationPipeline] = None,
        repair_pipeline: Optional[IDERepairPipeline] = None,
        project_runtime_manager: Optional[Any] = None,
    ):
        # 1. Workspace
        self.workspace = workspace or WorkspaceManager()
        self.workspace_root = Path(self.workspace.root_dir)

        # 2. Core Registries and Managers
        self.skill_registry = skill_registry or SkillRegistry()
        self.agent_registry = agent_registry or AgentRegistry()
        self.mcp_manager = mcp_manager or MCPManager(workspace_dir=self.workspace.root_dir)
        self.dispatcher = tool_dispatcher or UnifiedToolDispatcher(mcp_manager=self.mcp_manager)
        self.llm = llm_client or LLMClient()

        # 3. Persistent Repository Brain
        if repository_brain is not None:
            self.repo_brain = repository_brain
        else:
            db_path = self.workspace_root / ".orchestrator" / "repository_brain.db"
            self.repo_brain = RepositoryBrain(db_path=db_path, workspace_manager=self.workspace)

        # 4. Context Compiler
        self.context_compiler = ContextCompiler(
            skill_registry=self.skill_registry,
            agent_registry=self.agent_registry,
            mcp_manager=self.mcp_manager,
            tool_dispatcher=self.dispatcher,
            workspace_manager=self.workspace,
            llm_client=self.llm,
        )

        # 5. Reused Components (Do not rewrite)
        self.verification_gate = VerificationGate(
            workspace=self.workspace,
            tool_dispatcher=self.dispatcher,
        )
        self.tester_agent = TesterAgent(
            workspace=self.workspace,
            llm=self.llm,
            skill_registry=self.skill_registry,
        )
        self.reviewer_agent = ReviewerAgent(
            workspace=self.workspace,
            llm=self.llm,
            skill_registry=self.skill_registry,
        )
        self.replanner = EpistemicReplanner()

        # 6. Verification Pipeline (Consumed or constructed)
        self.verification_pipeline = verification_pipeline or IDEVerificationPipeline(
            workspace=self.workspace,
            tester_agent=self.tester_agent,
            reviewer_agent=self.reviewer_agent,
            verification_gate=self.verification_gate,
            tool_dispatcher=self.dispatcher,
            llm_client=self.llm,
        )

        # 7. Failure Repair Pipeline (Consumed or constructed)
        self.repair_pipeline = repair_pipeline or IDERepairPipeline(
            workspace=self.workspace,
            repository_brain=self.repo_brain,
            context_compiler=self.context_compiler,
            replan_engine=self.replanner,
            tool_dispatcher=self.dispatcher,
            llm_client=self.llm,
        )

        # 8. Unified Execution Spine Integration (TaskOrchestrator + AgentExecutionLoop)
        self._orchestrator = task_orchestrator
        self.execution_loop = execution_loop or AgentExecutionLoop(
            llm_client=self.llm,
            workspace_manager=self.workspace,
            tool_dispatcher=self.dispatcher,
        )

        # 9. Project Runtime Manager (Service Layer Process & Port Governance - PARTIAL FIX 3)
        if project_runtime_manager is not None:
            self.runtime_manager = project_runtime_manager
        else:
            try:
                from agent_orchestrator.runtime.project_runtime import ProjectRuntimeManager
                self.runtime_manager = ProjectRuntimeManager(
                    workspace_dir=self.workspace_root,
                )
            except Exception as e:
                logger.debug(f"ProjectRuntimeManager initialization fallback: {e}")
                self.runtime_manager = None
        self.project_runtime_manager = self.runtime_manager

    def edit(
        self,
        edits: Dict[str, str],
        commit_message: Optional[str] = None,
    ) -> StageOutcome:
        """Applies edits to the workspace and extracts git diff."""
        return self.verification_pipeline.stage_edit(edits=edits, commit_message=commit_message)

    def verify(
        self,
        edits: Optional[Dict[str, str]] = None,
        target_files: Optional[List[str]] = None,
        acceptance_command: Optional[str] = None,
        fail_fast: bool = True,
    ) -> VerificationReport:
        """
        Executes the 6-stage verification lifecycle:
        Edit -> Build -> Lint -> Tests -> Security -> Review -> PASS
        """
        return self.verification_pipeline.run_lifecycle(
            edits=edits,
            target_files=target_files,
            acceptance_command=acceptance_command,
            fail_fast=fail_fast,
        )

    def edit_and_verify(
        self,
        edits: Dict[str, str],
        acceptance_command: Optional[str] = None,
        max_repair_attempts: int = 2,
    ) -> Tuple[VerificationReport, List[RepairResult]]:
        """
        Orchestrates autonomous edit, verification, and on-failure closed-loop repair.
        Retries repair loop upon failures until PASS or repair attempts exhausted.
        """
        repair_history: List[RepairResult] = []
        modified_files = list(edits.keys())

        # Initial edit and verification run
        report = self.verify(
            edits=edits,
            target_files=modified_files,
            acceptance_command=acceptance_command,
            fail_fast=True,
        )

        attempt = 0
        while not report.passed and attempt < max_repair_attempts:
            attempt += 1
            f_stage = report.failure_stage.value if report.failure_stage else "unknown"
            f_reason = report.failure_reason or "Unknown stage failure"
            logger.info(f"Verification failed at stage '{f_stage}'. Launching repair attempt {attempt}...")

            # Run failure repair pipeline:
            # Failure -> Observation -> Repository Brain -> Context Compiler -> Replanner -> Repair
            repair_res = self.repair_pipeline.handle_failure(
                failure_stage=f_stage,
                raw_output=f_reason,
                active_diff=report.git_diff,
                iteration=attempt,
                target_files=modified_files,
            )
            repair_history.append(repair_res)

            # Re-run verification lifecycle
            report = self.verify(
                target_files=modified_files,
                acceptance_command=acceptance_command,
                fail_fast=True,
            )

        return report, repair_history

    def get_git_diff(self) -> str:
        """Extracts workspace uncommitted changes using GitPython."""
        return self.verification_pipeline.get_git_diff()

    @property
    def orchestrator(self) -> Any:
        """Lazily instantiates the authoritative TaskOrchestrator if not injected."""
        if self._orchestrator is None:
            from agent_orchestrator.orchestrator import TaskOrchestrator
            self._orchestrator = TaskOrchestrator(
                workspace=self.workspace,
                llm=self.llm,
                skill_registry=self.skill_registry,
                agent_registry=self.agent_registry,
                mcp_manager=self.mcp_manager,
                tool_dispatcher=self.dispatcher,
            )
        return self._orchestrator

    def run_task(self, user_request: str, **kwargs: Any) -> Any:
        """Executes a task through the authoritative TaskOrchestrator execution spine."""
        return self.orchestrator.run(user_request, **kwargs)

    def execute_prompt(self, prompt: str, **kwargs: Any) -> Any:
        """Executes a targeted prompt through the authoritative AgentExecutionLoop."""
        return self.execution_loop.run(user_prompt=prompt, **kwargs)

    def get_status(self) -> Dict[str, Any]:
        """Provides status summary of the production IDE session."""
        return {
            "workspace_root": str(self.workspace_root),
            "git_diff_length": len(self.get_git_diff()),
            "repo_brain_symbols": len(self.repo_brain.get_all_symbols()),
            "active_skills_count": len(self.skill_registry.list_skills()),
            "active_agents_count": len(self.agent_registry.list_agents()),
            "mcp_servers": self.mcp_manager.list_servers() if hasattr(self.mcp_manager, "list_servers") else [],
            "orchestrator_available": True,
            "execution_loop_available": True,
            "project_runtime_available": self.runtime_manager is not None,
        }

    def create_langgraph_node(self) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        """
        Creates a LangGraph-compatible StateGraph node function.
        Executes IDE edit and verification workflows in graph pipelines.
        """
        def ide_node(state: Dict[str, Any]) -> Dict[str, Any]:
            edits = state.get("edits") or {}
            acceptance_cmd = state.get("acceptance_command")
            max_repairs = state.get("max_repair_attempts", 1)

            report, repairs = self.edit_and_verify(
                edits=edits,
                acceptance_command=acceptance_cmd,
                max_repair_attempts=max_repairs,
            )

            new_state = dict(state)
            new_state["verification_passed"] = report.passed
            new_state["verification_report"] = report.to_dict()
            new_state["repair_results"] = [r.to_dict() for r in repairs]
            new_state["git_diff"] = report.git_diff
            return new_state

        return ide_node
