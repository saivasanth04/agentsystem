"""
Multi-Level Retrieval Hierarchy Engine.
Implements a 5-level context retrieval pyramid:
  Level 1: Exact File / Symbol (Full or windowed code of target components)
  Level 2: Dependency Graph (Direct 1-hop/2-hop interfaces, signatures, docstrings, callers)
  Level 3: Semantic Search (Conceptually relevant chunks, utilities, test fixtures via BM25/embeddings)
  Level 4: Architecture Knowledge (Layer boundaries, conventions, constraints from ArchitectureAnalyzer/CBM)
  Level 5: Broader Repository (Token-bounded PageRank centrality repo map, avoiding flat 'ALL FILES' dumps)
"""
from dataclasses import dataclass, field
from enum import IntEnum
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from ..security.trust_boundaries import TrustBoundaryEnforcer

logger = logging.getLogger("context.retrieval_hierarchy")


class RetrievalLevel(IntEnum):
    """5-Level Context Retrieval Hierarchy."""
    LEVEL_1_EXACT = 1                   # Target files/symbols to modify/test
    LEVEL_2_DEPENDENCY_GRAPH = 2        # Direct dependencies/callers (signatures only)
    LEVEL_3_SEMANTIC_SEARCH = 3         # Conceptually relevant snippets & test fixtures
    LEVEL_4_ARCHITECTURE_KNOWLEDGE = 4  # Layer boundaries, architecture rules, conventions
    LEVEL_5_BROADER_REPOSITORY = 5      # Centrality-ranked repository overview (PageRank)

    @property
    def label(self) -> str:
        labels = {
            RetrievalLevel.LEVEL_1_EXACT: "Level 1: Exact File / Symbol",
            RetrievalLevel.LEVEL_2_DEPENDENCY_GRAPH: "Level 2: Dependency Graph Interfaces",
            RetrievalLevel.LEVEL_3_SEMANTIC_SEARCH: "Level 3: Semantic Search Concepts",
            RetrievalLevel.LEVEL_4_ARCHITECTURE_KNOWLEDGE: "Level 4: Architecture Knowledge & Rules",
            RetrievalLevel.LEVEL_5_BROADER_REPOSITORY: "Level 5: Broader Repository Map",
        }
        return labels.get(self, f"Level {self.value}")


@dataclass
class HierarchyBudgetConfig:
    """Configures token allocation ratios across the 5 retrieval levels."""
    total_budget: int = 16000
    l1_ratio: float = 0.45  # 45% -> Exact targets
    l2_ratio: float = 0.25  # 25% -> Dependency interfaces
    l3_ratio: float = 0.15  # 15% -> Semantic snippets
    l4_ratio: float = 0.10  # 10% -> Architecture conventions
    l5_ratio: float = 0.05  # 5%  -> Ambient repo map

    def get_budget(self, level: RetrievalLevel) -> int:
        ratios = {
            RetrievalLevel.LEVEL_1_EXACT: self.l1_ratio,
            RetrievalLevel.LEVEL_2_DEPENDENCY_GRAPH: self.l2_ratio,
            RetrievalLevel.LEVEL_3_SEMANTIC_SEARCH: self.l3_ratio,
            RetrievalLevel.LEVEL_4_ARCHITECTURE_KNOWLEDGE: self.l4_ratio,
            RetrievalLevel.LEVEL_5_BROADER_REPOSITORY: self.l5_ratio,
        }
        ratio = ratios.get(level, 0.1)
        return max(200, int(self.total_budget * ratio))


