"""
Unit and integration tests for Context Management, Hierarchical Scoping, and Memory (Issue #36).
Tests ContextBudget, ContextAssembler, RelevanceRanker, WorkingMemory, and LongTermMemory.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.context.budget_allocator import ContextBudget, ContextSection, ContextAssembler
from agent_orchestrator.context.relevance_ranker import RelevanceRanker, ContextTier
from agent_orchestrator.memory.working_memory import WorkingMemory
from agent_orchestrator.memory.long_term_memory import LongTermMemory
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.code_graph import CodeGraphEngine
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry


class TestContextBudgetAllocator(unittest.TestCase):
    def test_default_budget(self):
        budget = ContextBudget.default()
        self.assertEqual(budget.total_budget, 32000)
        self.assertEqual(budget.focal_files, 8000)
        self.assertEqual(budget.interface_signatures, 3500)

    def test_priority_based_trimming(self):
        budget = ContextBudget(total_budget=500)  # very tight budget (~2000 chars)
        assembler = ContextAssembler(budget)

        sec_essential = ContextSection(
            name="system",
            title="System Instructions",
            content="Critical instructions that must be kept.",
            priority=1,
            max_tokens=100,
            is_essential=True,
        )
        sec_high = ContextSection(
            name="task",
            title="Active Task",
            content="Implement feature X in module Y.",
            priority=1,
            max_tokens=200,
            is_essential=True,
        )
        sec_low = ContextSection(
            name="history",
            title="Historical Background",
            content="A" * 3000,  # ~750 tokens, should be trimmed or omitted
            priority=5,
            max_tokens=1000,
            is_essential=False,
        )

        assembled = assembler.assemble([sec_essential, sec_high, sec_low])

        # Essential sections must be present
        self.assertIn("Critical instructions", assembled)
        self.assertIn("Implement feature X", assembled)
        # Low-priority section should be trimmed or marked omitted
        self.assertTrue("omitted to respect token budget" in assembled or len(assembled) <= 2500)

    def test_deduplication(self):
        assembler = ContextAssembler(ContextBudget.default())
        duplicate_text = "Exact duplicate specification text block."

        sec1 = ContextSection(name="spec1", title="Spec 1", content=duplicate_text, priority=2, max_tokens=500)
        sec2 = ContextSection(name="spec2", title="Spec 2", content=duplicate_text, priority=3, max_tokens=500)

        assembled = assembler.assemble([sec1, sec2])
        # Should appear once
        self.assertEqual(assembled.count(duplicate_text), 1)


class TestRelevanceRanker(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_ranker_")
        self.workspace = WorkspaceManager(self.test_dir)
        # Create a sample codebase
        self.workspace.write_file("src/auth.py", "class AuthToken:\n    def verify(self): pass\n")
        self.workspace.write_file("src/user.py", "from src.auth import AuthToken\nclass User:\n    def login(self): pass\n")
        self.workspace.write_file("src/utils.py", "def helper(): pass\n")
        self.code_graph = CodeGraphEngine(self.workspace.root_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_rank_context_focal_and_interface(self):
        ranker = RelevanceRanker(code_graph=self.code_graph, workspace=self.workspace)
        task_info = {
            "objective": "Update User login in user.py",
            "inputs": ["src/user.py"],
            "outputs": ["src/user.py"],
        }

        ranked = ranker.rank_context_for_task(task_info)
        focal = [r for r in ranked if r.tier == ContextTier.FOCAL]
        interfaces = [r for r in ranked if r.tier == ContextTier.INTERFACE]

        # src/user.py must be FOCAL
        self.assertTrue(any(r.filepath == "src/user.py" for r in focal))
        # src/auth.py must be INTERFACE (because user.py imports it)
        self.assertTrue(any(r.filepath == "src/auth.py" for r in interfaces))


class TestWorkingMemory(unittest.TestCase):
    def test_working_memory_lifecycle(self):
        wm = WorkingMemory(active_goal="Implement Payment Gateway")

        wm.record_fact("Stripe API key requires test mode prefix")
        wm.record_fact("Stripe API key requires test mode prefix")  # duplicate
        self.assertEqual(len(wm.verified_facts), 1)

        wm.record_pitfall("Webhook signature verification fails without raw payload")
        self.assertEqual(len(wm.discovered_pitfalls), 1)

        wm.record_modified_symbol("PaymentService.process")
        wm.update_scratchpad("Investigating idempotency keys")

        summary = wm.get_summary()
        self.assertIn("Implement Payment Gateway", summary)
        self.assertIn("Stripe API key requires test mode prefix", summary)
        self.assertIn("Webhook signature verification fails", summary)
        self.assertIn("PaymentService.process", summary)
        self.assertIn("Investigating idempotency keys", summary)

        # Serialization roundtrip
        data = wm.to_dict()
        restored = WorkingMemory.from_dict(data)
        self.assertEqual(restored.active_goal, wm.active_goal)
        self.assertEqual(restored.verified_facts, wm.verified_facts)


class TestLongTermMemory(unittest.TestCase):
    def setUp(self):
        self.memory = LongTermMemory(":memory:")

    def test_store_and_search(self):
        mem_id = self.memory.store(
            category="ARCH_DECISION",
            title="Database Connection Pooling",
            content="Use SQLAlchemy async connection pooling with max_overflow=10.",
            tags=["database", "sqlalchemy", "performance"],
        )
        self.assertGreater(mem_id, 0)

        results = self.memory.search(query="SQLAlchemy connection pool")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Database Connection Pooling")
        self.assertEqual(results[0]["category"], "ARCH_DECISION")

    def test_category_filtering(self):
        self.memory.store(category="BUG_PATTERN", title="Circular Import in Utils", content="Avoid importing app in utils.")
        self.memory.store(category="CONVENTION", title="PEP8 Naming", content="Follow snake_case for functions.")

        bugs = self.memory.search(query="import", category="BUG_PATTERN")
        self.assertEqual(len(bugs), 1)
        self.assertEqual(bugs[0]["title"], "Circular Import in Utils")

        convs = self.memory.search(query="import", category="CONVENTION")
        self.assertEqual(len(convs), 0)


class TestWorkspaceContextCeiling(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_ws_context_")
        self.workspace = WorkspaceManager(self.test_dir)
        for i in range(10):
            self.workspace.write_file(f"file_{i}.txt", "line of content\n" * 100)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_all_code_context_bounded(self):
        # 10 files * ~1500 chars = ~15000 chars. Setting ceiling to 100 tokens (~400 chars)
        context = self.workspace.get_all_code_context(max_tokens=100)
        self.assertIn("omitted to respect context budget", context)


class TestMemoryToolsIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_mem_tools_")
        self.workspace = WorkspaceManager(self.test_dir)
        self.registry = BuiltinToolRegistry(self.workspace)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_search_and_record_memory_tools(self):
        rec_res = self.registry.call_tool("record_project_memory", {
            "category": "CONVENTION",
            "title": "Use Pydantic V2",
            "content": "All data schemas must inherit from pydantic.BaseModel.",
            "tags": ["pydantic", "validation"],
        })
        self.assertTrue(rec_res.get("success"))

        search_res = self.registry.call_tool("search_project_memory", {
            "query": "Pydantic validation schemas",
        })
        self.assertTrue(search_res.get("success"))
        self.assertGreaterEqual(search_res.get("count", 0), 1)

    def test_update_scratchpad_tool(self):
        wm = WorkingMemory()
        self.registry.working_memory = wm

        res = self.registry.call_tool("update_scratchpad", {"note": "Discovered race condition in cache lock"})
        self.assertTrue(res.get("success"))
        self.assertIn("Discovered race condition in cache lock", wm.scratchpad_notes)


if __name__ == "__main__":
    unittest.main()
