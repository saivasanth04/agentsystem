"""
Comprehensive Integration Tests for Skills Runtime System.
Verifies compliance with the 10 Non-Negotiable Contract clauses:
- Real executable classes (Zero mocks, zero placeholders)
- Integration across:
  1. SkillRegistry
  2. AgentRegistry
  3. MCPManager
  4. UnifiedToolDispatcher
  5. WorkspaceManager
  6. LangGraph
  7. LiteLLM Gateway
- Exact verification requirement:
  - Skill requesting 'filesystem.read' and 'browser.console'
  - Hierarchy: MCP -> Dispatcher -> Builtin -> Thin Adapter
  - No custom filesystem implementations; strictly reuses existing Pathlib/WorkspaceManager
"""
import os
from pathlib import Path
import tempfile
import pytest

from agent_orchestrator.registry.skill_registry import SkillRegistry, SkillManifest
from agent_orchestrator.registry.agent_registry import AgentRegistry
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.llm import LLMClient

from skills.policy import ToolPolicy, RuntimePolicy, DEFAULT_TOOL_ALIASES
from skills.compiler import SkillCompiler, CompiledSkill, ExecutionProcedure, ExecutionStep
from skills.verifier import SkillVerifier, ToolVerificationStatus
from skills.resolver import SkillResolver, ResolvedSkillPlan
from skills.runtime import SkillRuntime, RuntimeExecutionReport


