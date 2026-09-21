"""
Unit tests for Intelligent Model Routing Layer (Issue #34).
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.routing.model_router import (
    ModelTier,
    TaskComplexity,
    ModelProfile,
    ComplexityClassifier,
    ModelRouter,
    model_router,
)
from agent_orchestrator.llm import LLMClient


class TestModelRouter(unittest.TestCase):

    def test_model_tier_and_profile(self):
        """Test ModelTier enum and ModelProfile dataclass."""
        self.assertEqual(ModelTier.CHEAP_FAST.value, "cheap_fast")
        self.assertEqual(ModelTier.CODING.value, "coding")
        self.assertEqual(ModelTier.REASONING.value, "reasoning")
        self.assertEqual(ModelTier.EMBEDDING.value, "embedding")
        self.assertEqual(ModelTier.FALLBACK.value, "fallback")

        profile = ModelProfile(
            name="claude-3-5-sonnet",
            tier=ModelTier.CODING,
            context_window=200000,
            supports_tools=True,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
        )
        self.assertEqual(profile.tier, ModelTier.CODING)
        self.assertTrue(profile.supports_tools)

    def test_complexity_classifier_trivial(self):
        """Test classification of trivial tasks."""
        task = {
            "task_id": "T-01",
            "objective": "Check AST syntax and format code",
            "required_capabilities": ["ast-syntax-check", "formatting"],
            "dependencies": [],
            "inputs": [],
            "outputs": [],
        }
        complexity = ComplexityClassifier.classify(task)
        self.assertEqual(complexity, TaskComplexity.TRIVIAL)

    def test_complexity_classifier_standard(self):
        """Test classification of standard tasks."""
        task = {
            "task_id": "T-01",
            "objective": "Build user auth route",
            "required_capabilities": ["code-generation", "unit-tests"],
            "dependencies": ["T-00"],
            "inputs": ["spec.md"],
            "outputs": ["auth.py"],
        }
        complexity = ComplexityClassifier.classify(task)
        self.assertEqual(complexity, TaskComplexity.STANDARD)

    def test_complexity_classifier_complex(self):
        """Test classification of complex architecture / planning tasks."""
        task = {
            "task_id": "T-01",
            "objective": "Design system topology and component hierarchy",
            "required_capabilities": ["software-architecture", "system-design"],
            "dependencies": [],
            "inputs": [],
            "outputs": [],
        }
        complexity = ComplexityClassifier.classify(task)
        self.assertEqual(complexity, TaskComplexity.COMPLEX)

    def test_complexity_classifier_escalation_on_retry_and_remediation(self):
        """Test escalation to CRITICAL_REASONING on retry >= 2 or remediation task."""
        task = {
            "task_id": "T-01",
            "objective": "Simple code edit",
            "required_capabilities": ["formatting"],
        }
        # Attempt 1: Trivial
        self.assertEqual(ComplexityClassifier.classify(task, attempt=1), TaskComplexity.TRIVIAL)
        # Attempt 2: Escalated to CRITICAL_REASONING
        self.assertEqual(ComplexityClassifier.classify(task, attempt=2), TaskComplexity.CRITICAL_REASONING)

        # Remediation task ID: Always CRITICAL_REASONING
        rem_task = {
            "task_id": "T-REM-1",
            "objective": "Fix syntax error",
            "required_capabilities": ["code-generation"],
        }
        self.assertEqual(ComplexityClassifier.classify(rem_task, attempt=1), TaskComplexity.CRITICAL_REASONING)

    def test_model_router_route(self):
        """Test routing logic across roles, complexities, and attempts."""
        router = ModelRouter(
            tier_models={
                ModelTier.CHEAP_FAST: "test-fast-model",
                ModelTier.CODING: "test-coding-model",
                ModelTier.REASONING: "test-reasoning-model",
                ModelTier.FALLBACK: "test-fallback-model",
            }
        )

        # Trivial complexity -> CHEAP_FAST
        self.assertEqual(
            router.route(role="CODER", complexity=TaskComplexity.TRIVIAL),
            "test-fast-model",
        )

        # Standard complexity CODER -> CODING
        self.assertEqual(
            router.route(role="CODER", complexity=TaskComplexity.STANDARD),
            "test-coding-model",
        )

        # Standard complexity PLANNER -> REASONING
        self.assertEqual(
            router.route(role="PLANNER", complexity=TaskComplexity.STANDARD),
            "test-reasoning-model",
        )

        # Critical complexity -> REASONING
        self.assertEqual(
            router.route(role="CODER", complexity=TaskComplexity.CRITICAL_REASONING),
            "test-reasoning-model",
        )

        # Escalation on attempt >= 2 -> REASONING
        self.assertEqual(
            router.route(role="CODER", complexity=TaskComplexity.TRIVIAL, attempt=2),
            "test-reasoning-model",
        )

    def test_model_router_resolve_model(self):
        """Test resolution of 'auto', ModelTier, and raw strings."""
        router = ModelRouter(
            tier_models={
                ModelTier.CHEAP_FAST: "fast-m",
                ModelTier.CODING: "coding-m",
                ModelTier.REASONING: "reasoning-m",
            }
        )
        self.assertEqual(router.resolve_model("auto"), "coding-m")
        self.assertEqual(router.resolve_model(None), "coding-m")
        self.assertEqual(router.resolve_model(ModelTier.REASONING), "reasoning-m")
        self.assertEqual(router.resolve_model("cheap_fast"), "fast-m")
        self.assertEqual(router.resolve_model("custom-provider/special-model"), "custom-provider/special-model")

    def test_model_router_fallback_chain(self):
        """Test fallback model retrieval."""
        router = ModelRouter(
            tier_models={
                ModelTier.CHEAP_FAST: "fast-m",
                ModelTier.CODING: "coding-m",
                ModelTier.REASONING: "reasoning-m",
                ModelTier.FALLBACK: "fallback-m",
            }
        )
        # coding-m should fall back to fallback-m
        self.assertEqual(router.get_fallback("coding-m"), "fallback-m")
        # Unknown model falls back to default fallback-m
        self.assertEqual(router.get_fallback("unknown-model"), "fallback-m")

    def test_llm_client_failover_chat(self):
        """Test LLMClient transparent failover to fallback model when primary fails."""
        router = ModelRouter(
            tier_models={
                ModelTier.CODING: "primary-model",
                ModelTier.FALLBACK: "fallback-model",
            },
            fallback_chains={"primary-model": ["fallback-model"]},
        )

        with patch("agent_orchestrator.llm.model_router", router):
            client = LLMClient(api_key="mock-key", default_model="primary-model")
            mock_openai = MagicMock()

            # First model (primary-model) fails on all attempts with 429
            # Second model (fallback-model) succeeds
            def mock_create(**kwargs):
                if kwargs.get("model") == "primary-model":
                    raise Exception("429 Too Many Requests: Rate limit exceeded")
                elif kwargs.get("model") == "fallback-model":
                    mock_resp = MagicMock()
                    mock_choice = MagicMock()
                    mock_choice.message.content = "Success from fallback model"
                    mock_resp.choices = [mock_choice]
                    return mock_resp
                raise ValueError("Unexpected model")

            mock_openai.chat.completions.create.side_effect = mock_create
            client.client = mock_openai

            content = client.chat(
                messages=[{"role": "user", "content": "hello"}],
                model="primary-model",
            )
            self.assertEqual(content, "Success from fallback model")

    def test_env_variable_tier_overrides(self):
        """Test environment variables overriding default tier models."""
        env_vars = {
            "FAST_MODEL": "custom-fast-1",
            "CODING_MODEL": "custom-coding-2",
            "REASONING_MODEL": "custom-reasoning-3",
            "FALLBACK_MODEL": "custom-fallback-4",
        }
        with patch.dict(os.environ, env_vars):
            router = ModelRouter()
            self.assertEqual(router.get_tier_model(ModelTier.CHEAP_FAST), "custom-fast-1")
            self.assertEqual(router.get_tier_model(ModelTier.CODING), "custom-coding-2")
            self.assertEqual(router.get_tier_model(ModelTier.REASONING), "custom-reasoning-3")
            self.assertEqual(router.get_tier_model(ModelTier.FALLBACK), "custom-fallback-4")


if __name__ == "__main__":
    unittest.main()
