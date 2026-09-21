"""
Unit and Integration tests for Codebase Memory (CBM) and Graph Retrieval.
Verifies knowledge graph construction, GraphRAG queries, symbol neighborhood traversal,
architectural slicing, RelevanceRanker sub-graph integration, and tool permission policies.
"""
import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.codebase.cbm import CodebaseMemory, CBMNode, CBMEdge
from agent_orchestrator.codebase.graph import CodebaseGraph
from agent_orchestrator.codebase.semantic_index import SemanticCodeIndex
from agent_orchestrator.codebase.architecture import ArchitectureAnalyzer
from agent_orchestrator.context.relevance_ranker import RelevanceRanker
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.runtime.permission_policy import ToolPermissionPolicyEngine, ToolOperationType


class TestCodebaseMemory(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_path = Path(self.temp_dir.name)

        # Create a sample project structure
        # Layer 1: Core / Domain
        core_dir = self.workspace_path / "core"
        core_dir.mkdir(parents=True, exist_ok=True)
        (core_dir / "user_model.py").write_text(
            'class User:\n'
            '    """User domain model representing an authenticated principal."""\n'
            '    def __init__(self, username: str, email: str):\n'
            '        self.username = username\n'
            '        self.email = email\n'
            '\n'
            '    def validate(self) -> bool:\n'
            '        """Validates user attributes."""\n'
            '        return bool(self.username and "@" in self.email)\n',
            encoding="utf-8"
        )

        # Layer 2: Service / Business Logic
        services_dir = self.workspace_path / "services"
        services_dir.mkdir(parents=True, exist_ok=True)
        (services_dir / "auth_service.py").write_text(
            'from core.user_model import User\n'
            '\n'
            'class AuthService:\n'
            '    """Authentication service managing login tokens and user sessions."""\n'
            '    def login(self, username: str, email: str) -> bool:\n'
            '        """Logs in a user and validates credentials."""\n'
            '        u = User(username, email)\n'
            '        return u.validate()\n'
            '\n'
            '    def logout(self, user: User) -> None:\n'
            '        """Logs out a user."""\n'
            '        pass\n',
            encoding="utf-8"
        )

        # Layer 3: API / Interface
        api_dir = self.workspace_path / "api"
        api_dir.mkdir(parents=True, exist_ok=True)
        (api_dir / "routes.py").write_text(
            'from services.auth_service import AuthService\n'
            '\n'
            'def handle_login(req):\n'
            '    """Route handler for user login endpoint."""\n'
            '    svc = AuthService()\n'
            '    return svc.login(req["username"], req["email"])\n',
            encoding="utf-8"
        )

        # Initialize CBM
        self.code_graph = CodebaseGraph(workspace_dir=self.workspace_path)
        self.semantic_index = SemanticCodeIndex(code_graph=self.code_graph)
        self.arch_analyzer = ArchitectureAnalyzer(workspace_dir=self.workspace_path, code_graph=self.code_graph)
        self.cbm = CodebaseMemory(
            workspace_dir=self.workspace_path,
            code_graph=self.code_graph,
            semantic_index=self.semantic_index,
            arch_analyzer=self.arch_analyzer,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cbm_graph_construction(self):
        """Verifies knowledge graph contains nodes and edges connecting symbols and files."""
        self.assertGreater(len(self.cbm.nodes), 0)
        self.assertGreater(len(self.cbm.edges), 0)

        # Verify User and AuthService symbols exist as nodes
        labels = {n.label for n in self.cbm.nodes.values()}
        self.assertIn("User", labels)
        self.assertIn("AuthService", labels)
        self.assertIn("login", labels)
        self.assertIn("validate", labels)

        # Verify communities are assigned
        for nid, node in self.cbm.nodes.items():
            self.assertIn(nid, self.cbm.communities)

    def test_retrieve_task_subgraph(self):
        """Verifies sub-graph retrieval for a task extracts focal files and interface contracts."""
        task_info = {
            "task_id": "T-01",
            "title": "Add password hashing to AuthService login",
            "objective": "Update AuthService to check hashed passwords and validate user",
            "files": ["services/auth_service.py"],
        }
        subgraph = self.cbm.retrieve_task_subgraph(task_info, max_tokens=2000)
        self.assertTrue(subgraph["success"])
        self.assertIn("services/auth_service.py", subgraph["focal_files"])
        self.assertGreater(subgraph["interface_nodes_count"], 0)
        self.assertIn("Related Interface Contracts", subgraph["formatted_context"])
        self.assertIn("AuthService", subgraph["formatted_context"])
        self.assertLessEqual(subgraph["estimated_tokens"], 2000)

    def test_query_graph_bfs_and_dfs(self):
        """Verifies GraphRAG query finds seed nodes and traverses graph."""
        bfs_res = self.cbm.query_graph("login authentication", max_tokens=1000, traversal="bfs")
        self.assertTrue(bfs_res["success"])
        self.assertGreater(bfs_res["total_nodes"], 0)
        self.assertIn("login", bfs_res["formatted_output"].lower())

        dfs_res = self.cbm.query_graph("login authentication", max_tokens=1000, traversal="dfs")
        self.assertTrue(dfs_res["success"])
        self.assertGreater(dfs_res["total_nodes"], 0)

    def test_get_symbol_neighbors(self):
        """Verifies 1-2 hop caller and callee inspection."""
        neighbors = self.cbm.get_symbol_neighbors("login")
        self.assertTrue(neighbors["success"])
        self.assertTrue(neighbors["found"])
        self.assertEqual(neighbors["symbol"], "login")
        self.assertIn("services/auth_service.py", neighbors["filepath"])

    def test_get_architecture_slice(self):
        """Verifies architectural slice returns layer and framework info for a file."""
        slice_info = self.cbm.get_architecture_slice("core/user_model.py")
        self.assertTrue(slice_info["success"])
        self.assertEqual(slice_info["filepath"], "core/user_model.py")
        self.assertIn("layer", slice_info)

    def test_relevance_ranker_with_cbm(self):
        """Verifies RelevanceRanker injects CBM sub-graph slice into context tiers."""
        ws_mgr = WorkspaceManager(root_dir=self.workspace_path)
        ranker = RelevanceRanker(
            workspace=ws_mgr,
            code_graph=self.code_graph,
            semantic_index=self.semantic_index,
            cbm=self.cbm,
        )
        task_info = {
            "task_id": "T-01",
            "title": "Enhance User model validation",
            "objective": "Update validate method in User",
            "files": ["core/user_model.py"],
        }
        items = ranker.rank_context_for_task(task_info)
        self.assertGreater(len(items), 0)
        all_content = "\n".join([item.formatted_content for item in items])
        self.assertIn("User", all_content)

    def test_builtin_tools_registration(self):
        """Verifies CBM tools are registered in BuiltinToolRegistry and available to agents."""
        ws_mgr = WorkspaceManager(root_dir=self.workspace_path)
        registry = BuiltinToolRegistry(workspace=ws_mgr)

        tool_names = [t.name for t in registry.get_all_tools()]
        self.assertIn("query_codebase_graph", tool_names)
        self.assertIn("get_symbol_neighbors", tool_names)
        self.assertIn("get_architecture_slice", tool_names)

        # Check CODER tools
        coder_tool_names = [t.name for t in registry.get_tools_for_agent("CODER")]
        self.assertIn("query_codebase_graph", coder_tool_names)
        self.assertIn("get_symbol_neighbors", coder_tool_names)
        self.assertIn("get_architecture_slice", coder_tool_names)

        # Check PLANNER tools
        planner_tool_names = [t.name for t in registry.get_tools_for_agent("PLANNER")]
        self.assertIn("query_codebase_graph", planner_tool_names)
        self.assertIn("get_symbol_neighbors", planner_tool_names)
        self.assertIn("get_architecture_slice", planner_tool_names)

        # Test tool invocations
        q_res = registry._query_codebase_graph(query="User domain model", max_tokens=1000)
        self.assertTrue(q_res["success"])

        n_res = registry._get_symbol_neighbors(symbol_name="User")
        self.assertTrue(n_res["success"])

        a_res = registry._get_architecture_slice(target_file="services/auth_service.py")
        self.assertTrue(a_res["success"])

    def test_permission_policy_allows_cbm_tools(self):
        """Verifies ToolPermissionPolicyEngine classifies CBM tools as READ and permits them for roles."""
        policy_engine = ToolPermissionPolicyEngine()
        for role in ["CODER", "PLANNER", "SPECIFICATION", "ARCHITECTURE", "TESTER", "REVIEWER"]:
            for tool_name in ["query_codebase_graph", "get_symbol_neighbors", "get_architecture_slice"]:
                eval_res = policy_engine.evaluate_tool_invocation(
                    agent_role=role,
                    tool_name=tool_name,
                    args={},
                )
                self.assertTrue(
                    eval_res.allowed,
                    f"Tool '{tool_name}' should be allowed for role '{role}': {eval_res.reason}"
                )
                self.assertEqual(eval_res.operation_type, ToolOperationType.READ)


if __name__ == "__main__":
    unittest.main()
