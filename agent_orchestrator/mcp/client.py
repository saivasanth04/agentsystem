"""
Model Context Protocol (MCP) Client Session.
Implements full MCP client handshake, capability discovery, full-duplex multiplexed RPC,
notification dispatch, cursor-based tool discovery, cancellation, and graceful shutdown.
"""
import concurrent.futures
import itertools
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Union

from .protocol import (
    JSONRPCNotification,
    JSONRPCRequest,
    MCPClientCapabilities,
    MCPConnectionError,
    MCPError,
    MCPErrorCode,
    MCPServerCapabilities,
    MCPServerState,
    MCPTimeoutError,
    MCPToolExecutionError,
    ServerHealthReport,
    ToolCallResult,
    ToolDefinition,
)
from .transport import BaseTransport

logger = logging.getLogger("mcp.client")


class MCPClientSession:
    """
    Manages an active, full-duplex MCP Client session over any BaseTransport.
    Handles multiplexed JSON-RPC request/response routing, background notification dispatch,
    initialization handshake, capability negotiation, tool discovery pagination, and cancellation.
    """

    def __init__(
        self,
        transport: BaseTransport,
        client_name: str = "AgentOrchestratorClient",
        client_version: str = "1.0.0",
        default_timeout: float = 30.0,
    ):
        self.transport = transport
        self.client_name = client_name
        self.client_version = client_version
        self.default_timeout = default_timeout

        self._id_counter = itertools.count(1)
        self._server_info: Dict[str, Any] = {}
        self._raw_capabilities: Dict[str, Any] = {}
        self._server_capabilities: MCPServerCapabilities = MCPServerCapabilities()
        self._state: MCPServerState = MCPServerState.STOPPED
        self._lock = threading.Lock()

        # Multiplexed pending requests table: id -> Future
        self._pending_requests: Dict[Union[str, int], concurrent.futures.Future] = {}
        # Registered notification handlers: method -> list of callbacks
        self._notification_handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

        self._demux_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_latency_ms: Optional[float] = None

    @property
    def state(self) -> MCPServerState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self.transport.is_alive() and self._state == MCPServerState.READY

    @property
    def server_info(self) -> Dict[str, Any]:
        return self._server_info

    @property
    def capabilities(self) -> MCPServerCapabilities:
        return self._server_capabilities

    @property
    def last_latency_ms(self) -> Optional[float]:
        return self._last_latency_ms

    def on_notification(self, method: str, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register a callback for an asynchronous MCP notification (e.g. notifications/tools/list_changed)."""
        with self._lock:
            if method not in self._notification_handlers:
                self._notification_handlers[method] = []
            self._notification_handlers[method].append(callback)

    def connect(self) -> "MCPClientSession":
        """Start the transport, launch background demux router, and perform handshake."""
        self._state = MCPServerState.STARTING
        try:
            self.transport.start()
            self._stop_event.clear()
            self._demux_thread = threading.Thread(target=self._demux_loop, daemon=True)
            self._demux_thread.start()

            self._state = MCPServerState.INITIALIZING
            self._initialize_handshake()
            self._state = MCPServerState.READY
            return self
        except Exception as e:
            self._state = MCPServerState.ERROR
            self.close()
            raise MCPConnectionError(f"Failed to establish MCP connection: {e}")

    def _next_id(self) -> int:
        return next(self._id_counter)

    def _demux_loop(self) -> None:
        """Background thread demultiplexing incoming messages into pending futures or notification callbacks."""
        while not self._stop_event.is_set():
            try:
                msg = self.transport.receive(timeout=0.2)
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.debug(f"MCP demux receive encountered termination: {e}")
                break

            if msg is None:
                continue

            # 1. Match response to pending request
            req_id = msg.get("id")
            if req_id is not None:
                fut = None
                with self._lock:
                    fut = self._pending_requests.pop(req_id, None)

                if fut and not fut.done():
                    if "error" in msg and msg["error"]:
                        err = msg["error"]
                        fut.set_exception(
                            MCPError(
                                message=err.get("message", "Unknown MCP error"),
                                code=err.get("code", MCPErrorCode.INTERNAL_ERROR),
                                data=err.get("data"),
                            )
                        )
                    else:
                        fut.set_result(msg.get("result"))
                continue

            # 2. Dispatch notification
            method = msg.get("method")
            if method:
                params = msg.get("params", {})
                handlers = []
                with self._lock:
                    handlers = list(self._notification_handlers.get(method, []))
                for h in handlers:
                    try:
                        h(params)
                    except Exception as ex:
                        logger.warning(f"Notification handler error for '{method}': {ex}")

    def send_request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        """Send a JSON-RPC request and await response via multiplexed future with cancellation support."""
        if not self.transport.is_alive():
            self._state = MCPServerState.ERROR
            raise MCPConnectionError("Cannot send request: Transport is not alive.")

        req_id = self._next_id()
        req = JSONRPCRequest(id=req_id, method=method, params=params)
        fut: concurrent.futures.Future = concurrent.futures.Future()

        with self._lock:
            self._pending_requests[req_id] = fut

        try:
            self.transport.send(req)
        except Exception as e:
            with self._lock:
                self._pending_requests.pop(req_id, None)
            raise MCPConnectionError(f"Failed sending MCP request '{method}': {e}")

        effective_timeout = timeout or self.default_timeout
        try:
            return fut.result(timeout=effective_timeout)
        except concurrent.futures.TimeoutError:
            with self._lock:
                self._pending_requests.pop(req_id, None)

            # Spec-compliant cancellation notice
            try:
                self.send_notification("notifications/cancelled", {"requestId": req_id, "reason": "timeout"})
            except Exception:
                pass

            raise MCPTimeoutError(f"MCP request '{method}' (id={req_id}) timed out after {effective_timeout}s")

    def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC notification (one-way message)."""
        notif = JSONRPCNotification(method=method, params=params)
        self.transport.send(notif)

    def _initialize_handshake(self) -> None:
        """Execute MCP initialize handshake with version negotiation and capability exchange."""
        params = {
            "protocolVersion": "2024-11-05",
            "capabilities": MCPClientCapabilities().to_dict(),
            "clientInfo": {
                "name": self.client_name,
                "version": self.client_version,
            },
        }

        try:
            res = self.send_request("initialize", params, timeout=15.0)
            if res:
                self._server_info = res.get("serverInfo", {})
                self._raw_capabilities = res.get("capabilities", {})
                self._server_capabilities = MCPServerCapabilities.from_dict(self._raw_capabilities)

            # Acknowledge initialization
            self.send_notification("notifications/initialized")
        except Exception as e:
            self._state = MCPServerState.ERROR
            self.close()
            raise MCPConnectionError(f"MCP initialization handshake failed: {e}")

    def ping(self, timeout: float = 5.0) -> bool:
        """Ping the server to verify connectivity and measure latency."""
        t0 = time.time()
        try:
            self.send_request("ping", timeout=timeout)
            self._last_latency_ms = round((time.time() - t0) * 1000.0, 2)
            return True
        except Exception:
            self._last_latency_ms = None
            return False

    def list_tools(self, timeout: Optional[float] = None) -> List[ToolDefinition]:
        """Fetch all tools exposed by the server using cursor-based pagination."""
        if not self.is_connected:
            raise MCPError("Session not initialized. Call connect() first.", code=MCPErrorCode.SERVER_NOT_INITIALIZED)

        tools: List[ToolDefinition] = []
        cursor: Optional[str] = None
        server_name = self._server_info.get("name", "unknown_server")

        while True:
            params: Dict[str, Any] = {"cursor": cursor} if cursor else {}
            res = self.send_request("tools/list", params, timeout=timeout)
            tools_raw = res.get("tools", []) if isinstance(res, dict) else []

            for t in tools_raw:
                tools.append(ToolDefinition(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    inputSchema=t.get("inputSchema", {"type": "object", "properties": {}}),
                    server_name=server_name,
                ))

            cursor = res.get("nextCursor") if isinstance(res, dict) else None
            if not cursor:
                break

        return tools

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> ToolCallResult:
        """Invoke a tool on the MCP server and return a normalized ToolCallResult."""
        if not self.is_connected:
            raise MCPError("Session not initialized. Call connect() first.", code=MCPErrorCode.SERVER_NOT_INITIALIZED)

        params = {
            "name": name,
            "arguments": arguments or {},
        }
        try:
            res = self.send_request("tools/call", params, timeout=timeout)
            if not isinstance(res, dict):
                return ToolCallResult.success(str(res))

            content = res.get("content", [])
            is_error = res.get("isError", False)
            structured_data = res.get("data")

            text_chunks = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            combined_text = "\n".join(text_chunks) if text_chunks else str(res)

            if is_error:
                return ToolCallResult.failure(combined_text, data=structured_data)
            return ToolCallResult(content=content, isError=False, structured_data=structured_data or res)
        except (MCPTimeoutError, MCPConnectionError):
            raise
        except MCPError as e:
            return ToolCallResult.failure(f"MCP Tool Execution Error: {e.message}", data=e.to_dict())
        except Exception as e:
            return ToolCallResult.failure(f"Unexpected Tool Error: {str(e)}")

    def list_resources(self, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
        """Fetch resources exposed by the server using pagination."""
        if not self.is_connected:
            raise MCPError("Session not initialized. Call connect() first.", code=MCPErrorCode.SERVER_NOT_INITIALIZED)

        resources: List[Dict[str, Any]] = []
        cursor: Optional[str] = None

        while True:
            params: Dict[str, Any] = {"cursor": cursor} if cursor else {}
            res = self.send_request("resources/list", params, timeout=timeout)
            raw = res.get("resources", []) if isinstance(res, dict) else []
            resources.extend(raw)
            cursor = res.get("nextCursor") if isinstance(res, dict) else None
            if not cursor:
                break

        return resources

    def get_health_report(self, server_name: str) -> ServerHealthReport:
        """Produce a structured health diagnostic report for this session."""
        is_conn = self.is_connected
        ping_ok = self.ping(timeout=3.0) if is_conn else False
        tools_count = 0
        if is_conn:
            try:
                tools_count = len(self.list_tools(timeout=3.0))
            except Exception:
                pass

        state = self._state
        if not is_conn and state == MCPServerState.READY:
            state = MCPServerState.DEGRADED

        return ServerHealthReport(
            server_name=server_name,
            state=state,
            is_connected=is_conn and ping_ok,
            latency_ms=self._last_latency_ms,
            tools_count=tools_count,
            server_info=dict(self._server_info),
            capabilities=self._raw_capabilities,
        )

    def close(self) -> None:
        """Gracefully terminate session, cancel all pending futures, and close transport."""
        self._state = MCPServerState.STOPPING
        self._stop_event.set()

        # Fail any awaiting requests cleanly
        with self._lock:
            for req_id, fut in list(self._pending_requests.items()):
                if not fut.done():
                    fut.set_exception(MCPConnectionError(f"Session closed while awaiting response for {req_id}"))
            self._pending_requests.clear()

        try:
            self.transport.close()
        except Exception:
            pass

        self._state = MCPServerState.STOPPED

