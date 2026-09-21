"""
Content Deduplicator for Multi-Section Prompt Assembly.
Detects exact and near-duplicate content blocks, cross-section file overlap,
and redundant factual statements across context sections.
"""
import hashlib
import re
from typing import Dict, List, Optional, Set, Tuple


class ContentDeduplicator:
    """
    Tracks seen content hashes and file representations across sections,
    suppressing duplicate blocks and replacing redundant file dumps with references.
    """

    def __init__(self):
        self._seen_exact_hashes: Set[str] = set()
        self._seen_normalized_hashes: Set[str] = set()
        self._seen_files: Set[str] = set()
        self._seen_facts: Set[str] = set()

    def clear(self) -> None:
        """Resets deduplicator state for a new assembly pass."""
        self._seen_exact_hashes.clear()
        self._seen_normalized_hashes.clear()
        self._seen_files.clear()
        self._seen_facts.clear()

    @staticmethod
    def compute_sha256(text: str) -> str:
        return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()

    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalizes text by removing punctuation, extra spaces, and lowercasing."""
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text)).strip().lower()

    def is_duplicate_block(self, text: str) -> bool:
        """Checks if an entire text block is an exact duplicate."""
        if not text.strip():
            return False
        h = self.compute_sha256(text)
        if h in self._seen_exact_hashes:
            return True
        self._seen_exact_hashes.add(h)
        return False

    def is_duplicate_fact(self, fact: str) -> bool:
        """Checks if a factual statement (e.g. bullet point) has already been seen."""
        norm = self.normalize_text(fact)
        if len(norm) < 15:
            return False
        if norm in self._seen_facts:
            return True
        self._seen_facts.add(norm)
        return False

    def record_file(self, filepath: str) -> None:
        """Records that a file has been presented in context."""
        clean = filepath.strip().replace("\\", "/")
        if clean:
            self._seen_files.add(clean)

    def is_file_seen(self, filepath: str) -> bool:
        """Checks if a file representation has already been included."""
        clean = filepath.strip().replace("\\", "/")
        return clean in self._seen_files

    def deduplicate_section_content(
        self,
        section_name: str,
        content: str,
        is_essential: bool = False,
    ) -> str:
        """
        Deduplicates a section's content:
        1. Checks for entire block exact duplicates (omitting non-essential duplicates).
        2. Detects if code blocks reference files already included in higher-priority sections.
        3. Filters repeated bullet points / verified facts.
        """
        if not content.strip():
            return ""

        # 1. Whole-block exact duplicate check
        if not is_essential and self.is_duplicate_block(content):
            return ""

        # 2. Extract and check file references in code headers
        lines = content.splitlines()
        filtered_lines: List[str] = []
        i = 0

        file_header_pattern = re.compile(r"^\s*(?://|#)\s*(?:File|Module Interface|Module Reference):\s*([^\s()]+)")

        while i < len(lines):
            line = lines[i]
            match = file_header_pattern.match(line)
            if match:
                fpath = match.group(1).strip().replace("\\", "/")
                # If this file has already been seen in a higher-priority section
                # and this is an interface/reference section:
                if fpath in self._seen_files and ("interface" in section_name or "outline" in section_name or "cbm" in section_name):
                    filtered_lines.append(f"// File: {fpath} (Already provided in focal targets above)")
                    # Skip the rest of this module's lines until the next file or closing fence
                    i += 1
                    while i < len(lines):
                        next_line = lines[i]
                        if file_header_pattern.match(next_line) or next_line.strip() == "```":
                            break
                        i += 1
                    continue
                else:
                    self._seen_files.add(fpath)

            # 3. Deduplicate bullet points in memory/findings sections
            if line.strip().startswith("•") or line.strip().startswith("- Task ["):
                bullet_fact = line.strip().lstrip("•-").strip()
                if self.is_duplicate_fact(bullet_fact) and not is_essential:
                    i += 1
                    continue

            filtered_lines.append(line)
            i += 1

        return "\n".join(filtered_lines).strip()


__all__ = ["ContentDeduplicator"]
