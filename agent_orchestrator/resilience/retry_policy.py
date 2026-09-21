"""
Configurable Retry Policies and Full-Jitter Backoff Calculator.
Implements the standard full-jitter exponential backoff algorithm:
sleep = uniform(0, min(max_delay, base_delay * 2^attempt))
"""
from dataclasses import dataclass
import random
from typing import Optional

from .error_classifier import LLMErrorCategory


@dataclass
class LLMRetryPolicy:
    """
    Retry policy configuration for LLM interactions.
    """
    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    jitter: bool = True
    rate_limit_multiplier: float = 1.5

    def is_retryable(self, category: LLMErrorCategory, attempt: int) -> bool:
        """Determines if an error category is retryable given current attempt count."""
        if attempt >= self.max_retries:
            return False
        if category == LLMErrorCategory.NON_RETRYABLE:
            return False
        return True

    def calculate_delay(
        self,
        attempt: int,
        category: LLMErrorCategory,
        retry_after: Optional[float] = None,
    ) -> float:
        """
        Calculates backoff delay in seconds.
        Prioritizes provider-specified retry_after with jitter buffer.
        """
        # Context overflow and non-retryable errors require no backoff
        if category in (LLMErrorCategory.NON_RETRYABLE, LLMErrorCategory.CONTEXT_OVERFLOW):
            return 0.0

        # Provider Retry-After header takes precedence
        if retry_after is not None and retry_after > 0:
            if self.jitter:
                return round(retry_after + random.uniform(0.1, 0.5), 3)
            return round(retry_after, 3)

        # Full-jitter exponential backoff
        mult = self.rate_limit_multiplier if category == LLMErrorCategory.RATE_LIMIT else 1.0
        calculated_cap = min(self.max_delay, (2 ** attempt) * self.base_delay * mult)

        if self.jitter:
            delay = random.uniform(0.05, calculated_cap)
        else:
            delay = calculated_cap

        return round(delay, 3)
