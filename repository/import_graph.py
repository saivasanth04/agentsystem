"""
Import & Dependency Graph.
Extracts module dependencies using Python standard library 'ast',
constructs a directed dependency graph using 'networkx',
and persists import edges into SQLite.
"""
import ast
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import networkx as nx

logger = logging.getLogger("repository.import_graph")


@dataclass
class ImportEdge:
    """Represents a single import statement dependency."""
    source_file: str
    imported_module: str
    imported_symbol: Optional[str] = None
    alias: Optional[str] = None
    line_number: int = 1
    is_relative: bool = False
    target_file: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_file": self.source_file,
            "imported_module": self.imported_module,
            "imported_symbol": self.imported_symbol,
            "alias": self.alias,
            "line_number": self.line_number,
            "is_relative": self.is_relative,
            "target_file": self.target_file,
        }


class PythonASTImportExtractor(ast.NodeVisitor):
    """
    Extracts import statements from Python AST using standard ast.NodeVisitor.
    Never relies on regular expressions.
    """

    def __init__(self, source_file: str):
        self.source_file = source_file
        self.imports: List[ImportEdge] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append(ImportEdge(
                source_file=self.source_file,
                imported_module=alias.name,
                alias=alias.asname,
                line_number=node.lineno,
                is_relative=False,
            ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = node.module or ""
        is_rel = (node.level or 0) > 0
        dots = "." * (node.level or 0) if is_rel else ""
        full_mod = f"{dots}{mod}" if dots else mod

        for alias in node.names:
            self.imports.append(ImportEdge(
                source_file=self.source_file,
                imported_module=full_mod,
                imported_symbol=alias.name,
                alias=alias.asname,
                line_number=node.lineno,
                is_relative=is_rel,
            ))
        self.generic_visit(node)


class ImportGraph:
    """
    Dependency and Import Graph powered by networkx.DiGraph.
    Tracks inter-module relationships, cycles, impact radii, and build order.
    """

    def __init__(self, root_dir: Union[str, Path], db_conn: Optional[sqlite3.Connection] = None):
        self.root_dir = Path(root_dir).resolve()
        self.db = db_conn
        self.graph = nx.DiGraph()

    def resolve_import_to_file(self, source_file: str, imported_module: str) -> Optional[str]:
        """
        Resolves an imported module name into a concrete workspace file path if possible.
        """
        source_p = Path(source_file)
        source_dir = (self.root_dir / source_p).parent

        # 1. Handle relative imports (e.g. '..registry.skill_registry' or '.policy')
        if imported_module.startswith("."):
            level = 0
            while imported_module.startswith("."):
                level += 1
                imported_module = imported_module[1:]
            
            target_dir = source_dir
            for _ in range(level - 1):
                target_dir = target_dir.parent

            rel_mod_path = imported_module.replace(".", "/")
            candidates = [
                target_dir / f"{rel_mod_path}.py",
                target_dir / rel_mod_path / "__init__.py",
            ]
            for c in candidates:
                if c.exists() and c.is_file():
                    try:
                        return c.relative_to(self.root_dir).as_posix()
                    except ValueError:
                        pass

        # 2. Handle absolute module imports relative to workspace root
        mod_as_path = imported_module.replace(".", "/")
        candidates = [
            self.root_dir / f"{mod_as_path}.py",
            self.root_dir / mod_as_path / "__init__.py",
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                try:
                    return c.relative_to(self.root_dir).as_posix()
                except ValueError:
                    pass

        return None

    def extract_imports_from_source(self, source_code: str, file_path: str) -> List[ImportEdge]:
        """Extracts imports using Python standard library ast."""
        if not source_code or not file_path.endswith(".py"):
            return []
        try:
            tree = ast.parse(source_code, filename=file_path)
            extractor = PythonASTImportExtractor(source_file=file_path)
            extractor.visit(tree)
            # Resolve target files
            for imp in extractor.imports:
                imp.target_file = self.resolve_import_to_file(file_path, imp.imported_module)
            return extractor.imports
        except SyntaxError as e:
            logger.debug(f"SyntaxError parsing imports in {file_path}: {e}")
            return []

    def index_file_imports(self, file_path: str, content: str) -> List[ImportEdge]:
        """Extracts and registers imports for a file, updating the networkx graph and SQLite."""
        imports = self.extract_imports_from_source(content, file_path)

        posix_src = Path(file_path).as_posix()
        self.graph.add_node(posix_src)

        if self.db and imports:
            self.db.execute("DELETE FROM imports WHERE source_file = ?", (posix_src,))

        for imp in imports:
            if imp.target_file:
                self.graph.add_edge(posix_src, imp.target_file, module=imp.imported_module, symbol=imp.imported_symbol)

            if self.db:
                self.db.execute(
                    """
                    INSERT OR REPLACE INTO imports
                    (source_file, imported_module, imported_symbol, alias, line_number, is_relative, target_file)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        posix_src,
                        imp.imported_module,
                        imp.imported_symbol,
                        imp.alias,
                        imp.line_number,
                        1 if imp.is_relative else 0,
                        imp.target_file,
                    ),
                )

        if self.db:
            try:
                self.db.commit()
            except Exception:
                pass

        return imports

    def get_dependencies(self, file_path: str, recursive: bool = False) -> List[str]:
        """Returns files that the given file depends upon (successors in import graph)."""
        posix_path = Path(file_path).as_posix()
        if posix_path not in self.graph:
            return []
        if recursive:
            return sorted(list(nx.descendants(self.graph, posix_path)))
        return sorted(list(self.graph.successors(posix_path)))

    def get_dependents(self, file_path: str, recursive: bool = False) -> List[str]:
        """Returns files that depend on the given file (predecessors in import graph)."""
        posix_path = Path(file_path).as_posix()
        if posix_path not in self.graph:
            return []
        if recursive:
            return sorted(list(nx.ancestors(self.graph, posix_path)))
        return sorted(list(self.graph.predecessors(posix_path)))

    def get_circular_dependencies(self) -> List[List[str]]:
        """Detects circular import cycles using networkx.simple_cycles."""
        try:
            return list(nx.simple_cycles(self.graph))
        except Exception:
            return []

    def get_topological_order(self) -> List[str]:
        """Returns topological build order using networkx."""
        try:
            return list(nx.topological_sort(self.graph))
        except (nx.NetworkXUnfeasible, Exception):
            # If cycles exist, return condensed component nodes or standard node list
            return list(self.graph.nodes())

    def get_isolated_files(self) -> List[str]:
        """Returns files that have no inbound or outbound dependencies."""
        return list(nx.isolates(self.graph))
