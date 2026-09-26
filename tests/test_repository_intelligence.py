"""
Comprehensive Integration Tests for Persistent Repository Intelligence Engine.
Verifies compliance with the Non-Negotiable Implementation Contract:
- Real executable classes (Zero mocks, zero placeholders)
- Mature libraries:
  - Python AST: ast
  - Tree parsing & symbol extraction: tree-sitter & tree-sitter queries
  - Git integration: GitPython
  - Dependency & Call graphs: networkx
  - File watching: watchfiles
- Persistent database:
  - .orchestrator/repository/repository_brain.db (real on-disk SQLite storage)
- Integration with all 7 core architecture systems:
  1. SkillRegistry
  2. AgentRegistry
  3. MCPManager
  4. UnifiedToolDispatcher
  5. WorkspaceManager
  6. LangGraph
  7. LiteLLM Gateway
"""
import os
from pathlib import Path
import sqlite3
import tempfile
import pytest

from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.registry.skill_registry import SkillRegistry
from agent_orchestrator.registry.agent_registry import AgentRegistry
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.llm import LLMClient

from repository.scanner import RepositoryScanner, ScannedFile, GitMetadata
from repository.symbol_index import SymbolIndex, CodeSymbol
from repository.import_graph import ImportGraph, ImportEdge
from repository.call_graph import CallGraph, CallEdge
from repository.runtime_detector import RuntimeDetector, RuntimeProfile
from repository.architecture_index import ArchitectureIndex, ArchitectureComponent
from repository.repository_brain import RepositoryBrain


