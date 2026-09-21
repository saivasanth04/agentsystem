"""
Comprehensive Tests for MCP Health Checking and Adaptive Routing.
Validates:
1. MCPCircuitBreaker state machine: CLOSED -> OPEN -> HALF_OPEN -> CLOSED / OPEN.
2. MCPManager operational health monitoring and fail-fast circuit breaking.
3. The exact requested multi-provider scenario:
   - FS MCP -> HEALTHY (routes to MCP Filesystem server)
   - Graft -> HEALTHY (routes to native task DAG engine)
   - CBM -> UNHEALTHY (circuit breaker trips, schemas dynamically pruned, and calls adaptively route to native fallback)
4. Self-healing / recovery when CBM MCP recovers.
"""
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict

from agent_orchestrator.mcp.circuit_breaker import CircuitState, MCPCircuitBreaker
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.mcp.protocol import MCPServerState, ToolCallResult
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.registry import ToolHealthStatus
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestMCPCircuitBreaker(unittest.TestCase):
    def test_circuit_breaker_transitions(self):
        cb = MCPCircuitBreaker(
            server_name="test-server",
            failure_threshold=3,
            recovery_timeout=0.1,  # Fast timeout for test
            half_open_max_trials=1,
        )

        # 1. Starts CLOSED
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.can_attempt())

        # 2. Record failures below threshold
        cb.record_failure(RuntimeError("fail 1"))
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.failure_count, 1)

        cb.record_failure(RuntimeError("fail 2"))
        self.assertEqual(cb.state, CircuitState.CLOSED)

        # 3. Third failure trips to OPEN
        cb.record_failure(RuntimeError("fail 3"))
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.can_attempt())

        # 4. Wait for recovery timeout -> transitions to HALF_OPEN
        time.sleep(0.15)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        self.assertTrue(cb.can_attempt())

        # 5. Success in HALF_OPEN resets to CLOSED
        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.failure_count, 0)
        self.assertTrue(cb.can_attempt())

    def test_circuit_breaker_canary_failure(self):
        cb = MCPCircuitBreaker(
            server_name="test-server",
            failure_threshold=2,
            recovery_timeout=0.1,
            half_open_max_trials=1,
        )
        cb.record_failure(RuntimeError("fail 1"))
        cb.record_failure(RuntimeError("fail 2"))
        self.assertEqual(cb.state, CircuitState.OPEN)

        time.sleep(0.15)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        # Canary fails -> immediately trips back to OPEN
        cb.record_failure(RuntimeError("canary probe failed"))
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.can_attempt())


class TestMCPManagerHealth(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir)
        self.manager = MCPManager(workspace_dir=self.workspace)

    def tearDown(self):
        self.manager.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_manager_health_evaluation(self):
        # By default reference servers are healthy
        self.assertEqual(self.manager.get_server_health_status("mcp-server-filesystem"), "HEALTHY")
        self.assertEqual(self.manager.get_server_health_status("mcp-server-memory"), "HEALTHY")

        # Explicitly trip breaker
        cb = self.manager.get_circuit_breaker("mcp-server-memory")
        cb.trip_open("Corrupted AST database")

        self.assertEqual(self.manager.get_server_health_status("mcp-server-memory"), "UNHEALTHY")

        # Fail-fast tool call
        res = self.manager.call_tool("store_memory", {"key": "k", "value": "v"})
        self.assertTrue(res.isError)
        self.assertIn("circuit breaker OPEN", str(res.content))
        self.assertTrue(res.structured_data.get("circuit_open"))

    def test_manager_health_check_active_ping(self):
        reports = self.manager.health_check()
        self.assertIn("mcp-server-filesystem", reports)
        fs_rep = reports["mcp-server-filesystem"]
        self.assertTrue(fs_rep.is_connected)
        self.assertEqual(fs_rep.circuit_state, "CLOSED")
        self.assertIsNotNone(fs_rep.latency_ms)


