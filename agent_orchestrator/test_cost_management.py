"""
Unit and integration tests for Token and Cost Management (Issue #35).
Tests CostEngine, PricingRegistry, BudgetTracker, ContextCompactor, and ReActAgentLoop budget integration.
"""
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.cost.cost_engine import CostEngine, PricingRegistry, ModelPricing
from agent_orchestrator.cost.budget_tracker import (
    BudgetTracker,
    BudgetSpec,
    BudgetExceededError,
    BudgetAction,
)
from agent_orchestrator.runtime.react_loop import ContextCompactor, ReActAgentLoop
from agent_orchestrator.runtime.task_graph import TokenUsage


class TestCostEngine(unittest.TestCase):
    def setUp(self):
        self.pricing_registry = PricingRegistry()
        self.engine = CostEngine(self.pricing_registry)

    def test_known_model_pricing(self):
        pricing = self.pricing_registry.get_pricing("gpt-4o-mini")
        self.assertAlmostEqual(pricing.prompt_cost_per_1m, 0.15)
        self.assertAlmostEqual(pricing.completion_cost_per_1m, 0.60)

    def test_fallback_prefix_matching(self):
        # Prefix matching should find claude-3-5-sonnet for subversion
        pricing = self.pricing_registry.get_pricing("claude-3-5-sonnet-20241022")
        self.assertAlmostEqual(pricing.prompt_cost_per_1m, 3.00)
        self.assertAlmostEqual(pricing.completion_cost_per_1m, 15.00)

    def test_local_model_pricing(self):
        pricing = self.pricing_registry.get_pricing("ollama/llama3")
        self.assertEqual(pricing.prompt_cost_per_1m, 0.0)
        self.assertEqual(pricing.completion_cost_per_1m, 0.0)

    def test_calculate_llm_cost(self):
        # 1,000 prompt tokens and 500 completion tokens on gpt-4o-mini
        # 1000 * 0.15 / 1e6 = 0.00015
        # 500 * 0.60 / 1e6 = 0.00030
        # Total = 0.00045
        cost = self.engine.calculate_llm_cost("gpt-4o-mini", prompt_tokens=1000, completion_tokens=500)
        self.assertAlmostEqual(cost, 0.00045, places=6)

    def test_calculate_tool_cost(self):
        # 2 seconds duration at $0.0001/sec = $0.0002
        tool_cost = self.engine.calculate_tool_cost("terminal_execute", duration_seconds=2.0)
        self.assertAlmostEqual(tool_cost, 0.0002, places=6)

    def test_custom_pricing_registration(self):
        self.pricing_registry.register("my-custom-model", ModelPricing(prompt_cost_per_1m=1.0, completion_cost_per_1m=2.0))
        cost = self.engine.calculate_llm_cost("my-custom-model", prompt_tokens=1000000, completion_tokens=1000000)
        self.assertAlmostEqual(cost, 3.0)


class TestBudgetTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = BudgetTracker()

    def test_record_spend_and_query(self):
        self.tracker.record_spend(
            task_id="T-01",
            agent_name="CODER",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.005,
            tool_name="file_writer",
        )
        task_spend = self.tracker.get_task_spend("T-01")
        self.assertEqual(task_spend["prompt_tokens"], 100)
        self.assertEqual(task_spend["completion_tokens"], 50)
        self.assertEqual(task_spend["total_tokens"], 150)
        self.assertAlmostEqual(task_spend["cost_usd"], 0.005)

        agent_spend = self.tracker.get_agent_spend("CODER")
        self.assertEqual(agent_spend["total_tokens"], 150)

        session_spend = self.tracker.get_session_spend()
        self.assertEqual(session_spend["total_tokens"], 150)
        self.assertAlmostEqual(session_spend["cost_usd"], 0.005)

    def test_task_token_budget_exceeded(self):
        self.tracker.spec = BudgetSpec(max_tokens_per_task=100)
        self.tracker.record_spend(task_id="T-01", prompt_tokens=60, completion_tokens=50)

        with self.assertRaises(BudgetExceededError) as ctx:
            self.tracker.assert_budget(task_id="T-01")
        self.assertIn("Task [T-01] token budget exceeded", str(ctx.exception))

    def test_task_cost_budget_exceeded(self):
        self.tracker.spec = BudgetSpec(max_cost_usd_per_task=0.01)
        self.tracker.record_spend(task_id="T-01", cost_usd=0.015)

        with self.assertRaises(BudgetExceededError) as ctx:
            self.tracker.assert_budget(task_id="T-01")
        self.assertIn("Task [T-01] cost budget exceeded", str(ctx.exception))

    def test_session_cost_budget_exceeded(self):
        self.tracker.spec = BudgetSpec(max_session_cost_usd=0.05)
        self.tracker.record_spend(task_id="T-01", cost_usd=0.03)
        self.tracker.record_spend(task_id="T-02", cost_usd=0.03)

        with self.assertRaises(BudgetExceededError) as ctx:
            self.tracker.assert_budget()
        self.assertIn("Session cost budget exceeded", str(ctx.exception))

    def test_agent_budget_exceeded(self):
        self.tracker.spec = BudgetSpec(agent_budgets={"CODER": 0.02})
        self.tracker.record_spend(agent_name="CODER", cost_usd=0.025)

        with self.assertRaises(BudgetExceededError) as ctx:
            self.tracker.assert_budget(agent_name="CODER")
        self.assertIn("Agent [CODER] cost budget exceeded", str(ctx.exception))

    def test_check_budget_warn_mode(self):
        self.tracker.spec = BudgetSpec(max_tokens_per_task=100, on_budget_exceeded=BudgetAction.WARN)
        self.tracker.record_spend(task_id="T-01", prompt_tokens=150)

        # assert_budget should not raise when action is WARN
        self.tracker.assert_budget(task_id="T-01")

        # check_budget correctly reports is_exceeded=True
        is_exceeded, reason = self.tracker.check_budget(task_id="T-01")
        self.assertTrue(is_exceeded)
        self.assertIn("exceeded", reason)


class TestContextCompactor(unittest.TestCase):
    def test_no_compaction_for_short_context(self):
        messages = [
            {"role": "system", "content": "You are an assistant."},
            {"role": "user", "content": "Write some code."},
            {"role": "assistant", "content": "Here is code."},
        ]
        compacted, was_compacted = ContextCompactor.compact(messages, max_tokens=1000)
        self.assertFalse(was_compacted)
        self.assertEqual(len(compacted), len(messages))

    def test_compaction_of_large_tool_observations(self):
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Initial prompt"},
            {"role": "assistant", "content": "Calling tool..."},
            {"role": "tool", "name": "read_file", "content": "x" * 2000},  # middle large observation
            {"role": "assistant", "content": "Observation received."},
            {"role": "user", "content": "Continue."},
            {"role": "assistant", "content": "Turn 2."},
            {"role": "user", "content": "Next step."},
            {"role": "assistant", "content": "Final turn."},
        ]
        # Low threshold to force compaction
        compacted, was_compacted = ContextCompactor.compact(messages, max_tokens=100)
        self.assertTrue(was_compacted)
        # Verify tool observation was replaced with summary
        tool_msg = compacted[3]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertIn("[Observation compacted", tool_msg["content"])
        self.assertIn("2000 chars", tool_msg["content"])
        # First 2 and last 4 messages preserved
        self.assertEqual(compacted[0]["content"], "System prompt")
        self.assertEqual(compacted[1]["content"], "Initial prompt")
        self.assertEqual(compacted[-1]["content"], "Final turn.")


class TestReActLoopBudgetIntegration(unittest.TestCase):
    def test_react_loop_halts_on_budget_exceeded(self):
        from agent_orchestrator.cost.budget_tracker import budget_tracker

        budget_tracker.reset()
        budget_tracker.spec = BudgetSpec(max_tokens_per_task=50, on_budget_exceeded=BudgetAction.HALT)

        mock_llm = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_schemas.return_value = []

        events = []
        loop = ReActAgentLoop(
            llm=mock_llm,
            tool_registry=mock_registry,
            max_turns=5,
            on_step_callback=lambda action, payload: events.append((action, payload)),
        )

        # Pre-seed spend to exceed budget
        budget_tracker.record_spend(task_id="task-budget-test", prompt_tokens=100)

        result = loop.run_loop(
            system_prompt="System",
            user_prompt="User",
            task_id="task-budget-test",
        )

        # Loop should immediately halt before turn 1
        self.assertEqual(result["turns_taken"], 0)
        self.assertTrue(any(ev[0] == "BUDGET_EXCEEDED" for ev in events))
        self.assertTrue(any("budget exceeded" in err.lower() for err in result["errors"]))

        # Cleanup
        budget_tracker.reset()


if __name__ == "__main__":
    unittest.main()
