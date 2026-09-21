"""
Polyglot Code Parsers and Symbol Extractors.
Provides Python AST parsing and regex-based extraction for TypeScript, JavaScript, Go, Rust, and Java.
"""
import ast
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set, Tuple

from .symbols import ReferenceEdge, ReferenceKind, SymbolKind, SymbolNode


class BaseCodeParser(ABC):
    """Abstract interface for language-specific symbol and reference extractors."""

    @abstractmethod
    def parse(
        self, content: str, filepath: str
    ) -> Tuple[List[SymbolNode], List[ReferenceEdge], Set[str]]:
        """
        Parses source content and returns:
          - List of defined SymbolNode
          - List of ReferenceEdge (imports, calls, usages)
          - Set of imported module identifiers
        """
        pass


class PythonASTParser(BaseCodeParser):
    """AST-based parser for Python source files."""

    def parse(
        self, content: str, filepath: str
    ) -> Tuple[List[SymbolNode], List[ReferenceEdge], Set[str]]:
        symbols: List[SymbolNode] = []
        references: List[ReferenceEdge] = []
        imported_modules: Set[str] = set()

        try:
            tree = ast.parse(content, filename=filepath)
        except Exception:
            return symbols, references, imported_modules

        lines = content.splitlines()

        class ASTVisitor(ast.NodeVisitor):
            def __init__(self):
                self.current_class: Optional[str] = None

            def visit_ClassDef(self, node: ast.ClassDef):
                doc = ast.get_docstring(node) or ""
                bases = [self._format_node_name(b) for b in node.bases]
                decorators = [self._format_node_name(d) for d in node.decorator_list]
                start = node.lineno
                end = getattr(node, "end_lineno", start)
                sig = f"class {node.name}" + (f"({', '.join(bases)})" if bases else "") + ":"

                sym = SymbolNode(
                    name=node.name,
                    kind=SymbolKind.CLASS.value,
                    filepath=filepath,
                    start_line=start,
                    end_line=end,
                    parent=self.current_class,
                    signature=sig,
                    docstring=doc.splitlines()[0] if doc else "",
                    bases=bases,
                    decorators=decorators,
                )
                symbols.append(sym)

                # Record base class references
                for b_name in bases:
                    if b_name:
                        references.append(ReferenceEdge(
                            symbol_name=b_name,
                            filepath=filepath,
                            line=start,
                            kind=ReferenceKind.BASE_CLASS.value,
                            context_snippet=lines[start - 1].strip() if start <= len(lines) else "",
                        ))

                prev_class = self.current_class
                self.current_class = node.name
                self.generic_visit(node)
                self.current_class = prev_class

            def visit_FunctionDef(self, node: ast.FunctionDef):
                self._record_func(node, is_async=False)
                self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                self._record_func(node, is_async=True)
                self.generic_visit(node)

            def _record_func(self, node: Any, is_async: bool):
                doc = ast.get_docstring(node) or ""
                start = node.lineno
                end = getattr(node, "end_lineno", start)
                kind = (
                    SymbolKind.METHOD.value
                    if self.current_class
                    else (SymbolKind.ASYNC_FUNCTION.value if is_async else SymbolKind.FUNCTION.value)
                )

                # Parameters
                params = []
                for a in node.args.args:
                    p_str = a.arg
                    if a.annotation:
                        p_str += f": {self._format_node_name(a.annotation)}"
                    params.append(p_str)

                # Return type
                ret_type = self._format_node_name(node.returns) if getattr(node, "returns", None) else ""
                decorators = [self._format_node_name(d) for d in node.decorator_list]

                prefix = "async def " if is_async else "def "
                sig = f"{prefix}{node.name}({', '.join(params)})"
                if ret_type:
                    sig += f" -> {ret_type}:"
                else:
                    sig += ":"

                # Calls inside function body
                calls = []
                for subnode in ast.walk(node):
                    if isinstance(subnode, ast.Call):
                        fn_name = self._format_call_name(subnode.func)
                        if fn_name and fn_name not in calls:
                            calls.append(fn_name)

                sym = SymbolNode(
                    name=node.name,
                    kind=kind,
                    filepath=filepath,
                    start_line=start,
                    end_line=end,
                    parent=self.current_class,
                    signature=sig,
                    docstring=doc.splitlines()[0] if doc else "",
                    calls=calls,
                    decorators=decorators,
                    parameters=params,
                    return_type=ret_type,
                )
                symbols.append(sym)

            def _format_node_name(self, node: Optional[ast.AST]) -> str:
                if not node:
                    return ""
                if isinstance(node, ast.Name):
                    return node.id
                elif isinstance(node, ast.Attribute):
                    return f"{self._format_node_name(node.value)}.{node.attr}"
                elif isinstance(node, ast.Constant):
                    return str(node.value)
                elif isinstance(node, ast.Call):
                    return f"{self._format_node_name(node.func)}()"
                elif isinstance(node, ast.Subscript):
                    return f"{self._format_node_name(node.value)}[{self._format_node_name(node.slice)}]"
                return ""

            def _format_call_name(self, func_node: ast.AST) -> str:
                if isinstance(func_node, ast.Name):
                    return func_node.id
                elif isinstance(func_node, ast.Attribute):
                    return func_node.attr
                return ""

        visitor = ASTVisitor()
        visitor.visit(tree)

        # Extract Imports & Calls across file
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
                    references.append(ReferenceEdge(
                        symbol_name=alias.name,
                        filepath=filepath,
                        line=node.lineno,
                        kind=ReferenceKind.IMPORT.value,
                        context_snippet=lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                    ))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod:
                    imported_modules.add(mod)
                for alias in node.names:
                    imported_modules.add(alias.name)
                    if alias.asname:
                        imported_modules.add(alias.asname)
                    full_name = f"{mod}.{alias.name}" if mod else alias.name
                    if full_name:
                        imported_modules.add(full_name)
                    references.append(ReferenceEdge(
                        symbol_name=alias.asname or alias.name,
                        filepath=filepath,
                        line=node.lineno,
                        kind=ReferenceKind.IMPORT.value,
                        context_snippet=lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                    ))
            elif isinstance(node, ast.Call):
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name:
                    references.append(ReferenceEdge(
                        symbol_name=name,
                        filepath=filepath,
                        line=node.lineno,
                        kind=ReferenceKind.CALL.value,
                        context_snippet=lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                    ))

        return symbols, references, imported_modules


