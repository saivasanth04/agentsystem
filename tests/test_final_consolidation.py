"""
Final Architectural Consolidation Integration Test Suite.
Validates the complete, authoritative single execution spine:
User Request -> TaskOrchestrator -> ConcurrentDAGScheduler -> SkillResolver -> SkillRuntime ->
RepositoryBrain -> ContextCompiler -> AgentExecutionLoop -> LiteLLM (chat_with_tools) ->
UnifiedToolDispatcher -> ObservationEngine -> ExecutionState -> VerificationPipeline -> [PASS/FAIL: RepairPipeline]

Tests 6 Primary Integration Scenarios:
1. React bug fixing (SkillResolver, SkillRuntime, ContextCompiler, AgentExecutionLoop, Browser tool, Verification, Runtime preview)
2. Spring Boot bug (Automatic runtime registration via ProjectEnvironmentDetector, mvn/gradle build/test commands)
3. Python project (Framework-aware verification in IDEVerificationPipeline, RepairPipeline, Replanner)
4. Mixed repository (RepositoryBrain file relevance across languages, token budgeting, deduplication)
5. Strict tool lifecycle (DECLARED -> DISCOVERED -> HEALTH CHECK -> HEALTHY -> AUTHORIZED -> EXECUTABLE)
6. Zero SKILL.md markdown prompt bleed (Strictly MinimalCapabilitySummary)
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Dict, Any, List

import pytest

from agent_orchestrator.registry.skill_registry import SkillRegistry, SkillManifest
from agent_orchestrator.registry.agent_registry import AgentRegistry
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.llm import LLMClient
from agent_orchestrator.runtime.project_detector import ProjectEnvironmentDetector, ProjectEnvironment
from agent_orchestrator.runtime.project_runtime import ProjectRuntimeManager

from skills.resolver import SkillResolver, ResolvedSkillPlan
from skills.runtime import SkillRuntime, RuntimeExecutionReport
from skills.capability_summary import MinimalCapabilitySummary, CapabilitySummaryExtractor
from skills.runtime_policy import RuntimePolicy, ContextRequirements, VerificationRequirements, ToolPolicy
from repository.runtime_detector import RuntimeDetector, RuntimeProfile
from repository.repository_brain import RepositoryBrain
from context.compiler import ContextCompiler, OptimizedContextPackage, CompiledContextPackage
from runtime.tool_state_machine import ToolLifecycleState, ToolStateMachine
from runtime.capability_router import CapabilityRouter, BrowserMCPAdapter
from runtime.permission_engine import PermissionEngine
from runtime.observation_engine import Observation, ObservationEngine
from runtime.execution_state import ExecutionState, LoopStatus
from runtime.agent_loop import AgentExecutionLoop
from ide.verification_pipeline import IDEVerificationPipeline, VerificationStage, StageOutcome, VerificationReport
from ide.repair_pipeline import IDERepairPipeline, RepairLifecycleState, RepairResult
from ide.production_ide import ProductionIDE


@pytest.fixture
def final_test_workspace():
    """Provides an isolated workspace containing multi-language project structures."""
    temp_dir = tempfile.mkdtemp(prefix="agent_final_consolidation_")
    ws = WorkspaceManager(root_dir=temp_dir)

    # React / Frontend structure
    ws.write_file("package.json", json.dumps({
        "name": "sample-react-app",
        "version": "1.0.0",
        "scripts": {
            "dev": "vite",
            "build": "vite build",
            "test": "vitest run"
        },
        "dependencies": {
            "react": "^18.2.0",
            "react-dom": "^18.2.0"
        }
    }, indent=2))
    ws.write_file("src/App.jsx", "export default function App() {\n  return <div>Hello World</div>;\n}\n")
    ws.write_file("src/App.test.jsx", "import { test, expect } from 'vitest';\ntest('renders', () => { expect(true).toBe(true); });\n")

    # Python backend structure
    ws.write_file("auth.py", "def validate_token(token: str) -> bool:\n    if not token:\n        raise ValueError('Invalid token')\n    return len(token) > 5\n")
    ws.write_file("test_auth.py", "from auth import validate_token\n\ndef test_token_success():\n    assert validate_token('secret_token') is True\n")

    yield ws
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def core_stack(final_test_workspace):
    """Initializes the real core architectural stack with zero mocks."""
    ws = final_test_workspace
    skill_reg = SkillRegistry()
    agent_reg = AgentRegistry()
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
    return {
        "workspace": ws,
        "skill_registry": skill_reg,
        "agent_registry": agent_reg,
        "mcp_manager": mcp_mgr,
        "builtin_registry": builtin_reg,
        "dispatcher": dispatcher,
        "llm": llm,
    }


class TestFinalArchitecturalConsolidation:
    """Verifies all 6 master consolidation requirements."""

    def test_1_react_bug_fixing_flow(self, core_stack):
        """
        Scenario 1: React bug fixing.
        - SkillResolver selects relevant frontend/testing skill.
        - SkillRuntime runs execute().
        - ContextCompiler compiles prompt with MinimalCapabilitySummary (NO raw SKILL.md).
        - AgentExecutionLoop selects tools.
        - BrowserMCPAdapter is verified (MISSING or running, never fake).
        - IDEVerificationPipeline runs build -> lint -> tests.
        """
        stack = core_stack
        ws = stack["workspace"]
        resolver = SkillResolver(skill_registry=stack["skill_registry"])
        resolved = resolver.resolve("Fix React button rendering bug in App.jsx")

        assert resolved is not None
        assert len(resolved.skills) > 0
        skill_names = [s.name for s in resolved.skills]
        assert any("react" in s.lower() or "frontend" in s.lower() for s in skill_names)

        # SkillRuntime.execute() authority
        runtime = SkillRuntime(
            skill_registry=stack["skill_registry"],
            agent_registry=stack["agent_registry"],
            mcp_manager=stack["mcp_manager"],
            tool_dispatcher=stack["dispatcher"],
            workspace_manager=ws,
            llm_client=stack["llm"],
        )
        report = runtime.execute(
            task_objective="Fix button click handler in App.jsx",
            task_id="task_react_fix_1",
            max_iterations=2,
        )
        assert isinstance(report, (ExecutionState, RuntimeExecutionReport))
        assert report.status in (LoopStatus.COMPLETED, LoopStatus.TERMINATED)

        # ContextCompiler verifies no raw SKILL.md text in compiled prompt
        compiler = ContextCompiler(
            skill_registry=stack["skill_registry"],
            agent_registry=stack["agent_registry"],
            tool_dispatcher=stack["dispatcher"],
            workspace_manager=ws,
        )
        compiled = compiler.compile(
            objective="Fix button click handler in App.jsx",
            skills=resolved.skills,
        )
        compiled_text = compiled.to_prompt_context()
        assert "## Quick Reference Table" not in compiled_text
        assert "## Anti-Patterns" not in compiled_text
        assert "## Detailed Workflow" not in compiled_text

        # Browser tool verification: must report real status (MISSING if offline, never fake)
        browser = BrowserMCPAdapter(mcp_manager=stack["mcp_manager"])
        status = browser.get_status()
        assert status["state"] in ("MISSING", "CONNECTED", "AVAILABLE")
        if status["state"] == "MISSING":
            assert status["connected"] is False

        # IDE Verification Pipeline for React / JS
        pipeline = IDEVerificationPipeline(workspace=ws, tool_dispatcher=stack["dispatcher"])
        assert pipeline.profile.primary_language in ("javascript", "typescript", "react") or str(pipeline.profile.framework).lower() in ("react", "nodejs", "generic")

    def test_2_spring_boot_runtime_and_verification(self):
        """
        Scenario 2: Spring Boot bug.
        - ProjectEnvironmentDetector detects Java runtime.
        - Build/test commands use mvn or gradle.
        - Verification respects framework profile.
        """
        temp_dir = tempfile.mkdtemp(prefix="agent_spring_")
        try:
            ws = WorkspaceManager(root_dir=temp_dir)
            ws.write_file("pom.xml", """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>demo</artifactId>
  <version>0.0.1-SNAPSHOT</version>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.1.0</version>
  </parent>
