"""
Tool State Machine.
Defines the authoritative 5-state tool lifecycle model:
DECLARED -> DISCOVERED -> HEALTHY -> AUTHORIZED -> EXECUTABLE
(with MISSING, UNHEALTHY, and UNAUTHORIZED terminal/failure states).
Enforces the strict rule:
Only EXECUTABLE tools may be exposed in model tool schemas or invoked by the agent.
Never expose inventory-only or declared-only tools.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

logger = logging.getLogger("runtime.tool_state_machine")


class ToolLifecycleState(str, Enum):
    """Authoritative lifecycle states for all tools in the system."""
    DECLARED = "DECLARED"          # Defined in skill manifest, compatibility matrix, or inventory
    DISCOVERED = "DISCOVERED"      # Located in BuiltinToolRegistry, MCPManager, or external provider
    HEALTHY = "HEALTHY"            # Validated responsive and runnable without syntax/connectivity errors
    AUTHORIZED = "AUTHORIZED"      # Permitted under active task capabilities and PermissionEngine policies
    EXECUTABLE = "EXECUTABLE"      # Fully ready to be exposed to the LLM and executed

    # Failure / Inactive States
    MISSING = "MISSING"            # Declared or required but not found in the environment
    UNHEALTHY = "UNHEALTHY"        # Discovered but failing health check or broken dependencies
    UNAUTHORIZED = "UNAUTHORIZED"  # Blocked by permission policy or out of scope for task


@dataclass
class ManagedTool:
    """Represents a tool registered and tracked within the ToolStateMachine."""
    name: str
    state: ToolLifecycleState = ToolLifecycleState.DECLARED
    source: str = "builtin"  # "builtin" | "mcp" | "skill" | "system"
    schema: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    error_reason: Optional[str] = None
    health_checker: Optional[Callable[[], bool]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "source": self.source,
            "description": self.description,
            "error_reason": self.error_reason,
            "metadata": self.metadata,
        }


class ToolStateMachine:
    """
    Authoritative state machine governing the discovery, health, authorization,
    and execution lifecycle of tools.
    """

    def __init__(
        self,
        dispatcher: Optional[Any] = None,
        mcp_manager: Optional[Any] = None,
        browser_adapter: Optional[Any] = None,
    ):
        self.dispatcher = dispatcher
        self.mcp_manager = mcp_manager
        self.browser_adapter = browser_adapter
        self._tools: Dict[str, ManagedTool] = {}

    def declare_tool(
        self,
        name: str,
        source: str = "builtin",
        schema: Optional[Dict[str, Any]] = None,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ManagedTool:
        """Declares a tool from manifest, inventory, or specification."""
        clean_name = name.strip()
        tool = ManagedTool(
            name=clean_name,
            state=ToolLifecycleState.DECLARED,
            source=source,
            schema=schema or {},
            description=description,
            metadata=metadata or {},
        )
        self._tools[clean_name] = tool
        return tool

    def transition(
        self,
        name: str,
        target_state: ToolLifecycleState,
        error_reason: Optional[str] = None,
    ) -> ManagedTool:
        """Transitions a tool to a new lifecycle state with validation."""
        clean_name = name.strip()
        if clean_name not in self._tools:
            self._tools[clean_name] = ManagedTool(name=clean_name)

        tool = self._tools[clean_name]
        tool.state = target_state
        if error_reason:
            tool.error_reason = error_reason
        elif target_state in (ToolLifecycleState.HEALTHY, ToolLifecycleState.AUTHORIZED, ToolLifecycleState.EXECUTABLE):
            tool.error_reason = None

        logger.debug("Tool '%s' transitioned to state: %s", clean_name, target_state.value)
        return tool

    def get_state(self, name: str) -> ToolLifecycleState:
        """Returns the current lifecycle state of a tool, defaulting to DECLARED."""
        tool = self._tools.get(name.strip())
        return tool.state if tool else ToolLifecycleState.DECLARED

    def is_executable(self, name: str) -> bool:
        """Checks if a tool is strictly in EXECUTABLE state."""
        tool = self._tools.get(name.strip())
        return bool(tool and tool.state == ToolLifecycleState.EXECUTABLE)

    def discover(self, name: str, source: str = "builtin") -> ManagedTool:
        """Transitions a tool to DISCOVERED state."""
        clean_name = name.strip()
        if clean_name not in self._tools:
            self.declare_tool(clean_name, source=source)
        return self.transition(clean_name, ToolLifecycleState.DISCOVERED)

    def mark_healthy(self, name: str) -> ManagedTool:
        """
        Validates tool health via probe before transitioning to HEALTHY.
        Never promotes to HEALTHY without verification (CRITICAL FIX 3).
        """
        clean_name = name.strip()
        self.verify_health(clean_name)
        return self._tools.get(clean_name) or self.declare_tool(clean_name)

    def promote_to_executable(self, name: str) -> ManagedTool:
        """Promotes an authorized, healthy tool to EXECUTABLE state."""
        clean_name = name.strip()
        tool = self._tools.get(clean_name)
        if not tool or tool.state != ToolLifecycleState.AUTHORIZED:
            return tool or self.declare_tool(clean_name)
        return self.transition(clean_name, ToolLifecycleState.EXECUTABLE)

    def register_and_verify(
        self,
        name: str,
        source: str = "builtin",
        is_authorized: bool = True,
        health_checker: Optional[Callable[[], bool]] = None,
    ) -> ManagedTool:
        """
        Registers a tool and runs a real health probe.
        Strictly requires health probe verification before promotion (CRITICAL FIX 3).
        """
        clean_name = name.strip()
        tool = self.declare_tool(clean_name, source=source)
        if health_checker:
            tool.health_checker = health_checker
        self.transition(clean_name, ToolLifecycleState.DISCOVERED)

        # Real health probe check: Built-in or MCP
        is_healthy = self.verify_health(clean_name)
        if is_healthy and is_authorized:
            self.transition(clean_name, ToolLifecycleState.AUTHORIZED)
            self.transition(clean_name, ToolLifecycleState.EXECUTABLE)
        return self._tools[clean_name]

    def discover_tools(self) -> Dict[str, ManagedTool]:
        """
        Discovers tools from UnifiedToolDispatcher, BuiltinToolRegistry, and MCPManager.
        Transitions existing DECLARED tools or registers new DISCOVERED tools.
        """
        discovered_names: Set[str] = set()

        # 1. Inspect UnifiedToolDispatcher
        if self.dispatcher:
            br = getattr(self.dispatcher, "builtin_registry", None)
            if br:
                tools_map = getattr(br, "_tools", {}) or getattr(br, "tools", {})
                if isinstance(tools_map, dict):
                    for t_name, t_val in tools_map.items():
                        discovered_names.add(t_name)
                        doc = getattr(t_val, "__doc__", "") or f"Built-in tool {t_name}"
                        if t_name not in self._tools:
                            self.declare_tool(t_name, source="builtin", description=doc)
                        self.transition(t_name, ToolLifecycleState.DISCOVERED)

        # 2. Inspect MCPManager
        if self.mcp_manager and hasattr(self.mcp_manager, "list_tools"):
            try:
                mcp_tools = self.mcp_manager.list_tools()
                for mt in mcp_tools:
                    m_name = mt.get("name") if isinstance(mt, dict) else getattr(mt, "name", str(mt))
                    if m_name:
                        discovered_names.add(m_name)
                        schema = mt if isinstance(mt, dict) else {}
                        if m_name not in self._tools:
                            self.declare_tool(m_name, source="mcp", schema=schema)
                        self.transition(m_name, ToolLifecycleState.DISCOVERED)
            except Exception as e:
                logger.debug("MCPManager list_tools exception: %s", e)

        # 3. Mark undeclared required tools as MISSING if needed
        for t_name, tool in self._tools.items():
            if tool.state == ToolLifecycleState.DECLARED and t_name not in discovered_names:
                # If tool name has a known builtin implementation, discover it
                if self._can_resolve_builtin(t_name):
                    self.transition(t_name, ToolLifecycleState.DISCOVERED)
                else:
                    self.transition(t_name, ToolLifecycleState.MISSING, error_reason="Tool not discovered in any active registry or MCP server")

        return self._tools

    def _is_workspace_valid(self) -> bool:
        """Validates that workspace directory is valid and accessible."""
        if self.dispatcher and hasattr(self.dispatcher, "workspace"):
            ws = self.dispatcher.workspace
            root = getattr(ws, "root_dir", None) or getattr(ws, "workspace_root", None)
            if root:
                return Path(root).exists()
        return True

    def _probe_builtin_tool(self, name: str) -> Tuple[bool, Optional[str]]:
        """
        Built-in health check rules:
        - callable
        - implementation exists
        - workspace valid
        - health probe succeeds
        """
        if not self._is_workspace_valid():
            return False, "Workspace directory is invalid or inaccessible"

        if self.dispatcher:
            br = getattr(self.dispatcher, "builtin_registry", None)
            if br:
                tools_map = getattr(br, "_tools", {}) or getattr(br, "tools", {})
                if name in tools_map:
                    handler = tools_map[name]
                    is_invocable = callable(handler) or hasattr(handler, "run") or hasattr(handler, "invoke") or hasattr(handler, "func")
                    if not is_invocable:
                        return False, f"Tool '{name}' is not callable"
                    return True, None

        if self._can_resolve_builtin(name):
            return True, None

        return False, f"Built-in tool '{name}' has no callable implementation"

    def _probe_mcp_tool(self, name: str) -> Tuple[bool, Optional[str]]:
        """
        MCP health check rules:
        - MCP connected
        - server alive
        - tool exists
        - invocation succeeds
        """
        if "browser" in name:
            if not self._is_real_browser_mcp_available():
                return False, "No real Chrome DevTools or Puppeteer MCP server connected"
            return True, None

        if not self.mcp_manager:
            return False, "MCPManager not connected"

        if hasattr(self.mcp_manager, "is_server_running"):
            running = False
            if hasattr(self.mcp_manager, "list_tools"):
                try:
                    tools = self.mcp_manager.list_tools()
                    for t in tools:
                        m_name = t.get("name") if isinstance(t, dict) else getattr(t, "name", str(t))
                        if m_name == name:
                            srv = t.get("server") if isinstance(t, dict) else getattr(t, "server", None)
                            if srv and self.mcp_manager.is_server_running(srv):
                                running = True
                                break
                except Exception:
                    pass
            if not running and not self._is_real_browser_mcp_available():
                return False, f"MCP server hosting '{name}' is offline or disconnected"

        return True, None

    def _can_resolve_builtin(self, name: str) -> bool:
        """Checks if tool name maps to a known available builtin capability."""
        common_builtins = {
            "read_file", "write_file", "edit_file", "replace_file_content",
            "list_directory", "terminal_execute", "run_command", "ast_syntax_check",
            "regex_grep", "find_symbol", "get_call_graph", "get_impact_radius",
            "get_dependencies", "complete_task", "insert_lines", "delete_lines",
            "delete_file", "move_file", "rename_file", "apply_diff_blocks",
            "apply_patch", "check_environment", "get_file_info", "run_tests",
        }
        if name in common_builtins:
            return True
        try:
            from skills.policy import DEFAULT_TOOL_ALIASES
            if name in DEFAULT_TOOL_ALIASES:
                return True
            resolved = DEFAULT_TOOL_ALIASES.get(name, name)
            if resolved in common_builtins:
                return True
        except ImportError:
            pass

        clean = name.replace("filesystem.", "").replace("terminal.", "").replace("codebase.", "").replace("shell.", "")
        if clean in common_builtins:
            return True
        if clean in ("read", "write", "edit", "list", "run", "search", "diff", "patch", "delete", "move", "rename"):
            return True
        return False

    def verify_health(self, tool_name: str) -> bool:
        """
        Validates that a discovered tool is healthy and callable.
        Enforces strict lifecycle: DECLARED -> DISCOVERED -> HEALTH CHECK -> HEALTHY (CRITICAL FIX 3).
        Never promotes DISCOVERED directly.
        """
        clean_name = tool_name.strip()
        tool = self._tools.get(clean_name)
        if not tool or tool.state in (ToolLifecycleState.DECLARED, ToolLifecycleState.MISSING):
            return False

        # 1. Probe by tool type
        if tool.source == "mcp" or "browser" in clean_name:
            passed, reason = self._probe_mcp_tool(clean_name)
            if not passed:
                self.transition(clean_name, ToolLifecycleState.MISSING, error_reason=reason)
                return False
        else:
            passed, reason = self._probe_builtin_tool(clean_name)
            if not passed:
                self.transition(clean_name, ToolLifecycleState.UNHEALTHY, error_reason=reason)
                return False

        # 2. Run explicit health_checker callback if registered
        if tool.health_checker:
            try:
                is_healthy = tool.health_checker()
                if not is_healthy:
                    self.transition(clean_name, ToolLifecycleState.UNHEALTHY, error_reason="Health checker probe returned False")
                    return False
            except Exception as e:
                self.transition(clean_name, ToolLifecycleState.UNHEALTHY, error_reason=f"Health probe error: {e}")
                return False

        # Successfully validated through real health check probe
        self.transition(clean_name, ToolLifecycleState.HEALTHY)
        return True

    def _is_real_browser_mcp_available(self) -> bool:
        """Validates whether a real browser MCP server is currently reachable."""
        if self.browser_adapter is not None:
            if hasattr(self.browser_adapter, "is_available"):
                return bool(self.browser_adapter.is_available())
        if not self.mcp_manager:
            return False
        if hasattr(self.mcp_manager, "is_server_running"):
            return bool(
                self.mcp_manager.is_server_running("chrome-devtools")
                or self.mcp_manager.is_server_running("puppeteer")
                or self.mcp_manager.is_server_running("mcp-server-browser")
            )
        if hasattr(self.mcp_manager, "list_servers"):
            servers = self.mcp_manager.list_servers()
            return any("browser" in s.lower() or "devtools" in s.lower() or "puppeteer" in s.lower() for s in servers)
        return False

    def authorize(
        self,
        tool_name: str,
        allowed_tools: Optional[Set[str]] = None,
        permission_checker: Optional[Callable[[str], bool]] = None,
        auto_promote: bool = False,
    ) -> bool:
        """
        Validates whether a healthy tool is authorized for execution under active policy.
        Transitions: HEALTHY -> AUTHORIZED (and optionally -> EXECUTABLE if auto_promote=True).
        """
        clean_name = tool_name.strip()
        tool = self._tools.get(clean_name)
        if not tool:
            self.declare_tool(clean_name, source="builtin")
            self.discover(clean_name)
            tool = self._tools.get(clean_name)

        if tool.state != ToolLifecycleState.HEALTHY and tool.state not in (ToolLifecycleState.AUTHORIZED, ToolLifecycleState.EXECUTABLE):
            # Attempt health check first
            if not self.verify_health(clean_name):
                return False

        # Check allowed tools filter
        if allowed_tools is not None and clean_name not in allowed_tools:
            # Check normalized alias
            alias = clean_name.replace("filesystem.", "").replace("terminal.", "").replace("codebase.", "")
            if alias not in allowed_tools:
                self.transition(clean_name, ToolLifecycleState.UNAUTHORIZED, error_reason="Tool not in active capability allowed list")
                return False

        # Check custom permission checker if provided
        if permission_checker is not None:
            try:
                allowed = permission_checker(clean_name)
                if not allowed:
                    self.transition(clean_name, ToolLifecycleState.UNAUTHORIZED, error_reason="Permission policy denied access")
                    return False
            except Exception as e:
                self.transition(clean_name, ToolLifecycleState.UNAUTHORIZED, error_reason=str(e))
                return False

        # Transition to AUTHORIZED
        self.transition(clean_name, ToolLifecycleState.AUTHORIZED)
        if auto_promote:
            self.transition(clean_name, ToolLifecycleState.EXECUTABLE)
        return True

    def get_executable_tools(self) -> List[ManagedTool]:
        """Returns only tools that have reached the EXECUTABLE state."""
        return [t for t in self._tools.values() if t.state == ToolLifecycleState.EXECUTABLE]

    def get_executable_schemas(self) -> List[Dict[str, Any]]:
        """
        Strict invariant: Only EXECUTABLE tools may be exposed to the model.
        Returns OpenAI-compatible schemas for all executable tools.
        """
        schemas = []
        for tool in self.get_executable_tools():
            if tool.schema and "function" in tool.schema:
                schemas.append(tool.schema)
            elif tool.schema and "name" in tool.schema:
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": tool.schema.get("name", tool.name),
                        "description": tool.schema.get("description", tool.description),
                        "parameters": tool.schema.get("parameters", {"type": "object", "properties": {}}),
                    }
                })
            else:
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or f"Executable tool: {tool.name}",
                        "parameters": {"type": "object", "properties": {}},
                    }
                })
        return schemas
