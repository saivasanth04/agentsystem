"""
Tool Lifecycle State Machine.
Enforces the 5-stage explicit lifecycle for every tool:
DECLARED -> DISCOVERED -> HEALTHY -> AUTHORIZED -> EXECUTABLE

Only EXECUTABLE tools may be exposed to the model or invoked.
Missing or unverified inventory tools remain non-executable with clear diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("runtime.tool_state_machine")


class ToolLifecycleState(str, Enum):
    DECLARED = "DECLARED"
    DISCOVERED = "DISCOVERED"
    HEALTHY = "HEALTHY"
    AUTHORIZED = "AUTHORIZED"
    EXECUTABLE = "EXECUTABLE"
    MISSING = "MISSING"
    UNHEALTHY = "UNHEALTHY"
    UNAUTHORIZED = "UNAUTHORIZED"


@dataclass
class ToolStateRecord:
    """Tracks state and diagnostics for a tool instance."""
    name: str
    state: ToolLifecycleState = ToolLifecycleState.DECLARED
    schema: Dict[str, Any] = field(default_factory=dict)
    source: str = "builtin"  # 'builtin' | 'mcp'
    server_name: Optional[str] = None
    required_capabilities: List[str] = field(default_factory=list)
    diagnostic_message: Optional[str] = None

    def is_executable(self) -> bool:
        return self.state == ToolLifecycleState.EXECUTABLE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "source": self.source,
            "server_name": self.server_name,
            "required_capabilities": self.required_capabilities,
            "diagnostic_message": self.diagnostic_message,
            "is_executable": self.is_executable(),
        }


class ToolStateMachine:
    """
    Authoritative state machine governing tool exposure.
    Transitions:
    1. DECLARED: Listed in manifest/inventory or registered by capability.
    2. DISCOVERED: Bound to a live implementation in UnifiedToolDispatcher or MCPManager.
    3. HEALTHY: Health check probe passed; responsive and operational.
    4. AUTHORIZED: Allowed by active ToolPolicy and PermissionEngine for the current task.
    5. EXECUTABLE: Ready for invocation; only tools in this state are provided to the LLM.
    """

    def __init__(
        self,
        dispatcher: Optional[Any] = None,
        mcp_manager: Optional[Any] = None,
    ):
        self.dispatcher = dispatcher
        self.mcp_manager = mcp_manager
        self._tools: Dict[str, ToolStateRecord] = {}

    def declare_tool(
        self,
        name: str,
        schema: Optional[Dict[str, Any]] = None,
        source: str = "builtin",
        server_name: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
    ) -> ToolStateRecord:
        """Stage 1: Declare a tool in the state machine."""
        record = ToolStateRecord(
            name=name,
            state=ToolLifecycleState.DECLARED,
            schema=schema or {},
            source=source,
            server_name=server_name,
            required_capabilities=capabilities or [],
        )
        self._tools[name] = record
        return record

    def transition_discovered(self, name: str) -> ToolStateRecord:
        """Stage 2: Verify tool existence in dispatcher or live MCP server."""
        if name not in self._tools:
            self.declare_tool(name)
        record = self._tools[name]

        # Check in dispatcher
        exists_in_dispatcher = False
        if self.dispatcher:
            if hasattr(self.dispatcher, "has_tool") and self.dispatcher.has_tool(name):
                exists_in_dispatcher = True
            elif hasattr(self.dispatcher, "builtin_registry"):
                br = self.dispatcher.builtin_registry
                if hasattr(br, "has_tool") and br.has_tool(name):
                    exists_in_dispatcher = True
                elif hasattr(br, "_tools") and name in br._tools:
                    exists_in_dispatcher = True

        # Check in live MCP
        exists_in_mcp = False
        if not exists_in_dispatcher and self.mcp_manager:
            if hasattr(self.mcp_manager, "is_tool_available") and self.mcp_manager.is_tool_available(name):
                exists_in_mcp = True
            elif hasattr(self.mcp_manager, "get_tool_info"):
                info = self.mcp_manager.get_tool_info(name)
                if info:
                    exists_in_mcp = True

        if exists_in_dispatcher or exists_in_mcp:
            record.state = ToolLifecycleState.DISCOVERED
            record.diagnostic_message = None
        else:
            record.state = ToolLifecycleState.MISSING
            record.diagnostic_message = f"Tool '{name}' not found in UnifiedToolDispatcher or active MCP servers."

        return record

    def transition_healthy(self, name: str) -> ToolStateRecord:
        """Stage 3: Run health checks to confirm tool readiness."""
        record = self._tools.get(name)
        if not record or record.state != ToolLifecycleState.DISCOVERED:
            record = self.transition_discovered(name)
        if record.state != ToolLifecycleState.DISCOVERED:
            return record

        # Special check for browser tools: must connect to real MCP, no dummy adapters!
        if name.startswith("browser_") or name.startswith("browser."):
            is_real_browser_active = False
            if self.mcp_manager:
                active_servers = self.mcp_manager.list_servers() if hasattr(self.mcp_manager, "list_servers") else []
                browser_servers = {"chrome-devtools", "puppeteer", "browser", "playwright"}
                if any(s.lower() in browser_servers for s in active_servers):
                    is_real_browser_active = True

            if not is_real_browser_active:
                record.state = ToolLifecycleState.MISSING
                record.diagnostic_message = f"Browser MCP server (chrome-devtools/puppeteer) is not connected. Fake browser adapters are prohibited."
                return record

        record.state = ToolLifecycleState.HEALTHY
        return record

    def transition_authorized(
        self,
        name: str,
        tool_policy: Optional[Any] = None,
        active_capabilities: Optional[List[str]] = None,
    ) -> ToolStateRecord:
        """Stage 4: Evaluate permissions and capability policies."""
        record = self._tools.get(name)
        if not record or record.state != ToolLifecycleState.HEALTHY:
            record = self.transition_healthy(name)
        if record.state != ToolLifecycleState.HEALTHY:
            return record

        # Policy validation
        if tool_policy:
            allowed = False
            if hasattr(tool_policy, "is_tool_allowed"):
                allowed = tool_policy.is_tool_allowed(name)
            elif hasattr(tool_policy, "allowed_tools"):
                allowed = "*" in tool_policy.allowed_tools or name in tool_policy.allowed_tools
            else:
                allowed = True

            if not allowed:
                record.state = ToolLifecycleState.UNAUTHORIZED
                record.diagnostic_message = f"Tool '{name}' is not authorized by active ToolPolicy."
                return record

        record.state = ToolLifecycleState.AUTHORIZED
        return record

    def transition_executable(
        self,
        name: str,
        tool_policy: Optional[Any] = None,
        active_capabilities: Optional[List[str]] = None,
    ) -> ToolStateRecord:
        """Stage 5: Final promotion to EXECUTABLE status."""
        record = self.transition_authorized(name, tool_policy=tool_policy, active_capabilities=active_capabilities)
        if record.state == ToolLifecycleState.AUTHORIZED:
            record.state = ToolLifecycleState.EXECUTABLE
            record.diagnostic_message = None
        return record

    def get_executable_tools(
        self,
        candidate_tool_names: List[str],
        tool_policy: Optional[Any] = None,
        active_capabilities: Optional[List[str]] = None,
    ) -> List[ToolStateRecord]:
        """Filters candidate tools and returns strictly those reaching EXECUTABLE status."""
        executable = []
        for name in candidate_tool_names:
            rec = self.transition_executable(
                name,
                tool_policy=tool_policy,
                active_capabilities=active_capabilities,
            )
            if rec.is_executable():
                executable.append(rec)
            else:
                logger.info(f"Tool '{name}' excluded: state={rec.state.value} ({rec.diagnostic_message})")
        return executable

    def get_tool_state(self, name: str) -> ToolLifecycleState:
        record = self._tools.get(name)
        return record.state if record else ToolLifecycleState.MISSING
