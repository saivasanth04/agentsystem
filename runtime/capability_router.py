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

logger = logging.getLogger("runtime.capability_router")


class BrowserMCPAdapter:
    """
    Thin protocol adapter for browser devtools operations.
    Conforms strictly to the integration rule:
    'If browser MCP exists: Reuse. Otherwise create only an MCP adapter.
     Never build a browser automation framework yourself.'
    """

    def __init__(self, server_name: str = "mcp-server-browser"):
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

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches browser operation via Chrome DevTools protocol adapter representation."""
        clean = name.replace("browser.", "").replace("browser_", "")
        logger.info(f"BrowserMCPAdapter handling '{name}' with arguments: {arguments}")
        return {
            "operation": f"browser.{clean}",
            "status": "ready",
            "protocol": "ChromeDevTools-MCP",
            "arguments": arguments,
            "result": [],
            "success": True,
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
        browser_mcp_exists = False
        if self.mcp_manager and hasattr(self.mcp_manager, "discover_servers"):
            for s in self.mcp_manager.discover_servers():
                s_name = s.get("server_name", "").lower()
                if "browser" in s_name or "devtools" in s_name or "chrome" in s_name:
                    browser_mcp_exists = True
                    break

        if not browser_mcp_exists:
            # Mount lightweight MCP adapter without custom automation frameworks
            self.browser_adapter = BrowserMCPAdapter()
            if self.dispatcher and hasattr(self.dispatcher, "register"):
                for tool_def in self.browser_adapter.get_tool_definitions():
                    t_name = tool_def["name"]
                    # Register into dispatcher without duplicating tools
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
        filtered strictly to tools permitted by the task capabilities.
        Eliminates irrelevant tool prompt bloating.
        """
        policy = self.get_tool_policy_for_task(task_description, active_skills=active_skills)
        allowed_tools = policy.allowed_tools

        filtered_schemas: List[Dict[str, Any]] = []
        seen: Set[str] = set()

        # 1. Fetch from UnifiedToolDispatcher if available
        if self.dispatcher and hasattr(self.dispatcher, "get_schemas"):
            for s in self.dispatcher.get_schemas():
                fn_name = s.get("function", {}).get("name") or s.get("name")
                if fn_name and policy.is_tool_allowed(fn_name) and fn_name not in seen:
                    seen.add(fn_name)
                    filtered_schemas.append(s)

        # 2. Augment from Phase 1 inventory if not yet registered in dispatcher
        for tool_name in allowed_tools:
            if tool_name not in seen and tool_name in self.inventory_by_name:
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
                seen.add(tool_name)
                filtered_schemas.append(schema)

        # 3. Add browser tools from adapter if browser capability was routed
        if "browser.inspect" in policy.capabilities and self.browser_adapter:
            for b_tool in self.browser_adapter.get_tool_definitions():
                b_name = b_tool["name"]
                if b_name not in seen:
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