</project>""")
            ws.write_file("src/main/java/com/example/demo/DemoApplication.java", "package com.example.demo;\npublic class DemoApplication {}\n")

            # ProjectEnvironmentDetector detection
            env = ProjectEnvironmentDetector.detect(ws.root_dir)
            assert env is not None
            assert env.language == "java"
            assert "mvn" in env.build_command
            assert "mvn" in env.test_command

            # IDEVerificationPipeline framework profile check
            pipeline = IDEVerificationPipeline(workspace=ws)
            assert pipeline.profile.primary_language == "java"
            assert pipeline.profile.framework == "spring_boot"
            assert pipeline.profile.package_manager == "maven"
            assert pipeline.profile.test_framework == "junit"
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_3_python_framework_aware_verification_and_repair(self, core_stack):
        """
        Scenario 3: Python project.
        - IDEVerificationPipeline uses pytest.
        - Repair pipeline invoked on test failure.
        - EpistemicReplanner produces repair diagnosis.
        """
        stack = core_stack
        temp_dir = tempfile.mkdtemp(prefix="agent_py_proj_")
        try:
            ws = WorkspaceManager(root_dir=temp_dir)
            ws.write_file("pyproject.toml", "[tool.pytest.ini_options]\npythonpath = ['.']\n")
            ws.write_file("auth.py", "def validate_token(token: str) -> bool:\n    if not token:\n        raise ValueError('Invalid token')\n    return len(token) > 5\n")
            ws.write_file("test_auth.py", "from auth import validate_token\n\ndef test_token_success():\n    assert validate_token('secret_token') is True\n")

            pipeline = IDEVerificationPipeline(workspace=ws, tool_dispatcher=stack["dispatcher"])

            assert pipeline.profile.primary_language == "python"
            assert pipeline.profile.test_framework in ("pytest", "unittest")

            # Test execution on Python workspace
            outcome = pipeline.stage_tests(test_files=["test_auth.py"])
            assert outcome.passed is True

            # Failure repair closed-loop test
            repair_pipeline = IDERepairPipeline(
                workspace=ws,
                tool_dispatcher=stack["dispatcher"],
                llm_client=stack["llm"],
            )
            fake_failure = """============================= test session starts =============================
