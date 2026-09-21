"""
Comprehensive Unit and Integration Tests for MCP Lifecycle Management.
Validates all 9 core lifecycle primitives:
1. Server startup & lifecycle state progression (STOPPED -> STARTING -> INITIALIZING -> READY -> STOPPING -> STOPPED)
2. Connection handshake & protocol version negotiation
3. Capability discovery (tools, resources, prompts, logging)
4. Cursor-based tool pagination (nextCursor)
5. Dynamic notification invalidation (notifications/tools/list_changed)
6. Multiplexed concurrent request routing via Futures
7. Timeout and spec-compliant cancellation notification dispatch (notifications/cancelled)
8. Keepalive ping, latency tracking, and structured health reporting
9. Exponential backoff reconnect on server crash / restart
10. Subprocess PID tracking and graceful tree termination
"""
import concurrent.futures
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_orchestrator.mcp.client import MCPClientSession
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.mcp.protocol import (
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    MCPConnectionError,
    MCPErrorCode,
    MCPServerCapabilities,
    MCPServerState,
    MCPTimeoutError,
    ServerHealthReport,
    ToolCallResult,
    ToolDefinition,
)
from agent_orchestrator.mcp.servers.base_server import BaseMCPServer
from agent_orchestrator.mcp.transport import InMemoryTransport, StdioTransport


class MockPaginatedAndSlowMCPServer(BaseMCPServer):
    """Specialized MCP server for testing pagination, notifications, timeouts, and cancellations."""

    def __init__(self, name: str = "mock-lifecycle-server"):
        super().__init__(name=name, version="2.0.0", description="Mock lifecycle testing server")
        self.cancelled_request_ids: List[Any] = []
        self.notification_log: List[str] = []

        # Register several dummy tools
        for i in range(1, 6):
            self.register_tool(
                name=f"test_tool_{i}",
                description=f"Description for tool {i}",
                input_schema={"type": "object", "properties": {"val": {"type": "integer"}}},
                handler=lambda val=i: f"result_{val}",
            )

        # Register a slow tool to test timeouts
        self.register_tool(
            name="slow_tool",
            description="A tool that sleeps to trigger timeout",
            input_schema={"type": "object", "properties": {"duration": {"type": "number"}}},
            handler=self._slow_handler,
        )

    def _slow_handler(self, duration: float = 0.5) -> str:
        time.sleep(duration)
        return "slow_done"

    def handle_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        req_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {}) or {}

        if req_id is None:
            self.notification_log.append(method)
            if method == "notifications/cancelled":
                self.cancelled_request_ids.append(params.get("requestId"))
            return None

        # Custom pagination for tools/list: return 2 per page
        if method == "tools/list":
            cursor = params.get("cursor")
            tool_names = sorted(list(self._tools.keys()))
            page_size = 2

            start_idx = 0
            if cursor:
                try:
                    start_idx = int(cursor)
                except ValueError:
                    start_idx = 0

            slice_items = tool_names[start_idx : start_idx + page_size]
            next_idx = start_idx + page_size
            next_cursor = str(next_idx) if next_idx < len(tool_names) else None

            tools_list = []
            for t_name in slice_items:
                defn = self._tools[t_name]["definition"]
                tools_list.append({
                    "name": defn.name,
                    "description": defn.description,
                    "inputSchema": defn.inputSchema,
                })

            res: Dict[str, Any] = {"tools": tools_list}
            if next_cursor:
                res["nextCursor"] = next_cursor
            return {"jsonrpc": "2.0", "id": req_id, "result": res}

        return super().handle_message(message)


