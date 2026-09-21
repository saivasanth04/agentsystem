"""
Semantic Memory Store: Conceptual Knowledge, Framework Patterns, and Library Contracts.
Stores non-code knowledge (framework idioms, library gotchas, architecture invariants,
domain requirements) with category filtering and keyword/concept retrieval.
"""
from dataclasses import dataclass, field
from datetime import datetime
import re
import threading
from typing import Any, Dict, List, Optional


@dataclass
class SemanticItem:
    """
    An individual semantic knowledge entry.
    """
    key: str
    content: str
    category: str = "GENERAL"  # FRAMEWORK, LIBRARY, ARCHITECTURE, DOMAIN, GENERAL
    tags: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "category": self.category.upper().strip(),
            "content": self.content.strip(),
            "tags": list(self.tags),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SemanticItem":
        return cls(
            key=data["key"],
            content=data["content"],
            category=data.get("category", "GENERAL"),
            tags=list(data.get("tags", [])),
            created_at=data.get("created_at", datetime.now().isoformat()),
        )


class SemanticMemoryStore:
    """
    Thread-safe store for semantic and conceptual knowledge with category filtering and concept search.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._items: Dict[str, SemanticItem] = {}

    def store(self, item: SemanticItem) -> None:
        """Stores or updates a semantic knowledge item."""
        with self._lock:
            self._items[item.key.lower().strip()] = item

    def batch_store(self, items: List[SemanticItem]) -> None:
        """Stores multiple semantic knowledge items."""
        with self._lock:
            for item in items:
                self.store(item)

    def get(self, key: str) -> Optional[SemanticItem]:
        with self._lock:
            return self._items.get(key.lower().strip())

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        top_k: int = 5,
    ) -> List[SemanticItem]:
        """
        Searches semantic items matching query terms and optional category.
        """
        query_clean = query.lower().strip()
        tokens = set(re.findall(r"\w+", query_clean))

        with self._lock:
            scored_items = []
            cat_filter = category.upper().strip() if category else None

            for item in self._items.values():
                if cat_filter and item.category != cat_filter:
                    continue

                item_text = f"{item.key} {item.content} {' '.join(item.tags)}".lower()
                item_tokens = set(re.findall(r"\w+", item_text))

                # Score based on token overlap and key substring matches
                score = 0.0
                if query_clean in item.key.lower():
                    score += 5.0
                if tokens:
                    overlap = len(tokens.intersection(item_tokens))
                    score += overlap * 1.5

                for tag in item.tags:
                    if tag.lower() in tokens:
                        score += 2.0

                if score > 0.0 or not query_clean:
                    scored_items.append((score, item))

            scored_items.sort(key=lambda x: x[0], reverse=True)
            return [it for _, it in scored_items[:top_k]]

    def format_for_prompt(self, items: List[SemanticItem], max_tokens: int = 1000) -> str:
        """
        Formats retrieved semantic knowledge into a prompt section.
        """
        if not items:
            return ""

        lines = ["**Semantic Knowledge & Contracts**:"]
        for it in items:
            lines.append(f"\n• [{it.category}] **{it.key}**:")
            lines.append(f"  {it.content}")

        summary = "\n".join(lines)
        max_chars = max_tokens * 4
        if len(summary) > max_chars:
            return summary[:max_chars] + "\n... [Semantic knowledge truncated] ..."
        return summary

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def list_all(self, category: Optional[str] = None) -> List[SemanticItem]:
        with self._lock:
            if category:
                cat_clean = category.upper().strip()
                return [it for it in self._items.values() if it.category == cat_clean]
            return list(self._items.values())
