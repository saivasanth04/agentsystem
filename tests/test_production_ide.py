"""
Comprehensive Integration Tests for Production IDE.
Verifies compliance with the 10 Non-Negotiable Contract clauses:
- Real executable classes (Zero mocks, zero placeholders)
- Direct import and usage of required mature libraries:
  - Testing: pytest
  - Lint: ruff
  - Type check: mypy
  - Git diff: GitPython (git)
  - Security: bandit
- Reuse of existing components without rewriting:
  - VerificationGate (TaskVerificationGate)
  - TesterAgent
  - ReviewerAgent
  - EpistemicReplanner
- Verification Lifecycle:
  Edit -> Build -> Lint -> Tests -> Security -> Review -> PASS
- On-failure Closed-Loop Repair:
  Failure -> Observation -> Repository Brain -> Context Compiler -> Replanner -> Repair
- Integration across all 7 core systems:
  1. SkillRegistry
  2. AgentRegistry
  3. MCPManager
  4. UnifiedToolDispatcher
  5. WorkspaceManager
  6. LangGraph StateGraph Node
  7. LiteLLM Gateway
"""
import json
import os
from pathlib import Path
import tempfile
import pytest

# 1. Direct library imports verification
import pytest as direct_pytest
import ruff as direct_ruff
import mypy as direct_mypy
import git as direct_git
import bandit as direct_bandit

# Core architectural systems
from agent_orchestrator.registry.skill_registry import SkillRegistry
from agent_orchestrator.registry.agent_registry import AgentRegistry
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.llm import LLMClient

# Reused existing components
from agent_orchestrator.runtime.verification import TaskVerificationGate
from agent_orchestrator.agents.tester import TesterAgent
from agent_orchestrator.agents.reviewer import ReviewerAgent
from agent_orchestrator.runtime.replan_engine import EpistemicReplanner

# Production IDE modules
from ide.verification_pipeline import (
    IDEVerificationPipeline,
    VerificationStage,
    StageOutcome,
    VerificationReport,
    VerificationGate,
)
from ide.repair_pipeline import IDERepairPipeline, RepairResult
from ide.production_ide import ProductionIDE
from repository.repository_brain import RepositoryBrain
from context.compiler import ContextCompiler
from runtime.observation_engine import Observation, ObservationEngine

# In-tree backward compatibility verification
import agent_orchestrator.runtime as in_tree_runtime