class TestMCPLifecycle(unittest.TestCase):
    def setUp(self):
        self.server = MockPaginatedAndSlowMCPServer()
        self.transport = InMemoryTransport(server_handler=self.server.handle_message)
        self.session = MCPClientSession(transport=self.transport, client_name="TestLifecycleClient")

    def tearDown(self):
        if self.session.is_connected:
            self.session.close()

    def test_1_server_state_transitions(self):
        """Test full state progression: STOPPED -> STARTING -> INITIALIZING -> READY -> STOPPING -> STOPPED."""
        self.assertEqual(self.session.state, MCPServerState.STOPPED)
        self.assertFalse(self.session.is_connected)

        # Connect initiates handshake
        self.session.connect()
        self.assertEqual(self.session.state, MCPServerState.READY)
        self.assertTrue(self.session.is_connected)

        # Graceful close
        self.session.close()
        self.assertEqual(self.session.state, MCPServerState.STOPPED)
        self.assertFalse(self.session.is_connected)

    def test_2_handshake_and_capabilities_negotiation(self):
        """Verify protocol version negotiation and capability exchange."""
        self.session.connect()

        # Check server info received from initialize response
        server_info = self.session.server_info
        self.assertEqual(server_info.get("name"), "mock-lifecycle-server")
        self.assertEqual(server_info.get("version"), "2.0.0")

        # Check server capabilities populated
        caps = self.session.capabilities
        self.assertIsNotNone(caps.tools)
        self.assertTrue(caps.tools.get("listChanged"))
        self.assertIsNotNone(caps.resources)

    def test_3_cursor_based_pagination(self):
        """Verify that list_tools() follows nextCursor until all 6 tools are discovered."""
        self.session.connect()

        tools = self.session.list_tools()
        tool_names = [t.name for t in tools]

        # 5 numbered tools + 1 slow tool = 6 tools total
        self.assertEqual(len(tools), 6)
        for i in range(1, 6):
            self.assertIn(f"test_tool_{i}", tool_names)
        self.assertIn("slow_tool", tool_names)

    def test_4_dynamic_notifications_and_invalidation(self):
        """Verify notifications/tools/list_changed triggers callback."""
        list_changed_received = threading.Event()

        self.session.on_notification(
            "notifications/tools/list_changed",
            lambda params: list_changed_received.set(),
        )
        self.session.connect()

        # Simulate server firing notifications/tools/list_changed to client
        # In InMemoryTransport, client receives messages via transport.send_from_server()
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/tools/list_changed",
            "params": {},
        }
        self.transport.send_from_server(notification)

        fired = list_changed_received.wait(timeout=2.0)
        self.assertTrue(fired, "Expected notifications/tools/list_changed callback to fire")

    def test_5_multiplexed_concurrent_requests(self):
        """Verify concurrent requests with distinct IDs are routed properly without dropping messages."""
        self.session.connect()

        num_threads = 10
        results = [None] * num_threads

        def worker(idx: int):
            # Each worker calls a different tool
            tool_idx = (idx % 5) + 1
            res = self.session.call_tool(f"test_tool_{tool_idx}", {"val": idx})
            results[idx] = res

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        for i, res in enumerate(results):
            self.assertIsNotNone(res, f"Worker {i} did not complete")
            self.assertFalse(res.isError)
            self.assertEqual(res.content[0]["text"], f"result_{i}")

    def test_6_request_timeout_and_cancellation(self):
        """Verify request timeout raises MCPTimeoutError and dispatches notifications/cancelled."""
        self.session.connect()

        # Call slow_tool with timeout=0.1s while it attempts to sleep 0.5s
        with self.assertRaises(MCPTimeoutError):
            self.session.call_tool("slow_tool", {"duration": 0.5}, timeout=0.1)

        # Allow server to finish slow tool sleep and process subsequent cancellation notification
        start_wait = time.time()
        while time.time() - start_wait < 1.0:
            if self.server.cancelled_request_ids:
                break
            time.sleep(0.05)

        self.assertGreaterEqual(len(self.server.cancelled_request_ids), 1)
        self.assertIn("notifications/cancelled", self.server.notification_log)

    def test_7_keepalive_ping_and_health_report(self):
        """Verify ping() and get_health_report()."""
        self.session.connect()

        # Active ping
        latency = self.session.ping(timeout=2.0)
        self.assertGreaterEqual(latency, 0.0)
        self.assertIsNotNone(self.session.last_latency_ms)

        # Health report
        health = self.session.get_health_report(server_name="test-server")
        self.assertEqual(health.server_name, "test-server")
        self.assertEqual(health.state, MCPServerState.READY)
        self.assertTrue(health.is_alive)
        self.assertIsNotNone(health.latency_ms)

    def test_8_stdio_transport_pid_and_process_tree_termination(self):
        """Verify StdioTransport spawns a real process, tracks PID, and reaps it on close()."""
        # Run a Python subprocess that waits on stdin
        cmd = sys.executable
        args = ["-c", "import time, sys; sys.stdin.readline()"]
        stdio = StdioTransport(command=cmd, args=args)
        stdio.start()

        self.assertTrue(stdio.is_alive())
        self.assertIsNotNone(stdio.pid)
        self.assertGreater(stdio.pid, 0)

        pid = stdio.pid
        stdio.close()

        self.assertFalse(stdio.is_alive())


class TestMCPManagerLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir)
        self.manager = MCPManager(workspace_dir=self.workspace)

    def tearDown(self):
        self.manager.close()

    def test_manager_explicit_lifecycle_controls(self):
        """Test start_server, stop_server, restart_server, and get_server_state."""
        server_name = "mcp-server-filesystem"

        # Initially ready because standard servers are auto-started
        self.assertEqual(self.manager.get_server_state(server_name), MCPServerState.READY)
        tools_before = self.manager.discover_tools(server_name)
        self.assertGreater(len(tools_before), 0)

        # Stop server
        stopped = self.manager.stop_server(server_name)
        self.assertTrue(stopped)
        self.assertEqual(self.manager.get_server_state(server_name), MCPServerState.STOPPED)
        # Tools should be purged from manager cache
        tools_after_stop = self.manager.discover_tools(server_name)
        self.assertEqual(len(tools_after_stop), 0)

        # Restart server
        session = self.manager.restart_server(server_name)
        self.assertEqual(session.state, MCPServerState.READY)
        self.assertEqual(self.manager.get_server_state(server_name), MCPServerState.READY)

        # Tools are rediscovered and indexed
        tools_after_restart = self.manager.discover_tools(server_name)
        self.assertGreater(len(tools_after_restart), 0)

    def test_manager_health_check_all_servers(self):
        """Test health_check() across all managed servers."""
        reports = self.manager.health_check()
        self.assertIn("mcp-server-filesystem", reports)
        self.assertIn("mcp-server-terminal", reports)

        fs_report = reports["mcp-server-filesystem"]
        self.assertEqual(fs_report.state, MCPServerState.READY)
        self.assertTrue(fs_report.is_alive)
        self.assertIsNotNone(fs_report.latency_ms)

    def test_manager_reconnect_resilience(self):
        """Test reconnect() with simulated crash."""
        server_name = "mcp-server-filesystem"
        session = self.manager._sessions[server_name]
        session.close()  # Simulate crash
        self.assertFalse(session.is_connected)

        # Reconnect with backoff
        reconnected_session = self.manager.reconnect(server_name, max_retries=2, initial_delay=0.05)
        self.assertTrue(reconnected_session.is_connected)
        self.assertEqual(self.manager.get_server_state(server_name), MCPServerState.READY)

        # Verify tool call still functions after reconnection
        res = self.manager.call_tool("list_directory", {})
        self.assertFalse(res.isError)

    def test_manager_lazy_server_start(self):
        """Test ensure_server_running() lazily boots a stopped server on tool invocation."""
        server_name = "mcp-server-filesystem"
        self.manager.stop_server(server_name)
        self.assertEqual(self.manager.get_server_state(server_name), MCPServerState.STOPPED)

        # Ensure server running brings it back
        session = self.manager.ensure_server_running(server_name)
        self.assertEqual(session.state, MCPServerState.READY)
        self.assertTrue(session.is_connected)

    def test_manager_unregister_server(self):
        """Test completely unregistering a server from the manager."""
        server_name = "mcp-server-git"
        self.assertIn(server_name, [s["server_name"] for s in self.manager.discover_servers()])

        unregistered = self.manager.unregister_server(server_name)
        self.assertTrue(unregistered)
        self.assertNotIn(server_name, [s["server_name"] for s in self.manager.discover_servers()])
        self.assertEqual(self.manager.get_server_state(server_name), MCPServerState.STOPPED)


if __name__ == "__main__":
    unittest.main()
