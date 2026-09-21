"""
Codebase Memory (CBM) & Graph Retrieval Engine.
Integrates structural AST graphs, semantic indices, and architectural models into a queryable knowledge graph.
Enables sub-graph extraction, GraphRAG queries, symbol neighborhood analysis, and architectural slicing.
"""
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .architecture import ArchitectureAnalyzer, ArchitectureSummary
from .graph import CodebaseGraph
from .semantic_index import SemanticCodeIndex
from .symbols import SymbolNode, is_module_import_match
from ..context.token_estimator import estimate_tokens


@dataclass
class CBMNode:
    id: str
    label: str
    kind: str  # file | class | method | function | interface | layer
    filepath: str
    line: int = 1
    docstring: str = ""
    signature: str = ""
    community: str = "core"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "filepath": self.filepath,
            "line": self.line,
            "docstring": self.docstring,
            "signature": self.signature,
            "community": self.community,
        }


@dataclass
class CBMEdge:
    source: str
    target: str
    relation: str  # calls | imports | implements | defines | depends_on | conceptually_related_to
    confidence: str = "EXTRACTED"  # EXTRACTED | INFERRED
    weight: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "confidence": self.confidence,
            "weight": self.weight,
        }


class CodebaseMemory:
    """
    Unified Codebase Memory (CBM).
    Maintains a persistent, queryable knowledge graph connecting symbols, calls, imports,
    communities, and architectural layers to provide scoped sub-graph retrieval.
    """

    def __init__(
        self,
        workspace_dir: Optional[Path] = None,
        code_graph: Optional[CodebaseGraph] = None,
        semantic_index: Optional[SemanticCodeIndex] = None,
        arch_analyzer: Optional[ArchitectureAnalyzer] = None,
        telemetry_engine: Optional[Any] = None,
    ):
        self.workspace_dir = Path(workspace_dir).resolve() if workspace_dir else Path.cwd()
        self.code_graph = code_graph or CodebaseGraph(self.workspace_dir)
        self.semantic_index = semantic_index or SemanticCodeIndex(self.code_graph)
        self.arch_analyzer = arch_analyzer or ArchitectureAnalyzer(self.workspace_dir, self.code_graph)
        self.telemetry_engine = telemetry_engine

        self.nodes: Dict[str, CBMNode] = {}
        self.edges: List[CBMEdge] = []
        self.adj_list: Dict[str, List[Tuple[str, str, float]]] = {}  # src -> [(tgt, rel, weight)]
        self.reverse_adj_list: Dict[str, List[Tuple[str, str, float]]] = {}  # tgt -> [(src, rel, weight)]
        self.communities: Dict[str, str] = {}  # node_id -> community_name

        self.build_memory()

    def build_memory(self) -> Dict[str, Any]:
        """Builds knowledge graph from code_graph, semantic_index, and arch_analyzer."""
        self.nodes.clear()
        self.edges.clear()
        self.adj_list.clear()
        self.reverse_adj_list.clear()
        self.communities.clear()

        # 1. Analyze architecture for layer communities
        arch_summary = self.arch_analyzer.analyze()
        file_to_layer: Dict[str, str] = {}
        for layer_name, files in arch_summary.layers.items():
            for f in files:
                file_to_layer[f] = layer_name

        # 2. Add File & Symbol Nodes
        for filepath, syms in self.code_graph.file_to_symbols.items():
            layer = file_to_layer.get(filepath, "general")
            file_node_id = f"file:{filepath}"
            file_node = CBMNode(
                id=file_node_id,
                label=Path(filepath).name,
                kind="file",
                filepath=filepath,
                line=1,
                community=layer,
            )
            self.nodes[file_node_id] = file_node
            self.communities[file_node_id] = layer

            for sym in syms:
                sym_node_id = f"sym:{sym.qualified_name}"
                sym_node = CBMNode(
                    id=sym_node_id,
                    label=sym.name,
                    kind=sym.kind,
                    filepath=filepath,
                    line=sym.start_line,
                    docstring=sym.docstring,
                    signature=sym.signature,
                    community=layer,
                )
                self.nodes[sym_node_id] = sym_node
                self.communities[sym_node_id] = layer

                # Edge: file defines symbol
                self._add_edge(file_node_id, sym_node_id, "defines", "EXTRACTED", 1.0)

        # 3. Add Import Edges
        for filepath, imps in self.code_graph.file_imports.items():
            src_file_id = f"file:{filepath}"
            for imp in imps:
                # Find matching target files or symbols
                for target_fp in self.code_graph.file_to_symbols:
                    if target_fp == filepath:
                        continue
                    if is_module_import_match(imp, target_fp, filepath):
                        tgt_file_id = f"file:{target_fp}"
                        self._add_edge(src_file_id, tgt_file_id, "imports", "EXTRACTED", 1.0)

        # 4. Add Resolved Call Edges
        for caller_sym, call_edges in self.code_graph.call_graph.items():
            caller_node_id = f"sym:{caller_sym}"
            for ce in call_edges:
                if ce.callee_symbol:
                    callee_node_id = f"sym:{ce.callee_symbol}"
                    self._add_edge(caller_node_id, callee_node_id, "calls", "EXTRACTED", 1.0)
                else:
                    # Unresolved external call
                    pass

        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "communities": len(set(self.communities.values())),
            "success": True,
        }

    def _add_edge(self, source: str, target: str, relation: str, confidence: str, weight: float) -> None:
        edge = CBMEdge(source=source, target=target, relation=relation, confidence=confidence, weight=weight)
        self.edges.append(edge)
        self.adj_list.setdefault(source, []).append((target, relation, weight))
        self.reverse_adj_list.setdefault(target, []).append((source, relation, weight))

    def delete_file(self, filepath: str) -> None:
        """Removes all nodes and edges belonging to a file from the knowledge graph."""
        rel_path = str(filepath).replace("\\", "/").strip("/")
        file_node_id = f"file:{rel_path}"
        sym_node_ids = {nid for nid, n in self.nodes.items() if n.filepath == rel_path}
        nodes_to_remove = sym_node_ids | {file_node_id}

        if not nodes_to_remove:
            return

        for nid in nodes_to_remove:
            self.nodes.pop(nid, None)
            self.communities.pop(nid, None)

        self.edges = [
            e for e in self.edges
            if e.source not in nodes_to_remove and e.target not in nodes_to_remove
        ]

        for nid in nodes_to_remove:
            self.adj_list.pop(nid, None)
            self.reverse_adj_list.pop(nid, None)

        for src, adj in list(self.adj_list.items()):
            self.adj_list[src] = [tup for tup in adj if tup[0] not in nodes_to_remove]
        for tgt, radj in list(self.reverse_adj_list.items()):
            self.reverse_adj_list[tgt] = [tup for tup in radj if tup[0] not in nodes_to_remove]

    def update_file(self, filepath: str) -> None:
        """Incrementally re-indexes a single file's sub-graph without rebuilding the whole graph."""
        rel_path = str(filepath).replace("\\", "/").strip("/")
        self.delete_file(rel_path)

        syms = self.code_graph.file_to_symbols.get(rel_path, [])
        layer = self.get_architecture_slice(rel_path).get("layer", "general")

        file_node_id = f"file:{rel_path}"
        file_node = CBMNode(
            id=file_node_id,
            label=Path(rel_path).name,
            kind="file",
            filepath=rel_path,
            line=1,
            community=layer,
        )
        self.nodes[file_node_id] = file_node
        self.communities[file_node_id] = layer

        for sym in syms:
            sym_node_id = f"sym:{sym.qualified_name}"
            sym_node = CBMNode(
                id=sym_node_id,
                label=sym.name,
                kind=sym.kind,
                filepath=rel_path,
                line=sym.start_line,
                docstring=sym.docstring,
                signature=sym.signature,
                community=layer,
            )
            self.nodes[sym_node_id] = sym_node
            self.communities[sym_node_id] = layer
            self._add_edge(file_node_id, sym_node_id, "defines", "EXTRACTED", 1.0)

        # Import edges for this file
        imps = self.code_graph.file_imports.get(rel_path, set())
        for imp in imps:
            for target_fp in self.code_graph.file_to_symbols:
                if target_fp == rel_path:
                    continue
                if is_module_import_match(imp, target_fp, rel_path):
                    tgt_file_id = f"file:{target_fp}"
                    self._add_edge(file_node_id, tgt_file_id, "imports", "EXTRACTED", 1.0)

        # Re-add call edges involving symbols in this file
        for sym in syms:
            caller_node_id = f"sym:{sym.qualified_name}"
            for ce in self.code_graph.call_graph.get(sym.qualified_name, []):
                if ce.callee_symbol and f"sym:{ce.callee_symbol}" in self.nodes:
                    self._add_edge(caller_node_id, f"sym:{ce.callee_symbol}", "calls", "EXTRACTED", 1.0)

            for ce in self.code_graph.reverse_call_graph.get(sym.qualified_name, []):
                caller_nid = f"sym:{ce.caller_symbol}"
                if caller_nid in self.nodes:
                    self._add_edge(caller_nid, f"sym:{sym.qualified_name}", "calls", "EXTRACTED", 1.0)

    def retrieve_task_subgraph(
        self,
        task_info: Dict[str, Any],
        max_tokens: int = 4000,
    ) -> Dict[str, Any]:
        """
        Dynamically extracts a scoped sub-graph for an active task.
        Identifies focal files, direct callers, callees, and architectural constraints,
        formatting them within a strict token budget.
        """
        objective = task_info.get("objective", "")
        description = task_info.get("description", "")
        inputs = task_info.get("inputs", []) or []
        outputs = task_info.get("outputs", []) or []
        query_text = f"{objective} {description} {' '.join(inputs)} {' '.join(outputs)}"

        if getattr(self, "telemetry_engine", None):
            try:
                self.telemetry_engine.record_retrieval("cbm_subgraph", count=1)
            except Exception:
                pass

        from ..tracing import get_tracer, SpanType, SpanStatus
        tracer = get_tracer("orchestrator")
        retr_scope = tracer.start_as_current_span(
            "Retrieval: CBM Subgraph",
            span_type=SpanType.RETRIEVAL,
            attributes={"query": query_text[:200], "inputs": inputs, "outputs": outputs},
        )
        retr_span = retr_scope.__enter__()

        # 1. Identify Focal Seed Nodes
        focal_node_ids: Set[str] = set()
        focal_files: Set[str] = set()

        for p in inputs + outputs:
            clean = p.split(":")[0].strip().replace("\\", "/")
            if clean:
                focal_files.add(clean)
                focal_node_ids.add(f"file:{clean}")

        # If inputs/outputs didn't specify files, seed from semantic index
        if not focal_node_ids and query_text.strip():
            sem_hits = self.semantic_index.search(query_text, top_k=3)
            for h in sem_hits:
                fp = h.get("filepath", "")
                sym_name = h.get("symbol_name", "")
                if fp:
                    focal_files.add(fp)
                    focal_node_ids.add(f"file:{fp}")
                if sym_name:
                    for s_id, node in self.nodes.items():
                        if node.label == sym_name:
                            focal_node_ids.add(s_id)

        # 2. 1-2 Hop Neighbor Expansion (Callers, Callees, Interfaces)
        interface_nodes: Dict[str, CBMNode] = {}
        visited: Set[str] = set(focal_node_ids)

        queue: deque = deque([(nid, 1) for nid in focal_node_ids])
        while queue:
            curr_id, depth = queue.popleft()
            if depth > 2:
                continue

            # Forward edges (Callees, Imports)
            for tgt_id, rel, _ in self.adj_list.get(curr_id, []):
                if tgt_id not in visited and tgt_id in self.nodes:
                    visited.add(tgt_id)
                    interface_nodes[tgt_id] = self.nodes[tgt_id]
                    queue.append((tgt_id, depth + 1))

            # Reverse edges (Callers, Dependents)
            for src_id, rel, _ in self.reverse_adj_list.get(curr_id, []):
                if src_id not in visited and src_id in self.nodes:
                    visited.add(src_id)
                    interface_nodes[src_id] = self.nodes[src_id]
                    queue.append((src_id, depth + 1))

        # 3. Format Scoped Context Slice within Token Budget
        lines: List[str] = ["# Scoped Codebase Memory (CBM) Sub-graph:"]
        used_tokens = 15

        # Architectural Slice
        arch_slices = []
        focal_arch_slices = []
        for ff in sorted(focal_files):
            a_slice = self.get_architecture_slice(ff)
            if a_slice.get("found"):
                focal_arch_slices.append(
                    f"• {ff} -> Layer: {a_slice['layer']} | Frameworks: {', '.join(a_slice['frameworks'] or ['None'])}"
                )
        # Architectural conventions for focal files
        if focal_arch_slices:
            lines.append("\n### Architectural Conventions:")
            for as_line in focal_arch_slices:
                lines.append(as_line)
                used_tokens += estimate_tokens(as_line)

        # Focal Target Symbols
        focal_sym_nodes = [self.nodes[nid] for nid in focal_node_ids if nid in self.nodes and self.nodes[nid].kind != "file"]
        if focal_sym_nodes:
            lines.append("\n### Focal Target Symbols:")
            for node in sorted(focal_sym_nodes, key=lambda x: (x.kind, x.label)):
                sig_display = node.signature if (node.signature.startswith("def ") or node.signature.startswith("class ") or node.signature.startswith("async def ")) else f"{node.label}{node.signature}"
                doc = f'    """{node.docstring}"""' if node.docstring else ""
                sig_line = f"  • [{node.kind}] {sig_display} ({node.filepath}:{node.line})"
                if doc:
                    sig_line += f"\n{doc}"
                tok = estimate_tokens(sig_line)
                if used_tokens + tok > max_tokens:
                    break
                lines.append(sig_line)
                used_tokens += tok

        # Interface Contracts (Signatures & Docstrings)
        lines.append("\n### Related Interface Contracts (1-2 Hop Callers & Dependencies):")
        for nid, node in sorted(interface_nodes.items(), key=lambda x: (x[1].kind, x[1].label)):
            if node.kind in ["method", "function", "async_function", "class", "interface"]:
                sig_display = node.signature if (node.signature.startswith("def ") or node.signature.startswith("class ") or node.signature.startswith("async def ")) else f"{node.label}{node.signature}"
                doc = f'    """{node.docstring}"""' if node.docstring else ""
                sig_line = f"  • [{node.kind}] {sig_display} ({node.filepath}:{node.line})"
                if doc:
                    sig_line += f"\n{doc}"
                tok = estimate_tokens(sig_line)
                if used_tokens + tok > max_tokens:
                    lines.append("  • ... [Remaining interface contracts omitted to fit token budget]")
                    break
                lines.append(sig_line)
                used_tokens += tok

        retr_span.set_attribute("focal_files_count", len(focal_files))
        retr_span.set_attribute("interface_nodes_count", len(interface_nodes))
        retr_span.set_attribute("estimated_tokens", used_tokens)
        retr_span.set_status(SpanStatus.OK)
        retr_scope.__exit__(None, None, None)

        return {
            "focal_files": sorted(list(focal_files)),
            "focal_nodes": [self.nodes[nid].to_dict() for nid in focal_node_ids if nid in self.nodes],
            "interface_nodes_count": len(interface_nodes),
            "formatted_context": "\n".join(lines),
            "estimated_tokens": used_tokens,
            "success": True,
        }

    def query_graph(
        self,
        query: str,
        max_tokens: int = 2000,
        traversal: str = "bfs",
    ) -> Dict[str, Any]:
        """
        GraphRAG-style graph search: finds seed nodes via semantic/BM25 search,
        traverses connected nodes via BFS or DFS up to max_tokens budget.
        """
        # 1. Semantic Seed Discovery
        sem_results = self.semantic_index.search(query, top_k=5)
        seed_nodes: Set[str] = set()

        for hit in sem_results:
            sym_name = hit.get("symbol_name", "")
            fp = hit.get("filepath", "")
            for nid, node in self.nodes.items():
                if node.label == sym_name or (node.kind == "file" and node.filepath == fp):
                    seed_nodes.add(nid)

        if not seed_nodes:
            return {
                "query": query,
                "nodes": [],
                "edges": [],
                "formatted_output": f"No relevant nodes found in codebase graph for query '{query}'.",
                "success": False,
            }

        # 2. Graph Traversal (BFS or DFS)
        visited: Set[str] = set(seed_nodes)
        traversed_nodes: List[CBMNode] = [self.nodes[nid] for nid in seed_nodes if nid in self.nodes]
        traversed_edges: List[Dict[str, Any]] = []

        if traversal.lower() == "dfs":
            stack = list(seed_nodes)
            while stack:
                curr_id = stack.pop()
                for tgt_id, rel, weight in self.adj_list.get(curr_id, []):
                    traversed_edges.append({"source": curr_id, "target": tgt_id, "relation": rel})
                    if tgt_id not in visited and tgt_id in self.nodes:
                        visited.add(tgt_id)
                        traversed_nodes.append(self.nodes[tgt_id])
                        stack.append(tgt_id)
                for src_id, rel, weight in self.reverse_adj_list.get(curr_id, []):
                    traversed_edges.append({"source": src_id, "target": curr_id, "relation": rel})
                    if src_id not in visited and src_id in self.nodes:
                        visited.add(src_id)
                        traversed_nodes.append(self.nodes[src_id])
                        stack.append(src_id)
                if len(traversed_nodes) >= 20:
                    break
        else:  # BFS
            queue = deque(seed_nodes)
            while queue:
                curr_id = queue.popleft()
                for tgt_id, rel, weight in self.adj_list.get(curr_id, []):
                    traversed_edges.append({"source": curr_id, "target": tgt_id, "relation": rel})
                    if tgt_id not in visited and tgt_id in self.nodes:
                        visited.add(tgt_id)
                        traversed_nodes.append(self.nodes[tgt_id])
                        queue.append(tgt_id)
                for src_id, rel, weight in self.reverse_adj_list.get(curr_id, []):
                    traversed_edges.append({"source": src_id, "target": curr_id, "relation": rel})
                    if src_id not in visited and src_id in self.nodes:
                        visited.add(src_id)
                        traversed_nodes.append(self.nodes[src_id])
                        queue.append(src_id)
                if len(traversed_nodes) >= 20:
                    break

        # 3. Format Output within Budget
        lines: List[str] = [f"# Codebase Graph Query: '{query}' (Traversal: {traversal.upper()})"]
        used_tokens = 15

        lines.append(f"\nDiscovered {len(traversed_nodes)} connected nodes and {len(traversed_edges)} relationships:")
        for node in traversed_nodes:
            doc = f" - {node.docstring}" if node.docstring else ""
            sig_display = node.signature if (node.signature.startswith("def ") or node.signature.startswith("class ") or node.signature.startswith("async def ")) else f"{node.label}{node.signature}"
            line_str = f"• [{node.kind}] {sig_display} ({node.filepath}:{node.line}){doc}"
            tok = estimate_tokens(line_str)
            if used_tokens + tok > max_tokens:
                lines.append("• ... [Remaining sub-graph nodes omitted to respect token budget]")
                break
            lines.append(line_str)
            used_tokens += tok

        return {
            "query": query,
            "traversal": traversal,
            "total_nodes": len(traversed_nodes),
            "total_edges": len(traversed_edges),
            "nodes": [n.to_dict() for n in traversed_nodes],
            "edges": traversed_edges,
            "formatted_output": "\n".join(lines),
            "estimated_tokens": used_tokens,
            "success": True,
        }

    def get_symbol_neighbors(self, symbol_name: str, depth: int = 1) -> Dict[str, Any]:
        """Retrieves 1-2 hop callers, callees, and interfaces for a specific symbol."""
        sym_res = self.code_graph.find_symbol(symbol_name)
        symbols = sym_res.get("symbols", [])
        if not symbols:
            return {"symbol": symbol_name, "found": False, "neighbors": [], "success": False}

        primary = symbols[0]
        node_id = f"sym:{primary['qualified_name']}"

        callers = []
        callees = []

        # Downstream callees
        for tgt_id, rel, _ in self.adj_list.get(node_id, []):
            if tgt_id in self.nodes:
                callees.append(self.nodes[tgt_id].to_dict())

        # Upstream callers
        for src_id, rel, _ in self.reverse_adj_list.get(node_id, []):
            if src_id in self.nodes:
                callers.append(self.nodes[src_id].to_dict())

        return {
            "symbol": symbol_name,
            "qualified_name": primary["qualified_name"],
            "filepath": primary["filepath"],
            "line": primary["start_line"],
            "signature": primary["signature"],
            "docstring": primary["docstring"],
            "callers": callers,
            "callees": callees,
            "total_callers": len(callers),
            "total_callees": len(callees),
            "found": True,
            "success": True,
        }

    def get_architecture_slice(self, target_file: str) -> Dict[str, Any]:
        """Retrieves architectural layer, framework conventions, and entrypoints for a target file."""
        clean_target = target_file.strip().replace("\\", "/")
        summary = self.arch_analyzer.analyze()

        target_layer = "general"
        for layer_name, files in summary.layers.items():
            if any(clean_target in f or f in clean_target for f in files):
                target_layer = layer_name
                break

        is_entrypoint = any(clean_target in ep or ep in clean_target for ep in summary.entrypoints)

        return {
            "filepath": clean_target,
            "layer": target_layer,
            "is_entrypoint": is_entrypoint,
            "frameworks": summary.detected_frameworks,
            "test_frameworks": summary.test_frameworks,
            "found": True,
            "success": True,
        }


# Singleton instance
codebase_memory = CodebaseMemory()
