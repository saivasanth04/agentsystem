"""
Codebase Understanding & Structural Intelligence Package.
Provides polyglot AST parsing, resolved call/dependency graphs, architecture analysis,
semantic/BM25 indexing, PageRank repo maps, and incremental SQLite caching.
"""
from .symbols import (
    SymbolNode,
    SymbolKind,
    ReferenceEdge,
    ReferenceKind,
    CallEdge,
    DependencyNode,
)
from .parser import (
    BaseCodeParser,
    PythonASTParser,
    PolyglotRegexParser,
    ParserRegistry,
)
from .graph import CodebaseGraph
from .architecture import (
    ArchitectureAnalyzer,
    ArchitectureSummary,
)
from .semantic_index import (
    SemanticCodeIndex,
    CodeChunk,
)
from .repo_map import PageRankRepoMap
from .cache import IncrementalCodeCache
from .cbm import CodebaseMemory, codebase_memory

__all__ = [
    "SymbolNode",
    "SymbolKind",
    "ReferenceEdge",
    "ReferenceKind",
    "CallEdge",
    "DependencyNode",
    "BaseCodeParser",
    "PythonASTParser",
    "PolyglotRegexParser",
    "ParserRegistry",
    "CodebaseGraph",
    "ArchitectureAnalyzer",
    "ArchitectureSummary",
    "SemanticCodeIndex",
    "CodeChunk",
    "PageRankRepoMap",
    "IncrementalCodeCache",
    "CodebaseMemory",
    "codebase_memory",
]
