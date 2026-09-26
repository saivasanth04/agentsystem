"""
Capability Router.
Directs tasks into granular capability requirements, restricts tools to active task demands,
and bridges existing Phase 1 tools and Browser MCP adapters without creating duplicate tools.
"""
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from .tool_policy import CAPABILITY_TO_TOOLS, DEFAULT_TOOL_ALIASES, ToolPolicy
from .tool_state_machine import ToolLifecycleState, ToolStateMachine

logger = logging.getLogger("runtime.capability_router")


class BrowserMCPAdapter:
    """
    Thin protocol adapter for browser devtools operations.
    Conforms strictly to the integration rule:
    'If browser MCP exists: Reuse. Otherwise create only an MCP adapter.
     Never build a browser automation framework yourself.'
    """

    def __init__(self, server_name: str = "mcp-server-browser", mcp_manager: Optional[Any] = None):
        self.server_name = server_name
        self.mcp_manager = mcp_manager
        self.tools = [
            {
                "name": "browser_console",
                "description": "Inspects console errors, warnings, and log messages from the browser devtools protocol.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "level": {"type": "string", "enum": ["error", "warning", "info", "all"], "default": "error"}
                    },
                    "required": []
                }
            },
            {
                "name": "browser_inspect",
                "description": "Inspects DOM nodes, CSS computed styles, and accessibility attributes via Chrome DevTools protocol.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS selector to inspect"}
                    },
                    "required": ["selector"]
                }
            },
            {
                "name": "browser_network",
                "description": "Inspects active HTTP network requests, latency, headers, and failed requests via DevTools protocol.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status_filter": {"type": "string", "description": "Filter by status e.g. 'failed' or '4xx'"}
                    },
                    "required": []
                }
            },
            {
                "name": "browser_snapshot",
                "description": "Captures DOM tree snapshot or layout structure for verification.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "full_page": {"type": "boolean", "default": False}
                    },
                    "required": []
                }
            }
        ]

    def is_available(self) -> bool:
        """Checks if a real Chrome DevTools or Puppeteer MCP server is reachable."""
        if not self.mcp_manager:
            return False
        if hasattr(self.mcp_manager, "is_server_running"):
            return bool(
                self.mcp_manager.is_server_running("chrome-devtools")
                or self.mcp_manager.is_server_running("puppeteer")
                or self.mcp_manager.is_server_running(self.server_name)
            )
        if hasattr(self.mcp_manager, "list_servers"):
            try:
                servers = self.mcp_manager.list_servers()
                return any("browser" in s.lower() or "devtools" in s.lower() or "puppeteer" in s.lower() for s in servers)
            except Exception:
                return False
        return False

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatches browser operation via real Chrome DevTools/Puppeteer MCP if available.
        Strict invariant: If browser MCP is unavailable, state is MISSING. Never fabricate success!
        """
        if not self.is_available():
            logger.warning("Browser tool '%s' called but no active browser MCP server found (MISSING).", name)
            return {
                "success": False,
                "error": f"Browser tool '{name}' is not available / unavailable (state: MISSING). Real browser operations require an active Chrome DevTools or Puppeteer MCP server.",
                "state": "MISSING",
                "operation": name,
                "arguments": arguments,
            }

        # Real MCP dispatch
        clean = name.replace("browser.", "").replace("browser_", "")
        logger.info("BrowserMCPAdapter dispatching '%s' to real MCP server '%s'", name, self.server_name)
        try:
            if hasattr(self.mcp_manager, "call_tool"):
                return self.mcp_manager.call_tool(self.server_name, clean, arguments)
            return {
                "success": False,
                "error": f"MCP manager unable to dispatch tool '{clean}' to server '{self.server_name}'",
                "operation": name,
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Browser MCP execution error: {str(e)}",
                "operation": name,
            }

    def get_status(self) -> Dict[str, Any]:
        """Returns the real connectivity status and lifecycle state of the browser adapter."""
        avail = self.is_available()
        return {
            "state": "EXECUTABLE" if avail else "MISSING",
            "connected": avail,
            "server": self.server_name,
        }

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return list(self.tools)


class CapabilityRouter:
    """
    Coordinates capability routing:
    Task ──> Capabilities ──> Skill Runtime ──> Allowed Tools ──> LLM
    """

    def __init__(
        self,
        dispatcher: Optional[Any] = None,
        mcp_manager: Optional[Any] = None,
        skill_registry: Optional[Any] = None,
        workspace_manager: Optional[Any] = None,
        inventory_path: Optional[Union[str, Path]] = None,
        **kwargs: Any,
    ):
        self.dispatcher = dispatcher or kwargs.get("tool_dispatcher")
        self.mcp_manager = mcp_manager
        self.skill_registry = skill_registry
        self.workspace = workspace_manager

        # Lazy defaults if not injected
        if self.workspace is None:
            try:
                from agent_orchestrator.tools.workspace import WorkspaceManager
                self.workspace = WorkspaceManager()
            except Exception as e:
                logger.debug(f"WorkspaceManager default initialization deferred: {e}")

        if self.skill_registry is None:
            try:
                from agent_orchestrator.registry.skill_registry import SkillRegistry
                self.skill_registry = SkillRegistry()
            except Exception as e:
                logger.debug(f"SkillRegistry default initialization deferred: {e}")

        if self.mcp_manager is None and self.workspace:
            try:
                from agent_orchestrator.mcp.manager import MCPManager
                self.mcp_manager = MCPManager(workspace_dir=self.workspace.root_dir)
            except Exception as e:
                logger.debug(f"MCPManager default initialization deferred: {e}")

        # Load Phase 1 Tool Inventory
        self.inventory_path = Path(inventory_path) if inventory_path else (
            Path(__file__).resolve().parents[1] / "tool_audit" / "tool_inventory.json"
        )
        self.tool_inventory: List[Dict[str, Any]] = []
        self.inventory_by_name: Dict[str, Dict[str, Any]] = {}
        self._load_inventory()

        # Browser tools check and adapter setup
        self.browser_adapter: Optional[BrowserMCPAdapter] = None
        self._init_browser_tools()

        # Tool State Machine (Fix 6: Tool Lifecycle Governance)
        self.tool_state_machine = ToolStateMachine(
            dispatcher=self.dispatcher,
            mcp_manager=self.mcp_manager,
            browser_adapter=self.browser_adapter,
        )
        self.tool_sm = self.tool_state_machine

    def _load_inventory(self):
        """Loads and indexes the Phase 1 tool inventory."""
        if self.inventory_path.exists():
            try:
                with open(self.inventory_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.tool_inventory = data
                        for item in data:
                            name = item.get("name")
                            if name:
                                self.inventory_by_name[name] = item
                logger.info(f"Loaded {len(self.tool_inventory)} tools from {self.inventory_path}")
            except Exception as e:
                logger.warning(f"Failed to load tool inventory from {self.inventory_path}: {e}")

    def _init_browser_tools(self):
        """
        Adheres to rule:
        If browser MCP exists: Reuse. Otherwise create only an MCP adapter.
        Never build a browser automation framework yourself.
        """
        self.browser_adapter = BrowserMCPAdapter(mcp_manager=self.mcp_manager)
        if self.browser_adapter.is_available() and self.dispatcher and hasattr(self.dispatcher, "register"):
            for tool_def in self.browser_adapter.get_tool_definitions():
                t_name = tool_def["name"]
                def _make_handler(name=t_name):
                    return lambda **kwargs: self.browser_adapter.call_tool(name, kwargs)

                try:
                    self.dispatcher.register(
                        name=t_name,
                        description=tool_def["description"],
                        parameters=tool_def["parameters"],
                        handler=_make_handler(t_name),
                        category="browser",
                    )
                except Exception as e:
                    logger.debug(f"Dispatcher registration for browser tool {t_name}: {e}")

    def route_task(
        self,
        task_description: str,
        active_skills: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """
        Infers exact required capabilities from task description and active skills.
        Ensures tools become capability-driven rather than agent-driven.
        """
        clean_task = (task_description or "").lower()
        capabilities: Set[str] = set()

        # 1. Map from active skills
        if active_skills and self.skill_registry:
            for s_name in active_skills:
                manifest = self.skill_registry.get_skill(s_name)
                if manifest and hasattr(manifest, "required_tools"):
                    for req in manifest.required_tools:
                        # Map required tool to capability
                        req_norm = req.strip()
                        if req_norm in DEFAULT_TOOL_ALIASES:
                            # e.g. 'filesystem.read' -> capability 'filesystem.read'
                            cap_prefix = req_norm.split(".")[0]
                            capabilities.add(f"{cap_prefix}.read" if "read" in req_norm else req_norm)
                        for cap, tools in CAPABILITY_TO_TOOLS.items():
                            if req_norm in tools:
                                capabilities.add(cap)

        # 2. Heuristic capability classification based on task objective
        # Browser / UI / DevTools
        if any(w in clean_task for w in ["browser", "ui", "devtools", "dom", "css", "html", "inspect page"]):
            capabilities.add("browser.inspect")

        # Tests / Execution
        if any(w in clean_task for w in ["test", "pytest", "run test", "verify", "execute", "benchmark"]):
            capabilities.add("terminal.run")
            capabilities.add("verification.test")

        # Filesystem write / Edit / Refactor
        if any(w in clean_task for w in ["write", "edit", "create", "modify", "refactor", "patch", "fix", "update"]):
            capabilities.add("filesystem.read")
            capabilities.add("filesystem.write")

        # Filesystem read / Inspect / Analyze
        if any(w in clean_task for w in ["read", "inspect", "search", "find", "check", "scan", "analyze"]):
            capabilities.add("filesystem.read")
            capabilities.add("codebase.search")

        # Git operations
        if any(w in clean_task for w in ["git", "commit", "branch", "checkout", "diff", "repo", "stash"]):
            capabilities.add("git.inspect")
            if any(w in clean_task for w in ["commit", "branch", "checkout", "push", "restore"]):
                capabilities.add("git.mutate")

        # Memory operations
        if any(w in clean_task for w in ["remember", "store", "memory", "recall"]):
            capabilities.add("memory.read")
            capabilities.add("memory.write")

        # Default fallback: safe baseline read capability
        if not capabilities:
            capabilities.add("filesystem.read")
            capabilities.add("codebase.search")

        return sorted(list(capabilities))

    def get_tool_policy_for_task(
        self,
        task_description: str,
        active_skills: Optional[List[str]] = None,
        read_only: bool = False,
    ) -> ToolPolicy:
        """Generates a capability-enforced ToolPolicy tailored for the specific task."""
        caps = self.route_task(task_description, active_skills=active_skills)
        return ToolPolicy.from_capabilities(caps, read_only=read_only)

    def get_allowed_tools_for_task(
        self,
        task_description: str,
        active_skills: Optional[List[str]] = None,
    ) -> List[str]:
        """Returns concrete tool names allowed for this task based on required capabilities."""
        policy = self.get_tool_policy_for_task(task_description, active_skills=active_skills)
        return sorted(list(policy.allowed_tools))

    def get_tool_schemas_for_task(
        self,
        task_description: str,
        active_skills: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns LiteLLM Gateway / OpenAI function calling schemas
        filtered strictly to tools permitted by task capabilities
        and verified in EXECUTABLE state by the ToolStateMachine.
        Enforces Fix 6 & Fix 8: Never exposes inventory-only or unauthorized tools.
        """
        policy = self.get_tool_policy_for_task(task_description, active_skills=active_skills)
        allowed_tools = set(policy.allowed_tools)

        # 1. Discover registered tools in state machine
        self.tool_state_machine.discover_tools()

        # 2. Ingest inventory schemas if declared
        for tool_name in allowed_tools:
            if tool_name not in self.tool_state_machine._tools and tool_name in self.inventory_by_name:
                item = self.inventory_by_name[tool_name]
                schema = {
                    "type": "function",
                    "function": {
                        "name": item.get("name", tool_name),
                        "description": item.get("description", ""),
                        "parameters": {
                            "type": "object",
                            "properties": item.get("parameters", {}),
                            "required": item.get("required_parameters", []),
                        },
                    },
                }
                self.tool_state_machine.declare_tool(
                    name=tool_name,
                    source="inventory",
                    schema=schema,
                    description=item.get("description", ""),
                )
                self.tool_state_machine.transition(tool_name, ToolLifecycleState.DISCOVERED)

        # 3. Handle browser tools: declare them; mark EXECUTABLE only if real browser MCP running
        if "browser.inspect" in policy.capabilities and self.browser_adapter:
            self.tool_state_machine.browser_adapter = self.browser_adapter
            for b_tool in self.browser_adapter.get_tool_definitions():
                b_name = b_tool["name"]
                if b_name not in self.tool_state_machine._tools:
                    self.tool_state_machine.declare_tool(
                        name=b_name,
                        source="browser_mcp",
                        schema={"type": "function", "function": b_tool},
                        description=b_tool.get("description", ""),
                    )
                if self.browser_adapter.is_available():
                    self.tool_state_machine.transition(b_name, ToolLifecycleState.DISCOVERED)
                    if self.tool_state_machine.verify_health(b_name):
                        self.tool_state_machine.transition(b_name, ToolLifecycleState.AUTHORIZED)
                        self.tool_state_machine.transition(b_name, ToolLifecycleState.EXECUTABLE)
                else:
                    self.tool_state_machine.transition(b_name, ToolLifecycleState.MISSING, error_reason="No real Chrome DevTools or Puppeteer MCP server connected")

        # 4. Authorize allowed tools through state machine
        for tool_name in allowed_tools:
            if self.tool_state_machine.authorize(tool_name, allowed_tools=allowed_tools):
                self.tool_state_machine.promote_to_executable(tool_name)

        # 5. Invariant: Only EXECUTABLE tools may be exposed to the model
        return self.tool_state_machine.get_executable_schemas()

    def create_langgraph_node(self) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        """
        Creates a LangGraph-compatible StateGraph node function.
        Dynamically calculates allowed tools and updates state for downstream LLM execution.
        """
        def capability_router_node(state: Dict[str, Any]) -> Dict[str, Any]:
            task = state.get("user_request") or state.get("task") or state.get("objective") or "Execute task"
            skills = state.get("active_skills") or []

            caps = self.route_task(task, active_skills=skills)
            policy = self.get_tool_policy_for_task(task, active_skills=skills)
            allowed = self.get_allowed_tools_for_task(task, active_skills=skills)
            schemas = self.get_tool_schemas_for_task(task, active_skills=skills)

            new_state = dict(state)
            new_state["task_capabilities"] = caps
            new_state["allowed_tools"] = allowed
            new_state["tool_policy"] = policy.to_dict()
            new_state["tool_schemas"] = schemas
            return new_state

        return capability_router_node