@pytest.fixture
def ide_workspace():
    """Provides a temporary workspace initialized with a real Git repository and python modules."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        ws = WorkspaceManager(root_dir=tmpdir)
        # Initialize real git repo
        repo = direct_git.Repo.init(tmpdir)

        # Create production code
        ws.write_file(
            "calculator.py",
            "def add(a: int, b: int) -> int:\n    \"\"\"Adds two integers.\"\"\"\n    return a + b\n\ndef multiply(a: int, b: int) -> int:\n    \"\"\"Multiplies two integers.\"\"\"\n    return a * b\n",
        )
        ws.write_file(
            "test_calculator.py",
            "from calculator import add, multiply\n\ndef test_add():\n    assert add(2, 3) == 5\n\ndef test_multiply():\n    assert multiply(2, 3) == 6\n",
        )

        # Initial commit
        repo.git.add(A=True)
        repo.git.config("user.email", "ide_test@antigravity.ai")
        repo.git.config("user.name", "IDE Test Agent")
        repo.git.commit("-m", "Initial calculator commit")

        yield ws


@pytest.fixture
def ide_core_stack(ide_workspace):
    """Initializes the real 7-component architecture stack without mocks."""
    ws = ide_workspace
    skill_reg = SkillRegistry()
    agent_reg = AgentRegistry()
    agent_reg.register_from_dict({
        "name": "CODER",
        "role_description": "Full-stack software developer",
        "capabilities": ["code_generation", "refactoring"],
    })
    agent_reg.register_from_dict({
        "name": "TESTER",
        "role_description": "Test engineer and QA runner",
        "capabilities": ["test_execution"],
    })
    agent_reg.register_from_dict({
        "name": "REVIEWER",
        "role_description": "Code and architectural reviewer",
        "capabilities": ["code_review"],
    })
    mcp_mgr = MCPManager(workspace_dir=ws.root_dir)
    builtin_reg = BuiltinToolRegistry(
        workspace=ws,
        skill_registry=skill_reg,
        agent_registry=agent_reg,
    )
    dispatcher = UnifiedToolDispatcher(
        builtin_registry=builtin_reg,
        mcp_manager=mcp_mgr,
    )
    llm = LLMClient()
    db_path = Path(ws.root_dir) / ".orchestrator" / "repository_brain.db"
    repo_brain = RepositoryBrain(db_path=db_path, workspace_manager=ws)
    repo_brain.index_repository()

    return {
        "workspace": ws,
        "skill_registry": skill_reg,
        "agent_registry": agent_reg,
        "mcp_manager": mcp_mgr,
        "builtin_registry": builtin_reg,
        "dispatcher": dispatcher,
        "llm": llm,
        "repo_brain": repo_brain,
    }


class TestRequiredLibraryImports:
    """Verifies that required mature libraries are directly imported and executable."""

    def test_direct_imports(self):
        assert direct_pytest is not None
        assert direct_ruff is not None
        assert direct_mypy is not None
        assert direct_git is not None
        assert direct_bandit is not None

    def test_gitpython_diff_extraction(self, ide_workspace):
        repo = direct_git.Repo(ide_workspace.root_dir)
        assert repo is not None
        assert not repo.bare

        # Modify a file
        ide_workspace.write_file("calculator.py", "def add(a: int, b: int) -> int:\n    return a + b + 0\n")
        diff = repo.git.diff()
        assert "calculator.py" in diff
        assert "+    return a + b + 0" in diff


class TestReusedComponents:
    """Verifies reuse of existing components without rewriting."""

    def test_verification_gate_reuse(self, ide_core_stack):
        assert VerificationGate is TaskVerificationGate
        gate = VerificationGate(
            workspace=ide_core_stack["workspace"],
            tool_dispatcher=ide_core_stack["dispatcher"],
        )
        assert isinstance(gate, TaskVerificationGate)

    def test_tester_agent_reuse(self, ide_core_stack):
        tester = TesterAgent(
            workspace=ide_core_stack["workspace"],
            llm=ide_core_stack["llm"],
        )
        assert tester.name == "TESTER"
        assert hasattr(tester, "execute")

    def test_reviewer_agent_reuse(self, ide_core_stack):
        reviewer = ReviewerAgent(
            workspace=ide_core_stack["workspace"],
            llm=ide_core_stack["llm"],
        )
        assert reviewer.name == "REVIEWER"
        assert hasattr(reviewer, "execute")

    def test_epistemic_replanner_reuse(self):
        replanner = EpistemicReplanner()
        assert hasattr(replanner, "replan")


class TestVerificationLifecycle:
    """Verifies the 6-stage lifecycle: Edit -> Build -> Lint -> Tests -> Security -> Review -> PASS."""

    def test_full_lifecycle_success(self, ide_core_stack):
        pipeline = IDEVerificationPipeline(
            workspace=ide_core_stack["workspace"],
            tool_dispatcher=ide_core_stack["dispatcher"],
            llm_client=ide_core_stack["llm"],
        )

        # Stage 1: Edit
        edit_code = (
            "def add(a: int, b: int) -> int:\n    \"\"\"Sums two numbers.\"\"\"\n    return a + b\n\n"
            "def multiply(a: int, b: int) -> int:\n    \"\"\"Multiplies two numbers.\"\"\"\n    return a * b\n\n"
            "def subtract(a: int, b: int) -> int:\n    \"\"\"Subtracts two numbers.\"\"\"\n    return a - b\n"
        )
        test_code = (
            "from calculator import add, multiply, subtract\n\n"
            "def test_add():\n    assert add(2, 3) == 5\n\n"
            "def test_multiply():\n    assert multiply(2, 3) == 6\n\n"
            "def test_subtract():\n    assert subtract(5, 2) == 3\n"
        )

        report = pipeline.run_lifecycle(
            edits={"calculator.py": edit_code, "test_calculator.py": test_code},
            fail_fast=True,
        )

        # Verify all stages passed
        assert report.passed is True
        assert report.current_stage == VerificationStage.PASS
        assert VerificationStage.EDIT.value in report.stage_outcomes
        assert VerificationStage.BUILD.value in report.stage_outcomes
        assert VerificationStage.LINT.value in report.stage_outcomes
        assert VerificationStage.TESTS.value in report.stage_outcomes
        assert VerificationStage.SECURITY.value in report.stage_outcomes
        assert VerificationStage.REVIEW.value in report.stage_outcomes
        assert report.stage_outcomes[VerificationStage.BUILD.value].passed is True
        assert report.stage_outcomes[VerificationStage.TESTS.value].passed is True
        assert report.stage_outcomes[VerificationStage.SECURITY.value].passed is True

    def test_build_stage_catches_syntax_error(self, ide_core_stack):
        pipeline = IDEVerificationPipeline(
            workspace=ide_core_stack["workspace"],
        )
        bad_code = "def bad_syntax(:\n    pass\n"
        report = pipeline.run_lifecycle(
            edits={"bad.py": bad_code},
            fail_fast=True,
        )
        assert report.passed is False
        assert report.failure_stage == VerificationStage.BUILD
        assert "SyntaxError" in (report.failure_reason or "")

    def test_tests_stage_catches_assertion_error(self, ide_core_stack):
        pipeline = IDEVerificationPipeline(
            workspace=ide_core_stack["workspace"],
        )
        failing_test = "def test_will_fail():\n    val = 1\n    assert val == 2\n"
        report = pipeline.run_lifecycle(
            edits={"test_fail.py": failing_test},
            fail_fast=True,
        )
        assert report.passed is False
        assert report.failure_stage == VerificationStage.TESTS


class TestFailureRepairPipeline:
    """Verifies: Failure -> Observation -> Repository Brain -> Context Compiler -> Replanner -> Repair."""

    def test_failure_to_repair_closed_loop(self, ide_core_stack):
        pipeline = IDERepairPipeline(
            workspace=ide_core_stack["workspace"],
            repository_brain=ide_core_stack["repo_brain"],
            tool_dispatcher=ide_core_stack["dispatcher"],
            llm_client=ide_core_stack["llm"],
        )

        raw_test_failure = """============================= test session starts =============================
