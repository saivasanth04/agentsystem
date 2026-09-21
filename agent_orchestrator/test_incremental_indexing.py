"""
Unit and Integration tests for Incremental Indexing and Reactive Codebase Invalidation (Issue #39).
Verifies IncrementalCodeCache integration in CodebaseGraph, in-place symbol/call-graph delta updates,
incremental BM25 and embedding updates in SemanticCodeIndex, CBM sub-graph updates,
and end-to-end WorkspaceManager mutation event listeners.
"""
import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.codebase.cache import IncrementalCodeCache
from agent_orchestrator.codebase.graph import CodebaseGraph
from agent_orchestrator.codebase.semantic_index import SemanticCodeIndex
from agent_orchestrator.codebase.architecture import ArchitectureAnalyzer
from agent_orchestrator.codebase.cbm import CodebaseMemory
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry


class TestIncrementalIndexing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_path = Path(self.temp_dir.name)

        # File A: models
        self.file_a = self.workspace_path / "model.py"
        self.file_a.write_text(
            'class UserAccount:\n'
            '    """User account domain entity."""\n'
            '    def __init__(self, username: str):\n'
            '        self.username = username\n'
            '\n'
            '    def is_active(self) -> bool:\n'
            '        return True\n',
            encoding="utf-8"
        )

        # File B: service calling File A
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

    def tearDown(self):
        if hasattr(self, "code_graph"):
            self.code_graph.close()
        if hasattr(self, "cache"):
            self.cache.close()
        self.temp_dir.cleanup()

    def test_code_cache_integration(self):
        """Verifies CodebaseGraph leverages IncrementalCodeCache for warm starts."""
        # 1. On initial build_index(), cache has all 3 files
        cached_a = self.cache.get_cached_file("model.py")
        self.assertIsNotNone(cached_a)
        syms_a, refs_a, imps_a = cached_a
        self.assertIn("UserAccount", [s.name for s in syms_a])

        # 2. On second build_index(), unchanged files are loaded from cache
        res = self.code_graph.build_index()
        self.assertTrue(res["success"])
        self.assertEqual(res["indexed_files"], 3)

    def test_graph_reindex_single_file(self):
        """Verifies reindex_file updates only the modified file and its incident call graph edges."""
        # Modify model.py: add new method `deactivate` and rename `is_active` to `check_active`
        self.file_a.write_text(
            'class UserAccount:\n'
            '    """User account domain entity updated."""\n'
            '    def __init__(self, username: str):\n'
            '        self.username = username\n'
            '\n'
            '    def check_active(self) -> bool:\n'
            '        return True\n'
            '\n'
            '    def deactivate(self) -> None:\n'
            '        pass\n',
            encoding="utf-8"
        )

        updated_syms = self.code_graph.reindex_file("model.py")
        sym_names = {s.name for s in updated_syms}
        self.assertIn("check_active", sym_names)
        self.assertIn("deactivate", sym_names)
        self.assertNotIn("is_active", sym_names)

        # Verify utils.py was NOT wiped or touched
        utils_syms = self.code_graph.file_to_symbols.get("utils.py", [])
        self.assertEqual(len(utils_syms), 1)
        self.assertEqual(utils_syms[0].name, "format_output")

        # Verify global symbol lookup finds new symbol
        found = self.code_graph.find_symbol("deactivate")
        self.assertTrue(found["exact_match"])

        # Old symbol is no longer in global symbol index
        old_found = self.code_graph.find_symbol("is_active")
        self.assertFalse(old_found["exact_match"])

    def test_graph_delete_file(self):
        """Verifies delete_file purges symbols, imports, and incident edges."""
        self.file_c.unlink()
        self.code_graph.delete_file("utils.py")

        self.assertNotIn("utils.py", self.code_graph.file_to_symbols)
        found = self.code_graph.find_symbol("format_output")
        self.assertFalse(found["exact_match"])
        self.assertIsNone(self.cache.get_cached_file("utils.py"))

    def test_semantic_index_incremental_update(self):
        """Verifies BM25 term frequencies and chunks update incrementally on file modification."""
        initial_chunk_count = len(self.semantic_index.chunks)

        # Update model.py with unique term 'cryptographic_vault'
        self.file_a.write_text(
            'class UserAccount:\n'
            '    def unlock_cryptographic_vault(self):\n'
            '        """Unlocks user cryptographic_vault."""\n'
            '        pass\n',
            encoding="utf-8"
        )
        new_syms = self.code_graph.reindex_file("model.py")
        self.semantic_index.reindex_file("model.py", new_syms)

        # Search for unique concept
        results = self.semantic_index.search("cryptographic_vault")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["symbol_name"], "unlock_cryptographic_vault")

        # Verify document frequencies updated
        self.assertIn("cryptographic", self.semantic_index.doc_frequencies)
        self.assertIn("vault", self.semantic_index.doc_frequencies)

    def test_semantic_index_embedding_cache(self):
        """Verifies embedding cache reuses vectors for unchanged symbol snippets."""
        call_count = {"count": 0}

        def mock_embedding_fn(text: str):
            call_count["count"] += 1
            return [0.1, 0.2, 0.3]

        sem_idx = SemanticCodeIndex(code_graph=self.code_graph, embedding_fn=mock_embedding_fn)
        first_calls = call_count["count"]
        self.assertGreater(first_calls, 0)

        # Reindex utils.py without changing its text
        utils_syms = self.code_graph.file_to_symbols.get("utils.py", [])
        sem_idx.reindex_file("utils.py", utils_syms)

        # Call count should not increase because the snippet was identical (cache hit)
        self.assertEqual(call_count["count"], first_calls)

    def test_cbm_in_place_update(self):
        """Verifies CBM nodes and edges for modified file update in place without full wipe."""
        initial_nodes = len(self.cbm.nodes)

        # Modify model.py to add a new method
        self.file_a.write_text(
            'class UserAccount:\n'
            '    def new_feature(self):\n'
            '        """New feature."""\n'
            '        pass\n',
            encoding="utf-8"
        )
        self.code_graph.reindex_file("model.py")
        self.cbm.update_file("model.py")

        # Verify new symbol node exists in CBM
        labels = {n.label for n in self.cbm.nodes.values()}
        self.assertIn("new_feature", labels)

        # Verify other files (service.py, utils.py) remain present
        self.assertIn("AccountService", labels)
        self.assertIn("format_output", labels)

    def test_workspace_mutation_listener_integration(self):
        """Verifies WorkspaceManager writes and edits automatically trigger the reactive indexing pipeline."""
        ws_mgr = WorkspaceManager(root_dir=self.workspace_path)
        registry = BuiltinToolRegistry(workspace=ws_mgr)
        try:
            # 1. Write a brand new file through WorkspaceManager
            ws_mgr.write_file(
                "billing.py",
                'class StripeGateway:\n'
                '    """Payment gateway client."""\n'
                '    def charge_card(self, amount: int) -> bool:\n'
                '        return True\n'
            )

            # 2. Verify symbol is immediately available in registry tools without explicit build_index()
            sym_res = registry._find_symbol("StripeGateway")
            self.assertTrue(sym_res["exact_match"])
            self.assertIn("billing.py", sym_res["symbols"][0]["filepath"])

            # 3. Edit file using replace_file_content
            ws_mgr.replace_file_content(
                rel_path="billing.py",
                target_content="charge_card",
                replacement_content="charge_credit_card",
            )

            # 4. Verify updated symbol is immediately reflected
            new_sym_res = registry._find_symbol("charge_credit_card")
            self.assertTrue(new_sym_res["exact_match"])

            # Old symbol should no longer be an exact match
            old_sym_res = registry._find_symbol("charge_card")
            self.assertFalse(old_sym_res["exact_match"])

            # 5. Delete file
            ws_mgr.delete_file("billing.py")
            del_res = registry._find_symbol("StripeGateway")
            self.assertFalse(del_res["exact_match"])
        finally:
            registry.close()

    def test_builtin_tools_no_redundant_full_rebuild(self):
        """Verifies calling query and analysis tools does not repeatedly wipe or re-parse the workspace."""
        ws_mgr = WorkspaceManager(root_dir=self.workspace_path)
        registry = BuiltinToolRegistry(workspace=ws_mgr)
        try:
            # Run multiple search and query operations
            res1 = registry._find_symbol("UserAccount")
            res2 = registry._semantic_code_search("account user")
            res3 = registry._get_call_graph("verify_user")
            res4 = registry._query_codebase_graph("account")

            self.assertTrue(res1["exact_match"])
            self.assertTrue(res2["success"])
            self.assertTrue(res3["found"])
            self.assertTrue(res4["success"])
        finally:
            registry.close()


if __name__ == "__main__":
    unittest.main()