class TestUnifiedDispatcherAdaptiveRouting(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_dir = Path(self.temp_dir)
        self.workspace = WorkspaceManager(self.workspace_dir)
        self.builtin_registry = BuiltinToolRegistry(self.workspace)
        self.mcp_manager = MCPManager(workspace_dir=self.workspace_dir)
        self.dispatcher = UnifiedToolDispatcher(self.builtin_registry, self.mcp_manager)

    def tearDown(self):
        self.dispatcher.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_exact_scenario_fs_healthy_graft_healthy_cbm_unhealthy(self):
        """
        Verify the exact scenario requested by user:
        FS MCP -> healthy
        Graft  -> healthy
        CBM    -> unhealthy
        and routing reacts accordingly!
        """
        # Set up health states
        # 1. FS MCP is HEALTHY
        self.assertEqual(self.mcp_manager.get_server_health_status("mcp-server-filesystem"), "HEALTHY")

        # 2. Graft (spawn_subtasks / task DAG grafting) is native & HEALTHY
        graft_schema = self.builtin_registry.get_schema("spawn_subtasks")
        self.assertIsNotNone(graft_schema)

        # 3. Simulate CBM becoming UNHEALTHY (e.g. database corruption or memory crash)
        cbm_cb = self.mcp_manager.get_circuit_breaker("mcp-server-memory")
        cbm_cb.trip_open("AST Index Server Out Of Memory")
        self.assertEqual(self.mcp_manager.get_server_health_status("mcp-server-memory"), "UNHEALTHY")

        # ----------------------------------------------------
        # Reaction 1: Dynamic Schema Pruning in get_schemas()
        # ----------------------------------------------------
        # Global schemas (unscoped)
        all_schemas = self.dispatcher.get_schemas()
        all_schema_names = [s["function"]["name"] for s in all_schemas]

        # FS MCP is healthy -> FS MCP tools ARE present
        self.assertIn("read_file", all_schema_names)
        self.assertIn("write_file", all_schema_names)

        # Graft is healthy -> spawn_subtasks IS present
        self.assertIn("spawn_subtasks", all_schema_names)

        # CBM is UNHEALTHY -> CBM MCP tools are SUPPRESSED from schemas to protect LLM context
        self.assertNotIn("store_memory", all_schema_names)
        self.assertNotIn("search_memory", all_schema_names)

        # Scoped schemas for CODER agent
        coder_schemas = self.dispatcher.get_schemas(agent_name="CODER")
        coder_schema_names = [s["function"]["name"] for s in coder_schemas]
        self.assertIn("read_file", coder_schema_names)
        self.assertIn("write_file", coder_schema_names)
        self.assertNotIn("store_memory", coder_schema_names)
        self.assertNotIn("search_memory", coder_schema_names)

        # ----------------------------------------------------
        # Reaction 2: Adaptive Routing during tool execution
        # ----------------------------------------------------

        # A. FS MCP call: routes to healthy MCP server
        fs_res = self.dispatcher.call_tool("write_file", {"path": "module.py", "content": "x = 10\n"})
        self.assertTrue(fs_res.get("success"))
        self.assertTrue(fs_res.get("is_mcp"))
        self.assertEqual(fs_res.get("server"), "mcp-server-filesystem")
        self.assertTrue((self.workspace_dir / "module.py").exists())

        # B. Graft call: routes to healthy native registry
        graft_res = self.dispatcher.call_tool(
            "spawn_subtasks",
            {"parent_task_id": "root", "subtasks": [{"description": "Subtask 1", "assigned_agent": "CODER"}]},
        )
        self.assertTrue(graft_res.get("success"))
        self.assertFalse(graft_res.get("is_mcp", False))

        # C. CBM call: routes adaptively to native CBM fallback without timing out
        mem_res = self.dispatcher.call_tool(
            "store_memory",
            {"key": "arch_decision", "value": "Use microkernel", "category": "architecture"},
        )
        # Should succeed via fallback!
        self.assertTrue(mem_res.get("success"))
        self.assertTrue(mem_res.get("fallback"))
        self.assertEqual(mem_res.get("fallback_provider"), "native_cbm")
        self.assertFalse(mem_res.get("is_mcp"))

        # Search memory also hits fallback
        search_res = self.dispatcher.call_tool("search_memory", {"query": "microkernel"})
        self.assertTrue(search_res.get("success"))
        self.assertTrue(search_res.get("fallback"))
        self.assertEqual(search_res.get("fallback_provider"), "native_cbm")
        self.assertGreaterEqual(len(search_res.get("data", {}).get("memories", [])), 1)

    def test_cbm_recovery_restores_primary_routing(self):
        """Verify that when CBM recovers, circuit breaker closes and primary MCP routing is restored."""
        cbm_cb = self.mcp_manager.get_circuit_breaker("mcp-server-memory")
        cbm_cb.trip_open("Temporary glitch")
        self.assertEqual(self.mcp_manager.get_server_health_status("mcp-server-memory"), "UNHEALTHY")

        # Call hits fallback
        res_fallback = self.dispatcher.call_tool("store_memory", {"key": "k1", "value": "v1"})
        self.assertTrue(res_fallback.get("fallback"))

        # Simulate CBM recovering: reset circuit breaker
        cbm_cb.reset()
        self.assertEqual(self.mcp_manager.get_server_health_status("mcp-server-memory"), "HEALTHY")

        # Schemas re-include CBM tools
        schemas = self.dispatcher.get_schemas(agent_name="CODER")
        schema_names = [s["function"]["name"] for s in schemas]
        self.assertIn("store_memory", schema_names)
        self.assertIn("search_memory", schema_names)

        # Tool calls route to primary MCP server again
        res_mcp = self.dispatcher.call_tool("store_memory", {"key": "k2", "value": "v2"})
        self.assertTrue(res_mcp.get("success"))
        self.assertTrue(res_mcp.get("is_mcp"))
        self.assertFalse(res_mcp.get("fallback", False))
        self.assertEqual(res_mcp.get("server"), "mcp-server-memory")

    def test_dispatcher_health_check_reporting(self):
        """Verify dispatcher.health_check() accurately reflects ToolHealthStatus."""
        cbm_cb = self.mcp_manager.get_circuit_breaker("mcp-server-memory")
        cbm_cb.trip_open("Disk crash")

        reports = self.dispatcher.health_check()

        self.assertIn("mcp_server_mcp-server-filesystem", reports)
        self.assertIn("mcp_server_mcp-server-memory", reports)

        fs_report = reports["mcp_server_mcp-server-filesystem"]
        self.assertEqual(fs_report.status, ToolHealthStatus.HEALTHY)

        cbm_report = reports["mcp_server_mcp-server-memory"]
        self.assertEqual(cbm_report.status, ToolHealthStatus.UNHEALTHY)
        self.assertIn("circuit=OPEN", cbm_report.details)


if __name__ == "__main__":
    unittest.main()
