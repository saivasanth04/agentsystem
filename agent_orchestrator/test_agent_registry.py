"""
Tests for Dynamic Agent Registry, Capability Matching, and DynamicAgent instantiation.
"""
from pathlib import Path
import pytest
from agent_orchestrator.registry.agent_registry import AgentRegistry, AgentManifest
from agent_orchestrator.agents.dynamic_agent import DynamicAgent
from agent_orchestrator.agents.coder import CoderAgent


class TestDynamicAgentRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        manifest = AgentManifest(
            name="python-debugger",
            role_description="Diagnoses Python stack traces and repairs bugs.",
            capabilities=["python", "debugging", "pytest"],
            tools=["terminal", "filesystem"],
            skills=["debugging-and-error-recovery"],
            model_requirements={"temperature": 0.1},
            category="debugging",
        )
        reg.register(manifest)

        retrieved = reg.get("python-debugger")
        assert retrieved is not None
        assert retrieved.name == "python-debugger"
        assert "debugging" in retrieved.capabilities

    def test_discover_by_capabilities_and_tools(self):
        reg = AgentRegistry()
        reg.register(AgentManifest(
            name="python-debugger",
            role_description="Diagnoses Python stack traces and fixes logic flaws.",
            capabilities=["python", "debugging", "traceback"],
            tools=["terminal", "filesystem"],
            category="debugging",
        ))
        reg.register(AgentManifest(
            name="react-specialist",
            role_description="Builds React components and JSX styles.",
            capabilities=["react", "jsx", "frontend"],
            tools=["filesystem"],
            category="frontend",
        ))
        reg.register(AgentManifest(
            name="sql-optimizer",
            role_description="Optimizes SQL schemas and indexes.",
            capabilities=["sql", "database", "query-tuning"],
            tools=["terminal", "filesystem"],
            category="database",
        ))

        # 1. Discover python debugger
        res = reg.discover(query="debug python exception", capabilities=["debugging", "python"], top_k=2)
        assert len(res) >= 1
        top_agent, score = res[0]
        assert top_agent.name == "python-debugger"
        assert score > 1.0

        # 2. Discover React specialist
        res_react = reg.discover(query="create button component in react", capabilities=["react", "frontend"], top_k=1)
        assert res_react[0][0].name == "react-specialist"

    def test_load_from_directory(self, tmp_path):
        reg = AgentRegistry()
        manifests_dir = Path(__file__).parent / "registry" / "manifests"
        if manifests_dir.exists():
            loaded = reg.load_from_directory(manifests_dir)
            assert len(loaded) >= 4
            assert reg.get("python-debugger") is not None
            assert reg.get("react-specialist") is not None
            assert reg.get("security-auditor") is not None
            assert reg.get("database-architect") is not None

    def test_create_agent_instance_dynamic(self):
        reg = AgentRegistry()
        manifest = AgentManifest(
            name="custom-debugger",
            role_description="Custom debugger persona",
            capabilities=["python", "debugging"],
            tools=["filesystem", "terminal"],
            skills=["debugging-and-error-recovery"],
            model_requirements={"temperature": 0.05},
        )
        reg.register(manifest)

        instance = reg.create_agent_instance("custom-debugger")
        assert isinstance(instance, DynamicAgent)
        assert instance.name == "custom-debugger"
        assert "debugging" in instance.capabilities

        scoped = instance._resolve_scoped_tools()
        assert "read_file" in scoped
        assert "terminal_execute" in scoped
        assert "complete_task" in scoped

    def test_create_agent_instance_class_bound(self):
        reg = AgentRegistry()
        manifest = AgentManifest(
            name="CODER",
            role_description="Generates production code",
            agent_class=CoderAgent,
            capabilities=["code-generation"],
            tools=["filesystem", "terminal"],
        )
        reg.register(manifest)

        instance = reg.create_agent_instance("CODER")
        assert isinstance(instance, CoderAgent)
        assert instance.name == "CODER"
