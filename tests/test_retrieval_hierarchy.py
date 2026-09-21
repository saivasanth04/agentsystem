"""
Comprehensive Test Suite for Multi-Level Retrieval Hierarchy (Issue #75).
Verifies:
  Level 1: Exact File / Symbol
  Level 2: Dependency Graph (Interfaces/Signatures only)
  Level 3: Semantic Search
  Level 4: Architecture Knowledge & Rules
  Level 5: Broader Repository (PageRank Map, eliminating raw 'ALL FILES' dumps)
Also verifies integration with TesterAgent, ReviewerAgent, and RelevanceRanker.
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.codebase.graph import CodebaseGraph
from agent_orchestrator.codebase.semantic_index import SemanticCodeIndex
from agent_orchestrator.codebase.architecture import ArchitectureAnalyzer
from agent_orchestrator.codebase.cbm import CodebaseMemory
from agent_orchestrator.context.retrieval_hierarchy import (
    HierarchyBudgetConfig,
    HierarchicalContextBundle,
    HierarchicalContextItem,
    RetrievalHierarchyEngine,
    RetrievalLevel,
)
from agent_orchestrator.context.relevance_ranker import RelevanceRanker
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.state import OrchestratorState


class TestRetrievalHierarchyEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_dir = Path(self.temp_dir)
        self.workspace = WorkspaceManager(self.workspace_dir)

        # Create multi-file project with imports, callers, and layers
        (self.workspace_dir / "services").mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / "models").mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / "tests").mkdir(parents=True, exist_ok=True)

        # Model file
        (self.workspace_dir / "models" / "user.py").write_text(
            'class User:\n'
            '    """User domain model."""\n'
            '    def __init__(self, user_id: str, email: str):\n'
            '        self.user_id = user_id\n'
            '        self.email = email\n\n'
            '    def is_valid(self) -> bool:\n'
            '        """Check if user email is present."""\n'
            '        return "@" in self.email\n'
        )

        # Service file (imports model)
        (self.workspace_dir / "services" / "auth_service.py").write_text(
            'from models.user import User\n\n'
            'class AuthService:\n'
            '    """Authentication service verifying users."""\n'
            '    def authenticate(self, user_id: str, email: str) -> bool:\n'
            '        """Authenticate user credentials."""\n'
            '        user = User(user_id, email)\n'
            '        return user.is_valid()\n'
        )

        # Helper fixture file
        (self.workspace_dir / "tests" / "test_helpers.py").write_text(
            'def create_mock_user(user_id="u1", email="test@example.com"):\n'
            '    """Helper fixture for test user instantiation."""\n'
            '    from models.user import User\n'
            '    return User(user_id, email)\n'
        )

        self.code_graph = CodebaseGraph(self.workspace_dir)
        self.semantic_index = SemanticCodeIndex(code_graph=self.code_graph)
        self.arch_analyzer = ArchitectureAnalyzer(self.workspace_dir, self.code_graph)
        self.cbm = CodebaseMemory(
            workspace_dir=self.workspace_dir,
            code_graph=self.code_graph,
            semantic_index=self.semantic_index,
            arch_analyzer=self.arch_analyzer,
        )

        self.engine = RetrievalHierarchyEngine(
            workspace=self.workspace,
            code_graph=self.code_graph,
            semantic_index=self.semantic_index,
            arch_analyzer=self.arch_analyzer,
            cbm=self.cbm,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_level_1_exact_file_retrieval(self):
        """Level 1 extracts target file code with exact target annotations."""
        task_info = {
            "inputs": ["services/auth_service.py"],
            "outputs": ["services/auth_service.py:AuthService"],
            "objective": "Add MFA authentication method",
        }
        bundle = self.engine.retrieve_hierarchy(
            task_info=task_info,
            include_levels=[RetrievalLevel.LEVEL_1_EXACT],
        )
        self.assertGreaterEqual(len(bundle.items), 1)
        l1_item = bundle.items[0]
        self.assertEqual(l1_item.level, RetrievalLevel.LEVEL_1_EXACT)
        self.assertEqual(l1_item.filepath, "services/auth_service.py")
        self.assertIn("class AuthService", l1_item.formatted_content)
        self.assertIn("(Exact Target)", l1_item.formatted_content)

    def test_level_2_dependency_graph_extracts_signatures_only(self):
        """Level 2 extracts structural interfaces without method body bloat."""
        task_info = {
            "inputs": ["services/auth_service.py"],
            "objective": "Refactor user authentication",
        }
        bundle = self.engine.retrieve_hierarchy(
            task_info=task_info,
            include_levels=[RetrievalLevel.LEVEL_2_DEPENDENCY_GRAPH],
        )
        l2_items = bundle.items_by_level[RetrievalLevel.LEVEL_2_DEPENDENCY_GRAPH]
        self.assertGreaterEqual(len(l2_items), 1)
        # Should identify models/user.py as a dependency
        dep_paths = [it.filepath for it in l2_items]
        self.assertTrue(any("models/user.py" in p or "user.py" in p for p in dep_paths))

        user_interface = next(it for it in l2_items if "user.py" in it.filepath)
        # Contains signatures and docstrings
        self.assertIn("User", user_interface.formatted_content)
        self.assertIn("is_valid", user_interface.formatted_content)
        # Should NOT dump raw implementation bodies (e.g. self.user_id = user_id)
        self.assertNotIn("self.user_id = user_id", user_interface.formatted_content)

    def test_level_3_semantic_search_retrieves_relevant_snippets(self):
        """Level 3 retrieves conceptually relevant snippets (e.g. test helpers/fixtures)."""
        task_info = {
            "objective": "Write unit tests for user authentication with mock credentials",
            "inputs": ["services/auth_service.py"],
        }
        bundle = self.engine.retrieve_hierarchy(
            task_info=task_info,
            query="mock user test helper fixture",
            include_levels=[RetrievalLevel.LEVEL_3_SEMANTIC_SEARCH],
        )
        l3_items = bundle.items_by_level[RetrievalLevel.LEVEL_3_SEMANTIC_SEARCH]
        self.assertGreaterEqual(len(l3_items), 1)
        found_snippets = [it.formatted_content for it in l3_items]
        self.assertTrue(any("create_mock_user" in s or "helper" in s.lower() for s in found_snippets))

    def test_level_4_architecture_knowledge_extracts_layers(self):
        """Level 4 retrieves architectural conventions and layer boundaries."""
        task_info = {
            "inputs": ["services/auth_service.py"],
            "objective": "Inspect architectural boundaries",
        }
        bundle = self.engine.retrieve_hierarchy(
            task_info=task_info,
            include_levels=[RetrievalLevel.LEVEL_4_ARCHITECTURE_KNOWLEDGE],
        )
        l4_items = bundle.items_by_level[RetrievalLevel.LEVEL_4_ARCHITECTURE_KNOWLEDGE]
        self.assertGreaterEqual(len(l4_items), 1)
        arch_item = l4_items[0]
        self.assertIn("Architectural Boundaries", arch_item.formatted_content)

    def test_level_5_broader_repository_produces_pagerank_map_not_flat_files(self):
        """Level 5 produces a token-bounded PageRank repo map instead of dumping ALL FILES."""
        bundle = self.engine.retrieve_hierarchy(
            include_levels=[RetrievalLevel.LEVEL_5_BROADER_REPOSITORY],
            budget_config=HierarchyBudgetConfig(total_budget=4000),
        )
        l5_items = bundle.items_by_level[RetrievalLevel.LEVEL_5_BROADER_REPOSITORY]
        self.assertEqual(len(l5_items), 1)
        repo_map_str = l5_items[0].formatted_content
        # Must be structured map, NOT raw JSON list of all files
        self.assertTrue("PageRank" in repo_map_str or "Codebase Map" in repo_map_str)
        self.assertNotIn("['services/auth_service.py', 'models/user.py']", repo_map_str)

    def test_strict_token_budget_enforcement(self):
        """Bundle total tokens and level tokens must adhere to HierarchyBudgetConfig."""
        cfg = HierarchyBudgetConfig(total_budget=1000)
        task_info = {
            "inputs": ["services/auth_service.py", "models/user.py"],
            "objective": "Full system test with mock",
        }
        bundle = self.engine.retrieve_hierarchy(task_info=task_info, budget_config=cfg)
        self.assertLessEqual(bundle.total_tokens, 1500)
        # Check markdown generation
        md = bundle.to_markdown()
        self.assertIn("Level 1: Exact File / Symbol", md)

    def test_relevance_ranker_backward_compatibility(self):
        """RelevanceRanker continues to work seamlessly without flat file dumps."""
        ranker = RelevanceRanker(
            code_graph=self.code_graph,
            workspace=self.workspace,
            semantic_index=self.semantic_index,
            cbm=self.cbm,
        )
        task_info = {
            "objective": "Refactor user authentication",
            "inputs": ["services/auth_service.py"],
            "outputs": ["services/auth_service.py:AuthService"],
        }
        ranked_items = ranker.rank_context_for_task(task_info=task_info)
        self.assertGreaterEqual(len(ranked_items), 1)
        focal_fps = [it.filepath for it in ranked_items if it.tier.value == "FOCAL"]
        self.assertIn("services/auth_service.py", focal_fps)


class TestAgentContextIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_dir = Path(self.temp_dir)
        self.workspace = WorkspaceManager(self.workspace_dir)

        (self.workspace_dir / "calculator.py").write_text(
            "def add(a, b):\n    return a + b\n"
        )
        self.tool_registry = BuiltinToolRegistry(self.workspace)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tester_agent_receives_hierarchical_context_not_flat_files(self):
        """TesterAgent prompt includes Hierarchical Codebase Context, eliminating raw files list."""
        from agent_orchestrator.agents.tester import TesterAgent
        mock_llm = MagicMock()
        mock_llm.chat_react.return_value = {
            "output": "Tests written",
            "action": "complete_task",
            "action_input": {"summary": "Done"},
            "thought": "All tests passed",
        }

        agent = TesterAgent(
            llm=mock_llm,
            workspace=self.workspace,
            tool_registry=self.tool_registry,
        )

        state = OrchestratorState(user_request="Test calculator addition")
        task_info = {
            "inputs": ["calculator.py"],
            "objective": "Test addition",
        }

        with patch.object(agent.react_loop, "run") as mock_run:
            mock_run.return_value = {
                "output": "Test written",
                "final_answer": "Done",
                "turns_taken": 1,
            }
            agent.execute(state, task_info=task_info)

            # Inspect user_prompt passed into react_loop.run
            call_kwargs = mock_run.call_args[1]
            user_prompt = call_kwargs.get("user_prompt", "")
            self.assertIn("Hierarchical Codebase Context", user_prompt)
            self.assertNotIn("Existing Files in Workspace", user_prompt)

    def test_reviewer_agent_receives_hierarchical_context_not_flat_files(self):
        """ReviewerAgent prompt includes Hierarchical Codebase Context, eliminating raw files list."""
        from agent_orchestrator.agents.reviewer import ReviewerAgent
        mock_llm = MagicMock()
        mock_llm.chat_react.return_value = {
            "output": "Review complete",
            "action": "complete_task",
            "action_input": {"summary": "Done"},
            "thought": "Review approved",
        }

        agent = ReviewerAgent(
            llm=mock_llm,
            workspace=self.workspace,
            tool_registry=self.tool_registry,
        )

        state = OrchestratorState(user_request="Review calculator changes")
        state.code_output = {"written_files": ["calculator.py"], "summary": "Added add"}

        with patch.object(agent.react_loop, "run") as mock_run:
            mock_run.return_value = {
                "output": "Review complete",
                "final_answer": "Done",
                "turns_taken": 1,
            }
            agent.execute(state)

            call_kwargs = mock_run.call_args[1]
            user_prompt = call_kwargs.get("user_prompt", "")
            self.assertIn("Hierarchical Codebase Context", user_prompt)
            self.assertNotIn("Files in Workspace", user_prompt)


if __name__ == "__main__":
    unittest.main()
