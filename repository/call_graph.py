"""
Call Graph.
Extracts function and method call relationships using Python standard library 'ast',
constructs an invocation network using 'networkx',
and calculates impact radii and call chains with persistent SQLite storage.
"""
import ast
from dataclasses import dataclass, field
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import networkx as nx

logger = logging.getLogger("repository.call_graph")


@dataclass
class CallEdge:
    """Represents a function or method invocation."""
    caller_file: str
    caller_symbol: str
    callee_name: str
    line_number: int
    args_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "caller_file": self.caller_file,
            "caller_symbol": self.caller_symbol,
            "callee_name": self.callee_name,
            "line_number": self.line_number,
            "args_count": self.args_count,
        }


class PythonASTCallExtractor(ast.NodeVisitor):
    """
    Extracts function and method invocations from Python AST using standard ast.NodeVisitor.
    Maintains class and function scoping without regex parsing.
    """

    def __init__(self, source_file: str):
        self.source_file = source_file
        self.calls: List[CallEdge] = []
        self._class_stack: List[str] = []
        self._func_stack: List[str] = []

    def _get_current_caller(self) -> str:
        cls_part = ".".join(self._class_stack)
        func_part = ".".join(self._func_stack)
        if cls_part and func_part:
            return f"{cls_part}.{func_part}"
        elif func_part:
            return func_part
        elif cls_part:
            return cls_part
        return "<module>"

    def visit_ClassDef(self, node: ast.ClassDef):
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_Call(self, node: ast.Call):
        callee_name = self._resolve_callee_name(node.func)
        caller = self._get_current_caller()

        if callee_name:
            self.calls.append(CallEdge(
                caller_file=self.source_file,
                caller_symbol=caller,
                callee_name=callee_name,
                line_number=node.lineno,
                args_count=len(node.args),
            ))
        self.generic_visit(node)

    def _resolve_callee_name(self, func_node: ast.AST) -> Optional[str]:
        """Resolves target callee name from AST call target."""
        if isinstance(func_node, ast.Name):
            return func_node.id
        elif isinstance(func_node, ast.Attribute):
            # e.g. self.do_something() -> do_something, or os.path.join -> join
            if hasattr(ast, "unparse"):
                try:
                    return ast.unparse(func_node)
                except Exception:
                    return func_node.attr
            return func_node.attr
        return None


class CallGraph:
    """
    Inter-procedural and intra-procedural Call Graph backed by networkx.DiGraph.
    Enables impact radius calculation, dead code detection, and execution path tracing.
    """

    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        self.db = db_conn
        self.graph = nx.DiGraph()

    def extract_calls_from_source(self, source_code: str, file_path: str) -> List[CallEdge]:
        """Extracts call edges using Python standard library ast."""
        if not source_code or not file_path.endswith(".py"):
            return []
        try:
            tree = ast.parse(source_code, filename=file_path)
            extractor = PythonASTCallExtractor(source_file=file_path)
            extractor.visit(tree)
            return extractor.calls
        except SyntaxError as e:
            logger.debug(f"SyntaxError parsing calls in {file_path}: {e}")
            return []

    def index_file_calls(self, file_path: str, content: str) -> List[CallEdge]:
        """Indexes calls in a file, populates networkx graph, and records to SQLite."""
        calls = self.extract_calls_from_source(content, file_path)

        posix_src = Path(file_path).as_posix()
        if self.db and calls:
            self.db.execute("DELETE FROM calls WHERE caller_file = ?", (posix_src,))

        for call in calls:
            caller_node = f"{posix_src}:{call.caller_symbol}"
            callee_node = call.callee_name

            self.graph.add_node(caller_node, file=posix_src, symbol=call.caller_symbol)
            self.graph.add_node(callee_node)
            self.graph.add_edge(caller_node, callee_node, line=call.line_number, args=call.args_count)

            if self.db:
                self.db.execute(
                    """
                    INSERT OR REPLACE INTO calls
                    (caller_file, caller_symbol, callee_name, line_number, args_count)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        posix_src,
                        call.caller_symbol,
                        call.callee_name,
                        call.line_number,
                        call.args_count,
                    ),
                )

        if self.db:
            try:
                self.db.commit()
            except Exception:
                pass

        return calls

    def get_callers(self, callee_name: str) -> List[Dict[str, Any]]:
        """Finds all functions/methods that invoke the given callee."""
        callers = []
        if callee_name in self.graph:
            for pred in self.graph.predecessors(callee_name):
                edge_data = self.graph.get_edge_data(pred, callee_name) or {}
                callers.append({
                    "caller_node": pred,
                    "line_number": edge_data.get("line"),
                    "args_count": edge_data.get("args"),
                })
        return callers

    def get_callees(self, caller_symbol: str) -> List[str]:
        """Finds all functions/methods called by the given symbol."""
        callees = []
        for node in self.graph.nodes():
            if node.endswith(f":{caller_symbol}") or node == caller_symbol:
                callees.extend(list(self.graph.successors(node)))
        return sorted(list(set(callees)))

    def get_call_chain(self, source_symbol: str, target_symbol: str) -> List[List[str]]:
        """Finds shortest execution paths between two symbols via networkx."""
        paths = []
        src_nodes = [n for n in self.graph.nodes() if source_symbol in n]
        tgt_nodes = [n for n in self.graph.nodes() if target_symbol in n]

        for s in src_nodes:
            for t in tgt_nodes:
                if nx.has_path(self.graph, s, t):
                    try:
                        for p in nx.all_shortest_paths(self.graph, s, t):
                            paths.append(p)
                    except Exception:
                        pass
        return paths

    def get_impact_radius(self, symbol_name: str, max_depth: int = 3) -> Dict[str, Any]:
        """
        Calculates the blast radius / impact radius of mutating a symbol:
        Identifies all direct and transitive upstream callers using networkx.
        """
        matching_nodes = [n for n in self.graph.nodes() if symbol_name in n]
        impacted_nodes: Set[str] = set()

        for node in matching_nodes:
            # Upstream ancestors (callers)
            try:
                ancestors = nx.ancestors(self.graph, node)
                impacted_nodes.update(ancestors)
            except Exception:
                pass

        impacted_files = set()
        for n in impacted_nodes:
            if ":" in n:
                impacted_files.add(n.split(":")[0])

        return {
            "symbol": symbol_name,
            "impact_radius_score": len(impacted_nodes),
            "affected_callers": sorted(list(impacted_nodes)),
            "affected_files": sorted(list(impacted_files)),
            "total_affected_files": len(impacted_files),
        }
