"""
Resilience & Fault-Tolerance Package for LLM Interactions.
Provides typed error classification, full-jitter exponential backoff,
header-aware rate-limit handling, multi-provider failover, and reactive context compaction.
"""
from .error_classifier import LLMErrorCategory, classify_error
from .retry_policy import LLMRetryPolicy
from .provider_pool import ProviderEndpoint, ProviderFailoverPool
from .resilient_caller import ResilientLLMCaller

__all__ = [
    "LLMErrorCategory",
    "classify_error",
    "LLMRetryPolicy",
    "ProviderEndpoint",
    "ProviderFailoverPool",
    "ResilientLLMCaller",
]
