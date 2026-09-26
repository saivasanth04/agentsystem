"""
Capability Router.
Directs tasks into granular capability requirements, restricts tools to active task demands,
and bridges existing Phase 1 tools and Browser MCP adapters without creating duplicate tools.
Enforces the Tool State Machine: Only EXECUTABLE tools are exposed to the model.
"""
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from .tool_policy import CAPABILITY_TO_TOOLS, DEFAULT_TOOL_ALIASES, ToolPolicy
from .tool_state_machine import ToolStateMachine, ToolLifecycleState

logger = logging.getLogger("runtime.capability_router")


class BrowserMCPAdapter:
    """
    Protocol adapter for browser devtools operations.
    Conforms strictly to the Non-Negotiable Contract:
    - Never build a custom browser automation framework.
    - If browser MCP is connected (chrome-devtools/puppeteer), dispatch real calls.
    - If browser MCP is unavailable, tool state is MISSING; NEVER fabricate logs, DOM, or screenshots.
    """

    def __init__(self, mcp_manager: Optional[Any] = None, server_name: str = "chrome-devtools"):
        self.mcp_manager = mcp_manager
        self.server_name = server_name
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
        """Checks if a real browser MCP server is active."""
        if not self.mcp_manager:
            return False
        try:
            servers = self.mcp_manager.list_servers() if hasattr(self.mcp_manager, "list_servers") else []
            browser_servers = {"chrome-devtools", "puppeteer", "browser", "playwright"}
            return any(s.lower() in browser_servers for s in servers)
        except Exception:
            return False

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches browser operation to real MCP server or reports MISSING."""
        if not self.is_available():
            logger.warning(f"Browser operation '{name}' rejected: browser MCP server is not connected.")
            return {
                "success": False,
                "status": "MISSING",
                "tool": name,
                "error": (
                    f"Browser tool '{name}' is unavailable because no browser MCP server "
                    "(chrome-devtools or puppeteer) is currently connected. "
                    "Simulated or fabricated browser execution is strictly prohibited."
                ),
            }

        # Dispatch to real MCP server via mcp_manager
        try:
            return self.mcp_manager.call_tool(name, arguments)
        except Exception as e:
            return {
                "success": False,
                "status": "ERROR",
                "tool": name,
                "error": f"Real browser MCP call failed: {e}",
            }

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return list(self.tools)


class CapabilityRouter:
    """
    Coordinates capability routing:
    Task ──> Capabilities ──> Skill Runtime ──> Allowed Tools (State Machine: EXECUTABLE only) ──> LLM
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

        # Authoritative Tool State Machine
        self.state_machine = ToolStateMachine(
            dispatcher=self.dispatcher,
            mcp_manager=self.mcp_manager,
        )

        # Load Phase 1 Tool Inventory for declaration
        self.inventory_path = Path(inventory_path) if inventory_path else (
            Path(__file__).resolve().parents[1] / "tool_audit" / "tool_inventory.json"
        )
        self.tool_inventory: List[Dict[str, Any]] = []
        self.inventory_by_name: Dict[str, Dict[str, Any]] = {}
        self._load_inventory()

        # Browser tools adapter
        self.browser_adapter = BrowserMCPAdapter(mcp_manager=self.mcp_manager)
        self._init_browser_tools()

    def _load_inventory(self):
        """Loads and indexes the Phase 1 tool inventory and declares in state machine."""
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
                                self.state_machine.declare_tool(
                                    name=name,
                                    schema=item,
                                    source=item.get("source", "builtin"),
                                    server_name=item.get("server_name"),
                                )
                logger.info(f"Loaded {len(self.tool_inventory)} tools into state machine from {self.inventory_path}")
            except Exception as e:
                logger.warning(f"Failed to load tool inventory from {self.inventory_path}: {e}")

    def _init_browser_tools(self):
        """Mounts real browser tools into dispatcher if browser MCP exists, else registers explicit stub."""
        for tool_def in self.browser_adapter.get_tool_definitions():
            t_name = tool_def["name"]
            self.state_machine.declare_tool(
                name=t_name,
                schema=tool_def,
                source="mcp",
                server_name="chrome-devtools",
                capabilities=["browser.inspect"],
            )
            if self.dispatcher and hasattr(self.dispatcher, "register"):
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
                        req_norm = req.strip()
                        if req_norm in DEFAULT_TOOL_ALIASES:
                            cap_prefix = req_norm.split(".")[0]
                            capabilities.add(f"{cap_prefix}.read" if "read" in req_norm else req_norm)
                        for cap, tools in CAPABILITY_TO_TOOLS.items():
                            if req_norm in tools:
                                capabilities.add(cap)

        # 2. Heuristic capability classification based on task objective
        if any(w in clean_task for w in ["browser", "ui", "devtools", "dom", "css", "html", "inspect page"]):
            capabilities.add("browser.inspect")

        if any(w in clean_task for w in ["test", "pytest", "run test", "verify", "execute", "benchmark"]):
            capabilities.add("terminal.run")
            capabilities.add("verification.test")

        if any(w in clean_task for w in ["write", "edit", "create", "modify", "refactor", "patch", "fix", "update"]):
            capabilities.add("filesystem.read")
            capabilities.add("filesystem.write")

        if any(w in clean_task for w in ["read", "inspect", "search", "find", "check", "scan", "analyze"]):
            capabilities.add("filesystem.read")
            capabilities.add("codebase.search")

        if any(w in clean_task for w in ["git", "commit", "branch", "checkout", "diff", "repo", "stash"]):
            capabilities.add("git.inspect")
            if any(w in clean_task for w in ["commit", "branch", "checkout", "push", "restore"]):
                capabilities.add("git.mutate")

        if any(w in clean_task for w in ["remember", "store", "memory", "recall"]):
            capabilities.add("memory.read")
            capabilities.add("memory.write")

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
        """Returns tool names that are BOTH authorized by policy AND verified EXECUTABLE."""
        policy = self.get_tool_policy_for_task(task_description, active_skills=active_skills)
        candidate_tools = sorted(list(policy.allowed_tools))

        # Enforce State Machine: filter out missing, unhealthy, or inventory-only tools
        executable_records = self.state_machine.get_executable_tools(
            candidate_tool_names=candidate_tools,
            tool_policy=policy,
            active_capabilities=policy.capabilities,
        )
        return sorted([r.name for r in executable_records])

    def get_tool_schemas_for_task(
        self,
        task_description: str,
        active_skills: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns LiteLLM Gateway / OpenAI function calling schemas
        filtered strictly to tools that are AUTHORIZED and EXECUTABLE.
        Never exposes inventory-only or missing tools.
        """
        policy = self.get_tool_policy_for_task(task_description, active_skills=active_skills)
        allowed_executable_tools = set(self.get_allowed_tools_for_task(task_description, active_skills=active_skills))

        filtered_schemas: List[Dict[str, Any]] = []
        seen: Set[str] = set()

        # Fetch from UnifiedToolDispatcher for verified executable tools
        if self.dispatcher and hasattr(self.dispatcher, "get_schemas"):
            for s in self.dispatcher.get_schemas():
                fn_name = s.get("function", {}).get("name") or s.get("name")
                if fn_name and fn_name in allowed_executable_tools and fn_name not in seen:
                    seen.add(fn_name)
                    filtered_schemas.append(s)

        # Check browser tools only if browser MCP is actively available
        if "browser.inspect" in policy.capabilities and self.browser_adapter.is_available():
            for b_tool in self.browser_adapter.get_tool_definitions():
                b_name = b_tool["name"]
                if b_name in allowed_executable_tools and b_name not in seen:
                    seen.add(b_name)
                    filtered_schemas.append({
                        "type": "function",
                        "function": b_tool,
                    })

        return filtered_schemas

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
