"""
Model Router & Task-Complexity-Based Model Selection Layer.
Provides ModelTier categorization, TaskComplexity classification, dynamic routing,
and transparent fallback failover chains for autonomous multi-agent systems.
"""
from dataclasses import dataclass, field
from enum import Enum
import os
from typing import Any, Dict, List, Optional, Set, Union


class ModelTier(str, Enum):
    """Hierarchical capability and cost tiers for LLMs."""
    CHEAP_FAST = "cheap_fast"          # Fast, low-cost (gpt-4o-mini, claude-3-5-haiku, llama-3.1-8b)
    CODING = "coding"                  # High-accuracy code generation (claude-3-5-sonnet, deepseek-coder, gpt-4o)
    REASONING = "reasoning"            # Deep reasoning & planning (o3-mini, o1, deepseek-r1, claude-3-7-sonnet)
    EMBEDDING = "embedding"            # Vector representation & search (text-embedding-3-small)
    FALLBACK = "fallback"              # Redundant failover model upon rate limits or provider downtime


class TaskComplexity(str, Enum):
    """Assessed complexity tier of an execution task."""
    TRIVIAL = "trivial"                # Simple format changes, syntax checks, commit messages
    STANDARD = "standard"              # Single-file implementation, unit test creation
    COMPLEX = "complex"                # Multi-file refactoring, architectural seams, cross-module changes
    CRITICAL_REASONING = "critical"    # Difficult bug isolation, repeated failed attempts, remediation


@dataclass
class ModelProfile:
    """Metadata profile for an available model."""
    name: str
    tier: ModelTier
    context_window: int = 128000
    supports_tools: bool = True
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    provider: str = "openai"


class ComplexityClassifier:
    """
    Deterministically assesses task complexity based on:
    1. Execution attempt count and failure history (escalation).
    2. Required capabilities and operational scope.
    3. Input/output dependency fan-in/fan-out.
    """

    CRITICAL_CAPABILITIES: Set[str] = {
        "deep-debugging",
        "concurrency",
        "distributed-systems",
        "security-audit",
        "remediation",
        "root-cause-analysis",
    }

    COMPLEX_CAPABILITIES: Set[str] = {
        "architecture",
        "software-architecture",
        "system-design",
        "planning",
        "roadmapping",
        "spec-writing",
        "integration-tests",
        "refactoring",
    }

    TRIVIAL_CAPABILITIES: Set[str] = {
        "code-simplification",
        "formatting",
        "ast-syntax-check",
        "git-commit",
        "documentation-only",
    }

    @classmethod
    def classify(cls, task: Any, attempt: int = 1) -> TaskComplexity:
        """
        Classifies an ExecutableTask or task dictionary into TaskComplexity.
        """
        # 1. Escalation rule: Any task on retry >= 2 or marked as remediation is CRITICAL_REASONING
        task_id = getattr(task, "task_id", "") or (task.get("task_id", "") if isinstance(task, dict) else "")
        attempt_num = getattr(task, "attempt", None) or attempt
        if attempt_num >= 2 or "T-REM" in task_id or "remediation" in str(task_id).lower():
            return TaskComplexity.CRITICAL_REASONING

        # Extract task metadata
        caps = set(
            getattr(task, "required_capabilities", None)
            or (task.get("required_capabilities", []) if isinstance(task, dict) else [])
        )
        objective = getattr(task, "objective", "") or (task.get("objective", "") if isinstance(task, dict) else "")
        obj_lower = objective.lower()

        # 2. Check for critical capability indicators
        if caps & cls.CRITICAL_CAPABILITIES or any(k in obj_lower for k in ("fix regression", "deadlock", "race condition", "vulnerability")):
            return TaskComplexity.CRITICAL_REASONING

        # 3. Check for complex capability indicators
        if caps & cls.COMPLEX_CAPABILITIES or any(k in obj_lower for k in ("architecture", "system topology", "decomposition", "re-plan")):
            return TaskComplexity.COMPLEX

        # 4. Check for trivial capability indicators
        if caps and caps.issubset(cls.TRIVIAL_CAPABILITIES):
            return TaskComplexity.TRIVIAL

        # 5. Check dependency graph breadth
        deps = getattr(task, "dependencies", None) or (task.get("dependencies", []) if isinstance(task, dict) else [])
        inputs = getattr(task, "inputs", None) or (task.get("inputs", []) if isinstance(task, dict) else [])
        outputs = getattr(task, "outputs", None) or (task.get("outputs", []) if isinstance(task, dict) else [])

        if len(deps) >= 3 or (len(inputs) + len(outputs)) >= 5:
            return TaskComplexity.COMPLEX

        return TaskComplexity.STANDARD


