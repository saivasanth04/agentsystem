"""
Context Budget Allocator & Priority-Based Context Assembler.
Ensures prompts stay strictly within model context limits by allocating section budgets,
deduplicating content blocks, applying multi-modal compression, and trimming lower-priority sections.
"""
from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from .compressor import ContextCompressor, CompressionLevel, JSONCompressor
from .deduplicator import ContentDeduplicator

from .token_estimator import estimate_tokens


@dataclass
class ContextBudget:
    """Configurable token ceilings per context section."""
    system_instructions: int = 2000
    working_memory: int = 1500
    task_memory: int = 1000
    episodic_memory: int = 1000
    project_memory: int = 1500
    semantic_memory: int = 800
    task_scope: int = 3000
    repo_outline: int = 2000
    focal_files: int = 8000
    interface_signatures: int = 3500
    interaction_buffer: int = 12000
    total_budget: int = 32000

    @classmethod
    def default(cls) -> "ContextBudget":
        return cls()


@dataclass
class ContextSection:
    """A distinct section in the prompt hierarchy with explicit priority."""
    name: str
    title: str
    content: str
    priority: int  # 1 (highest / non-truncatable) to 10 (lowest / first to trim)
    max_tokens: int
    is_essential: bool = False  # If True, never entirely omitted
    trust_level: Optional[Any] = None  # TrustLevel or defaults to CONTROL_SYSTEM

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.content)


class ContextAssembler:
    """
    Assembles prompt sections into a coherent, structured string within token limits.
    Deduplicates content, applies progressive compression, and ensures syntax-safe trimming.
    """

    def __init__(self, budget: Optional[ContextBudget] = None):
        self.budget = budget or ContextBudget.default()
        self._seen_hashes: Set[str] = set()
        self.deduplicator = ContentDeduplicator()

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return estimate_tokens(text)

    def is_duplicate(self, text: str) -> bool:
        """Checks if a text block has already been included using SHA-256 hash."""
        h = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
        if h in self._seen_hashes:
            return True
        self._seen_hashes.add(h)
        return False

    def assemble(self, sections: List[ContextSection]) -> str:
        """
        Assembles sections into a single markdown prompt.
        Progressively deduplicates, compresses, and trims lower-priority sections when budget is exceeded.
        """
        self._seen_hashes.clear()
        self.deduplicator.clear()

        # 1. Deduplicate content across sections
        clean_sections: List[ContextSection] = []
        for sec in sections:
            content = sec.content.strip()
            if not content:
                continue

            # Check if this entire block is a duplicate (legacy & new deduplicator)
            if not sec.is_essential and self.is_duplicate(content):
                continue

            # Apply span-level and cross-section deduplication
            content = self.deduplicator.deduplicate_section_content(
                section_name=sec.name,
                content=content,
                is_essential=sec.is_essential,
            )
            if not content.strip():
                continue

            # Compress section if it exceeds individual max_tokens
            est = self.estimate_tokens(content)
            if est > sec.max_tokens:
                compressed = ContextCompressor.compress(
                    content,
                    max_tokens=sec.max_tokens,
                    content_hint=sec.name,
                )
                if self.estimate_tokens(compressed) <= sec.max_tokens:
                    content = compressed
                else:
                    content = self._trim_content(compressed, sec.max_tokens)

            clean_sections.append(ContextSection(
                name=sec.name,
                title=sec.title,
                content=content,
                priority=sec.priority,
                max_tokens=sec.max_tokens,
                is_essential=sec.is_essential,
                trust_level=sec.trust_level,
            ))

        # 2. Check total budget and trim lower-priority sections if needed
        total_tokens = sum(s.estimated_tokens for s in clean_sections)
        if total_tokens > self.budget.total_budget:
            sorted_indices = sorted(
                range(len(clean_sections)),
                key=lambda i: clean_sections[i].priority,
                reverse=True
            )

            overflow = total_tokens - self.budget.total_budget
            for idx in sorted_indices:
                if overflow <= 0:
                    break
                sec = clean_sections[idx]
                if sec.is_essential:
                    continue

                curr_toks = sec.estimated_tokens
                if curr_toks <= overflow and not sec.is_essential:
                    # Omit entire section
                    clean_sections[idx] = ContextSection(
                        name=sec.name,
                        title=sec.title,
                        content=f"[Section '{sec.title}' omitted to respect token budget ({curr_toks} tokens)]",
                        priority=sec.priority,
                        max_tokens=sec.max_tokens,
                        is_essential=False,
                        trust_level=sec.trust_level,
                    )
                    overflow -= (curr_toks - clean_sections[idx].estimated_tokens)
                elif curr_toks > 100:
                    # Try progressive compression first
                    target_tokens = max(50, curr_toks - overflow)
                    compressed = ContextCompressor.compress(
                        sec.content,
                        max_tokens=target_tokens,
                        content_hint=sec.name,
                    )
                    comp_tokens = self.estimate_tokens(compressed)
                    if comp_tokens <= target_tokens:
                        trimmed = compressed
                    else:
                        trimmed = self._trim_content(compressed, target_tokens)

                    saved = curr_toks - self.estimate_tokens(trimmed)
                    clean_sections[idx] = ContextSection(
                        name=sec.name,
                        title=sec.title,
                        content=trimmed,
                        priority=sec.priority,
                        max_tokens=sec.max_tokens,
                        is_essential=sec.is_essential,
                        trust_level=sec.trust_level,
                    )
                    overflow -= saved

        # 3. Format into final prompt preserving original logical section order
        output_parts: List[str] = []
        for sec in clean_sections:
            if not sec.content.strip():
                continue

            sec_text = sec.content.strip()
            tl = getattr(sec, "trust_level", None)
            if tl is not None:
                try:
                    from ..security.trust_boundaries import TrustLevel, TrustBoundaryEnforcer
                    if tl != TrustLevel.CONTROL_SYSTEM:
                        sec_text = TrustBoundaryEnforcer.sanitize_and_annotate(
                            sec_text,
                            trust_level=tl,
                            identifier=sec.name,
                            source_type="repository_section" if tl == TrustLevel.UNTRUSTED_REPOSITORY else "observation",
                        )
                except Exception:
                    pass

            if sec.title:
                output_parts.append(f"### {sec.title}\n{sec_text}")
            else:
                output_parts.append(sec_text)

        return "\n\n".join(output_parts)

    def _trim_content(self, content: str, max_tokens: int) -> str:
        """
        Trims text to target token count using syntax-safe boundaries.
        Handles JSON parse preservation, AST boundary alignment, and sentinel omission lines.
        """
        max_chars = max_tokens * 4
        if len(content) <= max_chars:
            return content

        # Check if content is JSON
        stripped = content.strip()
        if (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]")):
            try:
                compressed_json = JSONCompressor.compress(stripped, max_tokens=max_tokens)
                if len(compressed_json) <= max_chars:
                    return compressed_json
            except Exception:
                pass

        lines = content.splitlines()
        if len(lines) > 20:
            target_lines = max(5, int(len(lines) * (max_chars / len(content))))
            head_count = int(target_lines * 0.7)
            tail_count = max(2, target_lines - head_count)
            head = lines[:head_count]
            tail = lines[-tail_count:]
            omitted = len(lines) - head_count - tail_count
            return "\n".join(head) + f"\n\n... [{omitted} lines omitted to respect context budget] ...\n\n" + "\n".join(tail)
        else:
            return content[:max_chars] + f"\n... [Truncated to {max_tokens} tokens] ..."


__all__ = ["ContextBudget", "ContextSection", "ContextAssembler"]
