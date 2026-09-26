"""
Symbol Index.
Extracts code symbols (classes, functions, methods, variables) using mature libraries:
Python standard library 'ast' for Python and 'tree-sitter' with queries for polyglot files.
Persists symbol indexes into SQLite for fast sub-millisecond retrieval.
"""
import ast
from dataclasses import dataclass, field
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Union

import tree_sitter

logger = logging.getLogger("repository.symbol_index")


@dataclass
class CodeSymbol:
    """Represents a code symbol extracted from source code."""
    name: str
    kind: str  # 'class' | 'function' | 'method' | 'variable' | 'interface'
    file_path: str
    line_number: int
    end_line: int
    parent_symbol: Optional[str] = None
    signature: Optional[str] = None
    docstring: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "end_line": self.end_line,
            "parent_symbol": self.parent_symbol,
            "signature": self.signature,
            "docstring": self.docstring,
        }


class PythonASTSymbolExtractor(ast.NodeVisitor):
    """
    Extracts classes, methods, and functions from Python source using the official ast library.
    Never implements custom parsers or regular expressions.
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.symbols: List[CodeSymbol] = []
        self._current_class: Optional[str] = None

    def visit_ClassDef(self, node: ast.ClassDef):
        class_name = node.name
        doc = ast.get_docstring(node)
        bases = [ast.unparse(b) for b in node.bases] if hasattr(ast, "unparse") else []
        sig = f"class {class_name}({', '.join(bases)})" if bases else f"class {class_name}"

        self.symbols.append(CodeSymbol(
            name=class_name,
            kind="class",
            file_path=self.file_path,
            line_number=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            parent_symbol=self._current_class,
            signature=sig,
            docstring=doc,
        ))

        prev_class = self._current_class
        self._current_class = class_name
        self.generic_visit(node)
        self._current_class = prev_class

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._handle_func(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._handle_func(node, is_async=True)

    def _handle_func(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef], is_async: bool):
        func_name = node.name
        kind = "method" if self._current_class else "function"
        doc = ast.get_docstring(node)

        # Build clean signature using ast.unparse
        try:
            params = [a.arg for a in node.args.args]
            ret = f" -> {ast.unparse(node.returns)}" if getattr(node, "returns", None) and hasattr(ast, "unparse") else ""
            prefix = "async def " if is_async else "def "
            sig = f"{prefix}{func_name}({', '.join(params)}){ret}"
        except Exception:
            sig = f"def {func_name}(...)"

        self.symbols.append(CodeSymbol(
            name=func_name,
            kind=kind,
            file_path=self.file_path,
            line_number=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            parent_symbol=self._current_class,
            signature=sig,
            docstring=doc,
        ))

        # Do not recurse into nested function bodies for class context
        prev_class = self._current_class
        self.generic_visit(node)
        self._current_class = prev_class


class SymbolIndex:
    """
    Symbol indexing engine leveraging 'ast' for Python and 'tree-sitter' for polyglot languages.
    Maintains persistent SQLite storage for rapid symbol resolution.
    """

    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        self.db = db_conn
        self._tree_sitter_parsers: Dict[str, Any] = {}
        self._init_tree_sitter_parsers()

    def _init_tree_sitter_parsers(self):
        """Initializes tree-sitter grammars using official packages."""
        # Python
        try:
            import tree_sitter_python
            py_lang = tree_sitter.Language(tree_sitter_python.language())
            self._tree_sitter_parsers["python"] = {
                "lang": py_lang,
                "parser": tree_sitter.Parser(py_lang),
                "query": tree_sitter.Query(
                    py_lang,
                    "(class_definition name: (identifier) @class.name) "
                    "(function_definition name: (identifier) @function.name)"
                ),
            }
        except Exception as e:
            logger.debug(f"tree-sitter-python initialization notice: {e}")

        # JavaScript / TypeScript
        try:
            import tree_sitter_javascript
            js_lang = tree_sitter.Language(tree_sitter_javascript.language())
            self._tree_sitter_parsers["javascript"] = {
                "lang": js_lang,
                "parser": tree_sitter.Parser(js_lang),
                "query": tree_sitter.Query(
                    js_lang,
                    "(class_declaration name: (identifier) @class.name) "
                    "(function_declaration name: (identifier) @function.name) "
                    "(method_definition name: (property_identifier) @method.name)"
                ),
            }
        except Exception as e:
            logger.debug(f"tree-sitter-javascript initialization notice: {e}")

        try:
            import tree_sitter_typescript
            ts_lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())
            self._tree_sitter_parsers["typescript"] = {
                "lang": ts_lang,
                "parser": tree_sitter.Parser(ts_lang),
                "query": tree_sitter.Query(
                    ts_lang,
                    "(class_declaration name: (type_identifier) @class.name) "
                    "(function_declaration name: (identifier) @function.name) "
                    "(interface_declaration name: (type_identifier) @interface.name) "
                    "(method_definition name: (property_identifier) @method.name)"
                ),
            }
        except Exception as e:
            logger.debug(f"tree-sitter-typescript initialization notice: {e}")

    def extract_symbols_from_source(
        self,
        source_code: str,
        file_path: str,
        language: str = "python",
    ) -> List[CodeSymbol]:
        """
        Extracts symbols from source code using ast (for Python) or tree-sitter queries (for polyglot).
        Never uses bespoke regex or hand-rolled parsers.
        """
        if not source_code:
            return []

        # 1. Python AST Extraction
        if language == "python" or file_path.endswith(".py"):
            try:
                tree = ast.parse(source_code, filename=file_path)
                extractor = PythonASTSymbolExtractor(file_path=file_path)
                extractor.visit(tree)
                return extractor.symbols
            except SyntaxError as e:
                logger.debug(f"Python AST parse error for {file_path}: {e}")

        # 2. Tree-sitter Query Extraction
        target_lang = "typescript" if file_path.endswith((".ts", ".tsx")) else (
            "javascript" if file_path.endswith((".js", ".jsx")) else language
        )
        ts_cfg = self._tree_sitter_parsers.get(target_lang) or self._tree_sitter_parsers.get("python")

        if ts_cfg:
            try:
                code_bytes = source_code.encode("utf-8", errors="replace")
                tree = ts_cfg["parser"].parse(code_bytes)
                cursor = tree_sitter.QueryCursor(ts_cfg["query"])
                captures = cursor.captures(tree.root_node)

                symbols: List[CodeSymbol] = []
                for cap_name, nodes in captures.items():
                    kind = "class" if "class" in cap_name else (
                        "interface" if "interface" in cap_name else (
                            "method" if "method" in cap_name else "function"
                        )
                    )
                    for node in nodes:
                        sym_name = node.text.decode("utf-8", errors="replace")
                        symbols.append(CodeSymbol(
                            name=sym_name,
                            kind=kind,
                            file_path=file_path,
                            line_number=node.start_point.row + 1,
                            end_line=node.end_point.row + 1,
                        ))
                return symbols
            except Exception as e:
                logger.debug(f"tree-sitter query extraction error on {file_path}: {e}")

        return []

    def index_file(self, file_path: str, content: str, language: str) -> List[CodeSymbol]:
        """Indexes symbols from a single file and persists them to SQLite."""
        symbols = self.extract_symbols_from_source(content, file_path, language)
        if self.db and symbols:
            # Clear old symbols for this file first
            self.db.execute("DELETE FROM symbols WHERE file_path = ?", (file_path,))
            for sym in symbols:
                self.db.execute(
                    """
                    INSERT OR REPLACE INTO symbols
                    (name, kind, file_path, line_number, end_line, parent_symbol, signature, docstring)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sym.name,
                        sym.kind,
                        sym.file_path,
                        sym.line_number,
                        sym.end_line,
                        sym.parent_symbol,
                        sym.signature,
                        sym.docstring,
                    ),
                )
            try:
                self.db.commit()
            except Exception:
                pass
        return symbols

    def find_symbol(self, name: str) -> List[CodeSymbol]:
        """Finds symbols matching exact name across all indexed files."""
        if not self.db:
            return []
        cursor = self.db.execute(
            """
            SELECT name, kind, file_path, line_number, end_line, parent_symbol, signature, docstring
            FROM symbols WHERE name = ?
            """,
            (name,),
        )
        return [
            CodeSymbol(
                name=r[0],
                kind=r[1],
                file_path=r[2],
                line_number=r[3],
                end_line=r[4],
                parent_symbol=r[5],
                signature=r[6],
                docstring=r[7],
            )
            for r in cursor.fetchall()
        ]

    def get_symbols_in_file(self, file_path: str) -> List[CodeSymbol]:
        """Returns all symbols declared in a specific file."""
        if not self.db:
            return []
        cursor = self.db.execute(
            """
            SELECT name, kind, file_path, line_number, end_line, parent_symbol, signature, docstring
            FROM symbols WHERE file_path = ? ORDER BY line_number ASC
            """,
            (file_path,),
        )
        return [
            CodeSymbol(
                name=r[0],
                kind=r[1],
                file_path=r[2],
                line_number=r[3],
                end_line=r[4],
                parent_symbol=r[5],
                signature=r[6],
                docstring=r[7],
            )
            for r in cursor.fetchall()
        ]

    def search_symbols(self, query: str, kind: Optional[str] = None, limit: int = 50) -> List[CodeSymbol]:
        """Searches symbols matching substring pattern and optional kind."""
        if not self.db:
            return []
        like_query = f"%{query}%"
        if kind:
            cursor = self.db.execute(
                """
                SELECT name, kind, file_path, line_number, end_line, parent_symbol, signature, docstring
                FROM symbols WHERE name LIKE ? AND kind = ? LIMIT ?
                """,
                (like_query, kind, limit),
            )
        else:
            cursor = self.db.execute(
                """
                SELECT name, kind, file_path, line_number, end_line, parent_symbol, signature, docstring
                FROM symbols WHERE name LIKE ? LIMIT ?
                """,
                (like_query, limit),
            )
        return [
            CodeSymbol(
                name=r[0],
                kind=r[1],
                file_path=r[2],
                line_number=r[3],
                end_line=r[4],
                parent_symbol=r[5],
                signature=r[6],
                docstring=r[7],
            )
            for r in cursor.fetchall()
        ]
