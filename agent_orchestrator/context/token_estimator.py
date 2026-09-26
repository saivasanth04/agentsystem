"""
Backward-compatibility delegation shim for token_estimator.
Authoritative implementation resides in context/budget.py.
"""
from context.budget import estimate_tokens, TokenCounter

__all__ = [
    "estimate_tokens",
    "TokenCounter",
]
