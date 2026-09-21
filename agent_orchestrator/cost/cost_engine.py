"""
Cost Engine & Pricing Registry for LLM and Tool Invocations.
Provides exact multi-model token pricing, compute cost calculations, and cost telemetry.
"""
from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional


@dataclass
class ModelPricing:
    """Pricing rates per 1,000,000 tokens."""
    prompt_cost_per_1m: float = 0.0
    completion_cost_per_1m: float = 0.0
    cached_prompt_cost_per_1m: float = 0.0


class PricingRegistry:
    """
    Registry of token and compute pricing across model families and providers.
    Supports exact and prefix/regex model matching.
    """

    DEFAULT_PRICING: Dict[str, ModelPricing] = {
        # OpenAI Models
        "gpt-4o": ModelPricing(prompt_cost_per_1m=2.50, completion_cost_per_1m=10.00),
        "gpt-4o-mini": ModelPricing(prompt_cost_per_1m=0.15, completion_cost_per_1m=0.60),
        "o1": ModelPricing(prompt_cost_per_1m=15.00, completion_cost_per_1m=60.00),
        "o1-mini": ModelPricing(prompt_cost_per_1m=1.10, completion_cost_per_1m=4.40),
        "o3-mini": ModelPricing(prompt_cost_per_1m=1.10, completion_cost_per_1m=4.40),
        "gpt-4-turbo": ModelPricing(prompt_cost_per_1m=10.00, completion_cost_per_1m=30.00),
        "text-embedding-3-small": ModelPricing(prompt_cost_per_1m=0.02, completion_cost_per_1m=0.00),
        "text-embedding-3-large": ModelPricing(prompt_cost_per_1m=0.13, completion_cost_per_1m=0.00),

        # Anthropic Models
        "claude-3-5-sonnet": ModelPricing(prompt_cost_per_1m=3.00, completion_cost_per_1m=15.00, cached_prompt_cost_per_1m=0.30),
        "claude-3-5-haiku": ModelPricing(prompt_cost_per_1m=0.80, completion_cost_per_1m=4.00, cached_prompt_cost_per_1m=0.08),
        "claude-3-7-sonnet": ModelPricing(prompt_cost_per_1m=3.00, completion_cost_per_1m=15.00, cached_prompt_cost_per_1m=0.30),
        "claude-3-opus": ModelPricing(prompt_cost_per_1m=15.00, completion_cost_per_1m=75.00),

        # DeepSeek Models
        "deepseek-coder": ModelPricing(prompt_cost_per_1m=0.14, completion_cost_per_1m=0.28),
        "deepseek-r1": ModelPricing(prompt_cost_per_1m=0.55, completion_cost_per_1m=2.19),
        "deepseek-chat": ModelPricing(prompt_cost_per_1m=0.14, completion_cost_per_1m=0.28),

        # Local / Mock / Free Endpoints
        "mock-key-for-testing": ModelPricing(prompt_cost_per_1m=0.0, completion_cost_per_1m=0.0),
        "local": ModelPricing(prompt_cost_per_1m=0.0, completion_cost_per_1m=0.0),
        "auto": ModelPricing(prompt_cost_per_1m=0.15, completion_cost_per_1m=0.60),
    }

    def __init__(self, custom_pricing: Optional[Dict[str, ModelPricing]] = None):
        self._pricing: Dict[str, ModelPricing] = dict(self.DEFAULT_PRICING)
        if custom_pricing:
            self._pricing.update(custom_pricing)

    def register_pricing(self, model_name: str, pricing: ModelPricing):
        """Registers or overrides pricing for a model name."""
        self._pricing[model_name.lower()] = pricing

    register = register_pricing

    def get_pricing(self, model_name: str) -> ModelPricing:
        """
        Resolves pricing for a model name:
        1. Exact match
        2. Substring/prefix match
        3. Default fallback (0.15 / 0.60)
        """
        if not model_name:
            return self._pricing["auto"]

        clean = model_name.lower().strip()
        if clean in self._pricing:
            return self._pricing[clean]

        # Substring / family matching
        for key, p in self._pricing.items():
            if key in clean or clean in key:
                return p

        # Check if local/mock
        if any(k in clean for k in ("mock", "local", "ollama", "vllm", "test")):
            return ModelPricing(prompt_cost_per_1m=0.0, completion_cost_per_1m=0.0)

        # Default fallback
        return ModelPricing(prompt_cost_per_1m=0.15, completion_cost_per_1m=0.60)


class CostEngine:
    """
    Computes precise financial costs for LLM tokens and tool executions.
    """

    # Tool compute cost rates (e.g. sandboxed execution, external search)
    TOOL_COMPUTE_RATES_PER_SEC: Dict[str, float] = {
        "terminal_execute": 0.0001,       # Sandbox CPU compute
        "run_build_pipeline": 0.0002,     # Containerized build pipeline
        "run_command": 0.0001,
        "search_skills": 0.00005,         # Skill registry search
    }

    def __init__(self, pricing_registry: Optional[PricingRegistry] = None):
        self.registry = pricing_registry or PricingRegistry()

    def calculate_llm_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
    ) -> float:
        """
        Calculates LLM cost in USD based on model rates.
        """
        pricing = self.registry.get_pricing(model)
        regular_prompt = max(0, prompt_tokens - cached_tokens)
        prompt_cost = (regular_prompt / 1_000_000.0) * pricing.prompt_cost_per_1m
        cached_cost = (cached_tokens / 1_000_000.0) * pricing.cached_prompt_cost_per_1m
        completion_cost = (completion_tokens / 1_000_000.0) * pricing.completion_cost_per_1m
        return round(prompt_cost + cached_cost + completion_cost, 6)

    def calculate_tool_cost(
        self,
        tool_name: str,
        duration_seconds: float = 0.0,
        bytes_processed: int = 0,
    ) -> float:
        """
        Calculates compute or retrieval cost for a tool execution.
        """
        rate = self.TOOL_COMPUTE_RATES_PER_SEC.get(tool_name, 0.0)
        compute_cost = duration_seconds * rate
        # Data transfer cost ($0.000001 per KB)
        data_cost = (bytes_processed / 1024.0) * 0.000001 if bytes_processed > 0 else 0.0
        return round(compute_cost + data_cost, 6)


# Global singleton CostEngine
cost_engine = CostEngine()
