"""
Unit tests for Issue #70: Tool Registry.
Validates register, unregister, discover, get_schema, authorize, execute, and health_check primitives.
"""
import tempfile
import unittest
from typing import Any, Dict

from pydantic import BaseModel, Field

from agent_orchestrator.runtime.permission_policy import (
    PermissionPolicy,
    ToolOperationType,
)
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.registry import (
    HealthReport,
    ToolEntry,
    ToolExecutionResult,
    ToolHealthStatus,
    ToolRegistry,
)
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.tools.workspace import WorkspaceManager


class CustomCalcInput(BaseModel):
    a: int = Field(description="First number")
    b: int = Field(description="Second number")


def custom_add(a: int, b: int) -> int:
    """Adds two numbers."""
    return a + b


class TestToolRegistryPrimitives(unittest.TestCase):
    """Tests core ToolRegistry lifecycle, discovery, schema, auth, execution, and health check."""

    def setUp(self):
        self.registry = ToolRegistry()

    def test_register_and_get(self):
        entry = self.registry.register(
            tool=custom_add,
            name="custom_add",
            description="Adds two integers",
            args_schema=CustomCalcInput,
            operation_type=ToolOperationType.READ,
            tags=["math", "calculation"],
            category="math",
            aliases=["add", "sum_tool"],
        )
        self.assertEqual(entry.name, "custom_add")
        self.assertIn("math", entry.tags)

        # Lookup by primary name and alias
        self.assertIsNotNone(self.registry.get("custom_add"))
        self.assertIsNotNone(self.registry.get("add"))
        self.assertIsNotNone(self.registry.get("SUM_TOOL"))

    def test_unregister(self):
        self.registry.register(
            tool=custom_add,
            name="temp_tool",
            aliases=["tmp"],
        )
        self.assertIsNotNone(self.registry.get("temp_tool"))
        self.assertIsNotNone(self.registry.get("tmp"))

        res = self.registry.unregister("temp_tool")
        self.assertTrue(res)
        self.assertIsNone(self.registry.get("temp_tool"))
        self.assertIsNone(self.registry.get("tmp"))

    def test_discover_semantic_and_tags(self):
        self.registry.register(
            tool=custom_add,
            name="calculate_sum",
            description="Computes addition of integer values",
            tags=["math", "arithmetic"],
            category="math",
        )
        self.registry.register(
            tool=lambda text: text.upper(),
            name="string_uppercase",
            description="Converts text string to uppercase format",
            tags=["string", "text"],
            category="text",
        )

        # Discovery by query keyword
        matches = self.registry.discover(query="addition integer")
        self.assertTrue(len(matches) > 0)
        self.assertEqual(matches[0][0].name, "calculate_sum")

        # Discovery by tag
        tag_matches = self.registry.discover(tags=["text"])
        self.assertTrue(len(tag_matches) > 0)
        self.assertEqual(tag_matches[0][0].name, "string_uppercase")

    def test_get_schema(self):
        self.registry.register(
            tool=custom_add,
            name="custom_add",
            description="Adds two numbers",
            args_schema=CustomCalcInput,
        )
        schema = self.registry.get_schema("custom_add")
        self.assertIsNotNone(schema)
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "custom_add")
        props = schema["function"]["parameters"]["properties"]
        self.assertIn("a", props)
        self.assertIn("b", props)

    def test_authorize(self):
        self.registry.register(
            tool=lambda filepath: "content",
            name="write_doc",
            operation_type=ToolOperationType.WRITE,
        )
        # Authorize against restricted reviewer role (cannot write)
        auth_res = self.registry.authorize(
            name="write_doc",
            args={"filepath": "test.txt"},
            agent_role="REVIEWER",
        )
        self.assertFalse(auth_res.allowed)

        # Authorize against coder role (can write)
        auth_res_coder = self.registry.authorize(
            name="write_doc",
            args={"filepath": "src/main.py"},
            agent_role="CODER",
        )
        self.assertTrue(auth_res_coder.allowed)

    def test_execute_pipeline(self):
        self.registry.register(
            tool=custom_add,
            name="add_numbers",
            description="Adds numbers",
        )
        result = self.registry.execute("add_numbers", {"a": 15, "b": 25})
        self.assertTrue(result.success)
        self.assertEqual(result.output, 40)
        self.assertGreater(result.duration_ms, 0.0)

    def test_execute_unauthorized_fails_gracefully(self):
        self.registry.register(
            tool=lambda filepath, content: "written",
            name="write_file",
            operation_type=ToolOperationType.WRITE,
        )
        result = self.registry.execute(
            name="write_file",
            args={"filepath": "src/app.py", "content": "x"},
            caller_role="REVIEWER",
        )
        self.assertFalse(result.success)
        self.assertIn("Permission Denied", result.error)

    def test_health_check_probes(self):
        # Healthy probe
        self.registry.register(
            tool=lambda: 1,
            name="healthy_service",
            health_check_fn=lambda: (True, "Service operational"),
        )
        # Unhealthy probe
        self.registry.register(
            tool=lambda: 2,
            name="unhealthy_service",
            health_check_fn=lambda: (False, "Connection refused to daemon"),
        )
        # Default probe (no custom fn)
        self.registry.register(
            tool=lambda: 3,
            name="default_service",
        )

        reports = self.registry.health_check()
        self.assertEqual(reports["healthy_service"].status, ToolHealthStatus.HEALTHY)
        self.assertEqual(reports["unhealthy_service"].status, ToolHealthStatus.UNHEALTHY)
        self.assertEqual(reports["default_service"].status, ToolHealthStatus.HEALTHY)

        # Single tool health check
        single_report = self.registry.health_check(name="unhealthy_service")
        self.assertEqual(single_report["unhealthy_service"].status, ToolHealthStatus.UNHEALTHY)


