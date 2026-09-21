"""
Unit and integration tests for Issue #40: Retrieval Feedback Loop & Evidence Sufficiency Engine.
Verifies ContextSufficiencyOracle, RelevanceRanker 3-pass automated expansion,
request_more_evidence developer tool, search reformulation hints, and CoderAgent evidence gating.
"""
from pathlib import Path
import tempfile
import unittest

from agent_orchestrator.codebase.graph import CodebaseGraph
from agent_orchestrator.codebase.semantic_index import SemanticCodeIndex
from agent_orchestrator.codebase.cbm import CodebaseMemory
from agent_orchestrator.codebase.architecture import ArchitectureAnalyzer
from agent_orchestrator.codebase.cache import IncrementalCodeCache
from agent_orchestrator.context.sufficiency_oracle import ContextSufficiencyOracle, SufficiencyEvaluation
from agent_orchestrator.context.relevance_ranker import RelevanceRanker, ContextTier, RankedContextItem
from agent_orchestrator.state import TaskStatus, OrchestratorState
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.agents.coder import CoderAgent


class TestRetrievalFeedbackLoop(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_path = Path(self.temp_dir.name)

        # File A: model
        self.file_a = self.workspace_path / "model.py"
        self.file_a.write_text(
            'class UserAccount:\n'
            '    """User domain entity."""\n'
            '    def __init__(self, name: str):\n'
            '        self.name = name\n'
            '    def is_active(self) -> bool:\n'
            '        return True\n',
            encoding="utf-8"
        )

        # File B: service importing model
        self.file_b = self.workspace_path / "service.py"
        self.file_b.write_text(
            'from model import UserAccount\n'
            '\n'
            'class AccountService:\n'
            '    def verify_user(self, name: str) -> bool:\n'
            '        user = UserAccount(name)\n'
            '        return user.is_active()\n',
            encoding="utf-8"
        )

        # File C: independent utility
        self.file_c = self.workspace_path / "utils.py"
        self.file_c.write_text(
            'def format_output(text: str) -> str:\n'
            '    return text.strip().upper()\n',
            encoding="utf-8"
        )

        self.cache = IncrementalCodeCache(Path(":memory:"))
        self.code_graph = CodebaseGraph(self.workspace_path, cache=self.cache)
        self.semantic_index = SemanticCodeIndex(code_graph=self.code_graph)
        self.arch_analyzer = ArchitectureAnalyzer(self.workspace_path, code_graph=self.code_graph)
        self.cbm = CodebaseMemory(
            workspace_dir=self.workspace_path,
            code_graph=self.code_graph,
            semantic_index=self.semantic_index,
            arch_analyzer=self.arch_analyzer,
        )
        self.workspace = WorkspaceManager(self.workspace_path)

    def tearDown(self):
        if hasattr(self, "code_graph"):
            self.code_graph.close()
        if hasattr(self, "cache"):
            self.cache.close()
        self.temp_dir.cleanup()

    def test_sufficiency_oracle_entity_extraction(self):
        """Verifies candidate class, function, and file entities are extracted from task info."""
        task_info = {
            "objective": "Integrate PaymentGateway with CheckoutService",
            "description": "Call verify_user from service.py and charge via StripeAdapter in billing/stripe.py",
            "inputs": ["service.py"],
            "outputs": ["checkout.py"],
        }
        entities = ContextSufficiencyOracle.extract_task_entities(task_info)
        self.assertIn("PaymentGateway", entities)
        self.assertIn("CheckoutService", entities)
        self.assertIn("StripeAdapter", entities)
        self.assertIn("verify_user", entities)
        self.assertIn("service.py", entities)
        self.assertIn("billing/stripe.py", entities)

    def test_sufficiency_oracle_scoring(self):
        """Verifies confidence score is high when entities are present, and low when absent."""
        oracle = ContextSufficiencyOracle(min_confidence_threshold=0.70)

        task_info = {
            "objective": "Verify account status",
            "description": "Call verify_user on AccountService",
            "inputs": ["service.py"],
        }

        # 1. Sufficient context containing referenced entities
        sufficient_items = [
            RankedContextItem(
                filepath="service.py",
                tier=ContextTier.FOCAL,
                score=100.0,
                formatted_content="class AccountService:\n    def verify_user(self, name: str) -> bool:\n        return True\n",
            )
        ]
        eval_sufficient = oracle.evaluate(task_info, sufficient_items, self.code_graph)
        self.assertTrue(eval_sufficient.is_sufficient)
        self.assertGreaterEqual(eval_sufficient.confidence_score, 0.70)
        self.assertIn("AccountService", eval_sufficient.found_entities)
        self.assertIn("verify_user", eval_sufficient.found_entities)
        self.assertEqual(len(eval_sufficient.missing_entities), 0)

        # 2. Insufficient context missing the required entities
        insufficient_items = [
            RankedContextItem(
                filepath="utils.py",
                tier=ContextTier.INTERFACE,
                score=50.0,
                formatted_content="def format_output(text: str) -> str:\n    return text.upper()\n",
            )
        ]
        eval_insufficient = oracle.evaluate(task_info, insufficient_items, self.code_graph)
        self.assertFalse(eval_insufficient.is_sufficient)
        self.assertLess(eval_insufficient.confidence_score, 0.70)
        self.assertIn("AccountService", eval_insufficient.missing_entities)
        self.assertIn("verify_user", eval_insufficient.missing_entities)
        self.assertGreater(len(eval_insufficient.recommended_actions), 0)

    def test_relevance_ranker_automated_expansion(self):
        """Verifies RelevanceRanker automatically resolves missing entities via 3-pass expansion."""
        ranker = RelevanceRanker(
            code_graph=self.code_graph,
            workspace=self.workspace,
            semantic_index=self.semantic_index,
            cbm=self.cbm,
        )

        # Task mentions UserAccount and verify_user, but only lists service.py as focal input
        task_info = {
            "objective": "Implement verification handler",
            "description": "Uses AccountService and UserAccount to validate active status",
            "inputs": ["service.py"],
            "outputs": [],
        }

        # Rank context with feedback expansion enabled
        ranked_items = ranker.rank_context_for_task(
            task_info=task_info,
            expand_if_insufficient=True,
            min_confidence=0.70,
        )

        # Confirm model.py was pulled into interface tier to resolve UserAccount
        retrieved_paths = [item.filepath for item in ranked_items]
        self.assertIn("service.py", retrieved_paths)
        self.assertIn("model.py", retrieved_paths)

        # Sufficiency evaluation on the ranker should now be sufficient
        last_eval = ranker.last_sufficiency_evaluation
        self.assertIsNotNone(last_eval)
        self.assertTrue(last_eval.is_sufficient)
        self.assertIn("UserAccount", last_eval.found_entities)

    def test_request_more_evidence_tool(self):
        """Verifies request_more_evidence tool resolves symbols, reads files, and provides clear guidance."""
        registry = BuiltinToolRegistry(workspace=self.workspace)
        try:
            # 1. Request existing symbol and existing file
            res = registry._request_more_evidence(
                reason="Need UserAccount contract and utility functions",
                missing_symbols=["UserAccount"],
                missing_files=["utils.py"],
            )
            self.assertTrue(res["success"])
            self.assertEqual(len(res["resolved_symbols"]), 1)
            self.assertEqual(res["resolved_symbols"][0]["name"], "UserAccount")
            self.assertIn("utils.py", res["file_contents"])
            self.assertEqual(len(res["unresolved_symbols"]), 0)

            # 2. Request non-existent symbol
            res_missing = registry._request_more_evidence(
                reason="Need third party gateway",
                missing_symbols=["NonExistentGateway"],
            )
            self.assertTrue(res_missing["success"])
            self.assertIn("NonExistentGateway", res_missing["unresolved_symbols"])
            self.assertIn("do not currently exist in the workspace", res_missing["guidance"])
        finally:
            registry.close()

    def test_search_zero_match_reformulation_hints(self):
        """Verifies search tools provide helpful reformulation advice when zero matches are found."""
        registry = BuiltinToolRegistry(workspace=self.workspace)
        try:
            sym_res = registry._find_symbol("ImaginaryService")
            self.assertIn("reformulation_hint", sym_res)
            self.assertIn("No symbol named", sym_res["reformulation_hint"])

            sem_res = registry._semantic_code_search("unindexed_cryptic_query_xyz")
            self.assertIn("reformulation_hint", sem_res)
            self.assertIn("No code matches for", sem_res["reformulation_hint"])

            cbm_res = registry._query_codebase_graph("quantum_entanglement_flux")
            self.assertIn("reformulation_hint", cbm_res)
            self.assertIn("No connected sub-graph found", cbm_res["reformulation_hint"])
        finally:
            registry.close()

    def test_coder_agent_evidence_warning_injection(self):
        """Verifies CoderAgent injects an evidence warning section when context is insufficient."""
        registry = BuiltinToolRegistry(workspace=self.workspace)
        try:
            coder = CoderAgent(tool_registry=registry, workspace=self.workspace)
            state = OrchestratorState(user_request="Build crypto payment processor")

            task_info = {
                "objective": "Build crypto processor",
                "description": "Interact with BitcoinNode and EthereumBridge to sign transactions",
                "inputs": [],
                "outputs": ["crypto/processor.py"],
            }

            # Mock react_loop to inspect prompt
            captured_prompt = None
            def mock_react_run(**kwargs):
                nonlocal captured_prompt
                captured_prompt = kwargs.get("user_prompt", "")
                return {"success": True, "deliverables": []}

            coder.react_loop.run = mock_react_run
            coder.execute(state=state, task_info=task_info)

            self.assertIsNotNone(captured_prompt)
            self.assertIn("Evidence Sufficiency & Context Assessment", captured_prompt)
            self.assertIn("Context Sufficiency Assessment", captured_prompt)
            self.assertIn("BitcoinNode", captured_prompt)
            self.assertIn("EthereumBridge", captured_prompt)
            self.assertIn("request_more_evidence", captured_prompt)
        finally:
            registry.close()

    def test_task_status_need_more_evidence(self):
        """Verifies TaskStatus.NEED_MORE_EVIDENCE is available in state."""
        self.assertEqual(TaskStatus.NEED_MORE_EVIDENCE.value, "NEED_MORE_EVIDENCE")
        state = OrchestratorState(user_request="Refactor database layer")
        state.status = TaskStatus.NEED_MORE_EVIDENCE
        self.assertEqual(state.status, TaskStatus.NEED_MORE_EVIDENCE)


if __name__ == "__main__":
    unittest.main()
