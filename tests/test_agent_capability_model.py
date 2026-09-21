"""
Comprehensive Unit Tests for Issue #69: Agent Capability Model.
Validates ModelTier, ModelConstraint, CapabilityDefinition, CapabilityRegistry,
AgentManifest capability resolution, BuiltinToolRegistry scoping, and dynamic permission evaluation.
"""
import unittest
from typing import Any, Dict, List

from agent_orchestrator.capabilities.model import (
    CapabilityDefinition,
    CapabilityRegistry,
    ModelConstraint,
    ModelTier,
    default_capability_registry,
    expand_tool_names,
)
from agent_orchestrator.registry.agent_registry import AgentManifest, AgentRegistry
from agent_orchestrator.runtime.permission_policy import (
    PermissionPolicy,
    PolicyEvaluationResult,
    ToolOperationType,
    ToolPermissionPolicyEngine,
)
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestModelConstraints(unittest.TestCase):
    """Tests for ModelTier and ModelConstraint validation."""

    def test_model_tier_ranking(self):
        self.assertLess(ModelTier.FAST.rank, ModelTier.BALANCED.rank)
        self.assertLess(ModelTier.BALANCED.rank, ModelTier.FRONTIER.rank)

    def test_model_constraint_context_window(self):
        constraint = ModelConstraint(min_context_window=32000)
        valid, reason = constraint.validate_model("gpt-4o", context_window=64000)
        self.assertTrue(valid)
        self.assertEqual(reason, "")

        valid, reason = constraint.validate_model("gpt-4o-mini", context_window=16000)
        self.assertFalse(valid)
        self.assertIn("below minimum required", reason)

    def test_model_constraint_required_features(self):
        constraint = ModelConstraint(required_features=["tools", "json_mode"])
        valid, reason = constraint.validate_model("test-model", features=["tools", "json_mode", "vision"])
        self.assertTrue(valid)

        valid, reason = constraint.validate_model("test-model", features=["tools"])
        self.assertFalse(valid)
        self.assertIn("lacks required feature 'json_mode'", reason)

    def test_model_constraint_forbidden_models(self):
        constraint = ModelConstraint(forbidden_models=["*deprecated*", "gpt-3.5*"])
        valid, reason = constraint.validate_model("gpt-3.5-turbo")
        self.assertFalse(valid)
        self.assertIn("matches forbidden pattern", reason)

        valid, reason = constraint.validate_model("claude-3-5-sonnet")
        self.assertTrue(valid)

    def test_model_constraint_supported_models(self):
        constraint = ModelConstraint(supported_models=["claude-3-5-sonnet", "gpt-4o*"])
        valid, reason = constraint.validate_model("claude-3-5-sonnet")
        self.assertTrue(valid)

        valid, reason = constraint.validate_model("gpt-4o-mini")
        self.assertTrue(valid)

        valid, reason = constraint.validate_model("gemini-pro")
        self.assertFalse(valid)
        self.assertIn("not in supported models list", reason)

    def test_model_constraint_serialization(self):
        mc = ModelConstraint(
            min_context_window=128000,
            required_features=["tools"],
            preferred_tier=ModelTier.FRONTIER,
            supported_models=["o1", "o3"],
            forbidden_models=["gpt-3.5*"],
        )
        d = mc.to_dict()
        reconstructed = ModelConstraint.from_dict(d)
        self.assertEqual(reconstructed.min_context_window, 128000)
        self.assertEqual(reconstructed.preferred_tier, ModelTier.FRONTIER)
        self.assertEqual(reconstructed.supported_models, ["o1", "o3"])


class TestCapabilityRegistry(unittest.TestCase):
    """Tests for CapabilityRegistry, tool resolution, and constraint aggregation."""

    def setUp(self):
        self.registry = CapabilityRegistry()

    def test_standard_capabilities_registered(self):
        for cap_id in [
            "planning",
            "specification",
            "architecture",
            "code_generation",
            "testing_execution",
            "code_review",
            "debugging",
            "security_audit",
            "swarm_coordination",
        ]:
            cap = self.registry.get(cap_id)
            self.assertIsNotNone(cap, f"Capability '{cap_id}' should be pre-populated")

    def test_capability_alias_resolution(self):
        cap = self.registry.get("coding")
        self.assertIsNotNone(cap)
        self.assertEqual(cap.capability_id, "code_generation")

        cap = self.registry.get("tdd")
        self.assertIsNotNone(cap)
        self.assertEqual(cap.capability_id, "testing_execution")

    def test_resolve_tools_group_expansion(self):
        tools = self.registry.resolve_tools(["testing_execution"])
        self.assertIn("read_file", tools)
        self.assertIn("write_file", tools)
        self.assertIn("terminal_execute", tools)
        self.assertIn("inspect_existing_tests", tools)
        # Should be deduplicated
        self.assertEqual(len(tools), len(set(tools)))

    def test_resolve_skills(self):
        skills = self.registry.resolve_skills(["code_generation", "code_review"])
        self.assertIn("code-simplification", skills)
        self.assertIn("code-review-and-quality", skills)
        self.assertIn("doubt-driven-development", skills)

    def test_resolve_model_constraints_aggregation(self):
        constraint = self.registry.resolve_model_constraints(["planning", "architecture"])
        self.assertIsNotNone(constraint)
        # Architecture requires 32000, planning requires 16000 -> max is 32000
        self.assertEqual(constraint.min_context_window, 32000)
        # Architecture requires FRONTIER, planning requires BALANCED -> highest is FRONTIER
        self.assertEqual(constraint.preferred_tier, ModelTier.FRONTIER)

    def test_resolve_permissions(self):
        policy = self.registry.resolve_permissions(["testing_execution"])
        self.assertIn(ToolOperationType.READ, policy.allowed_operations)
        self.assertIn(ToolOperationType.WRITE, policy.allowed_operations)
        self.assertIn(ToolOperationType.EXECUTE, policy.allowed_operations)