@dataclass
class HierarchicalContextItem:
    """A prioritized context item tagged with its specific retrieval hierarchy level."""
    level: RetrievalLevel
    source_type: str  # focal_file | symbol_window | interface_signature | semantic_snippet | arch_rule | repo_map
    filepath: str
    symbol_name: Optional[str] = None
    score: float = 1.0
    tokens: int = 0
    formatted_content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HierarchicalContextBundle:
    """A comprehensive context bundle containing items across all 5 retrieval levels."""
    items: List[HierarchicalContextItem] = field(default_factory=list)
    total_tokens: int = 0
    budget_config: HierarchyBudgetConfig = field(default_factory=HierarchyBudgetConfig)

    @property
    def items_by_level(self) -> Dict[RetrievalLevel, List[HierarchicalContextItem]]:
        grouped: Dict[RetrievalLevel, List[HierarchicalContextItem]] = {
            lvl: [] for lvl in RetrievalLevel
        }
        for item in self.items:
            grouped[item.level].append(item)
        return grouped

    def get_level_tokens(self, level: RetrievalLevel) -> int:
        return sum(item.tokens for item in self.items if item.level == level)

    def to_markdown(self) -> str:
        """Assembles a cleanly structured 5-tier context block for agent prompting."""
        sections = []
        grouped = self.items_by_level

        # Level 1: Exact File / Symbol
        l1_items = grouped[RetrievalLevel.LEVEL_1_EXACT]
        if l1_items:
            content = "\n\n".join(it.formatted_content for it in l1_items)
            sections.append(f"## {RetrievalLevel.LEVEL_1_EXACT.label}\n{content}")

        # Level 2: Dependency Graph Interfaces
        l2_items = grouped[RetrievalLevel.LEVEL_2_DEPENDENCY_GRAPH]
        if l2_items:
            content = "\n\n".join(it.formatted_content for it in l2_items)
            sections.append(f"## {RetrievalLevel.LEVEL_2_DEPENDENCY_GRAPH.label}\n{content}")

        # Level 3: Semantic Search Concepts
        l3_items = grouped[RetrievalLevel.LEVEL_3_SEMANTIC_SEARCH]
        if l3_items:
            content = "\n\n".join(it.formatted_content for it in l3_items)
            sections.append(f"## {RetrievalLevel.LEVEL_3_SEMANTIC_SEARCH.label}\n{content}")

        # Level 4: Architecture Knowledge & Rules
        l4_items = grouped[RetrievalLevel.LEVEL_4_ARCHITECTURE_KNOWLEDGE]
        if l4_items:
            content = "\n\n".join(it.formatted_content for it in l4_items)
            sections.append(f"## {RetrievalLevel.LEVEL_4_ARCHITECTURE_KNOWLEDGE.label}\n{content}")

        # Level 5: Broader Repository Map
        l5_items = grouped[RetrievalLevel.LEVEL_5_BROADER_REPOSITORY]
        if l5_items:
            content = "\n\n".join(it.formatted_content for it in l5_items)
            sections.append(f"## {RetrievalLevel.LEVEL_5_BROADER_REPOSITORY.label}\n{content}")

        return "\n\n---\n\n".join(sections)


