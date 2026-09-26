"""
Context Deduplication Engine.
Uses 'rapidfuzz' for fast fuzzy string matching and near-duplicate elimination.
Prevents prompt inflation from repeated error tracebacks, duplicate diff blocks,
and redundant memory records.
"""
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

import rapidfuzz
from rapidfuzz import fuzz

logger = logging.getLogger("context.dedup")


class ContextDeduplicator:
    """
    Fuzzy deduplication engine using rapidfuzz token_set_ratio and ratio algorithms.
    """

    def __init__(self, default_threshold: float = 85.0):
        self.default_threshold = default_threshold

    def is_near_duplicate(self, text_a: str, text_b: str, threshold: Optional[float] = None) -> bool:
        """
        Evaluates whether two text segments are near-duplicates using rapidfuzz.
        """
        th = threshold if threshold is not None else self.default_threshold
        if not text_a or not text_b:
            return False
        if text_a == text_b:
            return True

        # Rapidfuzz token_set_ratio is robust to reordering, variable whitespace, and minor variations
        ratio = fuzz.token_set_ratio(text_a, text_b)
        return ratio >= th

    def dedup_strings(self, items: List[str], threshold: Optional[float] = None) -> List[str]:
        """
        Eliminates duplicate or near-duplicate strings while preserving order.
        """
        th = threshold if threshold is not None else self.default_threshold
        unique_items: List[str] = []

        for item in items:
            clean = item.strip()
            if not clean:
                continue

            # Check if this item is a near-duplicate of any already accepted item
            is_dup = False
            for existing in unique_items:
                if fuzz.token_set_ratio(clean, existing) >= th:
                    is_dup = True
                    break

            if not is_dup:
                unique_items.append(clean)

        return unique_items

    def dedup_errors(self, errors: List[str], threshold: float = 80.0) -> List[str]:
        """
        Specialized error deduplication: collapses repeated stack traces and identical linter errors.
        Appends occurrence count (e.g. [x3 instances]) when repeated.
        """
        if not errors:
            return []

        unique_groups: List[Dict[str, Any]] = []
        for err in errors:
            clean = err.strip()
            if not clean:
                continue

            matched = False
            for group in unique_groups:
                if fuzz.token_set_ratio(clean, group["rep"]) >= threshold:
                    group["count"] += 1
                    matched = True
                    break

            if not matched:
                unique_groups.append({"rep": clean, "count": 1})

        result: List[str] = []
        for g in unique_groups:
            if g["count"] > 1:
                result.append(f"{g['rep']} [x{g['count']} instances]")
            else:
                result.append(g["rep"])
        return result

    def dedup_candidates(
        self,
        candidates: List[Dict[str, Any]],
        content_key: str = "content",
        threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Filters a list of candidate dictionaries, discarding near-duplicate entries based on content_key.
        """
        th = threshold if threshold is not None else self.default_threshold
        unique_cands: List[Dict[str, Any]] = []

        for cand in candidates:
            content = cand.get(content_key, "").strip()
            if not content:
                continue

            is_dup = False
            for existing in unique_cands:
                existing_content = existing.get(content_key, "").strip()
                if fuzz.token_set_ratio(content, existing_content) >= th:
                    is_dup = True
                    break

            if not is_dup:
                unique_cands.append(cand)

        return unique_cands
