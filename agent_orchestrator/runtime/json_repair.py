"""
Robust Algorithmic Dirty-JSON Repair Engine.
Handles common LLM deviations without external dependencies:
1. Markdown code fence extraction and preamble/postamble stripping.
2. Conversion of single-quoted keys and strings to standard double quotes.
3. Conversion of Python literals (True, False, None) to JSON literals (true, false, null).
4. Trailing comma removal before closing brackets/braces.
5. Escaping of raw control characters (literal newlines, tabs) inside string literals.
6. Automatic closure of unclosed strings and truncated brackets/braces.
7. Stripping of C-style (// and /* */) comments.
"""

import json
import re
from typing import Any, List, Optional, Tuple, Union


def extract_json_candidate(text: str) -> str:
    """
    Extract the most likely JSON substring from text containing
    markdown fences, explanations, or commentary.
    """
    if not text:
        return ""

    clean = text.strip()

    # Check for markdown code fences
    fence_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
    match = re.search(fence_pattern, clean, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # Find the outermost JSON container ({...} or [...])
    first_brace = clean.find("{")
    first_bracket = clean.find("[")

    if first_brace == -1 and first_bracket == -1:
        return clean

    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        start = first_brace
        end = clean.rfind("}")
    else:
        start = first_bracket
        end = clean.rfind("]")

    if end > start:
        return clean[start : end + 1].strip()
    elif start != -1:
        # Possibly truncated JSON: take from start to end of text
        return clean[start:].strip()

    return clean


def repair_json(text: str) -> str:
    """
    Perform multi-pass dirty-JSON repair on a candidate string.
    Returns a repaired JSON string candidate.
    """
    candidate = extract_json_candidate(text)
    if not candidate:
        return ""

    # Pass 1: Character-by-character scanner and repair
    repaired_chars: List[str] = []
    in_string: Optional[str] = None  # None, '"', or "'"
    escaped = False
    bracket_stack: List[str] = []  # Stack of expected closing brackets: '}' or ']'
    i = 0
    n = len(candidate)

    while i < n:
        char = candidate[i]

        # Handle string escape sequences
        if in_string is not None:
            if escaped:
                escaped = False
                repaired_chars.append(char)
                i += 1
                continue
            elif char == "\\":
                escaped = True
                repaired_chars.append(char)
                i += 1
                continue
            elif char == in_string:
                # String closing
                in_string = None
                repaired_chars.append('"')
                i += 1
                continue
            elif in_string == "'" and char == '"':
                # Double quote inside single-quoted string must be escaped
                repaired_chars.append('\\"')
                i += 1
                continue
            elif char == "\n":
                # Unescaped literal newline inside string
                repaired_chars.append("\\n")
                i += 1
                continue
            elif char == "\r":
                repaired_chars.append("\\r")
                i += 1
                continue
            elif char == "\t":
                repaired_chars.append("\\t")
                i += 1
                continue
            else:
                repaired_chars.append(char)
                i += 1
                continue

        # Outside string:
        # Check for comments
        if char == "/" and i + 1 < n:
            next_char = candidate[i + 1]
            if next_char == "/":
                # Single-line comment: skip until newline
                i += 2
                while i < n and candidate[i] != "\n":
                    i += 1
                continue
            elif next_char == "*":
                # Multi-line comment: skip until */
                i += 2
                while i + 1 < n and not (candidate[i] == "*" and candidate[i + 1] == "/"):
                    i += 1
                i += 2  # skip */
                continue

        # String opening
        if char in ('"', "'"):
            in_string = char
            repaired_chars.append('"')
            i += 1
            continue

        # Brackets tracking
        if char == "{":
            bracket_stack.append("}")
            repaired_chars.append(char)
            i += 1
            continue
        elif char == "[":
            bracket_stack.append("]")
            repaired_chars.append(char)
            i += 1
            continue
        elif char in ("}", "]"):
            if bracket_stack and bracket_stack[-1] == char:
                bracket_stack.pop()

            # Remove trailing comma before closing bracket/brace
            # Look backwards in repaired_chars for preceding non-whitespace
            idx = len(repaired_chars) - 1
            while idx >= 0 and repaired_chars[idx].isspace():
                idx -= 1
            if idx >= 0 and repaired_chars[idx] == ",":
                del repaired_chars[idx]

            repaired_chars.append(char)
            i += 1
            continue

        # Python literals replacement outside strings
        # True -> true
        if candidate[i : i + 4] == "True" and (i + 4 >= n or not candidate[i + 4].isalnum()):
            repaired_chars.extend(list("true"))
            i += 4
            continue
        # False -> false
        if candidate[i : i + 5] == "False" and (i + 5 >= n or not candidate[i + 5].isalnum()):
            repaired_chars.extend(list("false"))
            i += 5
            continue
        # None -> null
        if candidate[i : i + 4] == "None" and (i + 4 >= n or not candidate[i + 4].isalnum()):
            repaired_chars.extend(list("null"))
            i += 4
            continue

        # Normal character
        repaired_chars.append(char)
        i += 1

    # If string was left open due to truncation, close it
    if in_string is not None:
        repaired_chars.append('"')

    # Remove any trailing comma before auto-closing
    idx = len(repaired_chars) - 1
    while idx >= 0 and repaired_chars[idx].isspace():
        idx -= 1
    if idx >= 0 and repaired_chars[idx] == ",":
        del repaired_chars[idx]

    # Auto-close any unclosed brackets in reverse order
    while bracket_stack:
        repaired_chars.append(bracket_stack.pop())

    result = "".join(repaired_chars).strip()

    # Pass 2: Clean up any remaining trailing commas before } or ] using regex
    result = re.sub(r",\s*([\]}])", r"\1", result)

    return result


def loads_repaired(text: str) -> Any:
    """
    Attempt to parse JSON directly. If it fails, apply algorithmic repair
    and parse again. Raises json.JSONDecodeError if unrecoverable.
    """
    if not text:
        raise json.JSONDecodeError("Empty text", "", 0)

    clean = text.strip()

    # Fast path: try standard json.loads
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Second fast path: extract code fence and try
    candidate = extract_json_candidate(clean)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Third path: apply full algorithmic repair
    repaired = repair_json(clean)
    return json.loads(repaired)