class TestAgentManifestCapabilityIntegration(unittest.TestCase):
    """Tests for AgentManifest capability methods and AgentRegistry parsing."""

    def test_manifest_effective_tools_and_skills(self):
        manifest = AgentManifest(
            name="custom-debugger",
            role_description="Custom diagnostic persona",
            capabilities=["debugging"],
            tools=["git_status"],
            skills=["custom-skill"],
        )
        effective_tools = manifest.get_effective_tools()
        self.assertIn("git_status", effective_tools)
        self.assertIn("terminal_execute", effective_tools)
        self.assertIn("read_file", effective_tools)

        effective_skills = manifest.get_effective_skills()
        self.assertIn("custom-skill", effective_skills)
        self.assertIn("debugging-and-error-recovery", effective_skills)

    def test_manifest_model_validation(self):
        manifest = AgentManifest(
            name="architecture-bot",
            role_description="System architect",
            capabilities=["architecture"],
        )
        # Architecture capability prefers FRONTIER with min 32000 context
        valid, reason = manifest.validate_model_compatibility("claude-3-opus", context_window=64000)
        self.assertTrue(valid)

        valid, reason = manifest.validate_model_compatibility("gpt-3.5", context_window=4000)
        self.assertFalse(valid)

    def test_manifest_serialization_roundtrip(self):
        reg = AgentRegistry()
        data = {
            "name": "sec-auditor",
            "role_description": "Audits OWASP vulnerabilities",
            "capabilities": ["security_audit"],
            "model_constraint": {
                "min_context_window": 64000,
                "preferred_tier": "FRONTIER",
                "required_features": ["tools"],
            },
            "permission_policy": {
                "allowed_operations": ["READ", "CONTROL"],
                "allowed_write_patterns": [],
            },
        }
        manifest = reg.register_from_dict(data)
        self.assertEqual(manifest.name, "sec-auditor")
        self.assertIsNotNone(manifest.model_constraint)
        self.assertEqual(manifest.model_constraint.min_context_window, 64000)
        self.assertIsNotNone(manifest.permission_policy)
        self.assertEqual(manifest.permission_policy.allowed_operations, {ToolOperationType.READ, ToolOperationType.CONTROL})


class TestToolScopingAndPermissions(unittest.TestCase):
    """Tests for BuiltinToolRegistry scoping and ToolPermissionPolicyEngine dynamic evaluation."""

    def setUp(self):
        import tempfile
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = WorkspaceManager(root_dir=self.temp_dir.name)
        self.agent_registry = AgentRegistry()
        self.tool_registry = BuiltinToolRegistry(
            workspace=self.workspace,
            agent_registry=self.agent_registry,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_builtin_tool_registry_resolves_capabilities(self):
        tools = self.tool_registry.resolve_tools_for_capabilities(["debugging"])
        tool_names = [t.name for t in tools]
        self.assertIn("read_file", tool_names)
        self.assertIn("terminal_execute", tool_names)
        self.assertIn("complete_task", tool_names)

    def test_get_tools_for_agent_with_custom_manifest(self):
        manifest = AgentManifest(
            name="PYTHON_SPECIALIST",
            role_description="Python specialist",
            capabilities=["debugging"],
            tools=["git_status"],
        )
        self.agent_registry.register(manifest)

        tools = self.tool_registry.get_tools_for_agent("PYTHON_SPECIALIST")
        tool_names = [t.name for t in tools]
        self.assertIn("git_status", tool_names)
        self.assertIn("terminal_execute", tool_names)
        self.assertIn("read_file", tool_names)

    def test_permission_policy_evaluation_with_custom_policy(self):
        # Read-only policy
        read_only_policy = PermissionPolicy(
            allowed_operations={ToolOperationType.READ},
            allowed_write_patterns=[],
        )
        # Attempting write_file should be blocked
        res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="CUSTOM_AGENT",
            tool_name="write_file",
            args={"filepath": "test.py", "content": "print(1)"},
            policy=read_only_policy,
        )
        self.assertFalse(res.allowed)
        self.assertIn("forbidden from performing 'WRITE' operations", res.reason)

        # Attempting read_file should be allowed
        res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="CUSTOM_AGENT",
            tool_name="read_file",
            args={"filepath": "test.py"},
            policy=read_only_policy,
        )
        self.assertTrue(res.allowed)

    def test_permission_policy_with_agent_manifest(self):
        # Register an agent with specific write restrictions
        manifest = AgentManifest(
            name="DOCS_WRITER",
            role_description="Writes documentation",
            permission_policy=PermissionPolicy(
                allowed_operations={ToolOperationType.READ, ToolOperationType.WRITE},
                allowed_write_patterns=["docs/**", "*.md"],
            ),
        )
        self.agent_registry.register(manifest)

        # Writing to docs/README.md is allowed
        res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="DOCS_WRITER",
            tool_name="write_file",
            args={"filepath": "docs/README.md", "content": "# Docs"},
            agent_registry=self.agent_registry,
        )
        self.assertTrue(res.allowed)

        # Writing to src/main.py is forbidden
        res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role="DOCS_WRITER",
            tool_name="write_file",
            args={"filepath": "src/main.py", "content": "code"},
            agent_registry=self.agent_registry,
        )
        self.assertFalse(res.allowed)
        self.assertIn("can only write to authorized paths", res.reason)


if __name__ == "__main__":
    unittest.main()