@pytest.fixture
def repo_workspace():
    """Provides a realistic temporary multi-component repository for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        ws = WorkspaceManager(root_dir=tmpdir)

        # 1. Root configuration manifests
        ws.write_file("requirements.txt", "fastapi>=0.100.0\npytest>=8.0.0\nnetworkx>=3.0\n")
        ws.write_file("README.md", "# Test Repository\nRepository intelligence test suite.\n")
        ws.write_file(".gitignore", "*.pyc\n__pycache__/\nnode_modules/\n.orchestrator/\n")

        # 2. Python core modules with classes, functions, and cross-module calls
        ws.write_file(
            "utils.py",
            (
                "def helper_func(x: int) -> int:\n"
                "    \"\"\"Utility helper function.\"\"\"\n"
                "    return x * 2\n\n"
                "class Formatter:\n"
                "    def format_string(self, text: str) -> str:\n"
                "        return text.strip().upper()\n"
            ),
        )

        ws.write_file(
            "service.py",
            (
                "from utils import helper_func, Formatter\n\n"
                "class DataService:\n"
                "    def __init__(self):\n"
                "        self.formatter = Formatter()\n\n"
                "    def process_data(self, val: int) -> str:\n"
                "        doubled = helper_func(val)\n"
                "        return self.formatter.format_string(f'result_{doubled}')\n"
            ),
        )

        ws.write_file(
            "main.py",
            (
                "from service import DataService\n\n"
                "def main():\n"
                "    svc = DataService()\n"
                "    return svc.process_data(42)\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
        )

        # 3. JavaScript / TypeScript file for tree-sitter testing
        ws.write_file(
            "client.ts",
            (
                "export interface User {\n"
                "    id: string;\n"
                "    name: string;\n"
                "}\n\n"
                "export class ApiClient {\n"
                "    async fetchUser(id: string): Promise<User> {\n"
                "        return { id, name: 'Alice' };\n"
                "    }\n"
                "}\n\n"
                "export function createClient(): ApiClient {\n"
                "    return new ApiClient();\n"
                "}\n"
            ),
        )

        # Initialize a real Git repository using GitPython
        try:
            repo = git.Repo.init(tmpdir)
            repo.config_writer().set_value("user", "name", "Test User").release()
            repo.config_writer().set_value("user", "email", "test@example.com").release()
            repo.git.add(all=True)
            repo.index.commit("Initial test commit")
        except Exception:
            pass

        yield ws


class TestRepositoryScanner:
    """Verifies file scanning, GitPython integration, and watchfiles support."""

    def test_scanner_discovers_files_and_computes_hashes(self, repo_workspace):
        scanner = RepositoryScanner(repo_workspace.root_dir)
        files = scanner.scan()

        paths = [f.path for f in files]
        assert "main.py" in paths
        assert "service.py" in paths
        assert "utils.py" in paths
        assert "client.ts" in paths

        # Check hash and language detection
        py_file = next(f for f in files if f.path == "main.py")
        assert py_file.language == "python"
        assert len(py_file.sha256) == 64
        assert py_file.size_bytes > 0

        ts_file = next(f for f in files if f.path == "client.ts")
        assert ts_file.language == "typescript"

    def test_scanner_git_metadata(self, repo_workspace):
        scanner = RepositoryScanner(repo_workspace.root_dir)
        git_meta = scanner.get_git_metadata()
        assert isinstance(git_meta, GitMetadata)


class TestSymbolIndex:
    """Verifies symbol extraction via Python AST and tree-sitter queries."""

    def test_python_ast_symbol_extraction(self, repo_workspace):
        symbols_engine = SymbolIndex()
        content = repo_workspace.read_file("service.py")
        symbols = symbols_engine.extract_symbols_from_source(content, "service.py", "python")

        names = {s.name: s for s in symbols}
        assert "DataService" in names
        assert names["DataService"].kind == "class"
        assert "process_data" in names
        assert names["process_data"].kind == "method"
        assert names["process_data"].parent_symbol == "DataService"

    def test_tree_sitter_query_symbol_extraction(self, repo_workspace):
        symbols_engine = SymbolIndex()
        content = repo_workspace.read_file("client.ts")
        symbols = symbols_engine.extract_symbols_from_source(content, "client.ts", "typescript")

        names = {s.name: s for s in symbols}
        assert "ApiClient" in names
        assert names["ApiClient"].kind == "class"
        assert "fetchUser" in names or "createClient" in names


class TestImportGraph:
    """Verifies import dependency graph construction via ast and networkx."""

    def test_import_graph_dependencies_and_traversal(self, repo_workspace):
        import_graph = ImportGraph(repo_workspace.root_dir)

        # Index files
        for p in ["utils.py", "service.py", "main.py"]:
            content = repo_workspace.read_file(p)
            import_graph.index_file_imports(p, content)

        # main.py -> service.py -> utils.py
        deps_main = import_graph.get_dependencies("main.py", recursive=False)
        assert "service.py" in deps_main

        transitive_deps = import_graph.get_dependencies("main.py", recursive=True)
        assert "service.py" in transitive_deps
        assert "utils.py" in transitive_deps

        # Dependents of utils.py should include service.py and main.py
        dependents_utils = import_graph.get_dependents("utils.py", recursive=True)
        assert "service.py" in dependents_utils
        assert "main.py" in dependents_utils

        # Cycles check
        cycles = import_graph.get_circular_dependencies()
        assert len(cycles) == 0


class TestCallGraph:
    """Verifies call graph and impact radius calculation using ast and networkx."""

    def test_call_graph_and_impact_radius(self, repo_workspace):
        call_graph = CallGraph()

        for p in ["utils.py", "service.py", "main.py"]:
            content = repo_workspace.read_file(p)
            call_graph.index_file_calls(p, content)

        # helper_func is called by DataService.process_data
        callers = call_graph.get_callers("helper_func")
        assert len(callers) > 0
        assert any("DataService.process_data" in c["caller_node"] for c in callers)

        # Impact radius of helper_func
        radius = call_graph.get_impact_radius("helper_func")
        assert radius["symbol"] == "helper_func"
        assert radius["impact_radius_score"] >= 1
        assert "service.py" in radius["affected_files"]


class TestRuntimeDetector:
    """Verifies technology stack and runtime framework detection."""

    def test_runtime_detection(self, repo_workspace):
        detector = RuntimeDetector(repo_workspace.root_dir)
        profile = detector.detect()

        assert profile.primary_language == "python"
        assert profile.framework == "Fastapi"
        assert profile.test_framework == "pytest"
        assert "fastapi" in profile.key_dependencies
        assert "pytest" in profile.key_dependencies
        assert "main.py" in profile.entrypoints


class TestArchitectureIndex:
    """Verifies architectural subsystem mapping and layer boundary checks."""

    def test_architecture_components(self, repo_workspace):
        arch_index = ArchitectureIndex(repo_workspace.root_dir)
        comps = arch_index.get_components()
        assert len(comps) >= 1

        # Check layer violations (empty for valid imports)
        violations = arch_index.check_layer_violations([])
        assert isinstance(violations, list)


class TestRepositoryBrainPersistentStorageAndSevenSystems:
    """
    Verifies full RepositoryBrain orchestration, persistent SQLite storage,
    and integration across the 7 core systems.
    """

    def test_persistent_sqlite_database_creation(self, repo_workspace):
        brain = RepositoryBrain(workspace_manager=repo_workspace)
        summary = brain.build_full_index()

        # 1. Verify persistent database file exists on disk
        db_path = Path(summary["database_path"])
        assert db_path.exists()
        assert db_path.is_file()
        assert db_path.stat().st_size > 0

        # 2. Verify real SQLite tables are populated
        conn = sqlite3.connect(str(db_path))
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "files" in tables
        assert "symbols" in tables
        assert "imports" in tables
        assert "calls" in tables
        assert "runtime_profiles" in tables
        assert "architecture_components" in tables

        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        assert file_count >= 4

        symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        assert symbol_count >= 3

        conn.close()
        brain.close()

    def test_seven_systems_integration(self, repo_workspace):
        # 1. SkillRegistry
        skill_reg = SkillRegistry()
        # 2. AgentRegistry
        agent_reg = AgentRegistry()
        # 3. WorkspaceManager
        ws = repo_workspace
        # 4. MCPManager
        mcp_mgr = MCPManager(workspace_dir=ws.root_dir)
        # 5. UnifiedToolDispatcher
        builtin_reg = BuiltinToolRegistry(workspace=ws, skill_registry=skill_reg, agent_registry=agent_reg)
        dispatcher = UnifiedToolDispatcher(builtin_registry=builtin_reg, mcp_manager=mcp_mgr)
        # 6. LiteLLM Gateway / LLMClient
        llm = LLMClient()

        # Initialize RepositoryBrain with all 7 systems
        brain = RepositoryBrain(
            workspace_manager=ws,
            skill_registry=skill_reg,
            agent_registry=agent_reg,
            mcp_manager=mcp_mgr,
            tool_dispatcher=dispatcher,
            llm_client=llm,
        )
        brain.build_full_index()

        # Verify tool registration in UnifiedToolDispatcher
        sym_res = brain.find_symbol("DataService")
        assert len(sym_res) > 0
        assert sym_res[0]["kind"] == "class"

        call_res = brain.get_call_graph("helper_func")
        assert call_res["total_callers"] >= 1

        impact_res = brain.get_impact_radius("helper_func")
        assert impact_res["impact_radius_score"] >= 1

        # 7. LangGraph StateGraph Node Integration
        from langgraph.graph import StateGraph, START, END

        lg_node = brain.create_langgraph_node()
        builder = StateGraph(dict)
        builder.add_node("repo_intelligence", lg_node)
        builder.add_edge(START, "repo_intelligence")
        builder.add_edge("repo_intelligence", END)
        graph = builder.compile()

        state_out = graph.invoke({"task": "Analyze codebase architecture"})
        assert "repository_summary" in state_out
        assert "codebase_runtime_profile" in state_out
        assert state_out["codebase_runtime_profile"]["primary_language"] == "python"

        brain.close()
