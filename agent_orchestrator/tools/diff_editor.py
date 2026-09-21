"""
Diff & Delta Editing Engine.
Provides robust, surgical editing primitives for autonomous agents:
- Line-bounded and fuzzy search & replace
- Atomic line insertions and deletions
- Aider-style SEARCH/REPLACE multi-hunk patch application
- Unified diff generation using standard library difflib
- Lazy truncation placeholder detection
"""
import difflib
import re
from typing import List, Optional, Tuple


class DiffEditError(ValueError):
    """Raised when a surgical or diff-based edit fails to apply."""
    pass


TRUNCATION_PATTERNS = [
    re.compile(r"^\s*([#/]{1,2}|/\*|<!--|\*)\s*(\.\.\.|…)\s*(rest|existing|remaining|unchanged|previous|other|all)\s*(of\s*)?(code|file|functions?|methods?|imports?|implementation|content)?", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*([#/]{1,2}|/\*|<!--|\*)\s*(code\s*)?(remains\s*)?(the\s*)?same\s*(\.\.\.|…)?", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*([#/]{1,2}|/\*|<!--|\*)\s*(\.\.\.|…)\s*$", re.MULTILINE),
    re.compile(r"^\s*(\.\.\.|…)\s*(existing|rest\s*of|remaining)\s*(code|file|lines)", re.IGNORECASE | re.MULTILINE),
]


def detect_truncation_placeholders(text: str) -> List[str]:
    """
    Detects if LLM output contains dangerous lazy truncation comments
    (e.g., '// ... rest of code unchanged ...', '# ... existing code ...').
    Returns a list of matching placeholder lines, or an empty list if none found.
    """
    matches = []
    for pattern in TRUNCATION_PATTERNS:
        for m in pattern.finditer(text):
            line = m.group(0).strip()
            if line and line not in matches:
                matches.append(line)
    return matches


def generate_unified_diff(
    original_text: str,
    modified_text: str,
    filepath: str = "file",
) -> str:
    """Generates a standard unified diff string between original and modified text."""
    orig_lines = original_text.splitlines(keepends=True)
    mod_lines = modified_text.splitlines(keepends=True)
    
    diff_generator = difflib.unified_diff(
        orig_lines,
        mod_lines,
        fromfile=f"a/{filepath}",
        tofile=f"b/{filepath}",
        lineterm="",
    )
    return "".join(diff_generator)


def _fuzzy_find_lines(
    source_lines: List[str],
    search_lines: List[str],
    start_idx: int = 0,
    end_idx: Optional[int] = None,
) -> Optional[Tuple[int, int]]:
    """
    Finds index range [start, end) in source_lines where stripped lines match search_lines.
    Returns (match_start_idx, match_end_idx) or None.
    """
    if not search_lines or not source_lines:
        return None

    if end_idx is None or end_idx > len(source_lines):
        end_idx = len(source_lines)

    search_stripped = [l.strip() for l in search_lines if l.strip()]
    if not search_stripped:
        return None

    search_len = len(search_lines)
    max_start = end_idx - search_len
    if max_start < start_idx:
        max_start = start_idx

    for i in range(start_idx, max_start + 1):
        window = source_lines[i : i + search_len]
        window_stripped = [l.strip() for l in window]
        search_all_stripped = [l.strip() for l in search_lines]
        if window_stripped == search_all_stripped:
            return (i, i + search_len)

    # Secondary fallback: non-empty line alignment
    for i in range(start_idx, end_idx):
        if source_lines[i].strip() == search_stripped[0]:
            src_p = i
            search_p = 0
            while src_p < end_idx and search_p < len(search_stripped):
                if source_lines[src_p].strip() == search_stripped[search_p]:
                    search_p += 1
                elif source_lines[src_p].strip():
                    break
                src_p += 1
            if search_p == len(search_stripped):
                return (i, src_p)

    return None


def search_and_replace(
    content: str,
    search: str,
    replace: str,
    filepath: str = "file",
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    allow_multiple: bool = False,
    fuzzy: bool = True,
) -> Tuple[str, int, str]:
    """
    Surgically replaces target search content with replacement content.
    Supports line window bounding and fuzzy whitespace fallback.

    Returns:
        (new_content, occurrences_replaced, unified_diff)
    """
    if not search:
        raise DiffEditError("Target search content cannot be empty.")

    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    # 1. Bounded window handling (1-indexed)
    start_idx = max(0, (start_line - 1)) if start_line is not None else 0
    end_idx = min(total_lines, end_line) if end_line is not None else total_lines

    if start_idx >= total_lines and total_lines > 0:
        raise DiffEditError(f"start_line ({start_line}) exceeds total lines ({total_lines}).")
    if start_idx > end_idx:
        raise DiffEditError(f"start_line ({start_line}) is greater than end_line ({end_line}).")

    window_content = "".join(lines[start_idx:end_idx]) if (start_line or end_line) else content

    # 2. Exact match attempt
    if search in window_content:
        count = window_content.count(search)
        if count > 1 and not allow_multiple:
            raise DiffEditError(
                f"Target content occurred {count} times in the specified window. "
                "Specify a tighter start_line/end_line range or set allow_multiple=True."
            )
        
        if not (start_line or end_line):
            new_content = content.replace(search, replace, -1 if allow_multiple else 1)
        else:
            rep_window = window_content.replace(search, replace, -1 if allow_multiple else 1)
            new_content = "".join(lines[:start_idx]) + rep_window + "".join(lines[end_idx:])

        diff = generate_unified_diff(content, new_content, filepath=filepath)
        return (new_content, count if allow_multiple else 1, diff)

    # 3. Fuzzy whitespace-tolerant match fallback
    if fuzzy:
        search_lines_split = search.splitlines()
        found_range = _fuzzy_find_lines(
            source_lines=[l.rstrip("\r\n") for l in lines],
            search_lines=search_lines_split,
            start_idx=start_idx,
            end_idx=end_idx,
        )

        if found_range:
            m_start, m_end = found_range
            rep_lines_raw = replace.splitlines()
            rep_lines = [l + "\n" for l in rep_lines_raw]
            
            new_lines = lines[:m_start] + rep_lines + lines[m_end:]
            new_content = "".join(new_lines)
            diff = generate_unified_diff(content, new_content, filepath=filepath)
            return (new_content, 1, diff)

    # 4. Check for idempotent already-applied state (Issue #47)
    # If the replacement content is already present in the target window and search is not,
    # the requested modification was already applied. Return idempotent no-op.
    if replace in window_content or (
        fuzzy and _fuzzy_find_lines(
            source_lines=[l.rstrip("\r\n") for l in lines],
            search_lines=replace.splitlines(),
            start_idx=start_idx,
            end_idx=end_idx,
        )
    ):
        return (content, 0, "")

    # 5. Failed to locate
    window_desc = f" lines {start_line}-{end_line}" if (start_line or end_line) else ""
    raise DiffEditError(
        f"Target content not found in '{filepath}'{window_desc}. "
        "Verify lines, indentation, and surrounding context."
    )


def insert_lines(
    content: str,
    line_number: int,
    new_content: str,
    position: str = "after",
    filepath: str = "file",
) -> Tuple[str, str]:
    """
    Inserts new lines at the specified 1-indexed line number.
    position can be 'after' or 'before'.

    Returns:
        (new_content, unified_diff)
    """
    if line_number < 1:
        raise DiffEditError(f"Invalid line_number: {line_number}. Line numbers are 1-indexed.")

    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    insert_lines_split = [l + "\n" for l in new_content.splitlines()]
    if not insert_lines_split:
        return (content, "")

    pos_clean = position.lower().strip()
    if pos_clean not in ("after", "before"):
        raise DiffEditError(f"Invalid position '{position}'. Must be 'after' or 'before'.")

    if total_lines == 0:
        new_text = "".join(insert_lines_split)
        diff = generate_unified_diff(content, new_text, filepath=filepath)
        return (new_text, diff)

    if pos_clean == "before":
        insert_idx = min(line_number - 1, total_lines)
    else:  # after
        insert_idx = min(line_number, total_lines)

    # Check if lines are already present at the insert position (idempotency - Issue #47)
    slice_len = len(insert_lines_split)
    target_slice = lines[insert_idx : insert_idx + slice_len]
    if [l.strip() for l in target_slice] == [l.strip() for l in insert_lines_split]:
        return (content, "")

    new_lines = lines[:insert_idx] + insert_lines_split + lines[insert_idx:]
    new_text = "".join(new_lines)
    diff = generate_unified_diff(content, new_text, filepath=filepath)
    return (new_text, diff)


def delete_lines(
    content: str,
    start_line: int,
    end_line: int,
    filepath: str = "file",
) -> Tuple[str, str]:
    """
    Deletes lines in 1-indexed inclusive range [start_line, end_line].

    Returns:
        (new_content, unified_diff)
    """
    if start_line < 1 or end_line < 1:
        raise DiffEditError("Line numbers must be positive 1-indexed integers.")
    if start_line > end_line:
        raise DiffEditError(f"start_line ({start_line}) cannot be greater than end_line ({end_line}).")

    lines = content.splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines == 0:
        raise DiffEditError("Cannot delete lines from an empty file.")
    if start_line > total_lines:
        raise DiffEditError(f"start_line ({start_line}) exceeds total lines ({total_lines}).")

    start_idx = start_line - 1
    end_idx = min(end_line, total_lines)

    new_lines = lines[:start_idx] + lines[end_idx:]
    new_text = "".join(new_lines)
    diff = generate_unified_diff(content, new_text, filepath=filepath)
    return (new_text, diff)


DIFF_BLOCK_PATTERN = re.compile(
    r"<<<<<<<\s*SEARCH\s*\r?\n(.*?)\r?\n=======\s*\r?\n(.*?)\r?\n>>>>>>>\s*REPLACE",
    re.DOTALL,
)


def apply_diff_blocks(
    content: str,
    diff_blocks: str,
    filepath: str = "file",
    fuzzy: bool = True,
) -> Tuple[str, int, str]:
    """
    Parses and applies one or more Aider-style SEARCH/REPLACE blocks sequentially.

    Format:
    <<<<<<< SEARCH
    ... original lines to match ...
    =======
    ... replacement lines ...
    >>>>>>> REPLACE

    Returns:
        (new_content, blocks_applied_count, aggregated_unified_diff)
    """
    matches = list(DIFF_BLOCK_PATTERN.finditer(diff_blocks))
    if not matches:
        raise DiffEditError(
            "No valid SEARCH/REPLACE blocks found. Format must be:\n"
            "<<<<<<< SEARCH\n[original code]\n=======\n[new code]\n>>>>>>> REPLACE"
        )

    current_content = content
    blocks_applied = 0

    for idx, match in enumerate(matches, start=1):
        search_block = match.group(1)
        replace_block = match.group(2)

        try:
            current_content, _, _ = search_and_replace(
                content=current_content,
                search=search_block,
                replace=replace_block,
                filepath=filepath,
                allow_multiple=False,
                fuzzy=fuzzy,
            )
            blocks_applied += 1
        except DiffEditError as err:
            # Check if replace_block is already present in current_content (already applied - Issue #47)
            if replace_block in current_content or (
                fuzzy and _fuzzy_find_lines(
                    source_lines=[l.rstrip("\r\n") for l in current_content.splitlines()],
                    search_lines=replace_block.splitlines(),
                )
            ):
                blocks_applied += 1
            else:
                raise DiffEditError(f"Failed to apply SEARCH/REPLACE block #{idx} in '{filepath}': {str(err)}")

    aggregated_diff = generate_unified_diff(content, current_content, filepath=filepath)
    return (current_content, blocks_applied, aggregated_diff)
