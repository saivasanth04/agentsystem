"""
PageRank-Powered Codebase Map Engine.
Builds a directed reference graph, computes PageRank centrality, and renders token-bounded repository maps.
"""
from typing import Any, Dict, List, Optional, Set, Tuple

from .graph import CodebaseGraph
from .symbols import SymbolNode
from ..context.token_estimator import estimate_tokens


class PageRankRepoMap:
    """
    Renders a token-budgeted, architectural Repo Map using PageRank centrality over the reference graph.
    Prioritizes high-centrality classes, methods, and interfaces.
    """

    def __init__(self, code_graph: CodebaseGraph, damping_factor: float = 0.85, max_iterations: int = 20):
        self.code_graph = code_graph
        self.damping_factor = damping_factor
        self.max_iterations = max_iterations
        self.pagerank_scores: Dict[str, float] = {}  # filepath or qualified_symbol -> score

    def invalidate(self) -> None:
        """Clears cached PageRank scores so next map generation re-computes centrality."""
        self.pagerank_scores.clear()

    def compute_pagerank(self) -> Dict[str, float]:
        """Runs power iteration PageRank over the file-level and symbol-level reference graph."""
        nodes: Set[str] = set()
        out_edges: Dict[str, Set[str]] = {}
        in_edges: Dict[str, Set[str]] = {}

        # 1. Collect all files as nodes
        for fp in self.code_graph.file_to_symbols:
            nodes.add(fp)

        # 2. Add edges from file imports
        for src_file, imps in self.code_graph.file_imports.items():
            for imp in imps:
                # Find matching target files
                for tgt_file in self.code_graph.file_to_symbols:
                    if tgt_file == src_file:
                        continue
                    mod_dot = tgt_file.replace("/", ".").replace("\\", ".").replace(".py", "")
                    if imp in mod_dot or mod_dot.endswith(imp):
                        out_edges.setdefault(src_file, set()).add(tgt_file)
                        in_edges.setdefault(tgt_file, set()).add(src_file)

        # 3. Add edges from resolved call graph
        for caller_sym, edges in self.code_graph.call_graph.items():
            caller_node = caller_sym
            nodes.add(caller_node)
            for e in edges:
                if e.callee_symbol:
                    callee_node = e.callee_symbol
                    nodes.add(callee_node)
                    out_edges.setdefault(caller_node, set()).add(callee_node)
                    in_edges.setdefault(callee_node, set()).add(caller_node)

        n = len(nodes)
        if n == 0:
            return {}

        # Initialize uniform scores
        initial_score = 1.0 / n
        scores = {node: initial_score for node in nodes}

        # Power iteration
        for _ in range(self.max_iterations):
            new_scores = {}
            for node in nodes:
                incoming = in_edges.get(node, set())
                sum_in = 0.0
                for inc in incoming:
                    out_degree = len(out_edges.get(inc, set()))
                    if out_degree > 0:
                        sum_in += scores[inc] / out_degree
                new_scores[node] = ((1.0 - self.damping_factor) / n) + (self.damping_factor * sum_in)

            # Re-normalize
            total_sum = sum(new_scores.values()) or 1.0
            scores = {k: v / total_sum for k, v in new_scores.items()}

        self.pagerank_scores = scores
        return scores

    def generate_repo_map(
        self,
        max_tokens: int = 2000,
        filter_path: Optional[str] = None,
    ) -> str:
        """
        Generates a token-compact repository outline prioritized by PageRank centrality.
        """
        if not self.pagerank_scores:
            self.compute_pagerank()

        clean_filter = filter_path.strip().replace("\\", "/") if filter_path else ""

        # Rank files by PageRank
        ranked_files = []
        for fp, syms in self.code_graph.file_to_symbols.items():
            if clean_filter and not fp.startswith(clean_filter):
                continue
            pr_score = self.pagerank_scores.get(fp, 0.0)
            # Combine file score with maximum symbol score in file
            sym_scores = [self.pagerank_scores.get(s.qualified_name, 0.0) for s in syms]
            max_sym_pr = max(sym_scores) if sym_scores else 0.0
            combined_rank = pr_score + max_sym_pr * 2.0
            ranked_files.append((combined_rank, fp, syms))

        # Sort files by descending centrality
        ranked_files.sort(key=lambda x: x[0], reverse=True)

        lines: List[str] = ["# Centrality-Ranked Codebase Map (PageRank):"]
        used_tokens = 15

        for rank_score, fp, syms in ranked_files:
            file_header = f"\n📁 {fp}:"
            est_tokens = estimate_tokens(file_header)
            if used_tokens + est_tokens > max_tokens:
                lines.append("\n... [Remaining low-centrality files omitted to respect token budget] ...")
                break

            lines.append(file_header)
            used_tokens += est_tokens

            if not syms:
                continue

            # Sort symbols within file by centrality and kind
            sorted_syms = sorted(
                syms,
                key=lambda s: (
                    self.pagerank_scores.get(s.qualified_name, 0.0),
                    1 if s.kind == "class" else 0
                ),
                reverse=True
            )

            for sym in sorted_syms:
                indent = "    " if sym.parent else "  "
                kind_tag = f"[{sym.kind}]"
                doc = f" - {sym.docstring}" if sym.docstring else ""
                sig_line = f"{indent}• {kind_tag} {sym.name}{sym.signature}{doc}"
                sym_toks = estimate_tokens(sig_line)

                if used_tokens + sym_toks > max_tokens:
                    lines.append(f"{indent}• ... [Additional symbols omitted]")
                    break

                lines.append(sig_line)
                used_tokens += sym_toks

        return "\n".join(lines)
