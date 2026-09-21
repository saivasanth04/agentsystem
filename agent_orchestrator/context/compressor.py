"""
Context Compression Engine for Coding Agents.
Provides multi-modal, syntax-preserving compression across:
- Code (AST-based skeletonization, function body folding, comment pruning)
- JSON (Metadata pruning, empty-field elimination, valid list sentinels)
- Tool Logs (Stack-trace preservation, passing-test run collapsing)
- Prose / Text (Whitespace and redundant line normalization)
"""
import ast
from enum import Enum
import json
import re
from typing import Any, Dict, List, Optional, Set, Union


class CompressionLevel(str, Enum):
    NONE = "none"              # Original content
    WHITESPACE = "whitespace"  # Strip redundant whitespace & blank lines
    COMMENTS = "comments"      # Strip non-essential comments / trailing notes
    SKELETON = "skeleton"      # Fold implementation bodies to '...' preserving signatures & types


class CodeCompressor:
    """AST-driven code compression preserving syntax, types, and module contracts."""

    @staticmethod
    def strip_whitespace(code: str) -> str:
        """Removes trailing whitespace and collapses consecutive blank lines."""
        lines = [line.rstrip() for line in code.splitlines()]
        cleaned = []
        prev_blank = False
        for line in lines:
            if not line:
                if not prev_blank:
                    cleaned.append("")
                prev_blank = True
            else:
                cleaned.append(line)
                prev_blank = False
        return "\n".join(cleaned).strip()

    @staticmethod
    def strip_comments(code: str) -> str:
        """Removes inline comments (# ...) while preserving shebang and docstrings."""
        lines = code.splitlines()
        result = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#!"):
                result.append(line)
                continue
            if stripped.startswith("#"):
                continue
            # Check for inline comment without quotes
            if " #" in line:
                # Basic inline comment strip if not inside quotes
                parts = line.split(" #", 1)
                quote_count = parts[0].count('"') + parts[0].count("'")
                if quote_count % 2 == 0:
                    result.append(parts[0].rstrip())
                    continue
            result.append(line)
        return "\n".join(result)

    @classmethod
    def fold_python_ast(cls, code: str, keep_docstrings: bool = True) -> str:
        """
        Parses Python code via AST and folds function/method bodies into `...`,
        preserving class definitions, signatures, type annotations, and docstrings.
        """
        try:
            tree = ast.parse(code)
        except Exception:
            # Fallback to regex/indentation folding if code has syntax issues or is a fragment
            return cls._fold_fallback(code)

        class BodyFolder(ast.NodeTransformer):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
                self.generic_visit(node)
                return self._fold_func_body(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
                self.generic_visit(node)
                return self._fold_func_body(node)

            def _fold_func_body(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> Union[ast.FunctionDef, ast.AsyncFunctionDef]:
                docstring = ast.get_docstring(node) if keep_docstrings else None
                new_body: List[ast.stmt] = []
                if docstring:
                    # Truncate docstring if very long
                    short_doc = docstring.strip().split("\n\n")[0]
                    if len(short_doc) > 160:
                        short_doc = short_doc[:157] + "..."
                    new_body.append(ast.Expr(value=ast.Constant(value=short_doc)))

                # Replace body with Ellipsis `...`
                new_body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))
                node.body = new_body
                return node

        folder = BodyFolder()
        folded_tree = folder.visit(tree)
        ast.fix_missing_locations(folded_tree)
        try:
            return ast.unparse(folded_tree)
        except Exception:
            return cls._fold_fallback(code)

    @classmethod
    def _fold_fallback(cls, code: str) -> str:
        """Regex and indentation-based body folding when AST is unavailable."""
        lines = code.splitlines()
        folded_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            folded_lines.append(line)
            stripped = line.strip()
            if (stripped.startswith("def ") or stripped.startswith("async def ")) and stripped.endswith(":"):
                # Indentation of definition
                indent = len(line) - len(line.lstrip())
                body_indent = indent + 4
                # Skip body lines
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    if not next_line.strip():
                        i += 1
                        continue
                    next_indent = len(next_line) - len(next_line.lstrip())
                    if next_indent > indent:
                        i += 1
                    else:
                        break
                folded_lines.append(" " * body_indent + "...")
                continue
            i += 1
        return "\n".join(folded_lines)

    @classmethod
    def compress(
        cls,
        code: str,
        language: str = "python",
        level: CompressionLevel = CompressionLevel.SKELETON,
    ) -> str:
        """Applies requested compression level to code."""
        if not code or level == CompressionLevel.NONE:
            return code

        result = cls.strip_whitespace(code)
        if level == CompressionLevel.WHITESPACE:
            return result

        result = cls.strip_comments(result)
        if level == CompressionLevel.COMMENTS:
            return cls.strip_whitespace(result)

        if level == CompressionLevel.SKELETON:
            if language.lower() in ("python", "py"):
                folded = cls.fold_python_ast(result)
                return cls.strip_whitespace(folded)
            else:
                folded = cls._fold_fallback(result)
                return cls.strip_whitespace(folded)

        return result