class TestBuiltinToolRegistryIntegration(unittest.TestCase):
    """Tests that BuiltinToolRegistry and UnifiedToolDispatcher expose all ToolRegistry primitives."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = WorkspaceManager(root_dir=self.temp_dir.name)
        self.builtin = BuiltinToolRegistry(workspace=self.workspace)
        self.dispatcher = UnifiedToolDispatcher(builtin_registry=self.builtin)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_builtin_tool_registry_has_primitives(self):
        # 1. get_schema
        schema = self.builtin.get_schema("read_file")
        self.assertIsNotNone(schema)
        self.assertEqual(schema["function"]["name"], "read_file")

        # 2. discover
        discovered = self.builtin.discover(query="read file slice")
        self.assertTrue(len(discovered) > 0)
        tool_names = [t[0].name for t in discovered]
        self.assertIn("read_file", tool_names)

        # 3. authorize
        auth = self.builtin.authorize("write_file", {"filepath": "main.py"}, agent_role="REVIEWER")
        self.assertFalse(auth.allowed)

        # 4. execute
        self.workspace.write_file("hello.txt", "world")
        exec_res = self.builtin.execute("read_file", {"filepath": "hello.txt"})
        self.assertTrue(exec_res.success)
        self.assertIn("world", str(exec_res.output))

        # 5. health_check
        reports = self.builtin.health_check()
        self.assertIn("read_file", reports)
        self.assertEqual(reports["read_file"].status, ToolHealthStatus.HEALTHY)

        # 6. register & unregister dynamic tool
        def temp_fn():
            return "ok"
        self.builtin.register(tool=temp_fn, name="dynamic_probe")
        self.assertIsNotNone(self.builtin.get_schema("dynamic_probe"))
        self.builtin.unregister("dynamic_probe")
        self.assertIsNone(self.builtin.get_schema("dynamic_probe"))

    def test_unified_dispatcher_primitives(self):
        # Verify UnifiedToolDispatcher delegates discovery, schema, execution, and health check
        schema = self.dispatcher.get_schema("write_file")
        self.assertIsNotNone(schema)

        reports = self.dispatcher.health_check()
        self.assertIn("read_file", reports)
        # Check MCP server reports are present
        mcp_reports = [k for k in reports if k.startswith("mcp_server_")]
        self.assertTrue(len(mcp_reports) > 0)


if __name__ == "__main__":
    unittest.main()