@pytest.fixture
def test_workspace():
    """Provides a real, temporary workspace managed by WorkspaceManager."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = WorkspaceManager(root_dir=tmpdir)
        # Create a sample file for testing
        ws.write_file("README.md", "# Test Workspace\nReal content for integration testing.\n")
        ws.write_file("main.py", "def hello():\n    return 'world'\n")
        yield ws


@pytest.fixture
def runtime_stack(test_workspace):
    """Initializes the real 7-component architecture stack without mocks."""
    ws = test_workspace
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
    runtime = SkillRuntime(
        skill_registry=skill_reg,
        agent_registry=agent_reg,
        mcp_manager=mcp_mgr,
        tool_dispatcher=dispatcher,
        workspace_manager=ws,
        llm_client=llm,
    )
    return {
        "workspace": ws,
        "skill_registry": skill_reg,
        "agent_registry": agent_reg,
        "mcp_manager": mcp_mgr,
        "builtin_registry": builtin_reg,
        "dispatcher": dispatcher,
        "llm": llm,
        "runtime": runtime,
    }


class TestPolicyEngine:
    """Tests ToolPolicy and RuntimePolicy operations."""

    def test_tool_policy_resolution(self):
        policy = ToolPolicy(
            allowed_tools={"read_file", "write_file", "terminal_execute"},
            required_tools={"filesystem.read", "filesystem.write"},
        )
        # Verify alias resolution
        assert policy.resolve_tool_name("filesystem.read") == "read_file"
        assert policy.resolve_tool_name("filesystem.write") == "write_file"
        assert policy.resolve_tool_name("git.status") == "git_status"
        assert policy.resolve_tool_name("unknown_tool") == "unknown_tool"

        # Verify allowed check handles aliases
        assert policy.is_tool_allowed("filesystem.read") is True
        assert policy.is_tool_allowed("read_file") is True
        assert policy.is_tool_allowed("destructive_tool") is False

    def test_runtime_policy_merging(self):
        p1 = RuntimePolicy(timeout_seconds=300, max_turns=20, approval_required=False, model_tier="fast")
        p2 = RuntimePolicy(timeout_seconds=150, max_turns=10, approval_required=True, model_tier="reasoning")
        merged = p1.merge(p2)

        # Conservative bounds
        assert merged.timeout_seconds == 150
        assert merged.max_turns == 10
        assert merged.approval_required is True
        assert merged.model_tier == "reasoning"


class TestSkillCompiler:
    """Tests compilation of passive markdown into structured ExecutionProcedure."""

    def test_compile_from_manifest(self):
        compiler = SkillCompiler()
        manifest = SkillManifest(
            name="test-debug-skill",
            description="Systematic debugging workflow",
            category="python",
            required_tools=["read_file", "terminal_execute", "ast_syntax_check"],
            permissions=["workspace:read", "terminal:execute"],
            system_instructions=(
                "## Workflow\n"
                "1. Investigate the failure and inspect log outputs.\n"
                "2. Apply surgical fix to the affected files.\n"
                "3. Verify fix using unit tests and syntax checks.\n"
            ),
        )
        compiled = compiler.compile(manifest)

        assert isinstance(compiled, CompiledSkill)
        assert compiled.name == "test-debug-skill"
        assert len(compiled.procedure.steps) == 3
        assert compiled.procedure.steps[0].step_number == 1
        assert "Investigate" in compiled.procedure.steps[0].name
        assert compiled.runtime_policy.agent_affinity == "CODER"
        assert "read_file" in compiled.tool_policy.allowed_tools


class TestExactCompatibilityRequirement:
    """
    Contract Requirement:
    If a skill requests:
      required_tools:
        - filesystem.read
        - browser.console
    The resolver must verify that both tools exist:
      search existing MCPs -> search existing dispatcher -> search built-in registry
      -> Only then create a thin adapter.
      Never create filesystem implementations manually.
      Use existing Python/Pathlib adapter already present.
    """

    def test_resolver_verification_hierarchy_and_thin_adapter(self, runtime_stack):
        stack = runtime_stack
        resolver = stack["runtime"].resolver
        verifier = resolver.verifier

        # 1. Verify filesystem.read resolves via existing Pathlib / Builtin tools (NO manual filesystem code)
        fs_status = verifier.verify_tool(
            tool_name="filesystem.read",
            builtin_registry=stack["builtin_registry"],
            mcp_manager=stack["mcp_manager"],
            dispatcher=stack["dispatcher"],
            workspace=stack["workspace"],
        )
        assert fs_status.exists is True
        assert "read_file" in fs_status.resolved_name or "Builtin" in fs_status.source or "Alias" in fs_status.source

        # 2. Verify browser.console resolves via Thin Adapter
        browser_status = verifier.verify_tool(
            tool_name="browser.console",
            builtin_registry=stack["builtin_registry"],
            mcp_manager=stack["mcp_manager"],
            dispatcher=stack["dispatcher"],
            workspace=stack["workspace"],
        )
        assert browser_status.exists is True
        assert browser_status.source == "Thin Adapter"
        assert browser_status.adapter_callable is not None

        # Execute the thin adapter and verify it returns a valid response
        adapter_res = browser_status.adapter_callable()
        assert adapter_res.get("success") is True
        assert "browser.console" in adapter_res.get("operation")

        # 3. Test end-to-end skill resolution with BOTH tools
        custom_manifest = SkillManifest(
            name="react-debug-skill",
            description="Debugging React applications in real browsers",
            category="react",
            required_tools=["filesystem.read", "browser.console"],
            system_instructions="1. Inspect files.\n2. Check browser console.",
        )
        # Register in skill registry
        stack["skill_registry"].register_skill(custom_manifest)

        plan = resolver.resolve(
            task_description="Debug React issue using browser console and file inspection",
            preferred_skills=["react-debug-skill"],
        )

        assert plan.is_executable is True
        assert "react-debug-skill" in [s.name for s in plan.skills]
        assert "filesystem.read" in plan.tool_bindings or "read_file" in plan.composite_tool_policy.tool_aliases.values()
        assert "browser.console" in plan.tool_bindings
        assert callable(plan.tool_bindings["browser.console"])


class TestSevenComponentIntegration:
    """Verifies integration across all 7 core systems."""

    def test_skill_registry_integration(self, runtime_stack):
        sr = runtime_stack["skill_registry"]
        skills = sr.list_skills()
        assert len(skills) >= 30, "Expected at least 30 real discovered skills"

    def test_agent_registry_integration(self, runtime_stack):
        ar = runtime_stack["agent_registry"]
        coder_agent = ar.get("CODER")
        assert coder_agent is not None
        assert coder_agent.name == "CODER"

    def test_mcp_manager_integration(self, runtime_stack):
        mcp = runtime_stack["mcp_manager"]
        servers = mcp.discover_servers()
        assert len(servers) >= 4, "Expected filesystem, git, terminal, memory reference servers"

    def test_unified_tool_dispatcher_integration(self, runtime_stack):
        dispatcher = runtime_stack["dispatcher"]
        tools = dispatcher.get_all_tools()
        assert len(tools) >= 50, "Expected full tool inventory in dispatcher"

    def test_workspace_manager_integration(self, runtime_stack):
        ws = runtime_stack["workspace"]
        assert ws.file_exists("README.md") is True
        content = ws.read_file("README.md")
        assert "Test Workspace" in content

    def test_langgraph_node_integration(self, runtime_stack):
        """Tests that create_langgraph_node integrates into LangGraph StateGraph."""
        from langgraph.graph import StateGraph, START, END

        runtime = runtime_stack["runtime"]
        lg_node = runtime.create_langgraph_node()

        # Build a real LangGraph StateGraph using the node
        builder = StateGraph(dict)
        builder.add_node("skill_node", lg_node)
        builder.add_edge(START, "skill_node")
        builder.add_edge("skill_node", END)
        graph = builder.compile()

        input_state = {
            "user_request": "Refactor codebase using code-simplification",
            "preferred_skills": ["code-simplification"],
            "target_file": "main.py",
        }
        output_state = graph.invoke(input_state)

        assert "skill_execution_report" in output_state
        assert "active_skills" in output_state
        assert "code-simplification" in output_state["active_skills"]

    def test_litellm_gateway_integration(self, runtime_stack):
        llm = runtime_stack["llm"]
        assert llm.default_model is not None


class TestSkillRuntimeExecution:
    """Verifies end-to-end procedural execution through SkillRuntime."""

    def test_execute_plan(self, runtime_stack):
        runtime = runtime_stack["runtime"]
        resolver = runtime.resolver

        plan = resolver.resolve(
            task_description="Inspect project structure and check status",
            preferred_skills=["api-and-interface-design"],
        )

        task_ctx = {
            "target_file": "README.md",
        }
        report = runtime.execute_plan(plan, task_context=task_ctx)

        assert isinstance(report, RuntimeExecutionReport)
        assert report.status in ("SUCCESS", "PARTIAL")
        assert len(report.step_results) > 0
        assert report.total_duration_seconds >= 0.0
