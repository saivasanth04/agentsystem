"""
Backward-compatibility delegation shim for relevance_ranker.
Authoritative implementation resides in context/ranking.py.
"""
from context.ranking import (
    ContextTier,
    RankedContextItem,
    RelevanceRanker,
    ContextRanker,
    RankedCandidate,
)

__all__ = [
    "ContextTier",
    "RankedContextItem",
    "RelevanceRanker",
    "ContextRanker",
    "RankedCandidate",
]