class JSONCompressor:
    """Safe, syntax-preserving JSON state compressor."""

    DEFAULT_PRUNE_KEYS = {
        "file_hashes", "checkpoints", "attempts", "typed_artifacts",
        "checkpoint_id", "execution_id", "diagnostics",
    }

    @classmethod
    def prune_structure(
        cls,
        data: Any,
        prune_empty: bool = True,
        prune_keys: Optional[Set[str]] = None,
        max_list_items: int = 8,
    ) -> Any:
        """Recursively removes noise keys, empty structures, and caps long lists."""
        keys_to_drop = prune_keys or cls.DEFAULT_PRUNE_KEYS

        if isinstance(data, dict):
            new_dict = {}
            for k, v in data.items():
                if k in keys_to_drop:
                    continue
                pruned_v = cls.prune_structure(v, prune_empty, keys_to_drop, max_list_items)
                if prune_empty and pruned_v in (None, "", [], {}):
                    continue
                new_dict[k] = pruned_v
            return new_dict
        elif isinstance(data, list):
            pruned_list = [
                cls.prune_structure(item, prune_empty, keys_to_drop, max_list_items)
                for item in data
            ]
            if prune_empty:
                pruned_list = [item for item in pruned_list if item not in (None, "", [], {})]
            if len(pruned_list) > max_list_items:
                retained = pruned_list[:max_list_items]
                omitted_count = len(pruned_list) - max_list_items
                retained.append(f"[... {omitted_count} items omitted ...]")
                return retained
            return pruned_list
        return data

    @classmethod
    def compress(
        cls,
        json_input: Union[str, Dict[str, Any], List[Any]],
        max_tokens: Optional[int] = None,
        prune_empty: bool = True,
        prune_keys: Optional[Set[str]] = None,
    ) -> str:
        """Minifies and prunes JSON structure, returning valid parseable JSON."""
        if isinstance(json_input, str):
            try:
                data = json.loads(json_input)
            except Exception:
                # If not parseable JSON, return trimmed string
                return json_input.strip()
        else:
            data = json_input

        pruned = cls.prune_structure(data, prune_empty=prune_empty, prune_keys=prune_keys)

        # First attempt: indented for readability
        formatted = json.dumps(pruned, indent=2, default=str)
        est_tokens = len(formatted) // 4
        if max_tokens and est_tokens > max_tokens:
            # Second attempt: compact minification
            formatted = json.dumps(pruned, separators=(",", ":"), default=str)
            est_tokens = len(formatted) // 4
            if est_tokens > max_tokens:
                # Third attempt: aggressive list pruning
                aggressive = cls.prune_structure(pruned, prune_empty=True, prune_keys=prune_keys, max_list_items=3)
                formatted = json.dumps(aggressive, separators=(",", ":"), default=str)

        return formatted


class LogCompressor:
    """Compresses compiler output, terminal logs, and test execution traces."""

    ERROR_MARKERS = [
        "error", "failed", "traceback", "exception", "syntaxerror",
        "assertionerror", "fail:", "fatal:", "panic:", "err:"
    ]

    PASS_MARKERS = [
        "passed", "ok", "success", "100%", "completed", "running"
    ]

    @classmethod
    def compress(cls, log_text: str, max_lines: int = 40) -> str:
        """
        Compresses logs while strictly preserving error stack traces,
        failing test assertions, and final summaries.
        """
        lines = log_text.splitlines()
        if len(lines) <= max_lines:
            return log_text

        # Identify important line indices (error markers + surrounding context)
        important_indices: Set[int] = set()
        # Always include first 3 and last 5 lines
        for i in range(min(3, len(lines))):
            important_indices.add(i)
        for i in range(max(0, len(lines) - 5), len(lines)):
            important_indices.add(i)

        for i, line in enumerate(lines):
            low = line.lower()
            if any(marker in low for marker in cls.ERROR_MARKERS):
                # Include context around error: 2 lines before, 4 lines after
                for ctx in range(max(0, i - 2), min(len(lines), i + 5)):
                    important_indices.add(ctx)

        sorted_indices = sorted(important_indices)
        output_lines: List[str] = []
        prev_idx = -1

        for idx in sorted_indices:
            if prev_idx != -1 and idx > prev_idx + 1:
                gap = idx - prev_idx - 1
                output_lines.append(f"... [{gap} lines of logs/passes collapsed] ...")
            output_lines.append(lines[idx])
            prev_idx = idx

        return "\n".join(output_lines)


class ContextCompressor:
    """Unified coordinator for progressive multi-modal context compression."""

    @classmethod
    def compress(
        cls,
        content: str,
        max_tokens: int,
        content_hint: Optional[str] = None,
    ) -> str:
        """
        Selects appropriate compression strategy based on content type
        and applies progressive reduction to fit under max_tokens.
        """
        if not content:
            return ""

        est_tokens = max(1, len(content) // 4)
        if est_tokens <= max_tokens:
            return content

        hint = (content_hint or "").lower()

        # 1. JSON Content Detection
        stripped = content.strip()
        if hint == "json" or (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]")):
            try:
                compressed_json = JSONCompressor.compress(stripped, max_tokens=max_tokens)
                if len(compressed_json) // 4 <= max_tokens:
                    return compressed_json
            except Exception:
                pass

        # 2. Log / Test Results Detection
        if hint in ("log", "terminal", "test_output") or "Traceback (most recent call last)" in content or "=== test session starts ===" in content:
            compressed_log = LogCompressor.compress(content, max_lines=max(15, max_tokens // 4))
            if len(compressed_log) // 4 <= max_tokens:
                return compressed_log

        # 3. Code Content Detection
        if hint in ("code", "python") or "def " in content or "class " in content or "import " in content:
            # Try Level: COMMENTS first
            c_comments = CodeCompressor.compress(content, level=CompressionLevel.COMMENTS)
            if len(c_comments) // 4 <= max_tokens:
                return c_comments

            # Try Level: SKELETON
            c_skeleton = CodeCompressor.compress(content, level=CompressionLevel.SKELETON)
            if len(c_skeleton) // 4 <= max_tokens:
                return c_skeleton

        # 4. Fallback: Whitespace normalization
        norm = CodeCompressor.strip_whitespace(content)
        if len(norm) // 4 <= max_tokens:
            return norm

        return norm


__all__ = [
    "CompressionLevel",
    "CodeCompressor",
    "JSONCompressor",
    "LogCompressor",
    "ContextCompressor",
]