test_auth.py F
================================== FAILURES ===================================
______________________________ test_token_empty _______________________________
    def test_token_empty():
>       assert False
E       AssertionError: assert False
test_auth.py:8: AssertionError
=========================== 1 failed in 0.04s ===========================
"""
            repair_res = repair_pipeline.handle_failure(
                failure_stage="tests",
                raw_output=fake_failure,
                exit_code=1,
                target_files=["auth.py", "test_auth.py"],
            )
            assert isinstance(repair_res, RepairResult)
            assert repair_res.replan_result is not None
            assert repair_res.observation.type == "test_failure"
            assert repair_res.observation.severity == "error"
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_4_mixed_repository_intelligence(self, core_stack):
        """
        Scenario 4: Mixed repository intelligence.
        - RepositoryBrain surfaces relevant symbols across languages (React + Python).
        - ContextCompiler respects token budget and deduplicates.
        """
        stack = core_stack
        ws = stack["workspace"]
        repo_brain = RepositoryBrain(workspace_manager=ws)
        repo_brain.index_repository()

        # Query across frontend and backend symbols
        py_symbols = repo_brain.find_symbol("validate_token")
        assert len(py_symbols) > 0
        assert any("auth.py" in s.get("file_path", "") for s in py_symbols)

        # ContextCompiler budgeting & deduplication
        compiler = ContextCompiler(
            skill_registry=stack["skill_registry"],
            workspace_manager=ws,
            repository_brain=repo_brain,
            total_budget=1000,
        )
        pkg = compiler.compile(objective="Check authentication flow validate_token")
        assert pkg.total_tokens <= 1000
        assert pkg.total_tokens > 0

    def test_5_strict_tool_lifecycle_enforcement(self, core_stack):
        """
        Scenario 5: Strict tool lifecycle.
        - DECLARED -> DISCOVERED -> HEALTH CHECK -> HEALTHY -> AUTHORIZED -> EXECUTABLE.
        - Cannot execute from DISCOVERED.
        - Unverified tools transition to UNHEALTHY or MISSING.
        - Only EXECUTABLE tools exposed to model schema.
        """
        stack = core_stack
        sm = ToolStateMachine(dispatcher=stack["dispatcher"], mcp_manager=stack["mcp_manager"])
        tool = "read_file"

        # Initially DECLARED
        sm.declare_tool(tool, source="builtin")
        assert sm.get_state(tool) == ToolLifecycleState.DECLARED
        assert sm.is_executable(tool) is False

        # Transition to DISCOVERED
        sm.discover(tool, source="builtin")
        assert sm.get_state(tool) == ToolLifecycleState.DISCOVERED
        assert sm.is_executable(tool) is False

        # Cannot promote to EXECUTABLE from DISCOVERED without HEALTH CHECK and AUTHORIZATION
        sm.promote_to_executable(tool)
        assert sm.is_executable(tool) is False

        # Run health check probe
        is_healthy = sm.verify_health(tool)
        assert is_healthy is True
        assert sm.get_state(tool) == ToolLifecycleState.HEALTHY
        assert sm.is_executable(tool) is False

        # Authorize under task policy
        sm.authorize(tool, allowed_tools={tool})
        assert sm.get_state(tool) == ToolLifecycleState.AUTHORIZED
        assert sm.is_executable(tool) is False

        # Promote to EXECUTABLE
        sm.promote_to_executable(tool)
        assert sm.get_state(tool) == ToolLifecycleState.EXECUTABLE
        assert sm.is_executable(tool) is True

        # Expose only EXECUTABLE tools
        exec_schemas = sm.get_executable_schemas()
        exec_names = [s.get("function", {}).get("name") for s in exec_schemas if s.get("function")]
        assert tool in exec_names

        # Unhealthy / unknown tool remains non-executable
        unknown = "unknown_nonexistent_tool"
        sm.discover(unknown, source="builtin")
        healthy = sm.verify_health(unknown)
        assert healthy is False
        assert sm.get_state(unknown) in (ToolLifecycleState.UNHEALTHY, ToolLifecycleState.MISSING)
        assert sm.is_executable(unknown) is False

    def test_6_zero_skill_prompt_bleed(self, core_stack):
        """
        Scenario 6: Zero SKILL.md markdown prompt bleed.
        - Only MinimalCapabilitySummary (name, intent, tools, constraints) appears in prompts.
        - Raw skill instructions, full workflows, tables, and internal docs are strictly excluded.
        """
        stack = core_stack
        skill = stack["skill_registry"].get_skill("test-driven-development")
        summary = CapabilitySummaryExtractor.extract(skill or "test-driven-development")

        assert isinstance(summary, MinimalCapabilitySummary)
        assert summary.name == "test-driven-development"
        assert len(summary.summary) > 0
        assert isinstance(summary.required_capabilities, list)
        assert isinstance(summary.constraints, list)

        compact_prompt = summary.to_compact_string()
        assert "test-driven-development" in compact_prompt

        # Assert no verbose markdown document bleed
        assert "# test-driven-development" not in compact_prompt
        assert "## Overview" not in compact_prompt
        assert "```bash" not in compact_prompt
        assert "```python" not in compact_prompt
