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
