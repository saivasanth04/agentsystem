"""
Codebase Symbol, Edge, and Dependency Data Structures.
Provides typed definitions for AST symbols, reference edges, call edges, and module dependencies.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SymbolKind(str, Enum):
    CLASS = "class"
    METHOD = "method"
    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"
    VARIABLE = "variable"
    INTERFACE = "interface"
    TYPE_ALIAS = "type_alias"


class ReferenceKind(str, Enum):
    IMPORT = "import"
    CALL = "call"
    USAGE = "usage"
    BASE_CLASS = "base_class"
    TYPE_ANNOTATION = "type_annotation"


@dataclass
class SymbolNode:
    name: str
    kind: str  # class | function | method | async_function | interface | variable | type_alias
    filepath: str
    start_line: int
    end_line: int
    parent: Optional[str] = None  # Class name if method
    signature: str = ""
    docstring: str = ""
    bases: List[str] = field(default_factory=list)
    calls: List[str] = field(default_factory=list)
    qualified_name: str = ""
    decorators: List[str] = field(default_factory=list)
    parameters: List[str] = field(default_factory=list)
    return_type: str = ""

    def __post_init__(self):
        if not self.qualified_name:
            mod = self.filepath.replace("/", ".").replace("\\", ".").replace(".py", "").replace(".ts", "").replace(".js", "")
            if self.parent:
                self.qualified_name = f"{mod}.{self.parent}.{self.name}"
            else:
                self.qualified_name = f"{mod}.{self.name}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "filepath": self.filepath,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "parent": self.parent,
            "signature": self.signature,
            "docstring": self.docstring,
            "bases": self.bases,
            "calls": self.calls,
            "qualified_name": self.qualified_name,
            "decorators": self.decorators,
            "parameters": self.parameters,
            "return_type": self.return_type,
        }


@dataclass
class ReferenceEdge:
    symbol_name: str
    filepath: str
    line: int
    kind: str  # import | call | usage | base_class | type_annotation
    context_snippet: str = ""
    target_filepath: Optional[str] = None
    target_qualified_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol_name": self.symbol_name,
            "filepath": self.filepath,
            "line": self.line,
            "kind": self.kind,
            "context_snippet": self.context_snippet,
            "target_filepath": self.target_filepath,
            "target_qualified_name": self.target_qualified_name,
        }


@dataclass
class CallEdge:
    caller_symbol: str  # Qualified name of caller
    callee_name: str    # Function/method name called
    caller_filepath: str
    line: int
    callee_symbol: Optional[str] = None  # Resolved qualified name if found
    target_filepath: Optional[str] = None
    resolved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "caller_symbol": self.caller_symbol,
            "callee_name": self.callee_name,
            "caller_filepath": self.caller_filepath,
            "line": self.line,
            "callee_symbol": self.callee_symbol,
            "target_filepath": self.target_filepath,
            "resolved": self.resolved,
        }


@dataclass
class DependencyNode:
    filepath: str
    imports: List[str] = field(default_factory=list)
    imported_modules: List[str] = field(default_factory=list)
    dependent_files: List[str] = field(default_factory=list)
    defined_symbols: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filepath": self.filepath,
            "imports": self.imports,
            "imported_modules": self.imported_modules,
            "dependent_files": self.dependent_files,
            "defined_symbols": self.defined_symbols,
        }


def is_module_import_match(imp: str, target_fp: str, current_fp: Optional[str] = None) -> bool:
    """
    Deterministically evaluates whether an import identifier `imp` targets `target_fp`.
    Guards against catastrophic false-positive substring matches (e.g. 're' matching 'relevance_ranker').
    """
    if not imp or not target_fp:
        return False

    clean_imp = imp.lstrip(".").strip()
    if not clean_imp:
        return False

    from pathlib import Path
    tgt_norm = target_fp.replace("\\", "/").strip("/")
    tgt_stem = Path(tgt_norm).stem
    tgt_mod = tgt_norm.replace("/", ".").replace(".py", "").replace(".ts", "").replace(".js", "")

    # 1. Exact module path match
    if clean_imp == tgt_mod:
        return True

    # 2. Exact stem match (e.g. 'relevance_ranker')
    if clean_imp == tgt_stem:
        if current_fp:
            curr_dir = str(Path(current_fp.replace("\\", "/")).parent).replace("\\", "/")
            tgt_dir = str(Path(tgt_norm).parent).replace("\\", "/")
            if curr_dir == tgt_dir or imp.startswith("."):
                return True
        return True

    # 3. Suffix dot-boundary match (e.g. 'context.relevance_ranker' matches 'agent_orchestrator.context.relevance_ranker')
    if clean_imp.endswith("." + tgt_stem) and (tgt_mod == clean_imp or tgt_mod.endswith("." + clean_imp)):
        return True

    # 4. Target module is a submodule of target package
    if tgt_mod.endswith("." + clean_imp):
        return True

    return False

