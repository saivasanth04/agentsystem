"""
Backward-compatibility delegation shim for budget_allocator.
Authoritative implementation resides in context/budget.py.
"""
from context.budget import (
    ContextBudget,
    ContextSection,
    ContextAssembler,
    TokenCounter,
    ContextBudgetManager,
    estimate_tokens,
)

__all__ = [
    "ContextBudget",
    "ContextSection",
    "ContextAssembler",
    "TokenCounter",
    "ContextBudgetManager",
    "estimate_tokens",
]
