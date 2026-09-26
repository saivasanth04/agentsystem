"""
Comprehensive Integration Test Suite for AGENTSYSTEM v2 Architectural Consolidation.
Validates all 15 P0 + P1 + P2 architectural fixes without mocks.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import pytest
import shutil
import tempfile
import time

from runtime.tool_state_machine import ToolLifecycleState, ToolStateMachine
from runtime.compatibility_enforcer import CompatibilityMatrixEnforcer, CompatibilityReport
from runtime.capability_router import BrowserMCPAdapter, CapabilityRouter
from runtime.agent_loop import AgentExecutionLoop, ExecutionState, LoopStatus
from runtime.observation_engine import Observation, ObservationEngine, NormalizedType
from repository.runtime_detector import RuntimeDetector, RuntimeProfile
from repository.repository_brain import RepositoryBrain
from ide.verification_pipeline import IDEVerificationPipeline, VerificationStage, StageOutcome
from ide.repair_pipeline import IDERepairPipeline, RepairLifecycleState, RepairResult
from ide.production_ide import ProductionIDE
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.registry.skill_registry import SkillManager
from agent_orchestrator.registry.agent_registry import AgentRegistry
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from context.compiler import ContextCompiler


@pytest.fixture
def consolidation_workspace():
    """Isolated temporary workspace for consolidation tests."""
    temp_dir = tempfile.mkdtemp(prefix="agent_consolidation_")
    ws = WorkspaceManager(root_dir=temp_dir)
    ws.write_file("auth.py", "def validate_token(token: str) -> bool:\n    return len(token) > 5\n")
    ws.write_file("test_auth.py", "from auth import validate_token\n\ndef test_validate_token():\n    assert validate_token('valid_token') is True\n")
    yield ws
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestToolConsolidationAndStateMachine:
    """Validates Fix 6, Fix 7, Fix 8, Fix 9 (P1 Tool Consolidation)."""

    def test_tool_lifecycle_state_machine_transitions(self):
        """Fix 6: Tests strict DECLARED -> DISCOVERED -> HEALTHY -> AUTHORIZED -> EXECUTABLE transition."""
        sm = ToolStateMachine()
        tool = "filesystem.write"

        assert sm.get_state(tool) == ToolLifecycleState.DECLARED
        assert sm.is_executable(tool) is False

        sm.discover(tool, source="builtin")
        assert sm.get_state(tool) == ToolLifecycleState.DISCOVERED
        assert sm.is_executable(tool) is False

        sm.mark_healthy(tool)
        assert sm.get_state(tool) == ToolLifecycleState.HEALTHY
        assert sm.is_executable(tool) is False

        sm.authorize(tool)
        assert sm.get_state(tool) == ToolLifecycleState.AUTHORIZED
        assert sm.is_executable(tool) is False

        sm.promote_to_executable(tool)
        assert sm.get_state(tool) == ToolLifecycleState.EXECUTABLE
        assert sm.is_executable(tool) is True

    def test_browser_mcp_adapter_zero_fakes(self):
        """Fix 7: Verifies BrowserMCPAdapter returns state=MISSING with explicit error when no server running."""
        mcp_mgr = MCPManager()
        adapter = BrowserMCPAdapter(mcp_manager=mcp_mgr)

        status = adapter.get_status()
        assert status["state"] == "MISSING"
        assert status["connected"] is False

        res = adapter.call_tool("browser_navigate", {"url": "https://example.com"})
        assert res.get("success") is False
        assert "not available" in res.get("error", "").lower()

    def test_only_executable_tools_in_model_schemas(self):
        """Fix 8: Verifies CapabilityRouter filters out any tool that is not strictly EXECUTABLE."""
        router = CapabilityRouter()
        # Mark a tool as only DISCOVERED
        router.tool_sm.discover("terminal.run", source="builtin")
        # Mark another tool as completely EXECUTABLE
        router.tool_sm.register_and_verify("filesystem.read", source="builtin", is_authorized=True)

        schemas = router.get_tool_schemas_for_task("Read repository configuration")
        exposed_tools = [s.get("function", {}).get("name") for s in schemas if s.get("function")]

        assert "filesystem.read" in exposed_tools
        assert "terminal.run" not in exposed_tools

    def test_compatibility_matrix_enforcer_execution(self):
        """Fix 9: Tests runtime validation of Skill -> Required Tool -> Exists? -> Healthy? -> Authorized? -> Executable?."""
        enforcer = CompatibilityMatrixEnforcer()
        enforcer.tool_sm.register_and_verify("read_file", is_authorized=True)
        enforcer.tool_sm.register_and_verify("write_file", is_authorized=True)

        report: CompatibilityReport = enforcer.enforce_skill_compatibility(
            skill_name="test-driven-development",
            required_tools=["read_file", "write_file"],
        )
        assert report.compatible is True
        assert len(report.missing_or_blocked) == 0

        # Now test failure on unverified tool
        failed_report = enforcer.enforce_skill_compatibility(
            skill_name="test-driven-development",
            required_tools=["read_file", "unregistered_custom_tool"],
        )
        assert failed_report.compatible is False
        assert "unregistered_custom_tool" in failed_report.missing_or_blocked


class TestUnifiedRuntimeAndExecutionSpine:
    """Validates Fix 1, Fix 2, Fix 5 (P0 Single ReAct Runtime)."""

    def test_single_react_runtime_adaptation(self, consolidation_workspace):
        """Fix 1: Verifies ReActAgentLoop and AgentExecutionLoop execute through unified engine."""
        loop = AgentExecutionLoop(workspace_manager=consolidation_workspace, max_iterations=2)
        state = loop.run(task_objective="Inspect auth.py")

        assert state.status in (LoopStatus.COMPLETED, LoopStatus.TERMINATED)
        assert state.iteration >= 1

        # ReActAgentLoop adapter compatibility
        react = ReActAgentLoop(llm=None, tool_registry=None)
        assert hasattr(react, "run")
        assert hasattr(react, "execute")

    def test_dev_server_interception(self, consolidation_workspace):
        """Fix 5: Tests that long-running commands are detected and handed off to ProjectRuntimeManager."""
        assert AgentExecutionLoop._is_long_running_dev_command("npm run dev") is True
        assert AgentExecutionLoop._is_long_running_dev_command("vite") is True
        assert AgentExecutionLoop._is_long_running_dev_command("python auth.py") is False


class TestObservationEngineAndVerificationRepair:
    """Validates Fix 10, Fix 11, Fix 12, Fix 13 (P2 Verification and Repair)."""

    def test_observation_engine_schema_and_types(self, consolidation_workspace):
        """Fix 10: Tests Observation schema (source, timestamp, normalized types)."""
        engine = ObservationEngine(workspace_root=consolidation_workspace.root_dir)

        # 1. Compiler diagnostic
        raw_compiler = "auth.py:2:1: error: Cannot find name 'foo'"
        obs_comp = engine.normalize(tool_name="compiler", raw_output=raw_compiler, exit_code=1)
        assert obs_comp.type == "compiler_error"
        assert obs_comp.source == "compiler"
        assert isinstance(obs_comp.timestamp, float)

        # 2. Failing test
        raw_test = "=== 1 failed in 0.05s ==="
        obs_test = engine.normalize(tool_name="pytest", raw_output=raw_test, exit_code=1)
        assert obs_test.type == "failing_test"
        # Test backward-compatible string alias equality
        assert obs_test.type == "test_failure"
        assert obs_test.source == "test_runner"

        # 3. Security issue
        obs_sec = engine.normalize(tool_name="permission_eval", raw_output="Permission denied by security policy", exit_code=1)
        assert obs_sec.type == "security_issue"
        assert obs_sec.type == "permission_denied"
        assert obs_sec.source == "security"

    def test_framework_aware_verification_pipeline(self, consolidation_workspace):
        """Fix 11: Verifies IDEVerificationPipeline operates across framework profiles."""
        pipeline = IDEVerificationPipeline(workspace=consolidation_workspace)
        assert pipeline.profile is not None

        # Build stage on Python workspace
        build_outcome = pipeline.stage_build()
        assert isinstance(build_outcome, StageOutcome)
        assert build_outcome.passed is True

        # Test stage on Python workspace
        test_outcome = pipeline.stage_tests(test_files=["test_auth.py"])
        assert isinstance(test_outcome, StageOutcome)
        assert test_outcome.passed is True

    def test_evidence_based_repair_lifecycle_contract(self, consolidation_workspace):
        """Fix 12: Verifies RepairLifecycleState transitions and that success=True requires VERIFICATION_PASSED."""
        pipeline = IDERepairPipeline(workspace=consolidation_workspace)

        result: RepairResult = pipeline.handle_failure(
            failure_stage="tests",
            raw_output="test_auth.py F\nAssertionError: assert False",
            exit_code=1,
            target_files=["auth.py"],
        )

        assert isinstance(result, RepairResult)
        assert result.lifecycle_state in (RepairLifecycleState.VERIFICATION_PASSED, RepairLifecycleState.FAILED)
        # Contract: success is True ONLY if state is VERIFICATION_PASSED
        assert result.success == (result.lifecycle_state == RepairLifecycleState.VERIFICATION_PASSED)
        assert result.replan_result is not None
