"""
Semantic & Lexical Code Search Index.
Implements BM25 / TF-IDF lexical search with identifier splitting and pluggable vector embeddings.
"""
import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .graph import CodebaseGraph
from .symbols import SymbolNode


@dataclass
class CodeChunk:
    id: str
    symbol_name: str
    filepath: str
    kind: str
    start_line: int
    end_line: int
    content: str
    docstring: str
    tokens: List[str] = field(default_factory=list)
    term_frequencies: Dict[str, int] = field(default_factory=dict)
    length: int = 0


class SemanticCodeIndex:
    """
    BM25 and Hybrid Code Search Index with Incremental Updates and Embedding Cache.
    Chunks code by AST symbols, tokenizes identifiers, and provides concept retrieval.
    """

    def __init__(
        self,
        code_graph: Optional[CodebaseGraph] = None,
        embedding_fn: Optional[Callable[[str], List[float]]] = None,
        k1: float = 1.5,
        b: float = 0.75,
    ):
        self.code_graph = code_graph
        self.embedding_fn = embedding_fn
        self.k1 = k1
        self.b = b

        self.chunks: List[CodeChunk] = []
        self.doc_frequencies: Dict[str, int] = {}
        self.avg_doc_len: float = 0.0
        self.total_docs: int = 0
        self.dense_embeddings: Dict[str, List[float]] = {}  # chunk_id -> vector
        self.embedding_cache: Dict[str, List[float]] = {}  # sha256(snippet) -> vector

        if self.code_graph:
            self.build_index()

    @staticmethod
    def tokenize(text: str) -> List[str]:
        """
        Splits code text into normalized sub-tokens:
        Splits camelCase, PascalCase, snake_case, and extracts words.
        """
        if not text:
            return []

        # Replace punctuation with spaces
        cleaned = re.sub(r"[^A-Za-z0-9_]", " ", text)
        words = cleaned.split()
        subtokens: List[str] = []

        for w in words:
            # Split snake_case
            parts = w.split("_")
            for p in parts:
                if not p:
                    continue
                # Split camelCase / PascalCase: e.g. "TokenBucket" -> ["Token", "Bucket"]
                camel_split = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z][a-z0-9]|\b)", p)
                if camel_split:
                    for cs in camel_split:
                        if len(cs) > 1:
                            subtokens.append(cs.lower())
                elif len(p) > 1:
                    subtokens.append(p.lower())

        return subtokens

    def build_index(self) -> None:
        """Chunks symbols from code graph and indexes term frequencies for BM25."""
        self.chunks.clear()
        self.doc_frequencies.clear()
        self.dense_embeddings.clear()

        if not self.code_graph:
            return

        total_len = 0

        for filepath, syms in self.code_graph.file_to_symbols.items():
            for s in syms:
                chunk_id = f"{filepath}:{s.name}:{s.start_line}"
                snippet = f"{s.signature}\n{s.docstring}"

                # Tokenize signature, docstring, and symbol name
                name_tokens = self.tokenize(s.name) * 3  # 3x boost for symbol name
                body_tokens = self.tokenize(snippet)
                all_tokens = name_tokens + body_tokens

                # Term frequencies
                tf: Dict[str, int] = {}
                for t in all_tokens:
                    tf[t] = tf.get(t, 0) + 1

                chunk = CodeChunk(
                    id=chunk_id,
                    symbol_name=s.name,
                    filepath=filepath,
                    kind=s.kind,
                    start_line=s.start_line,
                    end_line=s.end_line,
                    content=snippet,
                    docstring=s.docstring,
                    tokens=all_tokens,
                    term_frequencies=tf,
                    length=len(all_tokens),
                )
                self.chunks.append(chunk)
                total_len += chunk.length

                # Update document frequencies
                for term in set(all_tokens):
                    self.doc_frequencies[term] = self.doc_frequencies.get(term, 0) + 1

        self.total_docs = len(self.chunks)
        self.avg_doc_len = (total_len / self.total_docs) if self.total_docs > 0 else 0.0

        # Optional Dense Embeddings with Cache
        if self.embedding_fn and self.chunks:
            for c in self.chunks:
                try:
                    snippet_text = f"{c.symbol_name}: {c.content}"
                    snippet_hash = hashlib.sha256(snippet_text.encode("utf-8")).hexdigest()
                    if snippet_hash in self.embedding_cache:
                        self.dense_embeddings[c.id] = self.embedding_cache[snippet_hash]
                    else:
                        vec = self.embedding_fn(snippet_text)
                        if vec:
                            self.embedding_cache[snippet_hash] = vec
                            self.dense_embeddings[c.id] = vec
                except Exception:
                    pass

    def delete_file(self, filepath: str) -> None:
        """Removes all indexed chunks and updates document frequencies for a deleted/evicted file."""
        rel_path = str(filepath).replace("\\", "/").strip("/")
        old_chunks = [c for c in self.chunks if c.filepath == rel_path]
        if not old_chunks:
            return

        for c in old_chunks:
            for term in set(c.tokens):
                if term in self.doc_frequencies:
                    self.doc_frequencies[term] -= 1
                    if self.doc_frequencies[term] <= 0:
                        del self.doc_frequencies[term]
            self.dense_embeddings.pop(c.id, None)

        self.chunks = [c for c in self.chunks if c.filepath != rel_path]
        self.total_docs = len(self.chunks)
        total_len = sum(c.length for c in self.chunks)
        self.avg_doc_len = (total_len / self.total_docs) if self.total_docs > 0 else 0.0

    def reindex_file(self, filepath: str, symbols: List[SymbolNode]) -> None:
        """Incrementally re-indexes symbols for a single file and reuses cached embeddings."""
        rel_path = str(filepath).replace("\\", "/").strip("/")
        self.delete_file(rel_path)

        for s in symbols:
            chunk_id = f"{rel_path}:{s.name}:{s.start_line}"
            snippet = f"{s.signature}\n{s.docstring}"

            name_tokens = self.tokenize(s.name) * 3
            body_tokens = self.tokenize(snippet)
            all_tokens = name_tokens + body_tokens

            tf: Dict[str, int] = {}
            for t in all_tokens:
                tf[t] = tf.get(t, 0) + 1

            chunk = CodeChunk(
                id=chunk_id,
                symbol_name=s.name,
                filepath=rel_path,
                kind=s.kind,
                start_line=s.start_line,
                end_line=s.end_line,
                content=snippet,
                docstring=s.docstring,
                tokens=all_tokens,
                term_frequencies=tf,
                length=len(all_tokens),
            )
            self.chunks.append(chunk)

            for term in set(all_tokens):
                self.doc_frequencies[term] = self.doc_frequencies.get(term, 0) + 1

            if self.embedding_fn:
                try:
                    snippet_text = f"{chunk.symbol_name}: {chunk.content}"
                    snippet_hash = hashlib.sha256(snippet_text.encode("utf-8")).hexdigest()
                    if snippet_hash in self.embedding_cache:
                        self.dense_embeddings[chunk.id] = self.embedding_cache[snippet_hash]
                    else:
                        vec = self.embedding_fn(snippet_text)
                        if vec:
                            self.embedding_cache[snippet_hash] = vec
                            self.dense_embeddings[chunk.id] = vec
                except Exception:
                    pass

        self.total_docs = len(self.chunks)
        total_len = sum(c.length for c in self.chunks)
        self.avg_doc_len = (total_len / self.total_docs) if self.total_docs > 0 else 0.0

    def _score_bm25(self, query_tokens: List[str], chunk: CodeChunk) -> float:
        """Calculates standard Okapi BM25 score for a chunk."""
        score = 0.0
        for q in query_tokens:
            if q not in chunk.term_frequencies:
                continue

            tf = chunk.term_frequencies[q]
            df = self.doc_frequencies.get(q, 0)
            idf = math.log(1.0 + (self.total_docs - df + 0.5) / (df + 0.5))

            numerator = tf * (self.k1 + 1.0)
            denominator = tf + self.k1 * (1.0 - self.b + self.b * (chunk.length / max(1.0, self.avg_doc_len)))
            score += idf * (numerator / denominator)

        return score

    @staticmethod
    def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Searches code symbols using BM25 and optional dense embeddings.
        Returns top_k ranked symbols with snippets and relevance scores.
        """
        if not self.chunks:
            return []

        q_tokens = self.tokenize(query)
        if not q_tokens:
            return []

        # 1. Lexical BM25 Ranking
        bm25_scored: List[Tuple[float, CodeChunk]] = []
        for c in self.chunks:
            s = self._score_bm25(q_tokens, c)
            if s > 0.0:
                bm25_scored.append((s, c))

        bm25_scored.sort(key=lambda x: x[0], reverse=True)

        # 2. Dense Vector Ranking (if embedding_fn available)
        query_vec: Optional[List[float]] = None
        if self.embedding_fn and self.dense_embeddings:
            try:
                query_vec = self.embedding_fn(query)
            except Exception:
                query_vec = None

        if not query_vec:
            # Pure BM25 results
            results = []
            for score, c in bm25_scored[:top_k]:
                results.append({
                    "chunk_id": c.id,
                    "symbol_name": c.symbol_name,
                    "filepath": c.filepath,
                    "kind": c.kind,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "content": c.content,
                    "docstring": c.docstring,
                    "score": round(score, 3),
                    "search_type": "bm25",
                })
            return results

        # 3. Hybrid Reciprocal Rank Fusion (RRF)
        dense_scored: List[Tuple[float, CodeChunk]] = []
        for c in self.chunks:
            c_vec = self.dense_embeddings.get(c.id)
            if c_vec:
                sim = self._cosine_similarity(query_vec, c_vec)
                dense_scored.append((sim, c))

        dense_scored.sort(key=lambda x: x[0], reverse=True)

        # RRF Combination: 1 / (60 + rank)
        rrf_scores: Dict[str, float] = {}
        for rank, (_, c) in enumerate(bm25_scored):
            rrf_scores[c.id] = rrf_scores.get(c.id, 0.0) + (1.0 / (60.0 + rank + 1.0))
        for rank, (_, c) in enumerate(dense_scored):
            rrf_scores[c.id] = rrf_scores.get(c.id, 0.0) + (1.0 / (60.0 + rank + 1.0))

        sorted_rrf = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        chunk_map = {c.id: c for c in self.chunks}

        results = []
        for cid, rrf_s in sorted_rrf[:top_k]:
            c = chunk_map[cid]
            results.append({
                "chunk_id": c.id,
                "symbol_name": c.symbol_name,
                "filepath": c.filepath,
                "kind": c.kind,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "content": c.content,
                "docstring": c.docstring,
                "score": round(rrf_s, 4),
                "search_type": "hybrid_rrf",
            })

        return results
