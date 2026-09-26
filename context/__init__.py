"""
Context Optimization Package.
Replaces static prompt concatenation with an active, budget-enforced Context Compiler Pipeline.
Powered by tiktoken, numpy, and rapidfuzz.
"""
from .budget import TokenCounter, BudgetAllocation, ContextBudgetManager
from .ranking import ContextRanker, RankedCandidate
from .dedup import ContextDeduplicator
from .pack import SkillSectionSlicer, RepositorySlicer, ConversationSlicer, OptimizedContextPackage
from .compiler import ContextCompiler

__all__ = [
    "TokenCounter",
    "BudgetAllocation",
    "ContextBudgetManager",
    "ContextRanker",
    "RankedCandidate",
    "ContextDeduplicator",
    "SkillSectionSlicer",
    "RepositorySlicer",
    "ConversationSlicer",
    "OptimizedContextPackage",
    "ContextCompiler",
]
