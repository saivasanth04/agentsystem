"""
Comprehensive Integration Tests for Capability-Driven Tool Runtime.
Verifies compliance with the 10 Non-Negotiable Contract clauses:
- Real executable classes (Zero mocks, zero placeholders)
- Capability-driven architecture:
  Task -> Capabilities -> Skill Runtime -> Allowed Tools -> LLM
- Integration rules:
  1. Phase 1 inventory ingestion (tool_audit/tool_inventory.json)
  2. dispatcher.register(existing_tool) - zero duplicate tool wrapper classes
  3. Browser MCP adapter (browser.console, browser.inspect) without custom browser frameworks
- Integration across all 7 core systems:
  1. SkillRegistry
  2. AgentRegistry
  3. MCPManager
  4. UnifiedToolDispatcher
  5. WorkspaceManager
  6. LangGraph (StateGraph dynamic tool scoping)
  7. LiteLLM Gateway
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

from runtime.tool_policy import ToolPolicy, DEFAULT_TOOL_ALIASES, CAPABILITY_TO_TOOLS
from runtime.capability_router import CapabilityRouter, BrowserMCPAdapter
from runtime.permission_engine import PermissionEngine, PermissionEvaluationResult


@pytest.fixture
def test_workspace():
    """Provides a temporary workspace with real files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = WorkspaceManager(root_dir=tmpdir)
        ws.write_file("app.py", "def main():\n    print('Hello world')\n")
        ws.write_file("test_app.py", "def test_app():\n    assert True\n")
        yield ws


@pytest.fixture
def core_systems(test_workspace):
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
    return {
        "workspace": ws,
        "skill_registry": skill_reg,
        "agent_registry": agent_reg,
        "mcp_manager": mcp_mgr,
        "builtin_registry": builtin_reg,
        "dispatcher": dispatcher,
        "llm": llm,
    }


class TestToolPolicy:
    """Verifies capability mapping, alias resolution, and policy merging."""

    def test_alias_resolution(self):
        policy = ToolPolicy(
            allowed_tools={"read_file", "write_file", "git_status", "browser_inspect"},
        )
        assert policy.resolve_tool_name("filesystem.read") == "read_file"
        assert policy.resolve_tool_name("filesystem:read") == "read_file"
        assert policy.resolve_tool_name("git.status") == "git_status"
        assert policy.resolve_tool_name("browser.inspect") == "browser_inspect"
        assert policy.resolve_tool_name("unknown.tool") == "unknown.tool"

    def test_capability_expansion(self):
        policy = ToolPolicy.from_capabilities(
            ["filesystem.read", "git.inspect", "browser.inspect"],
            read_only=True,
        )
        # Should allow read tools
        assert policy.is_tool_allowed("read_file") is True
        assert policy.is_tool_allowed("filesystem.read") is True
        assert policy.is_tool_allowed("git_status") is True
        assert policy.is_tool_allowed("browser_inspect") is True

        # Should block write tools under read_only
        assert policy.is_tool_allowed("write_file") is False
        assert policy.is_tool_allowed("git_commit") is False

    def test_forbidden_tools_override(self):
        policy = ToolPolicy.from_capabilities(
            ["filesystem.read", "terminal.run"],
            extra_forbidden=["terminal_execute"],
        )
        assert policy.is_tool_allowed("read_file") is True
        # Explicit forbidden overrides capability grant
        assert policy.is_tool_allowed("terminal_execute") is False
        assert policy.is_tool_allowed("terminal.run") is False

    def test_policy_merging(self):
        p1 = ToolPolicy(allowed_tools={"read_file"}, approval_required=False, read_only=False)
        p2 = ToolPolicy(allowed_tools={"write_file"}, approval_required=True, read_only=True)
        merged = p1.merge(p2)

        assert "read_file" in merged.allowed_tools
        assert "write_file" in merged.allowed_tools
        assert merged.approval_required is True
        assert merged.read_only is True