class PolyglotRegexParser(BaseCodeParser):
    """Regex-based parser for TypeScript, JavaScript, Go, Rust, Java, and other languages."""

    def parse(
        self, content: str, filepath: str
    ) -> Tuple[List[SymbolNode], List[ReferenceEdge], Set[str]]:
        symbols: List[SymbolNode] = []
        references: List[ReferenceEdge] = []
        imported_modules: Set[str] = set()

        lines = content.splitlines()

        # TypeScript / JavaScript Patterns
        # Classes: class Foo [extends Bar] [implements Baz]
        ts_class = re.compile(r"^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z0-9_]+)(?:\s+extends\s+([A-Za-z0-9_]+))?(?:\s+implements\s+([A-Za-z0-9_,\s]+))?")
        # Interfaces: interface IFoo [extends Bar]
        ts_interface = re.compile(r"^(?:export\s+)?interface\s+([A-Za-z0-9_]+)(?:\s+extends\s+([A-Za-z0-9_]+))?")
        # Functions: function foo(param: type): returnType
        ts_func = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)\s*\((.*?)\)(?:\s*:\s*([^{]+))?")
        # Arrow functions / const: const foo = (param) => ...
        ts_arrow = re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s*)?\((.*?)\)(?:\s*:\s*([^{=]+))?\s*=>")
        # Imports: import { a, b } from 'foo' or import foo from 'bar'
        ts_import = re.compile(r"""(?:import\s+(?:\{([^}]+)\}|([A-Za-z0-9_]+))\s+from\s+['"]([^'"]+)['"])|(?:const\s+.*=\s*require\(['"]([^'"]+)['"]\))""")

        # Go Patterns
        # func (r *Receiver) Method(args) (ret)
        go_method = re.compile(r"^func\s+\((?:[A-Za-z0-9_]+\s+)?\*?([A-Za-z0-9_]+)\)\s+([A-Za-z0-9_]+)\s*\((.*?)\)")
        # func Function(args) (ret)
        go_func = re.compile(r"^func\s+([A-Za-z0-9_]+)\s*\((.*?)\)")
        # type Foo struct / interface
        go_type = re.compile(r"^type\s+([A-Za-z0-9_]+)\s+(struct|interface)")
        # import "foo/bar"
        go_import = re.compile(r"""(?:import\s+['"]([^'"]+)['"])|(?:import\s+\(([\s\S]*?)\))""")

        # Rust Patterns
        # fn foo(...)
        rs_func = re.compile(r"^(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z0-9_]+)\s*\((.*?)\)")
        # struct / enum / trait Foo
        rs_type = re.compile(r"^(?:pub\s+)?(struct|enum|trait)\s+([A-Za-z0-9_]+)")
        # use foo::bar;
        rs_import = re.compile(r"^use\s+([A-Za-z0-9_:]+)")

        for idx, line in enumerate(lines):
            line_no = idx + 1
            stripped = line.strip()

            # 1. TypeScript / JavaScript
            m = ts_class.match(stripped)
            if m:
                c_name = m.group(1)
                bases = [m.group(2)] if m.group(2) else []
                symbols.append(SymbolNode(
                    name=c_name,
                    kind=SymbolKind.CLASS.value,
                    filepath=filepath,
                    start_line=line_no,
                    end_line=line_no,
                    signature=stripped[:120],
                    bases=bases,
                ))
                continue

            m = ts_interface.match(stripped)
            if m:
                i_name = m.group(1)
                symbols.append(SymbolNode(
                    name=i_name,
                    kind=SymbolKind.INTERFACE.value,
                    filepath=filepath,
                    start_line=line_no,
                    end_line=line_no,
                    signature=stripped[:120],
                ))
                continue

            m = ts_func.match(stripped)
            if m:
                f_name = m.group(1)
                symbols.append(SymbolNode(
                    name=f_name,
                    kind=SymbolKind.FUNCTION.value,
                    filepath=filepath,
                    start_line=line_no,
                    end_line=line_no,
                    signature=stripped[:120],
                ))
                continue

            m = ts_arrow.match(stripped)
            if m:
                a_name = m.group(1)
                symbols.append(SymbolNode(
                    name=a_name,
                    kind=SymbolKind.FUNCTION.value,
                    filepath=filepath,
                    start_line=line_no,
                    end_line=line_no,
                    signature=stripped[:120],
                ))
                continue

            m = ts_import.search(stripped)
            if m:
                named = m.group(1)
                default_imp = m.group(2)
                from_mod = m.group(3) or m.group(4)
                if from_mod:
                    imported_modules.add(from_mod)
                if named:
                    for s in named.split(","):
                        sym = s.strip().split(" as ")[0].strip()
                        if sym:
                            imported_modules.add(sym)
                            references.append(ReferenceEdge(
                                symbol_name=sym,
                                filepath=filepath,
                                line=line_no,
                                kind=ReferenceKind.IMPORT.value,
                                context_snippet=stripped,
                            ))
                if default_imp:
                    imported_modules.add(default_imp)
                    references.append(ReferenceEdge(
                        symbol_name=default_imp,
                        filepath=filepath,
                        line=line_no,
                        kind=ReferenceKind.IMPORT.value,
                        context_snippet=stripped,
                    ))
                continue

            # 2. Go
            m = go_method.match(stripped)
            if m:
                recv = m.group(1)
                m_name = m.group(2)
                symbols.append(SymbolNode(
                    name=m_name,
                    kind=SymbolKind.METHOD.value,
                    filepath=filepath,
                    start_line=line_no,
                    end_line=line_no,
                    parent=recv,
                    signature=stripped[:120],
                ))
                continue

            m = go_func.match(stripped)
            if m:
                f_name = m.group(1)
                symbols.append(SymbolNode(
                    name=f_name,
                    kind=SymbolKind.FUNCTION.value,
                    filepath=filepath,
                    start_line=line_no,
                    end_line=line_no,
                    signature=stripped[:120],
                ))
                continue

            m = go_type.match(stripped)
            if m:
                t_name = m.group(1)
                t_kind = m.group(2)
                symbols.append(SymbolNode(
                    name=t_name,
                    kind=SymbolKind.CLASS.value if t_kind == "struct" else SymbolKind.INTERFACE.value,
                    filepath=filepath,
                    start_line=line_no,
                    end_line=line_no,
                    signature=stripped[:120],
                ))
                continue

            # 3. Rust
            m = rs_func.match(stripped)
            if m:
                f_name = m.group(1)
                symbols.append(SymbolNode(
                    name=f_name,
                    kind=SymbolKind.FUNCTION.value,
                    filepath=filepath,
                    start_line=line_no,
                    end_line=line_no,
                    signature=stripped[:120],
                ))
                continue

            m = rs_type.match(stripped)
            if m:
                kind_str = m.group(1)
                t_name = m.group(2)
                symbols.append(SymbolNode(
                    name=t_name,
                    kind=SymbolKind.CLASS.value if kind_str == "struct" else SymbolKind.INTERFACE.value,
                    filepath=filepath,
                    start_line=line_no,
                    end_line=line_no,
                    signature=stripped[:120],
                ))
                continue

            m = rs_import.match(stripped)
            if m:
                imp = m.group(1)
                imported_modules.add(imp)
                references.append(ReferenceEdge(
                    symbol_name=imp.split("::")[-1],
                    filepath=filepath,
                    line=line_no,
                    kind=ReferenceKind.IMPORT.value,
                    context_snippet=stripped,
                ))

        return symbols, references, imported_modules


class ParserRegistry:
    """Selects and caches language parsers based on file extension."""

    def __init__(self):
        self._python_parser = PythonASTParser()
        self._polyglot_parser = PolyglotRegexParser()

        self._ext_map: Dict[str, BaseCodeParser] = {
            ".py": self._python_parser,
            ".ts": self._polyglot_parser,
            ".tsx": self._polyglot_parser,
            ".js": self._polyglot_parser,
            ".jsx": self._polyglot_parser,
            ".mjs": self._polyglot_parser,
            ".cjs": self._polyglot_parser,
            ".go": self._polyglot_parser,
            ".rs": self._polyglot_parser,
            ".java": self._polyglot_parser,
            ".sql": self._polyglot_parser,
        }

    def get_parser(self, filepath: str) -> Optional[BaseCodeParser]:
        """Returns the appropriate parser for the file, or None if unsupported."""
        for ext, parser in self._ext_map.items():
            if filepath.endswith(ext):
                return parser
        return None

    def is_supported(self, filepath: str) -> bool:
        return any(filepath.endswith(ext) for ext in self._ext_map)
