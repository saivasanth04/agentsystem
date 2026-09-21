"""
Model Context Protocol (MCP) Server Manager & Registry.
Manages server lifecycle, connections, tool discovery, dynamic dispatch, circuit breaking, and error handling.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from .circuit_breaker import CircuitState, MCPCircuitBreaker
from .client import MCPClientSession
from .protocol import (
    MCPConnectionError,
    MCPError,
    MCPErrorCode,
    MCPServerCapabilities,
    MCPServerState,
    MCPTimeoutError,
    ServerHealthReport,
    ToolCallResult,
    ToolDefinition,
)
from .servers import (
    BaseMCPServer,
    CodebaseMemoryMCPServer,
    FilesystemMCPServer,
    GitMCPServer,
    TerminalMCPServer,
)
from .transport import InMemoryTransport, StdioTransport

logger = logging.getLogger("mcp.manager")


class MCPManager:
    """
    Central Manager and Registry for all MCP Servers.
    Provides server discovery, tool enumeration, dynamic invocation, session recovery,
    active health checking, per-server circuit breaking, and complete lifecycle management.
    """

    def __init__(self, workspace_dir: Optional[Path] = None, config_path: Optional[Path] = None):
        self.workspace_dir = Path(workspace_dir or Path.cwd()).resolve()
        self.config_path = config_path or (self.workspace_dir / "mcp_servers.json")

        self._sessions: Dict[str, MCPClientSession] = {}
        self._servers: Dict[str, BaseMCPServer] = {}
        self._tool_cache: Dict[str, ToolDefinition] = {}
        self._tool_to_server: Dict[str, str] = {}
        self._server_configs: Dict[str, Dict[str, Any]] = {}
        self._circuit_breakers: Dict[str, MCPCircuitBreaker] = {}

        # Auto-register embedded standard reference servers
        self._init_standard_reference_servers()

    def _init_standard_reference_servers(self) -> None:
        """Instantiate in-process reference servers for immediate out-of-the-box operation."""
        fs_server = FilesystemMCPServer(root_dir=self.workspace_dir)
        git_server = GitMCPServer(repo_dir=self.workspace_dir)
        term_server = TerminalMCPServer(working_dir=self.workspace_dir)
        mem_server = CodebaseMemoryMCPServer(workspace_dir=self.workspace_dir)

        self.register_in_memory_server(fs_server)
        self.register_in_memory_server(git_server)
        self.register_in_memory_server(term_server)
        self.register_in_memory_server(mem_server)

    def get_circuit_breaker(self, server_name: str) -> MCPCircuitBreaker:
        """Retrieve or initialize the circuit breaker for a given MCP server."""
        if server_name not in self._circuit_breakers:
            self._circuit_breakers[server_name] = MCPCircuitBreaker(server_name=server_name)
        return self._circuit_breakers[server_name]

    def get_server_health_status(self, server_name: str) -> str:
        """
        Evaluate the operational health of an MCP server.
        Returns 'HEALTHY', 'DEGRADED', or 'UNHEALTHY'.
        """
        cb = self.get_circuit_breaker(server_name)
        if cb.state == CircuitState.OPEN:
            return "UNHEALTHY"

        session = self._sessions.get(server_name)
        if not session or not session.is_connected:
            if server_name in self._servers or server_name in self._server_configs:
                return "DEGRADED"  # Configured but stopped / uninitialized
            return "UNHEALTHY"

        if session.state == MCPServerState.DEGRADED:
            return "DEGRADED"
        if session.state in (MCPServerState.ERROR, MCPServerState.STOPPED):
            return "UNHEALTHY"

        if cb.state == CircuitState.HALF_OPEN:
            return "DEGRADED"

        return "HEALTHY"

    def register_in_memory_server(self, server: BaseMCPServer) -> MCPClientSession:
        """Attach an in-memory MCP server directly to the manager session pool."""
        transport = InMemoryTransport(server_handler=server.handle_message)
        session = MCPClientSession(transport=transport, client_name=f"Manager_{server.name}")
        session.connect()

        self._servers[server.name] = server
        self._sessions[server.name] = session
        self.get_circuit_breaker(server.name).reset()

        # Auto-subscribe to dynamic tool discovery updates
        session.on_notification(
            "notifications/tools/list_changed",
            lambda _: self._refresh_tools_for_server(server.name),
        )
        self._refresh_tools_for_server(server.name)
        return session

    def register_stdio_server(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> MCPClientSession:
        """Spawn and register an external MCP server running over stdio."""
        transport = StdioTransport(
            command=command,
            args=args,
            env=env,
            cwd=cwd or str(self.workspace_dir),
        )
        session = MCPClientSession(transport=transport, client_name=f"Manager_{name}")
        session.connect()

        self._sessions[name] = session
        self._server_configs[name] = {
            "command": command,
            "args": args or [],
            "env": env,
            "cwd": cwd,
        }
        self.get_circuit_breaker(name).reset()

        # Auto-subscribe to dynamic tool discovery updates
        session.on_notification(
            "notifications/tools/list_changed",
            lambda _: self._refresh_tools_for_server(name),
        )
        self._refresh_tools_for_server(name)
        return session

    def load_config_file(self, path: Optional[Path] = None) -> None:
        """Load external MCP server configurations from JSON file."""
        target_path = Path(path or self.config_path)
        if not target_path.exists():
            return

        try:
            content = target_path.read_text(encoding="utf-8")
            data = json.loads(content)
            mcp_servers = data.get("mcpServers", {})

            for name, s_cfg in mcp_servers.items():
                if s_cfg.get("disabled", False):
                    continue
                cmd = s_cfg.get("command")
                if not cmd:
                    continue
                args = s_cfg.get("args", [])
                env = s_cfg.get("env")
                cwd = s_cfg.get("cwd")
                try:
                    self.register_stdio_server(name=name, command=cmd, args=args, env=env, cwd=cwd)
                except Exception as e:
                    logger.warning(f"Could not connect to configured MCP server '{name}': {e}")
        except Exception as e:
            logger.error(f"Failed parsing MCP config file '{target_path}': {e}")

    def start_server(self, name: str) -> MCPClientSession:
        """Explicitly start or restart a registered server."""
        session = self._sessions.get(name)
        if session and session.is_connected:
            self.get_circuit_breaker(name).reset()
            return session

        # If in-memory server registered
        if name in self._servers:
            server = self._servers[name]
            return self.register_in_memory_server(server)

        # If stdio config exists
        if name in self._server_configs:
            cfg = self._server_configs[name]
            return self.register_stdio_server(
                name=name,
                command=cfg["command"],
                args=cfg.get("args"),
                env=cfg.get("env"),
                cwd=cfg.get("cwd"),
            )

        raise MCPError(f"Server '{name}' is not registered in MCP manager.")

    def stop_server(self, name: str) -> bool:
        """Gracefully stop a registered server session and purge its tools from active cache."""
        session = self._sessions.get(name)
        if not session:
            return False

        try:
            session.close()
        except Exception as e:
            logger.warning(f"Error while stopping MCP server '{name}': {e}")

        # Purge tool cache for this server
        keys_to_remove = [k for k, s_name in self._tool_to_server.items() if s_name == name]
        for k in keys_to_remove:
            self._tool_cache.pop(k, None)
            self._tool_to_server.pop(k, None)

        return True

    def restart_server(self, name: str) -> MCPClientSession:
        """Stop and restart a registered MCP server."""
        self.stop_server(name)
        sess = self.start_server(name)
        self.get_circuit_breaker(name).reset()
        return sess

    def get_server_state(self, name: str) -> MCPServerState:
        """Retrieve current lifecycle state for an MCP server."""
        session = self._sessions.get(name)
        if not session:
            return MCPServerState.STOPPED
        return session.state

    def get_server_capabilities(self, name: str) -> Optional[MCPServerCapabilities]:
        """Retrieve negotiated server capabilities for an MCP server."""
        session = self._sessions.get(name)
        if not session or not session.is_connected:
            return None
        return session.capabilities

    def unregister_server(self, name: str) -> bool:
        """Fully unregister and terminate an MCP server."""
        self.stop_server(name)
        had_server = (name in self._sessions) or (name in self._servers) or (name in self._server_configs)
        self._sessions.pop(name, None)
        self._servers.pop(name, None)
        self._server_configs.pop(name, None)
        self._circuit_breakers.pop(name, None)
        return had_server

    def reconnect(
        self,
        server_name: str,
        max_retries: int = 3,
        initial_delay: float = 0.5,
        backoff_factor: float = 1.5,
    ) -> MCPClientSession:
        """Attempt reconnection to a disconnected or crashed MCP server with exponential backoff."""
        delay = initial_delay
        last_error = None

        for attempt in range(1, max_retries + 1):
            logger.info(f"Reconnection attempt {attempt}/{max_retries} for MCP server '{server_name}'...")
            try:
                session = self.restart_server(server_name)
                if session.is_connected:
                    logger.info(f"Successfully reconnected to MCP server '{server_name}'.")
                    self.get_circuit_breaker(server_name).record_success()
                    return session
            except Exception as e:
                last_error = e
                logger.warning(f"Reconnection attempt {attempt} for '{server_name}' failed: {e}")

            if attempt < max_retries:
                time.sleep(delay)
                delay *= backoff_factor

        self.get_circuit_breaker(server_name).record_failure(last_error)
        raise MCPConnectionError(
            f"Failed to reconnect to '{server_name}' after {max_retries} retries: {last_error}"
        )

    def ensure_server_running(self, server_name: str) -> MCPClientSession:
        """Ensure that the named server is started and ready; start it lazily if not."""
        session = self._sessions.get(server_name)
        if session and session.is_connected:
            return session
        return self.start_server(server_name)

    def health_check(
        self, server_name: Optional[str] = None
    ) -> Union[ServerHealthReport, Dict[str, ServerHealthReport]]:
        """
        Check health of a specific server or all known servers.
        Performs an active ping check on connected servers to record latency and updates circuit breakers.
        """
        def _check_single(name: str) -> ServerHealthReport:
            cb = self.get_circuit_breaker(name)
            session = self._sessions.get(name)

            if not session or not session.is_connected or not cb.can_attempt():
                return ServerHealthReport(
                    server_name=name,
                    state=MCPServerState.ERROR if cb.state == CircuitState.OPEN else MCPServerState.STOPPED,
                    is_connected=session.is_connected if session else False,
                    circuit_state=cb.state.value,
                    error=cb.last_error or "Server is not connected or circuit breaker is OPEN",
                )

            # Perform active ping check
            try:
                latency = session.ping(timeout=2.0)
                cb.record_success()
                report = session.get_health_report(name)
                report.circuit_state = cb.state.value
                return report
            except Exception as ex:
                cb.record_failure(ex)
                report = session.get_health_report(name)
                report.circuit_state = cb.state.value
                report.error = f"Health ping failed: {ex}"
                report.state = MCPServerState.DEGRADED if cb.state != CircuitState.OPEN else MCPServerState.ERROR
                return report

        if server_name:
            return _check_single(server_name)

        # All known servers
        all_names = set(self._sessions.keys()) | set(self._servers.keys()) | set(self._server_configs.keys())
        reports: Dict[str, ServerHealthReport] = {}
        for name in sorted(all_names):
            reports[name] = _check_single(name)
        return reports

    def _refresh_tools_for_server(self, server_name: str) -> None:
        """Fetch and index all tools from a registered server (supports pagination)."""
        session = self._sessions.get(server_name)
        if not session or not session.is_connected:
            return

        try:
            # Purge existing keys for this server first so removed tools disappear cleanly
            old_keys = [k for k, s_name in self._tool_to_server.items() if s_name == server_name]
            for k in old_keys:
                self._tool_cache.pop(k, None)
                self._tool_to_server.pop(k, None)

            tools = session.list_tools()
            for t in tools:
                # Key by full qualified name and bare name
                full_name = f"{server_name}__{t.name}"
                self._tool_cache[full_name] = t
                self._tool_cache[t.name] = t
                self._tool_to_server[full_name] = server_name
                self._tool_to_server[t.name] = server_name
            logger.info(f"Refreshed {len(tools)} tools for MCP server '{server_name}'.")
        except Exception as e:
            logger.warning(f"Failed refreshing tools for MCP server '{server_name}': {e}")

    def discover_servers(self) -> List[Dict[str, Any]]:
        """List all active MCP servers and their connection statuses, states, circuit breaker, and capabilities."""
        servers = []
        for name, session in self._sessions.items():
            transport_pid = getattr(session.transport, "pid", None)
            cb = self.get_circuit_breaker(name)
            servers.append({
                "server_name": name,
                "is_connected": session.is_connected,
                "state": session.state.value,
                "circuit_state": cb.state.value,
                "health_status": self.get_server_health_status(name),
                "server_info": session.server_info,
                "capabilities": session.capabilities.to_dict() if session.capabilities else {},
                "tools_count": len([t for s_name, t in self._tool_to_server.items() if s_name == name and "__" in s_name]),
                "last_latency_ms": session.last_latency_ms,
                "pid": transport_pid,
            })
        return servers

    def discover_tools(self, server_name: Optional[str] = None) -> List[ToolDefinition]:
        """Discover all exposed tools across servers or for a specific server."""
        if server_name:
            session = self._sessions.get(server_name)
            if not session or not session.is_connected:
                return []
            return session.list_tools()

        # Return unique tool definitions
        seen = set()
        tools = []
        for name, tool in self._tool_cache.items():
            if "__" not in name:
                continue
            if tool.name not in seen:
                seen.add(tool.name)
                tools.append(tool)
        return tools

    def get_tool_schema(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Returns the function schema for a given tool."""
        tool = self._tool_cache.get(tool_name)
        if tool:
            return tool.to_function_schema()
        return None

    def call_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> ToolCallResult:
        """
        Dynamically route and invoke a tool across the appropriate MCP server session.
        Ensures target server is ready, respects circuit breaker, and attempts recovery if disconnected.
        """
        server_name = self._tool_to_server.get(tool_name)
        if not server_name or (
            server_name not in self._sessions
            and server_name not in self._server_configs
            and server_name not in self._servers
        ):
            # Check if prefixed with mcp_<server_name>_
            for s_name in list(self._sessions.keys()) + list(self._servers.keys()) + list(self._server_configs.keys()):
                prefix = f"mcp_{s_name}_"
                if tool_name.startswith(prefix):
                    bare_tool = tool_name[len(prefix):]
                    return self.call_tool(bare_tool, arguments, timeout)

            return ToolCallResult.failure(f"Tool '{tool_name}' not found on any active MCP server.")

        # Circuit breaker check: Fail-fast if server is UNHEALTHY / circuit OPEN
        cb = self.get_circuit_breaker(server_name)
        if not cb.can_attempt():
            logger.warning(
                f"Circuit breaker for MCP server '{server_name}' is {cb.state.value}. Failing fast for tool '{tool_name}'."
            )
            return ToolCallResult.failure(
                f"MCP server '{server_name}' is UNHEALTHY (circuit breaker {cb.state.value}: {cb.last_error}). Fail-fast triggered.",
                data={
                    "circuit_open": True,
                    "circuit_state": cb.state.value,
                    "server_name": server_name,
                    "error": cb.last_error,
                },
            )

        session = self._sessions.get(server_name)
        if not session or not session.is_connected:
            try:
                session = self.ensure_server_running(server_name)
            except Exception as e:
                cb.record_failure(e)
                return ToolCallResult.failure(f"Failed to start MCP server '{server_name}' for tool '{tool_name}': {e}")

        bare_tool_name = tool_name.split("__")[-1]

        try:
            res = session.call_tool(bare_tool_name, arguments or {}, timeout=timeout)
            if res.isError and ("connection" in str(res.content).lower() or "timeout" in str(res.content).lower()):
                cb.record_failure()
            else:
                cb.record_success()
            return res
        except Exception as e:
            cb.record_failure(e)
            return self.handle_tool_error(tool_name, server_name, e)

    def handle_tool_error(self, tool_name: str, server_name: str, error: Exception) -> ToolCallResult:
        """Handle errors with automatic diagnostics and recovery."""
        logger.error(f"Error invoking tool '{tool_name}' on server '{server_name}': {error}")
        session = self._sessions.get(server_name)

        if isinstance(error, MCPConnectionError) or (session and not session.is_connected):
            # Attempt reconnect if it was a registered server
            if server_name in self._server_configs or server_name in self._servers:
                try:
                    logger.info(f"Attempting reconnection to crashed server '{server_name}'...")
                    self.reconnect(server_name, max_retries=2, initial_delay=0.2)
                    return ToolCallResult.failure(f"MCP server '{server_name}' crashed and was restarted. Please retry tool call.")
                except Exception as re_err:
                    return ToolCallResult.failure(f"MCP server '{server_name}' is disconnected and reconnection failed: {re_err}")

        return ToolCallResult.failure(f"MCP tool error ({server_name}::{tool_name}): {str(error)}")

    def close(self) -> None:
        """Gracefully shut down all MCP sessions and servers."""
        for name, session in list(self._sessions.items()):
            try:
                session.close()
            except Exception:
                pass
        self._sessions.clear()
        self._servers.clear()
        self._tool_cache.clear()
        self._tool_to_server.clear()
        self._circuit_breakers.clear()
