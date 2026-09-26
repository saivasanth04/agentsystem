"""
Context Relevance & Diversity Ranking.
Leverages 'numpy' for vectorized cosine similarity, dot products,
and Maximal Marginal Relevance (MMR) diversity reranking.
"""
from dataclasses import dataclass, field
import logging
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import numpy as np

logger = logging.getLogger("context.ranking")


@dataclass
class RankedCandidate:
    """A scored context candidate."""
    item_id: str
    content: str
    relevance_score: float
    category: str = "general"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return self.relevance_score

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "content": self.content,
            "relevance_score": round(self.relevance_score, 4),
            "score": round(self.relevance_score, 4),
            "category": self.category,
            "metadata": self.metadata,
        }


class ContextRanker:
    """
    Ranks context items using numpy vectorized similarity and MMR diversity selection.
    """

    def __init__(self, diversity_lambda: float = 0.7):
        # lambda = 1.0 means pure relevance; lambda = 0.5 balances relevance and novelty
        self.diversity_lambda = diversity_lambda

    def _tokenize(self, text: str) -> List[str]:
        """Simple alphanumeric tokenizer for TF-IDF feature extraction."""
        return re.findall(r"[a-zA-Z0-9_\.]+", text.lower())

    def _build_tfidf_vectors(self, query: str, documents: List[str]) -> Tuple[np.ndarray, np.ndarray]:
        """
        Constructs TF-IDF feature vectors for query and documents using numpy.
        """
        all_texts = [query] + documents
        tokenized_corpus = [self._tokenize(t) for t in all_texts]

        # Build vocabulary
        vocab: Dict[str, int] = {}
        for tokens in tokenized_corpus:
            for tok in tokens:
                if tok not in vocab:
                    vocab[tok] = len(vocab)

        V = len(vocab)
        N = len(documents)
        if V == 0 or N == 0:
            return np.zeros((1, max(1, V))), np.zeros((max(1, N), max(1, V)))

        # Term frequencies
        tf_matrix = np.zeros((len(all_texts), V), dtype=np.float32)
        for doc_idx, tokens in enumerate(tokenized_corpus):
            for tok in tokens:
                tf_matrix[doc_idx, vocab[tok]] += 1.0

        # Document frequencies (across documents only)
        df = np.sum(tf_matrix[1:] > 0, axis=0) + 1.0  # Laplace smoothing
        idf = np.log((N + 1.0) / df) + 1.0

        # TF-IDF calculation
        tfidf = tf_matrix * idf

        # L2 Normalize
        norms = np.linalg.norm(tfidf, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        normalized = tfidf / norms

        query_vec = normalized[0:1]  # shape (1, V)
        doc_vecs = normalized[1:]    # shape (N, V)
        return query_vec, doc_vecs

    def rank_candidates(
        self,
        query: str,
        candidates: List[Union[str, Dict[str, Any]]],
        top_k: Optional[int] = None,
        use_mmr: bool = True,
    ) -> List[RankedCandidate]:
        """Convenience method accepting either strings or dictionaries."""
        cand_dicts: List[Dict[str, Any]] = []
        for i, c in enumerate(candidates):
            if isinstance(c, dict):
                cand_dicts.append(c)
            else:
                cand_dicts.append({"id": f"item_{i}", "content": str(c)})
        return self.rank(query, cand_dicts, top_k=top_k, use_mmr=use_mmr)

    def rank(
        self,
        query: str,
        candidates: List[Union[str, Dict[str, Any]]],
        top_k: Optional[int] = None,
        use_mmr: bool = True,
    ) -> List[RankedCandidate]:
        """
        Ranks candidate documents against a query using numpy vectorized cosine similarity.
        Applies MMR to penalize redundant candidates.
        """
        if not candidates:
            return []

        # Normalize candidates if strings passed
        norm_candidates: List[Dict[str, Any]] = []
        for i, c in enumerate(candidates):
            if isinstance(c, dict):
                norm_candidates.append(c)
            else:
                norm_candidates.append({"id": f"item_{i}", "content": str(c)})
        candidates = norm_candidates

        doc_texts = [c.get("content", "") for c in candidates]
        query_vec, doc_vecs = self._build_tfidf_vectors(query, doc_texts)

        # 1. Cosine similarity to query: shape (N,)
        sim_scores = np.squeeze(np.dot(doc_vecs, query_vec.T), axis=1)

        N = len(candidates)
        k = min(top_k or N, N)

        if not use_mmr or N <= 1:
            # Simple sorting by similarity score
            sorted_indices = np.argsort(-sim_scores)[:k]
            ranked = []
            for idx in sorted_indices:
                cand = candidates[idx]
                ranked.append(RankedCandidate(
                    item_id=str(cand.get("id") or cand.get("item_id") or idx),
                    content=cand.get("content", ""),
                    relevance_score=float(sim_scores[idx]),
                    category=cand.get("category", "general"),
                    metadata=cand.get("metadata", {}),
                ))
            return ranked

        # 2. Maximal Marginal Relevance (MMR)
        # Pairwise document similarity matrix: shape (N, N)
        pairwise_sim = np.dot(doc_vecs, doc_vecs.T)

        selected_indices: List[int] = []
        candidate_indices = list(range(N))

        # Select top item first
        first_idx = int(np.argmax(sim_scores))
        selected_indices.append(first_idx)
        candidate_indices.remove(first_idx)

        while len(selected_indices) < k and candidate_indices:
            best_mmr_score = -float("inf")
            best_cand_idx = -1

            for c_idx in candidate_indices:
                rel = sim_scores[c_idx]
                max_sim_to_selected = float(np.max(pairwise_sim[c_idx, selected_indices]))
                mmr_score = (self.diversity_lambda * rel) - ((1.0 - self.diversity_lambda) * max_sim_to_selected)

                if mmr_score > best_mmr_score:
                    best_mmr_score = mmr_score
                    best_cand_idx = c_idx

            if best_cand_idx != -1:
                selected_indices.append(best_cand_idx)
                candidate_indices.remove(best_cand_idx)
            else:
                break

        ranked = []
        for idx in selected_indices:
            cand = candidates[idx]
            ranked.append(RankedCandidate(
                item_id=str(cand.get("id") or cand.get("item_id") or idx),
                content=cand.get("content", ""),
                relevance_score=float(sim_scores[idx]),
                category=cand.get("category", "general"),
                metadata=cand.get("metadata", {}),
            ))
        return ranked


from enum import Enum


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

    def rank_context_for_task(
        self,
        task_info: Dict[str, Any],
        max_focal_tokens: int = 8000,
        max_interface_tokens: int = 3500,
    ) -> List[RankedContextItem]:
        items: List[RankedContextItem] = []
        inputs = task_info.get("inputs", []) or []
        outputs = task_info.get("outputs", []) or []
        target_fps = set(inputs + outputs)

        for fp in sorted(list(target_fps)):
            clean_fp = str(fp).replace("\\", "/").strip()
            if not clean_fp or clean_fp == "*":
                continue
            content = ""
            if self.workspace and hasattr(self.workspace, "read_file"):
                try:
                    res = self.workspace.read_file(clean_fp)
                    content = res if isinstance(res, str) else res.get("content", "")
                except Exception:
                    content = ""
            items.append(RankedContextItem(
                filepath=clean_fp,
                tier=ContextTier.FOCAL,
                score=1.0,
                formatted_content=content,
                estimated_tokens=len(content) // 4,
            ))
        return items

