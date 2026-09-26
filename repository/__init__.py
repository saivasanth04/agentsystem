"""
Repository Intelligence Package.
Provides persistent codebase intelligence using mature libraries:
ast, tree-sitter, GitPython, networkx, watchfiles, and sqlite3.
"""
from .scanner import RepositoryScanner, ScannedFile, GitMetadata
from .symbol_index import SymbolIndex, CodeSymbol
from .import_graph import ImportGraph, ImportEdge
from .call_graph import CallGraph, CallEdge
from .runtime_detector import RuntimeDetector, RuntimeProfile
from .architecture_index import ArchitectureIndex, ArchitectureComponent
from .repository_brain import RepositoryBrain

__all__ = [
    "RepositoryScanner",
    "ScannedFile",
    "GitMetadata",
    "SymbolIndex",
    "CodeSymbol",
    "ImportGraph",
    "ImportEdge",
    "CallGraph",
    "CallEdge",
    "RuntimeDetector",
    "RuntimeProfile",
    "ArchitectureIndex",
    "ArchitectureComponent",
    "RepositoryBrain",
]
