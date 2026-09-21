"""
Code Knowledge Graph & AST Relational Retrieval Engine.
Backward-compatible wrapper around agent_orchestrator.codebase.CodebaseGraph.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..codebase.symbols import ReferenceEdge, SymbolNode
from ..codebase.graph import CodebaseGraph, IGNORE_DIRS


class CodeGraphEngine(CodebaseGraph):
    """
    Backward-compatible facade for CodebaseGraph.
    Enables multi-hop code retrieval: Symbol Definition -> References -> Dependencies -> Codebase Map.
    """

    def __init__(self, workspace_dir: Path, *args, **kwargs):
        super().__init__(workspace_dir=workspace_dir, *args, **kwargs)


__all__ = ["CodeGraphEngine", "SymbolNode", "ReferenceEdge", "IGNORE_DIRS"]