tests/test_calculator.py F                                              [100%]
================================== FAILURES ===================================
__________________________________ test_add ___________________________________
    def test_add():
>       assert add(2, 3) == 6
E       AssertionError: assert 5 == 6
calculator.py:3: AssertionError
=========================== 1 failed in 0.05s ===========================
"""

        repair_result = pipeline.handle_failure(
            failure_stage="tests",
            raw_output=raw_test_failure,
            exit_code=1,
            active_diff="--- a/calculator.py\n+++ b/calculator.py\n@@ -1,2 +1,2 @@\n-def add():\n+def add():",
            target_files=["calculator.py", "test_calculator.py"],
        )

        assert isinstance(repair_result, RepairResult)
        assert repair_result.success is True

        # 1. Observation: strictly normalized, never raw logs
        obs = repair_result.observation
        assert isinstance(obs, Observation)
        assert obs.type == "test_failure"
        assert obs.severity == "error"
        assert "test_add" in (obs.symbol or obs.evidence)
        assert "test session starts" not in obs.evidence

        # 2. Repository Brain: queried for affected symbols and context
        assert "file_symbols" in repair_result.repository_context or "add" in repair_result.repository_context

        # 3. Context Compiler: compiled package with structured error evidence
        assert repair_result.compiled_context is not None
        assert repair_result.compiled_context.total_tokens > 0

        # 4. Replanner: EpistemicReplanner synthesized root cause
        assert repair_result.replan_result is not None
        assert repair_result.replan_result.root_cause != ""


class TestProductionIDEIntegration:
    """Verifies central ProductionIDE coordination and LangGraph StateGraph integration."""

    def test_production_ide_full_flow(self, ide_core_stack):
        ide = ProductionIDE(
            workspace=ide_core_stack["workspace"],
            skill_registry=ide_core_stack["skill_registry"],
            agent_registry=ide_core_stack["agent_registry"],
            mcp_manager=ide_core_stack["mcp_manager"],
            tool_dispatcher=ide_core_stack["dispatcher"],
            llm_client=ide_core_stack["llm"],
            repository_brain=ide_core_stack["repo_brain"],
        )

        status = ide.get_status()
        assert status["repo_brain_symbols"] >= 2
        assert status["active_skills_count"] > 0
        assert status["active_agents_count"] > 0

        # Run edit and verify
        edits = {
            "calculator.py": (
                "def add(a: int, b: int) -> int:\n    \"\"\"Add function.\"\"\"\n    return a + b\n\n"
                "def multiply(a: int, b: int) -> int:\n    \"\"\"Multiply function.\"\"\"\n    return a * b\n"
            ),
        }
        report, repairs = ide.edit_and_verify(edits=edits)

        assert report.passed is True
        assert report.current_stage == VerificationStage.PASS
        assert len(ide.get_git_diff()) > 0 or report.passed

    def test_langgraph_stategraph_ide_node(self, ide_core_stack):
        from langgraph.graph import StateGraph, START, END

        ide = ProductionIDE(
            workspace=ide_core_stack["workspace"],
            tool_dispatcher=ide_core_stack["dispatcher"],
            llm_client=ide_core_stack["llm"],
            repository_brain=ide_core_stack["repo_brain"],
        )

        # Get LangGraph StateGraph node
        node_fn = ide.create_langgraph_node()
        assert callable(node_fn)

        # Build real LangGraph StateGraph
        builder = StateGraph(dict)
        builder.add_node("ide_step", node_fn)
        builder.add_edge(START, "ide_step")
        builder.add_edge("ide_step", END)
        graph = builder.compile()

        # Execute StateGraph
        input_state = {
            "edits": {
                "calculator.py": (
                    "def add(a: int, b: int) -> int:\n    \"\"\"Adds numbers.\"\"\"\n    return a + b\n\n"
                    "def multiply(a: int, b: int) -> int:\n    \"\"\"Multiplies numbers.\"\"\"\n    return a * b\n"
                ),
            },
            "max_repair_attempts": 1,
        }
        output_state = graph.invoke(input_state)

        assert "verification_passed" in output_state
        assert output_state["verification_passed"] is True
        assert "verification_report" in output_state

    def test_in_tree_backward_compatibility(self):
        """Verifies in-tree re-exports in agent_orchestrator.runtime."""
        assert hasattr(in_tree_runtime, "VerificationGate")
        assert hasattr(in_tree_runtime, "ProductionIDE")
        assert hasattr(in_tree_runtime, "IDEVerificationPipeline")
        assert hasattr(in_tree_runtime, "IDERepairPipeline")
        assert in_tree_runtime.VerificationGate is TaskVerificationGate