class TestCapabilityRouter:
    """Verifies Task -> Capabilities -> Allowed Tools pipeline and browser MCP adapter."""

    def test_phase1_inventory_loading(self, core_systems):
        router = CapabilityRouter(
            dispatcher=core_systems["dispatcher"],
            mcp_manager=core_systems["mcp_manager"],
            skill_registry=core_systems["skill_registry"],
            workspace_manager=core_systems["workspace"],
        )
        assert len(router.tool_inventory) > 0
        assert "read_file" in router.inventory_by_name
        assert "git_status" in router.inventory_by_name

    def test_browser_mcp_adapter_integration(self, core_systems):
        router = CapabilityRouter(
            dispatcher=core_systems["dispatcher"],
            mcp_manager=core_systems["mcp_manager"],
        )
        assert router.browser_adapter is not None

        tools = router.browser_adapter.get_tool_definitions()
        tool_names = [t["name"] for t in tools]
        assert "browser_console" in tool_names
        assert "browser_inspect" in tool_names
        assert "browser_network" in tool_names
        assert "browser_snapshot" in tool_names

        # Call adapter
        res = router.browser_adapter.call_tool("browser.inspect", {"selector": "#main-app"})
        assert res["success"] is True
        assert res["protocol"] == "ChromeDevTools-MCP"
        assert res["arguments"]["selector"] == "#main-app"

    def test_route_task_to_capabilities(self, core_systems):
        router = CapabilityRouter(
            dispatcher=core_systems["dispatcher"],
            skill_registry=core_systems["skill_registry"],
        )

        # 1. UI task -> browser capability
        ui_caps = router.route_task("Inspect button styles and browser console errors on login page")
        assert "browser.inspect" in ui_caps

        # 2. Test task -> terminal & verification capabilities
        test_caps = router.route_task("Run pytest and fix broken assertions")
        assert "terminal.run" in test_caps or "verification.test" in test_caps

        # 3. Code edit task -> filesystem capabilities
        edit_caps = router.route_task("Refactor authentication token generator in app.py")
        assert "filesystem.read" in edit_caps
        assert "filesystem.write" in edit_caps

        # 4. Git task -> git capabilities
        git_caps = router.route_task("Create a git branch and commit changes")
        assert "git.inspect" in git_caps
        assert "git.mutate" in git_caps

    def test_get_allowed_tools_for_task(self, core_systems):
        router = CapabilityRouter(dispatcher=core_systems["dispatcher"])

        # When task is purely browser inspection, filesystem write should not be included
        allowed = router.get_allowed_tools_for_task("Inspect browser DOM and verify layout")
        assert "browser_inspect" in allowed
        assert "write_file" not in allowed

    def test_get_tool_schemas_for_task(self, core_systems):
        router = CapabilityRouter(dispatcher=core_systems["dispatcher"])

        schemas = router.get_tool_schemas_for_task("Inspect browser console for JavaScript errors")
        schema_names = [s.get("function", {}).get("name") for s in schemas]

        assert any("browser" in name for name in schema_names)
        # Verify schema structure complies with OpenAI/LiteLLM format
        first_schema = schemas[0]
        assert first_schema["type"] == "function"
        assert "name" in first_schema["function"]
        assert "parameters" in first_schema["function"]


