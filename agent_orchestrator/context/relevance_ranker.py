"""
Relevance Ranker & Hierarchical Context Scoper.
Scores workspace files and symbols against an active task using lexical overlap
and AST call/dependency graphs to construct a 3-tier Context Pyramid:
  - FOCAL: Directly targeted files (full or windowed content).
  - INTERFACE: Directly imported/dependent modules (signatures + docstrings only).
  - OUTLINE: High-level repo symbol map.
Equipped with ContextSufficiencyOracle and automated 3-pass retrieval feedback loop.
"""
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from ..codebase.symbols import is_module_import_match
from .token_estimator import estimate_tokens


class ContextTier(str, Enum):
    FOCAL = "FOCAL"          # Target files to modify/inspect
    INTERFACE = "INTERFACE"  # Dependent/imported files (signatures only)
    OUTLINE = "OUTLINE"      # Ambient repository overview


@dataclass
class RankedContextItem:
    """A prioritized context item with its tier and formatted content."""
    filepath: str
    tier: ContextTier
    score: float
    symbols: List[str] = field(default_factory=list)
    formatted_content: str = ""
    estimated_tokens: int = 0


class RelevanceRanker:
    """
    Ranks files and symbols relative to a task using AST knowledge graph and lexical scoring.
    Features automated multi-pass expansion when initial context retrieval is insufficient.
    """

    def __init__(
        self,
        code_graph: Optional[Any] = None,
        workspace: Optional[Any] = None,
        semantic_index: Optional[Any] = None,
        cbm: Optional[Any] = None,
    ):
        self.code_graph = code_graph
        self.workspace = workspace
        self.semantic_index = semantic_index
        self.cbm = cbm
        self.last_sufficiency_evaluation: Optional[Any] = None

    @staticmethod
    def extract_keywords(text: str) -> Set[str]:
        """Extracts meaningful identifiers and keywords from task descriptions."""
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)
        stopwords = {
            "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "with",
            "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
            "do", "does", "did", "can", "could", "should", "would", "will", "shall",
            "this", "that", "these", "those", "it", "its", "from", "as", "by", "of",
        }
        return {t.lower() for t in tokens if len(t) > 2 and t.lower() not in stopwords}

    def _format_ranked_items(
        self,
        focal_paths: Set[str],
        interface_paths: Set[str],
        cbm_slice: Optional[str],
        max_focal_tokens: int,
        max_interface_tokens: int,
    ) -> List[RankedContextItem]:
        """Formats discovered focal and interface files into RankedContextItem tiers."""
        ranked_items: List[RankedContextItem] = []
        focal_used_tokens = 0

        for fp in sorted(focal_paths):
            content = ""
            if self.workspace and hasattr(self.workspace, "read_file"):
                raw_c = self.workspace.read_file(fp)
                if isinstance(raw_c, dict) and raw_c.get("success"):
                    content = raw_c.get("content", "")
                elif isinstance(raw_c, str):
                    content = raw_c

            if not content:
                content = "[File to be created or currently empty]"

            est_tok = estimate_tokens(content)
            if focal_used_tokens + est_tok > max_focal_tokens:
                avail = max(200, max_focal_tokens - focal_used_tokens)
                lines = content.splitlines()
                content = "\n".join(lines[:avail]) + f"\n... [Truncated to {avail} lines to fit focal budget] ..."
                est_tok = estimate_tokens(content)

            focal_used_tokens += est_tok
            formatted = f"```\n// File: {fp} (Focal Target)\n{content}\n```"
            ranked_items.append(RankedContextItem(
                filepath=fp,
                tier=ContextTier.FOCAL,
                score=100.0,
                formatted_content=formatted,
                estimated_tokens=est_tok,
            ))

        interface_used_tokens = 0
        if cbm_slice:
            cbm_tok = estimate_tokens(cbm_slice)
            ranked_items.append(RankedContextItem(
                filepath="codebase_memory://subgraph",
                tier=ContextTier.INTERFACE,
                score=95.0,
                formatted_content=f"```markdown\n{cbm_slice}\n```",
                estimated_tokens=cbm_tok,
            ))
            interface_used_tokens += cbm_tok

        for ip in sorted(interface_paths):
            if interface_used_tokens >= max_interface_tokens:
                break

            sym_signatures: List[str] = []
            if self.code_graph and hasattr(self.code_graph, "file_to_symbols"):
                file_syms = self.code_graph.file_to_symbols.get(ip, [])
                for s in file_syms:
                    doc = f'    """{s.docstring}"""' if s.docstring else ""
                    sig_line = f"  • {s.kind} {s.name}{s.signature}"
                    if doc:
                        sig_line += f"\n{doc}"
                    sym_signatures.append(sig_line)

            if sym_signatures:
                content = f"// Module Interface: {ip}\n" + "\n".join(sym_signatures)
            else:
                content = f"// Module Reference: {ip} (Dependent module)"

            est_tok = estimate_tokens(content)
            if interface_used_tokens + est_tok > max_interface_tokens:
                break

            interface_used_tokens += est_tok
            formatted = f"```\n{content}\n```"
            ranked_items.append(RankedContextItem(
                filepath=ip,
                tier=ContextTier.INTERFACE,
                score=50.0,
                symbols=[s.split(" ")[2] for s in sym_signatures if len(s.split(" ")) > 2],
                formatted_content=formatted,
                estimated_tokens=est_tok,
            ))

        return ranked_items

    def _expand_retrieval(
        self,
        task_info: Dict[str, Any],
        eval_result: Any,
        focal_paths: Set[str],
        interface_paths: Set[str],
    ) -> Tuple[Set[str], Set[str]]:
        """
        Executes automated 3-pass retrieval expansion:
        Pass 1: Query Reformulation & Semantic Search
        Pass 2: Multi-Hop Call & Impact Radius Expansion
        Pass 3: Global Symbol Resolution for Missing Entities
        """
        new_focal = set(focal_paths)
        new_interface = set(interface_paths)

        missing = getattr(eval_result, "missing_entities", [])

        # Pass 1: Query Reformulation & Semantic Search
        if missing and self.semantic_index and hasattr(self.semantic_index, "search"):
            for m in missing[:5]:
                terms = re.findall(r"[A-Za-z][a-z0-9]*", m)
                reformulated = " ".join(t.lower() for t in terms if t)
                if reformulated:
                    try:
                        results = self.semantic_index.search(query=reformulated, top_k=2)
                        for r in results:
                            fp = r.get("filepath")
                            if fp and fp not in new_focal:
                                new_interface.add(fp)
                    except Exception:
                        pass

        # Pass 2: Multi-Hop Call & Impact Radius Expansion
        if self.code_graph:
            current_targets = list(new_focal) + list(new_interface)[:5]
            for target_fp in current_targets:
                if hasattr(self.code_graph, "get_impact_radius"):
                    try:
                        impact = self.code_graph.get_impact_radius(target_fp, max_depth=2)
                        if impact.get("success"):
                            for aff in impact.get("affected_files", []):
                                if aff not in new_focal:
                                    new_interface.add(aff)
                    except Exception:
                        pass

        # Pass 3: Global Symbol Resolution for Missing Entities
        if self.code_graph and hasattr(self.code_graph, "find_symbol"):
            for entity in missing:
                try:
                    res = self.code_graph.find_symbol(entity)
                    if res.get("exact_match") or res.get("found"):
                        for sym in res.get("symbols", []):
                            fp = sym.get("filepath")
                            if fp and fp not in new_focal:
                                new_interface.add(fp)
                except Exception:
                    pass

        return new_focal, new_interface

    def rank_context_for_task(
        self,
        task_info: Dict[str, Any],
        max_focal_tokens: int = 8000,
        max_interface_tokens: int = 3500,
        expand_if_insufficient: bool = True,
        min_confidence: float = 0.70,
    ) -> List[RankedContextItem]:
        """
        Ranks and formats relevant files into FOCAL and INTERFACE tiers within token limits.
        If initial retrieval confidence is below min_confidence, triggers automated expansion loop.
        """
        from .sufficiency_oracle import ContextSufficiencyOracle

        objective = task_info.get("objective", "")
        description = task_info.get("description", "")
        inputs = task_info.get("inputs", []) or []
        outputs = task_info.get("outputs", []) or []
        query_text = f"{objective} {description} {' '.join(inputs)} {' '.join(outputs)}"
        keywords = self.extract_keywords(query_text)

        # 1. Identify Explicit Focal Files (from inputs and outputs)
        focal_paths: Set[str] = set()
        for p in inputs + outputs:
            clean = p.split(":")[0].strip().replace("\\", "/")
            if clean:
                focal_paths.add(clean)

        # 2. Gather Interface Dependencies via CodeGraph
        interface_paths: Set[str] = set()

        if self.code_graph:
            for fp in focal_paths:
                deps = self.code_graph.get_dependencies(fp)
                if deps.get("success"):
                    for imp in deps.get("imports", []):
                        for known_file in getattr(self.code_graph, "file_to_symbols", {}):
                            if is_module_import_match(imp, known_file, fp):
                                if known_file not in focal_paths:
                                    interface_paths.add(known_file)

                if hasattr(self.code_graph, "get_impact_radius"):
                    impact = self.code_graph.get_impact_radius(fp, max_depth=2)
                    if impact.get("success"):
                        for aff in impact.get("affected_files", []):
                            if aff not in focal_paths:
                                interface_paths.add(aff)

        # 3. Semantic index lookup for query keywords
        if self.semantic_index and hasattr(self.semantic_index, "search"):
            try:
                sem_results = self.semantic_index.search(query=query_text, top_k=3)
                for res in sem_results:
                    res_fp = res.get("filepath", "")
                    if res_fp and res_fp not in focal_paths:
                        interface_paths.add(res_fp)
            except Exception:
                pass

        # 4. Codebase Memory (CBM) Sub-graph Retrieval
        cbm_slice: Optional[str] = None
        if self.cbm and hasattr(self.cbm, "retrieve_task_subgraph"):
            try:
                subg = self.cbm.retrieve_task_subgraph(task_info=task_info, max_tokens=max_interface_tokens)
                if subg.get("success"):
                    cbm_slice = subg.get("formatted_context", "")
                    for ff in subg.get("focal_files", []):
                        focal_paths.add(ff)
            except Exception:
                pass

        # 5. Targeted Interface Discovery (replacing flat unconstrained ALL FILES scan)
        if len(interface_paths) < 10 and self.code_graph and hasattr(self.code_graph, "file_to_symbols"):
            for wf in list(self.code_graph.file_to_symbols.keys())[:50]:
                wf_clean = wf.replace("\\", "/")
                if wf_clean in focal_paths or wf_clean in interface_paths:
                    continue
                wf_keywords = self.extract_keywords(wf_clean)
                if keywords.intersection(wf_keywords):
                    interface_paths.add(wf_clean)
                    if len(interface_paths) >= 15:
                        break
        elif len(interface_paths) < 5 and self.workspace and hasattr(self.workspace, "list_files"):
            all_workspace_files = self.workspace.list_files()[:30]
            for wf in all_workspace_files:
                wf_clean = wf.replace("\\", "/")
                if wf_clean in focal_paths or wf_clean in interface_paths:
                    continue
                wf_keywords = self.extract_keywords(wf_clean)
                if keywords.intersection(wf_keywords):
                    interface_paths.add(wf_clean)
                    if len(interface_paths) >= 10:
                        break

        # 6. Format initial items
        ranked_items = self._format_ranked_items(
            focal_paths=focal_paths,
            interface_paths=interface_paths,
            cbm_slice=cbm_slice,
            max_focal_tokens=max_focal_tokens,
            max_interface_tokens=max_interface_tokens,
        )

        # 7. Evaluate Sufficiency & Evidence
        oracle = ContextSufficiencyOracle(min_confidence_threshold=min_confidence)
        eval_result = oracle.evaluate(
            task_info=task_info,
            ranked_items=ranked_items,
            code_graph=self.code_graph,
            semantic_index=self.semantic_index,
            cbm=self.cbm,
        )

        # 8. Automated Retrieval Feedback Expansion Loop
        if expand_if_insufficient and not eval_result.is_sufficient:
            focal_paths, interface_paths = self._expand_retrieval(
                task_info=task_info,
                eval_result=eval_result,
                focal_paths=focal_paths,
                interface_paths=interface_paths,
            )
            # Re-format items with expanded paths
            ranked_items = self._format_ranked_items(
                focal_paths=focal_paths,
                interface_paths=interface_paths,
                cbm_slice=cbm_slice,
                max_focal_tokens=max_focal_tokens,
                max_interface_tokens=max_interface_tokens,
            )
            # Re-evaluate sufficiency
            eval_result = oracle.evaluate(
                task_info=task_info,
                ranked_items=ranked_items,
                code_graph=self.code_graph,
                semantic_index=self.semantic_index,
                cbm=self.cbm,
            )

        self.last_sufficiency_evaluation = eval_result
        return ranked_items


__all__ = ["ContextTier", "RankedContextItem", "RelevanceRanker"]