class ModelRouter:
    """
    Intelligent Model Selection & Failover Router.
    Maps task complexity, operational agent roles, and retry counts to optimal model profiles,
    and manages redundant fallback chains for high availability.
    """

    DEFAULT_MODELS: Dict[ModelTier, str] = {
        ModelTier.CHEAP_FAST: "gpt-4o-mini",
        ModelTier.CODING: "claude-3-5-sonnet",
        ModelTier.REASONING: "o3-mini",
        ModelTier.EMBEDDING: "text-embedding-3-small",
        ModelTier.FALLBACK: "gpt-4o",
    }

    def __init__(
        self,
        tier_models: Optional[Dict[ModelTier, str]] = None,
        fallback_chains: Optional[Dict[str, List[str]]] = None,
    ):
        self.tier_models: Dict[ModelTier, str] = dict(self.DEFAULT_MODELS)
        if tier_models:
            self.tier_models.update(tier_models)

        # Load environment variable overrides if present
        self._load_env_overrides()

        # Fallback chains mapping model -> list of fallbacks
        self.fallback_chains: Dict[str, List[str]] = fallback_chains or {}
        self._init_default_fallback_chains()

    def _load_env_overrides(self):
        """Loads model tier overrides from environment variables."""
        if os.getenv("FAST_MODEL") and os.getenv("FAST_MODEL") != "auto":
            self.tier_models[ModelTier.CHEAP_FAST] = os.getenv("FAST_MODEL")
        if os.getenv("CODING_MODEL") and os.getenv("CODING_MODEL") != "auto":
            self.tier_models[ModelTier.CODING] = os.getenv("CODING_MODEL")
        elif os.getenv("CODER_MODEL") and os.getenv("CODER_MODEL") != "auto":
            self.tier_models[ModelTier.CODING] = os.getenv("CODER_MODEL")

        if os.getenv("REASONING_MODEL") and os.getenv("REASONING_MODEL") != "auto":
            self.tier_models[ModelTier.REASONING] = os.getenv("REASONING_MODEL")
        if os.getenv("FALLBACK_MODEL") and os.getenv("FALLBACK_MODEL") != "auto":
            self.tier_models[ModelTier.FALLBACK] = os.getenv("FALLBACK_MODEL")
        if os.getenv("EMBEDDING_MODEL") and os.getenv("EMBEDDING_MODEL") != "auto":
            self.tier_models[ModelTier.EMBEDDING] = os.getenv("EMBEDDING_MODEL")

    def _init_default_fallback_chains(self):
        """Initializes sensible fallback chains for default tier models."""
        fast = self.tier_models[ModelTier.CHEAP_FAST]
        coding = self.tier_models[ModelTier.CODING]
        reasoning = self.tier_models[ModelTier.REASONING]
        fallback = self.tier_models[ModelTier.FALLBACK]

        # Coding falls back to Fallback model, then Cheap model
        self.fallback_chains[coding] = [fallback, fast]
        # Reasoning falls back to Coding, then Fallback
        self.fallback_chains[reasoning] = [coding, fallback]
        # Fast falls back to Fallback
        self.fallback_chains[fast] = [fallback]
        # Fallback falls back to Coding
        self.fallback_chains[fallback] = [coding]

    def set_tier_model(self, tier: ModelTier, model_name: str):
        """Overrides the model mapped to a tier."""
        self.tier_models[tier] = model_name
        self._init_default_fallback_chains()

    def get_tier_model(self, tier: ModelTier) -> str:
        """Returns the active model for a tier."""
        return self.tier_models.get(tier, self.DEFAULT_MODELS[tier])

    def route(
        self,
        role: Optional[str] = None,
        complexity: Optional[Union[TaskComplexity, str]] = None,
        tier: Optional[Union[ModelTier, str]] = None,
        attempt: int = 1,
    ) -> str:
        """
        Dynamically selects the optimal model:
        1. If explicit tier is provided, returns that tier's model.
        2. If attempt >= 2, escalates to REASONING tier.
        3. If complexity is CRITICAL_REASONING, selects REASONING tier.
        4. If complexity is TRIVIAL, selects CHEAP_FAST tier.
        5. If complexity is COMPLEX, selects REASONING (for planning/arch) or CODING.
        6. Default: selects CODING or role-specific model.
        """
        # 1. Explicit tier override
        if tier:
            t = ModelTier(tier) if not isinstance(tier, ModelTier) else tier
            return self.get_tier_model(t)

        # 2. Dynamic escalation on retry attempts
        if attempt >= 2:
            return self.get_tier_model(ModelTier.REASONING)

        # 3. Complexity-based selection
        c = TaskComplexity(complexity) if complexity and not isinstance(complexity, TaskComplexity) else complexity

        if c == TaskComplexity.CRITICAL_REASONING:
            return self.get_tier_model(ModelTier.REASONING)

        if c == TaskComplexity.TRIVIAL:
            return self.get_tier_model(ModelTier.CHEAP_FAST)

        role_clean = (role or "").upper().strip()

        if c == TaskComplexity.COMPLEX:
            if role_clean in ("PLANNER", "ARCHITECTURE", "SPECIFICATION", "REVIEWER"):
                return self.get_tier_model(ModelTier.REASONING)
            return self.get_tier_model(ModelTier.CODING)

        # 4. Standard complexity: route based on agent persona
        if role_clean == "PLANNER":
            return self.get_tier_model(ModelTier.REASONING)
        elif role_clean in ("SPECIFICATION", "ARCHITECTURE"):
            return self.get_tier_model(ModelTier.REASONING)
        elif role_clean == "REVIEWER":
            return self.get_tier_model(ModelTier.REASONING)
        elif role_clean in ("CODER", "TESTER"):
            return self.get_tier_model(ModelTier.CODING)

        return self.get_tier_model(ModelTier.CODING)

    def resolve_model(self, model_or_tier: Optional[Union[str, ModelTier]]) -> str:
        """
        Resolves an input parameter which might be a ModelTier, 'auto', None, or a model string.
        """
        if model_or_tier is None or model_or_tier == "auto" or model_or_tier == "":
            return self.get_tier_model(ModelTier.CODING)

        if isinstance(model_or_tier, ModelTier):
            return self.get_tier_model(model_or_tier)

        # Check if the string matches a ModelTier value
        for tier in ModelTier:
            if model_or_tier.lower() in (tier.value, tier.name.lower()):
                return self.get_tier_model(tier)

        return str(model_or_tier)

    def get_fallback(self, current_model: str, error: Optional[Exception] = None) -> Optional[str]:
        """
        Returns the next fallback model in the chain if one exists and differs from current_model.
        """
        chain = self.fallback_chains.get(current_model, [])
        for candidate in chain:
            if candidate and candidate != current_model:
                return candidate

        fallback_default = self.get_tier_model(ModelTier.FALLBACK)
        if fallback_default != current_model:
            return fallback_default

        return None


# Global singleton ModelRouter
model_router = ModelRouter()