class TestPermissionEngine:
    """Verifies security boundaries, workspace sandboxing, and execution safety."""

    def test_capability_authorization_check(self, core_systems):
        engine = PermissionEngine(
            workspace_manager=core_systems["workspace"],
            dispatcher=core_systems["dispatcher"],
        )

        # Policy allowing only read
        policy = ToolPolicy.from_capabilities(["filesystem.read"])

        # Allowed read
        res_read = engine.evaluate_tool_call("read_file", {"path": "app.py"}, policy=policy)
        assert res_read.allowed is True

        # Blocked write
        res_write = engine.evaluate_tool_call("write_file", {"path": "app.py", "content": "test"}, policy=policy)
        assert res_write.allowed is False
        assert res_write.violation_category == "UNAUTHORIZED_TOOL"

    def test_workspace_path_traversal_blocking(self, core_systems):
        engine = PermissionEngine(
            workspace_manager=core_systems["workspace"],
            dispatcher=core_systems["dispatcher"],
        )

        policy = ToolPolicy.from_capabilities(["filesystem.read", "filesystem.write"])

        # Directory traversal attempt
        res = engine.evaluate_tool_call(
            "read_file",
            {"path": "../../etc/shadow"},
            policy=policy,
        )
        assert res.allowed is False
        assert res.violation_category == "PATH_TRAVERSAL"
        assert "escapes workspace root" in res.reason

    def test_dangerous_command_blocking(self, core_systems):
        engine = PermissionEngine(
            workspace_manager=core_systems["workspace"],
            dispatcher=core_systems["dispatcher"],
        )

        policy = ToolPolicy.from_capabilities(["terminal.run"])

        # Dangerous command
        res = engine.evaluate_tool_call(
            "terminal_execute",
            {"command": "rm -rf /"},
            policy=policy,
        )
        assert res.allowed is False
        assert res.violation_category == "DANGEROUS_COMMAND"
        assert "strictly blocked" in res.reason

        # Safe command
        res_safe = engine.evaluate_tool_call(
            "terminal_execute",
            {"command": "pytest test_app.py"},
            policy=policy,
        )
        assert res_safe.allowed is True

    def test_approval_requirement(self, core_systems):
        engine = PermissionEngine(
            workspace_manager=core_systems["workspace"],
            dispatcher=core_systems["dispatcher"],
        )

        policy = ToolPolicy.from_capabilities(["filesystem.write"])

        # delete_file requires approval by default
        res = engine.evaluate_tool_call("delete_file", {"path": "app.py"}, policy=policy)
        assert res.requires_approval is True
        assert res.violation_category == "APPROVAL_REQUIRED"

        # If user approved, passes
        res_approved = engine.evaluate_tool_call(
            "delete_file",
            {"path": "app.py"},
            policy=policy,
            context={"user_approved": True},
        )
        assert res_approved.requires_approval is False


class TestSevenSystemsAndLangGraphIntegration:
    """Verifies full integration across all 7 core systems and LangGraph StateGraph."""

    def test_langgraph_node_integration(self, core_systems):
        from langgraph.graph import StateGraph, START, END

        router = CapabilityRouter(
            dispatcher=core_systems["dispatcher"],
            mcp_manager=core_systems["mcp_manager"],
            skill_registry=core_systems["skill_registry"],
            workspace_manager=core_systems["workspace"],
        )

        lg_node = router.create_langgraph_node()

        builder = StateGraph(dict)
        builder.add_node("capability_router", lg_node)
        builder.add_edge(START, "capability_router")
        builder.add_edge("capability_router", END)
        graph = builder.compile()

        input_state = {
            "user_request": "Inspect browser network latency and check console errors",
        }
        output_state = graph.invoke(input_state)

        assert "task_capabilities" in output_state
        assert "allowed_tools" in output_state
        assert "tool_schemas" in output_state
        assert "browser.inspect" in output_state["task_capabilities"]
        assert "browser_console" in output_state["allowed_tools"]
        assert len(output_state["tool_schemas"]) > 0

    def test_execute_with_permission_via_dispatcher(self, core_systems):
        ws = core_systems["workspace"]
        engine = PermissionEngine(
            workspace_manager=ws,
            dispatcher=core_systems["dispatcher"],
        )

        policy = ToolPolicy.from_capabilities(["filesystem.read"])

        # Execute permitted tool
        res = engine.execute_with_permission("read_file", {"path": "app.py"}, policy=policy)
        assert res["success"] is True

        # Execute denied tool
        res_denied = engine.execute_with_permission(
            "terminal_execute",
            {"command": "python app.py"},
            policy=policy,
        )
        assert res_denied["success"] is False
        assert res_denied["is_error"] is True

    def test_in_tree_backward_compatibility(self):
        """Verifies agent_orchestrator.runtime re-exports the capability runtime classes."""
        from agent_orchestrator.runtime import (
            ToolPolicy as OrchestratorToolPolicy,
            CapabilityRouter as OrchestratorCapabilityRouter,
            PermissionEngine as OrchestratorPermissionEngine,
            BrowserMCPAdapter as OrchestratorBrowserMCPAdapter,
        )

        assert OrchestratorToolPolicy is ToolPolicy
        assert OrchestratorCapabilityRouter is CapabilityRouter
        assert OrchestratorPermissionEngine is PermissionEngine
        assert OrchestratorBrowserMCPAdapter is BrowserMCPAdapter
