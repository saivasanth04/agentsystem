"""
Comprehensive Test Suite for Multi-Tier Capability Fallback Engine.
Tests Issue #74 requirements:
If high-tier capability providers (e.g. Graft) are unavailable, the system cascades:
Graft (Tier 1) -> CBM (Tier 2) -> ripgrep (Tier 3) -> tree-sitter (Tier 4) -> filesystem (Tier 5)
depending on capability (find_symbols, find_references, search_code, get_repo_map).
"""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.capabilities.fallback import (
    CapabilityFallbackEngine,
    CapabilityProvider,
    CapabilityTier,
    CBMProvider,
    FilesystemProvider,
    GraftProvider,
    RipgrepProvider,
    TreeSitterProvider,
)
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.registry import ToolHealthStatus
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestIndividualCapabilityProviders(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir)

        # Create sample Python project structure in workspace
        (self.workspace / "src").mkdir(parents=True, exist_ok=True)
        self.main_file = self.workspace / "src" / "main.py"
        self.main_file.write_text(
            "class OrderService:\n"
            "    def process_order(self, order_id: str):\n"
            "        validate_order(order_id)\n"
            "        return True\n\n"
            "def validate_order(order_id: str):\n"
            "    return len(order_id) > 0\n"
        )
        self.helper_file = self.workspace / "src" / "helpers.py"
        self.helper_file.write_text(
            "from src.main import OrderService\n\n"
            "def run():\n"
            "    service = OrderService()\n"
            "    return service.process_order('123')\n"
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_filesystem_provider_tier_5_baseline(self):
        """Tier 5 FilesystemProvider must always be available and succeed on raw files."""
        provider = FilesystemProvider()
        self.assertEqual(provider.tier, CapabilityTier.TIER_5_FILESYSTEM)
        self.assertTrue(provider.is_available(self.workspace))

        # 1. find_symbols
        symbols = provider.find_symbols("OrderService", self.workspace)
        self.assertGreaterEqual(len(symbols), 1)
        sym_names = [s["name"] for s in symbols]
        self.assertIn("OrderService", sym_names)

        # 2. find_references
        refs = provider.find_references("OrderService", self.workspace)
        self.assertGreaterEqual(len(refs), 1)

        # 3. search_code
        matches = provider.search_code("validate_order", self.workspace)
        self.assertGreaterEqual(len(matches), 2)

        # 4. get_repo_map
        repo_map = provider.get_repo_map(self.workspace)
        self.assertIn("files", repo_map)
        self.assertGreaterEqual(repo_map["total_entries"], 2)

    def test_tree_sitter_provider_tier_4(self):
        """Tier 4 TreeSitterProvider performs AST/CST structural symbol extraction."""
        provider = TreeSitterProvider()
        self.assertEqual(provider.tier, CapabilityTier.TIER_4_TREE_SITTER)
        self.assertTrue(provider.is_available(self.workspace))

        symbols = provider.find_symbols("OrderService", self.workspace)
        self.assertGreaterEqual(len(symbols), 1)
        self.assertEqual(symbols[0]["name"], "OrderService")
        self.assertEqual(symbols[0]["kind"], "class")

        func_symbols = provider.find_symbols("validate_order", self.workspace)
        self.assertGreaterEqual(len(func_symbols), 1)
        self.assertEqual(func_symbols[0]["kind"], "function")

        # Repo map
        repo_map = provider.get_repo_map(self.workspace)
        self.assertIn("modules", repo_map)

    def test_ripgrep_provider_tier_3(self):
        """Tier 3 RipgrepProvider performs fast regex matching."""
        provider = RipgrepProvider()
        self.assertEqual(provider.tier, CapabilityTier.TIER_3_RIPGREP)
        self.assertTrue(provider.is_available(self.workspace))

        # Search code
        results = provider.search_code("process_order", self.workspace)
        self.assertGreaterEqual(len(results), 2)

        # Symbol search via regex heuristics
        symbols = provider.find_symbols("OrderService", self.workspace)
        self.assertGreaterEqual(len(symbols), 1)
        self.assertEqual(symbols[0]["name"], "OrderService")

    def test_cbm_provider_tier_2(self):
        """Tier 2 CBMProvider queries in-process AST knowledge graph."""
        provider = CBMProvider()
        self.assertEqual(provider.tier, CapabilityTier.TIER_2_CBM)
        self.assertTrue(provider.is_available(self.workspace))

        symbols = provider.find_symbols("OrderService", self.workspace)
        self.assertGreaterEqual(len(symbols), 1)
        self.assertEqual(symbols[0]["name"], "OrderService")

        refs = provider.find_references("OrderService", self.workspace)
        self.assertGreaterEqual(len(refs), 1)

        repo_map = provider.get_repo_map(self.workspace)
        self.assertIn("modules", repo_map)

    def test_graft_provider_tier_1_fallback_when_command_missing(self):
        """Tier 1 GraftProvider gracefully reports unavailable when graft binary is missing."""
        provider = GraftProvider(graft_cmd=None, enabled=False)
        self.assertEqual(provider.tier, CapabilityTier.TIER_1_GRAFT)
        self.assertFalse(provider.is_available(self.workspace))


class TestMultiTierCascadeLadder(unittest.TestCase):
    """
    Validates the cascade ladder:
    Graft (Tier 1) -> CBM (Tier 2) -> ripgrep (Tier 3) -> tree-sitter (Tier 4) -> filesystem (Tier 5)
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir)
        (self.workspace / "app.py").write_text(
            "def calculate_total(items):\n"
            "    return sum(items)\n\n"
            "def checkout():\n"
            "    return calculate_total([10, 20])\n"
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cascade_when_graft_unavailable_uses_cbm(self):
        """When Graft is unavailable, engine degrades to CBM (Tier 2)."""
        engine = CapabilityFallbackEngine(
            workspace_dir=self.workspace,
            providers=[
                GraftProvider(graft_cmd="non_existent_graft_bin", enabled=False),
                CBMProvider(enabled=True),
                RipgrepProvider(enabled=True),
                TreeSitterProvider(enabled=True),
                FilesystemProvider(enabled=True),
            ],
        )

        res = engine.execute("find_symbols", "calculate_total")
        self.assertTrue(res.success)
        self.assertEqual(res.active_tier, CapabilityTier.TIER_2_CBM)
        self.assertEqual(res.provider_name, "cbm")
        self.assertTrue(res.degraded)
        self.assertIn("graft", res.cascade_path)
        self.assertIn("cbm", res.cascade_path)

    def test_cascade_when_graft_and_cbm_unavailable_uses_ripgrep(self):
        """When Graft and CBM are unavailable, engine degrades to Ripgrep (Tier 3)."""
        engine = CapabilityFallbackEngine(
            workspace_dir=self.workspace,
            providers=[
                GraftProvider(enabled=False),
                CBMProvider(enabled=False),
                RipgrepProvider(enabled=True),
                TreeSitterProvider(enabled=True),
                FilesystemProvider(enabled=True),
            ],
        )

        res = engine.execute("search_code", "calculate_total")
        self.assertTrue(res.success)
        self.assertEqual(res.active_tier, CapabilityTier.TIER_3_RIPGREP)
        self.assertEqual(res.provider_name, "ripgrep")
        self.assertTrue(res.degraded)
        self.assertEqual(res.cascade_path, ["graft", "cbm", "ripgrep"])

    def test_cascade_when_tiers_1_to_3_unavailable_uses_tree_sitter(self):
        """When Tiers 1-3 unavailable, engine degrades to Tree-sitter (Tier 4)."""
        engine = CapabilityFallbackEngine(
            workspace_dir=self.workspace,
            providers=[
                GraftProvider(enabled=False),
                CBMProvider(enabled=False),
                RipgrepProvider(enabled=False),
                TreeSitterProvider(enabled=True),
                FilesystemProvider(enabled=True),
            ],
        )

        res = engine.execute("find_symbols", "calculate_total")
        self.assertTrue(res.success)
        self.assertEqual(res.active_tier, CapabilityTier.TIER_4_TREE_SITTER)
        self.assertEqual(res.provider_name, "tree-sitter")
        self.assertTrue(res.degraded)
        self.assertEqual(res.cascade_path, ["graft", "cbm", "ripgrep", "tree-sitter"])

    def test_cascade_when_tiers_1_to_4_unavailable_uses_filesystem_baseline(self):
        """When Tiers 1-4 unavailable, engine degrades to Filesystem (Tier 5 baseline)."""
        engine = CapabilityFallbackEngine(
            workspace_dir=self.workspace,
            providers=[
                GraftProvider(enabled=False),
                CBMProvider(enabled=False),
                RipgrepProvider(enabled=False),
                TreeSitterProvider(enabled=False),
                FilesystemProvider(enabled=True),
            ],
        )

        res = engine.execute("find_symbols", "calculate_total")
        self.assertTrue(res.success)
        self.assertEqual(res.active_tier, CapabilityTier.TIER_5_FILESYSTEM)
        self.assertEqual(res.provider_name, "filesystem")
        self.assertTrue(res.degraded)
        self.assertEqual(
            res.cascade_path,
            ["graft", "cbm", "ripgrep", "tree-sitter", "filesystem"],
        )

    def test_tier_1_graft_selected_when_available(self):
        """When Graft is available and healthy, Tier 1 is selected with degraded=False."""
        mock_graft = MagicMock(spec=CapabilityProvider)
        mock_graft.name = "graft"
        mock_graft.tier = CapabilityTier.TIER_1_GRAFT
        mock_graft.is_available.return_value = True
        mock_graft.find_symbols.return_value = [{"name": "calculate_total", "tier": 1}]

        engine = CapabilityFallbackEngine(
            workspace_dir=self.workspace,
            providers=[
                mock_graft,
                CBMProvider(enabled=True),
                FilesystemProvider(enabled=True),
            ],
        )

        res = engine.execute("find_symbols", "calculate_total")
        self.assertTrue(res.success)
        self.assertEqual(res.active_tier, CapabilityTier.TIER_1_GRAFT)
        self.assertEqual(res.provider_name, "graft")
        self.assertFalse(res.degraded)
        self.assertEqual(res.cascade_path, ["graft"])


class TestUnifiedDispatcherCapabilityIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_dir = Path(self.temp_dir)
        self.workspace = WorkspaceManager(self.workspace_dir)
        self.builtin_registry = BuiltinToolRegistry(self.workspace)

        # Create test code
        (self.workspace_dir / "service.py").write_text(
            "class PaymentGateway:\n"
            "    def charge(self, amount):\n"
            "        return amount > 0\n"
        )

        self.dispatcher = UnifiedToolDispatcher(self.builtin_registry)

    def tearDown(self):
        self.dispatcher.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_dispatch_capability_find_symbols(self):
        """UnifiedToolDispatcher.dispatch_capability executes with degraded transparency."""
        res = self.dispatcher.dispatch_capability("find_symbols", "PaymentGateway")
        self.assertTrue(res["success"])
        self.assertIn("active_tier", res)
        self.assertIn("provider", res)
        self.assertIn("cascade_path", res)
        self.assertIsInstance(res["data"], list)

    def test_dispatch_capability_search_code(self):
        """UnifiedToolDispatcher.dispatch_capability searches code."""
        res = self.dispatcher.dispatch_capability("search_code", "charge")
        self.assertTrue(res["success"])
        self.assertIsInstance(res["data"], list)
        self.assertGreaterEqual(len(res["data"]), 1)

    def test_dispatch_capability_get_repo_map(self):
        """UnifiedToolDispatcher.dispatch_capability generates repository map."""
        res = self.dispatcher.dispatch_capability("get_repo_map")
        self.assertTrue(res["success"])
        self.assertIn("active_tier", res)

    def test_health_check_reports_capability_providers(self):
        """health_check includes capability provider status."""
        reports = self.dispatcher.health_check(name="capability")
        self.assertTrue(any("capability_provider_" in k for k in reports.keys()))
        self.assertIn("capability_provider_filesystem", reports)
        fs_rep = reports["capability_provider_filesystem"]
        self.assertEqual(fs_rep.status, ToolHealthStatus.HEALTHY)


if __name__ == "__main__":
    unittest.main()
