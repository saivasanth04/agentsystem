"""
Codebase Memory & Knowledge Graph MCP Server.
Provides AST symbol indexing, semantic code search, and session memory storage over MCP.
"""
import ast
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base_server import BaseMCPServer


class CodebaseMemoryMCPServer(BaseMCPServer):
    """
    Standard MCP Codebase Memory Server.
    Maintains symbol tables, AST hierarchies, and episodic memory notes for agents.
    """

    def __init__(self, workspace_dir: Path, name: str = "mcp-server-memory"):
        super().__init__(
            name=name,
            version="1.0.0",
            description="Codebase AST symbol indexing, relationship mapping, and persistent agent memory.",
        )
        self.workspace_dir = Path(workspace_dir).resolve()
        self._memories: Dict[str, Any] = {}
        self._symbol_index: Dict[str, List[Dict[str, Any]]] = {}
        self._register_memory_tools()

    def _register_memory_tools(self) -> None:
        # 1. store_memory
        self.register_tool(
            name="store_memory",
            description="Persists key architectural decisions, invariants, or state notes into agent memory.",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key identifier"},
                    "value": {"type": "string", "description": "Content or structured information to remember"},
                    "category": {"type": "string", "description": "Category (architecture, bug, contract, etc.)", "default": "general"},
                },
                "required": ["key", "value"],
            },
            handler=self.store_memory,
        )

        # 2. search_memory
        self.register_tool(
            name="search_memory",
            description="Retrieves stored memories matching a query key or category filter.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term or key prefix", "default": ""},
                    "category": {"type": "string", "description": "Optional category filter", "default": ""},
                },
            },
            handler=self.search_memory,
        )

        # 3. index_codebase
        self.register_tool(
            name="index_codebase",
            description="Scans workspace Python files, extracts AST classes and functions, and builds symbol graph.",
            input_schema={"type": "object", "properties": {}},
            handler=self.index_codebase,
        )

        # 4. query_symbols
        self.register_tool(
            name="query_symbols",
            description="Searches for function/class definitions, docstrings, and signatures across the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "symbol_name": {"type": "string", "description": "Name or substring of class/function to locate"},
                },
                "required": ["symbol_name"],
            },
            handler=self.query_symbols,
        )

        # 5. find_references
        self.register_tool(
            name="find_references",
            description="Finds all files, line numbers, and callers referencing a given symbol.",
            input_schema={
                "type": "object",
                "properties": {
                    "symbol_name": {"type": "string", "description": "Name of symbol to find all usages for"},
                },
                "required": ["symbol_name"],
            },
            handler=self.find_references,
        )

        # 6. get_dependencies
        self.register_tool(
            name="get_dependencies",
            description="Finds upstream imports/dependencies and downstream dependents for a file or symbol.",
            input_schema={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Filepath or Symbol name"},
                },
                "required": ["target"],
            },
            handler=self.get_dependencies,
        )

        # 7. get_codebase_map
        self.register_tool(
            name="get_codebase_map",
            description="Generates a compact hierarchical symbol outline map of the project.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Optional directory filter", "default": ""},
                },
            },
            handler=self.get_codebase_map,
        )

    def store_memory(self, key: str, value: str, category: str = "general") -> Dict[str, Any]:
        self._memories[key] = {
            "key": key,
            "value": value,
            "category": category,
        }
        return {"status": "stored", "key": key, "category": category, "success": True}

    def search_memory(self, query: str = "", category: str = "") -> Dict[str, Any]:
        results = []
        q_lower = query.lower()
        for k, v in self._memories.items():
            if category and v.get("category") != category:
                continue
            if not query or q_lower in k.lower() or q_lower in str(v.get("value", "")).lower():
                results.append(v)
        return {"count": len(results), "memories": results, "success": True}

    def index_codebase(self) -> Dict[str, Any]:
        self._symbol_index.clear()
        indexed_files = 0
        total_symbols = 0

        for py_file in self.workspace_dir.rglob("*.py"):
            rel_path = str(py_file.relative_to(self.workspace_dir))
            if any(part in ("__pycache__", ".git", ".venv", "venv") for part in py_file.parts):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(content, filename=rel_path)
                indexed_files += 1

                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        sym_type = "class" if isinstance(node, ast.ClassDef) else "function"
                        doc = ast.get_docstring(node) or ""
                        sym_info = {
                            "name": node.name,
                            "type": sym_type,
                            "file": rel_path,
                            "line": node.lineno,
                            "docstring": doc.splitlines()[0] if doc else "",
                        }
                        self._symbol_index.setdefault(node.name.lower(), []).append(sym_info)
                        total_symbols += 1
            except Exception:
                continue

        return {
            "indexed_files": indexed_files,
            "total_symbols": total_symbols,
            "success": True,
        }

    def query_symbols(self, symbol_name: str) -> Dict[str, Any]:
        if not self._symbol_index:
            self.index_codebase()

        sym_lower = symbol_name.lower()
        matches = []
        for name, syms in self._symbol_index.items():
            if sym_lower in name:
                matches.extend(syms)

        return {"query": symbol_name, "matches": matches[:30], "success": True}

    def find_references(self, symbol_name: str) -> Dict[str, Any]:
        from ...tools.code_graph import CodeGraphEngine
        graph = CodeGraphEngine(self.workspace_dir)
        return graph.find_references(symbol_name)

    def get_dependencies(self, target: str) -> Dict[str, Any]:
        from ...tools.code_graph import CodeGraphEngine
        graph = CodeGraphEngine(self.workspace_dir)
        return graph.get_dependencies(target)

    def get_codebase_map(self, path: str = "") -> Dict[str, Any]:
        from ...tools.code_graph import CodeGraphEngine
        graph = CodeGraphEngine(self.workspace_dir)
        outline = graph.get_codebase_map(path)
        return {"codebase_map": outline, "output": outline, "success": True}