class RetrievalHierarchyEngine:
    """
    Coordinates multi-level progressive context retrieval.
    Replaces naive flat file listings ('ALL FILES') with a structured 5-tier context pyramid.
    """

    def __init__(
        self,
        workspace: Optional[Any] = None,
        code_graph: Optional[Any] = None,
        semantic_index: Optional[Any] = None,
        arch_analyzer: Optional[Any] = None,
        cbm: Optional[Any] = None,
        fallback_engine: Optional[Any] = None,
    ):
        self.workspace = workspace
        self.code_graph = code_graph
        self.semantic_index = semantic_index
        self.arch_analyzer = arch_analyzer
        self.cbm = cbm
        self.fallback_engine = fallback_engine

    @staticmethod
    def extract_keywords(text: str) -> Set[str]:
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)
        stopwords = {
            "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "with",
            "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
            "do", "does", "did", "can", "could", "should", "would", "will", "shall",
            "this", "that", "these", "those", "it", "its", "from", "as", "by", "of",
        }
        return {t.lower() for t in tokens if len(t) > 2 and t.lower() not in stopwords}

    def retrieve_hierarchy(
        self,
        task_info: Optional[Dict[str, Any]] = None,
        budget_config: Optional[HierarchyBudgetConfig] = None,
        target_files: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_levels: Optional[List[RetrievalLevel]] = None,
    ) -> HierarchicalContextBundle:
        """
        Executes multi-level progressive retrieval:
        Level 1: Exact File / Symbol
        Level 2: Dependency Graph
        Level 3: Semantic Search
        Level 4: Architecture Knowledge
        Level 5: Broader Repository
        """
        cfg = budget_config or HierarchyBudgetConfig()
        task = task_info or {}
        active_levels = set(include_levels or list(RetrievalLevel))
        bundle = HierarchicalContextBundle(budget_config=cfg)

        focal_paths: Set[str] = set()
        target_symbols: Set[str] = set()

        # Extract explicit targets from task inputs, outputs, or target_files
        raw_targets = (target_files or []) + (task.get("inputs") or []) + (task.get("outputs") or [])
        for p in raw_targets:
            if not p or p == "*":
                continue
            clean = p.split(":")[0].strip().replace("\\", "/")
            if clean:
                focal_paths.add(clean)
            if ":" in p:
                sym = p.split(":")[-1].strip()
                if sym:
                    target_symbols.add(sym)

        # Build task query text
        objective = task.get("objective", "")
        description = task.get("description", "")
        q_text = f"{query or ''} {objective} {description}".strip()
        keywords = self.extract_keywords(q_text)

        # ------------------------------------------------------------------
        # LEVEL 1: Exact File / Symbol (Target Code Window)
        # ------------------------------------------------------------------
        if RetrievalLevel.LEVEL_1_EXACT in active_levels:
            l1_budget = cfg.get_budget(RetrievalLevel.LEVEL_1_EXACT)
            used_l1 = 0

            for fp in sorted(focal_paths):
                if used_l1 >= l1_budget:
                    break
                content = self._read_file_content(fp)
                if not content:
                    content = "[File currently empty or pending creation]"

                est_tok = max(1, len(content) // 4)
                if used_l1 + est_tok > l1_budget:
                    avail_lines = max(50, (l1_budget - used_l1) * 3)
                    lines = content.splitlines()
                    content = "\n".join(lines[:avail_lines]) + f"\n... [Truncated to {avail_lines} lines to fit Level 1 budget] ..."
                    est_tok = max(1, len(content) // 4)

                formatted = TrustBoundaryEnforcer.wrap_untrusted_content(
                    content,
                    source_type="focal_file",
                    identifier=fp,
                    header=f"// File: {fp} (Exact Target)",
                )
                bundle.items.append(HierarchicalContextItem(
                    level=RetrievalLevel.LEVEL_1_EXACT,
                    source_type="focal_file",
                    filepath=fp,
                    score=1.0,
                    tokens=est_tok,
                    formatted_content=formatted,
                ))
                used_l1 += est_tok

        # ------------------------------------------------------------------
        # LEVEL 2: Dependency Graph (Interfaces & Signatures Only)
        # ------------------------------------------------------------------
        if RetrievalLevel.LEVEL_2_DEPENDENCY_GRAPH in active_levels:
            l2_budget = cfg.get_budget(RetrievalLevel.LEVEL_2_DEPENDENCY_GRAPH)
            used_l2 = 0
            dep_paths: Set[str] = set()

            # 1. Gather dependencies via CodebaseGraph or FallbackEngine
            if self.code_graph:
                for fp in focal_paths:
                    deps = self.code_graph.get_dependencies(fp)
                    if deps.get("success"):
                        for imp in deps.get("imports", []):
                            imp_clean = imp.replace(".", "/")
                            for kf in getattr(self.code_graph, "file_to_symbols", {}):
                                kf_norm = kf.replace("\\", "/")
                                mod_dot = kf_norm.replace("/", ".").replace(".py", "")
                                if (
                                    imp == mod_dot
                                    or imp in mod_dot
                                    or mod_dot.endswith(imp)
                                    or imp_clean in kf_norm
                                    or kf_norm.endswith(f"{imp_clean}.py")
                                    or Path(kf_norm).stem == imp
                                ):
                                    if kf_norm not in focal_paths:
                                        dep_paths.add(kf_norm)

                            # If imp is a symbol name (e.g. "User")
                            if hasattr(self.code_graph, "symbols"):
                                for sym_node in self.code_graph.symbols.get(imp.lower(), []):
                                    sym_fp = sym_node.filepath.replace("\\", "/")
                                    if sym_fp not in focal_paths:
                                        dep_paths.add(sym_fp)

                        # Include downstream dependent files as well
                        for df in deps.get("dependent_files", []):
                            df_norm = df.replace("\\", "/")
                            if df_norm not in focal_paths:
                                dep_paths.add(df_norm)

            # 2. Gather caller references for target symbols
            for sym in target_symbols:
                if self.fallback_engine:
                    ws_dir = getattr(self.workspace, "root_dir", Path.cwd()) if self.workspace else Path.cwd()
                    ref_res = self.fallback_engine.execute("find_references", sym, workspace_dir=ws_dir)
                    if ref_res.success and isinstance(ref_res.data, list):
                        for r in ref_res.data[:3]:
                            caller_fp = r.get("filepath")
                            if caller_fp:
                                c_norm = caller_fp.replace("\\", "/")
                                if c_norm not in focal_paths:
                                    dep_paths.add(c_norm)

            # 3. Format interfaces (signatures + docstrings only, NO method bodies)
            for dp in sorted(dep_paths):
                if used_l2 >= l2_budget:
                    break

                signatures: List[str] = []
                if self.code_graph and hasattr(self.code_graph, "file_to_symbols"):
                    # Find matching symbols regardless of slash style
                    syms = self.code_graph.file_to_symbols.get(dp)
                    if not syms:
                        for kf, v in self.code_graph.file_to_symbols.items():
                            if dp == kf.replace("\\", "/") or kf.replace("\\", "/") in dp or dp in kf.replace("\\", "/"):
                                syms = v
                                break
                    for s in (syms or []):
                        doc = f'    """{s.docstring}"""' if s.docstring else ""
                        sig_line = f"  • {s.kind} {s.name}{s.signature}"
                        if doc:
                            sig_line += f"\n{doc}"
                        signatures.append(sig_line)

                if signatures:
                    content = f"// Module Interface: {dp}\n" + "\n".join(signatures)
                else:
                    content = f"// Dependent Module: {dp} (Signature interface)"

                est_tok = max(1, len(content) // 4)
                if used_l2 + est_tok > l2_budget:
                    break

                formatted = TrustBoundaryEnforcer.wrap_untrusted_content(
                    content,
                    source_type="interface_signature",
                    identifier=dp,
                )
                bundle.items.append(HierarchicalContextItem(
                    level=RetrievalLevel.LEVEL_2_DEPENDENCY_GRAPH,
                    source_type="interface_signature",
                    filepath=dp,
                    score=0.8,
                    tokens=est_tok,
                    formatted_content=formatted,
                ))
                used_l2 += est_tok

        # ------------------------------------------------------------------
        # LEVEL 3: Semantic Search (Conceptually Relevant Chunks & Fixtures)
        # ------------------------------------------------------------------
        if RetrievalLevel.LEVEL_3_SEMANTIC_SEARCH in active_levels:
            l3_budget = cfg.get_budget(RetrievalLevel.LEVEL_3_SEMANTIC_SEARCH)
            used_l3 = 0

            if self.semantic_index and hasattr(self.semantic_index, "search") and q_text:
                try:
                    sem_results = self.semantic_index.search(query=q_text, top_k=4)
                    for r in sem_results:
                        if used_l3 >= l3_budget:
                            break
                        rfp = r.get("filepath", "")
                        if rfp in focal_paths:
                            continue

                        snip = r.get("content") or r.get("docstring") or ""
                        sym_name = r.get("symbol_name") or rfp
                        formatted = TrustBoundaryEnforcer.wrap_untrusted_content(
                            snip,
                            source_type="semantic_snippet",
                            identifier=rfp,
                            header=f"// Semantic Match: {rfp} ({sym_name})",
                        )
                        est_tok = max(1, len(formatted) // 4)

                        if used_l3 + est_tok > l3_budget:
                            break

                        bundle.items.append(HierarchicalContextItem(
                            level=RetrievalLevel.LEVEL_3_SEMANTIC_SEARCH,
                            source_type="semantic_snippet",
                            filepath=rfp,
                            symbol_name=sym_name,
                            score=r.get("score", 0.7),
                            tokens=est_tok,
                            formatted_content=formatted,
                        ))
                        used_l3 += est_tok
                except Exception as e:
                    logger.debug(f"Semantic search in retrieval hierarchy skipped: {e}")

        # ------------------------------------------------------------------
        # LEVEL 4: Architecture Knowledge & Layer Constraints
        # ------------------------------------------------------------------
        if RetrievalLevel.LEVEL_4_ARCHITECTURE_KNOWLEDGE in active_levels:
            l4_budget = cfg.get_budget(RetrievalLevel.LEVEL_4_ARCHITECTURE_KNOWLEDGE)
            used_l4 = 0
            arch_rules = []

            # 1. Query CBM architecture slices
            if self.cbm and hasattr(self.cbm, "get_architecture_slice"):
                for fp in focal_paths:
                    try:
                        asl = self.cbm.get_architecture_slice(fp)
                        if asl.get("found"):
                            arch_rules.append(
                                f"• Component '{fp}' is in layer '{asl['layer']}'. "
                                f"Conventions: {', '.join(asl.get('frameworks', []) or ['standard'])}"
                            )
                    except Exception:
                        pass

            # 2. Query ArchitectureAnalyzer for layers
            if not arch_rules and self.arch_analyzer and hasattr(self.arch_analyzer, "analyze"):
                try:
                    summary = self.arch_analyzer.analyze()
                    if summary and hasattr(summary, "layers") and summary.layers:
                        for lyr, files in list(summary.layers.items())[:4]:
                            arch_rules.append(f"• Layer [{lyr}]: {len(files)} files (e.g. {Path(files[0]).name if files else 'none'})")
                except Exception:
                    pass

            if arch_rules:
                content = "Architectural Boundaries & Layer Rules:\n" + "\n".join(arch_rules)
                est_tok = max(1, len(content) // 4)
                if est_tok <= l4_budget:
                    bundle.items.append(HierarchicalContextItem(
                        level=RetrievalLevel.LEVEL_4_ARCHITECTURE_KNOWLEDGE,
                        source_type="arch_rule",
                        filepath="architecture://layers",
                        score=0.9,
                        tokens=est_tok,
                        formatted_content=content,
                    ))
                    used_l4 += est_tok

        # ------------------------------------------------------------------
        # LEVEL 5: Broader Repository (PageRank Map Instead of Raw File Dump)
        # ------------------------------------------------------------------
        if RetrievalLevel.LEVEL_5_BROADER_REPOSITORY in active_levels:
            l5_budget = cfg.get_budget(RetrievalLevel.LEVEL_5_BROADER_REPOSITORY)
            repo_map_content = ""

            # 1. Prefer PageRankRepoMap
            if self.code_graph:
                try:
                    from ..codebase.repo_map import PageRankRepoMap
                    pr_map = PageRankRepoMap(self.code_graph)
                    repo_map_content = pr_map.generate_repo_map(max_tokens=l5_budget)
                except Exception as ex:
                    logger.debug(f"PageRank repo map skipped: {ex}")

            # 2. Fallback to capability engine repo map
            if not repo_map_content and self.fallback_engine:
                ws_dir = getattr(self.workspace, "root_dir", Path.cwd()) if self.workspace else Path.cwd()
                map_res = self.fallback_engine.execute("get_repo_map", "", workspace_dir=ws_dir)
                if map_res.success and isinstance(map_res.data, dict):
                    repo_map_content = f"# Ambient Codebase Map ({map_res.provider_name}):\n"
                    if "modules" in map_res.data:
                        repo_map_content += "\n".join(f"• {m}" for m in map_res.data["modules"][:25])
                    elif "files" in map_res.data:
                        repo_map_content += "\n".join(f"• {f}" for f in map_res.data["files"][:25])

            if repo_map_content:
                est_tok = max(1, len(repo_map_content) // 4)
                bundle.items.append(HierarchicalContextItem(
                    level=RetrievalLevel.LEVEL_5_BROADER_REPOSITORY,
                    source_type="repo_map",
                    filepath="codebase://repo_map",
                    score=0.5,
                    tokens=est_tok,
                    formatted_content=repo_map_content,
                ))

        bundle.total_tokens = sum(it.tokens for it in bundle.items)
        return bundle

    def _read_file_content(self, filepath: str) -> str:
        """Helper to read file content safely across workspace implementations."""
        if not self.workspace:
            return ""
        if hasattr(self.workspace, "read_file"):
            try:
                res = self.workspace.read_file(filepath)
                if isinstance(res, dict) and res.get("success"):
                    return res.get("content", "")
                elif isinstance(res, str):
                    return res
            except Exception:
                pass
        return ""
