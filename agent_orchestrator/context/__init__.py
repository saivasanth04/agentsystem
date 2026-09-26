"""
Context Management, Hierarchical Scoping, Context Budgeting, Compression & Deduplication.
"""
from .budget_allocator import (
    ContextBudget,
    ContextSection,
    ContextAssembler,
)
from .compressor import (
    CompressionLevel,
    CodeCompressor,
    JSONCompressor,
    LogCompressor,
    ContextCompressor,
)
from .deduplicator import ContentDeduplicator
from .isolation import (
    IsolatedTaskContext,
    ContextIsolationEngine,
)
from .relevance_ranker import (
    ContextTier,
    RankedContextItem,
    RelevanceRanker,
)
from .retrieval_hierarchy import (
    HierarchyBudgetConfig,
    HierarchicalContextBundle,
    HierarchicalContextItem,
    RetrievalHierarchyEngine,
    RetrievalLevel,
)
from context.budget import TokenCounter, ContextBudgetManager, BudgetAllocation
from context.ranking import ContextRanker, RankedCandidate
from context.dedup import ContextDeduplicator
from context.pack import SkillSectionSlicer, RepositorySlicer, ConversationSlicer, OptimizedContextPackage
from context.compiler import ContextCompiler

__all__ = [
    "ContextBudget",
    "ContextSection",
    "ContextAssembler",
    "CompressionLevel",
    "CodeCompressor",
    "JSONCompressor",
    "LogCompressor",
    "ContextCompressor",
    "ContentDeduplicator",
    "IsolatedTaskContext",
    "ContextIsolationEngine",
    "ContextTier",
    "RankedContextItem",
    "RelevanceRanker",
    "HierarchyBudgetConfig",
    "HierarchicalContextBundle",
    "HierarchicalContextItem",
    "RetrievalHierarchyEngine",
    "RetrievalLevel",
    "TokenCounter",
    "ContextBudgetManager",
    "BudgetAllocation",
    "ContextRanker",
    "RankedCandidate",
    "ContextDeduplicator",
    "SkillSectionSlicer",
    "RepositorySlicer",
    "ConversationSlicer",
    "OptimizedContextPackage",
    "ContextCompiler",
]

