"""
Context Packing & Section Slicing.
Enforces the Non-Negotiable Context Rules:
- Never send entire repository: Slices targeted symbols and call graphs only.
- Never send entire conversation: Slices relevant recent turns and observations only.
- Never send entire SKILL.md: Slices only relevant operational sections.
Renders an OptimizedContextPackage ready for LLM consumption.
"""
from dataclasses import dataclass, field
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from .budget import TokenCounter

logger = logging.getLogger("context.pack")


class SkillSectionSlicer:
    """
    Parses and slices SKILL.md documents into modular sections.
    Enforces rule: Never send entire SKILL.md into prompt.
    """

    def __init__(self, token_counter: Optional[TokenCounter] = None):
        self.counter = token_counter or TokenCounter()

    def slice_skill(
        self,
        skill_name: str,
        skill_content: str,
        task_objective: str = "",
        max_tokens: int = 500,
    ) -> str:
        """
        Extracts relevant sections of a skill and truncates to max_tokens.
        """
        sections = self.get_relevant_sections(skill_content, query=task_objective, max_sections=3)
        header = f"### Skill: {skill_name}\n" if skill_name else ""
        full = f"{header}{sections}" if sections else ""
        return self.counter.truncate(full, max_tokens=max_tokens)

    @classmethod
    def slice_sections(cls, markdown: str, query: str = "") -> List[Dict[str, str]]:
        """
        Splits markdown by headings and extracts individual sections.
        Returns list of {'title': str, 'content': str}.
        """
        if not markdown:
            return []

        # Remove YAML frontmatter if present
        clean_md = markdown
        if clean_md.startswith("---"):
            parts = clean_md.split("---", 2)
            if len(parts) >= 3:
                clean_md = parts[2].strip()

        # Split on headers (# Header, ## Header, ### Header)
        header_pattern = re.compile(r"^(#{1,3}\s+.+)$", re.MULTILINE)
        splits = list(header_pattern.finditer(clean_md))

        if not splits:
            return [{"title": "Guidelines", "content": clean_md[:1200]}]

        sections: List[Dict[str, str]] = []
        for i, match in enumerate(splits):
            title = match.group(1).lstrip("#").strip()
            start = match.end()
            end = splits[i + 1].start() if i + 1 < len(splits) else len(clean_md)
            body = clean_md[start:end].strip()
            if body:
                sections.append({"title": title, "content": f"## {title}\n{body}"})

        return sections

    @classmethod
    def get_relevant_sections(cls, markdown: str, query: str = "", max_sections: int = 2) -> str:
        """
        Extracts ONLY the most relevant sections of a SKILL.md matching the query.
        Never returns the entire document.
        """
        sections = cls.slice_sections(markdown, query)
        if not sections:
            return ""

        q_terms = set(re.findall(r"\w+", query.lower())) if query else set()

        scored_sections = []
        for s in sections:
            score = 0
            text_lower = (s["title"] + " " + s["content"]).lower()
            # Priority titles
            if any(k in s["title"].lower() for k in ("workflow", "procedure", "steps", "rules", "instructions")):
                score += 5
            # Query term matches
            for term in q_terms:
                if term in text_lower:
                    score += 2
            scored_sections.append((score, s["content"]))

        scored_sections.sort(key=lambda x: x[0], reverse=True)
        chosen = [content for _, content in scored_sections[:max_sections]]
        return "\n\n".join(chosen)


class RepositorySlicer:
    """
    Enforces rule: Never send entire repository.
    Extracts targeted symbols, call graphs, and specific dependency edges only.
    """

    def __init__(self, repository_brain: Optional[Any] = None, token_counter: Optional[TokenCounter] = None):
        self.repo_brain = repository_brain
        self.counter = token_counter or TokenCounter()

    def slice_repository(self, task_objective: str, max_tokens: int = 600) -> str:
        """Extracts targeted symbol definitions and call paths for a task objective."""
        symbols = []
        call_graph_info = None

        if self.repo_brain:
            terms = re.findall(r"[A-Za-z0-9_]{3,}", task_objective)
            if hasattr(self.repo_brain, "find_symbol"):
                for term in terms[:5]:
                    found = self.repo_brain.find_symbol(term)
                    if found:
                        symbols.extend(found[:2])
                        if not call_graph_info and hasattr(self.repo_brain, "get_call_graph"):
                            call_graph_info = self.repo_brain.get_call_graph(term)

        content = self.slice_repository_context(symbols, call_graph_info)
        formatted = f"Repository Intelligence:\n{content}" if content else ""
        return self.counter.truncate(formatted, max_tokens=max_tokens)

    @classmethod
    def slice_repository_context(cls, symbols: List[Dict[str, Any]], call_graph: Optional[Dict[str, Any]] = None) -> str:
        parts = []
        if symbols:
            parts.append("### Relevant Code Symbols:")
            for s in symbols[:8]:
                sig = s.get("signature") or f"{s.get('kind')} {s.get('name')}"
                f_path = s.get("file_path", "")
                line = s.get("line_number", 1)
                doc = f" - {s.get('docstring')[:80]}..." if s.get("docstring") else ""
                parts.append(f"- `{sig}` ({f_path}:{line}){doc}")

        if call_graph:
            symbol = call_graph.get("symbol")
            callers = call_graph.get("callers", [])
            callees = call_graph.get("callees", [])
            if callers or callees:
                parts.append(f"\n### Call Hierarchy for `{symbol}`:")
                if callers:
                    parts.append(f"- Callers: {', '.join([c.get('caller_node', '') for c in callers[:4]])}")
                if callees:
                    parts.append(f"- Callees: {', '.join(callees[:4])}")

        return "\n".join(parts)


class ConversationSlicer:
    """
    Enforces rule: Never send entire conversation.
    Slices only recent relevant turns and observations.
    """

    def __init__(self, token_counter: Optional[TokenCounter] = None):
        self.counter = token_counter or TokenCounter()

    def slice_conversation(self, messages: List[Dict[str, Any]], max_turns: int = 3, max_tokens: int = 500) -> str:
        text = self._slice_conversation_impl(messages, max_turns=max_turns)
        return self.counter.truncate(text, max_tokens=max_tokens)

    @classmethod
    def _slice_conversation_impl(cls, messages: List[Dict[str, Any]], max_turns: int = 3) -> str:
        if not messages:
            return ""
        # Keep only the last max_turns messages
        recent = messages[-max_turns:]
        formatted = []
        for msg in recent:
            role = msg.get("role", "unknown").upper()
            content = str(msg.get("content", ""))[:400]
            formatted.append(f"[{role}]: {content}")
        return "\n".join(formatted)


@dataclass
class OptimizedContextPackage:
    """
    Structured, budget-enforced context package produced by the ContextCompiler.
    """
    task_objective: str = ""
    errors: List[str] = field(default_factory=list)
    task_objective_section: str = ""
    verification_state_section: str = ""
    errors_section: str = ""
    diff_section: str = ""
    repository_intelligence_section: str = ""
    skills_section: str = ""
    working_memory_section: str = ""
    token_breakdown: Dict[str, int] = field(default_factory=dict)
    total_tokens: int = 0

    def render(self) -> str:
        """
        Renders the context into structured, XML and markdown-bounded prompt sections.
        """
        parts = []

        if self.task_objective_section:
            parts.append(f"# Task Objective\n<task_objective>\n{self.task_objective_section}\n</task_objective>")

        if self.verification_state_section:
            parts.append(f"# Verification State\n<verification_state>\n{self.verification_state_section}\n</verification_state>")

        if self.errors_section:
            parts.append(f"# Deduplicated Errors\n<recent_errors>\n{self.errors_section}\n</recent_errors>")

        if self.diff_section:
            parts.append(f"# Working Diff\n<code_diff>\n{self.diff_section}\n</code_diff>")

        if self.repository_intelligence_section:
            parts.append(f"# Repository Intelligence\n<codebase_context>\n{self.repository_intelligence_section}\n</codebase_context>")

        if self.skills_section:
            parts.append(f"# Active Skills\n<active_skills>\n{self.skills_section}\n</active_skills>")

        if self.working_memory_section:
            parts.append(f"# Working Memory\n<working_memory>\n{self.working_memory_section}\n</working_memory>")

        return "\n\n".join(parts)

    def to_prompt_context(self) -> str:
        """Alias for render() returning formatted prompt context string."""
        return self.render()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "token_breakdown": self.token_breakdown,
            "has_objective": bool(self.task_objective_section),
            "has_verification": bool(self.verification_state_section),
            "has_errors": bool(self.errors_section),
            "has_diff": bool(self.diff_section),
            "has_repository_context": bool(self.repository_intelligence_section),
            "has_skills": bool(self.skills_section),
            "has_memory": bool(self.working_memory_section),
        }


from enum import Enum
import json


class CompressionLevel(str, Enum):
    NONE = "none"
    WHITESPACE = "whitespace"
    COMMENTS = "comments"
    SKELETON = "skeleton"


class CodeCompressor:
    @staticmethod
    def strip_whitespace(code: str) -> str:
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


class JSONCompressor:
    @staticmethod
    def compress(data: Any, max_tokens: int = 3000) -> str:
        if isinstance(data, str):
            return data[:max_tokens * 4]
        try:
            dumped = json.dumps(data, indent=2, default=str)
            if len(dumped) > max_tokens * 4:
                return dumped[:max_tokens * 4] + "\n... [truncated]"
            return dumped
        except Exception:
            return str(data)[:max_tokens * 4]


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

        important_indices: Set[int] = set()
        for i in range(min(3, len(lines))):
            important_indices.add(i)
        for i in range(max(0, len(lines) - 5), len(lines)):
            important_indices.add(i)

        for i, line in enumerate(lines):
            low = line.lower()
            if any(marker in low for marker in cls.ERROR_MARKERS):
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
    @staticmethod
    def compress_text(text: str, level: CompressionLevel = CompressionLevel.WHITESPACE) -> str:
        if level == CompressionLevel.NONE or not text:
            return text
        return CodeCompressor.strip_whitespace(text)

    @classmethod
    def compress(
        cls,
        content: str,
        max_tokens: int,
        content_hint: Optional[str] = None,
    ) -> str:
        if not content:
            return ""
        est_tokens = max(1, len(content) // 4)
        if est_tokens <= max_tokens:
            return content

        hint = (content_hint or "").lower()
        stripped = content.strip()
        if hint == "json" or (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]")):
            try:
                compressed_json = JSONCompressor.compress(stripped, max_tokens=max_tokens)
                if len(compressed_json) // 4 <= max_tokens:
                    return compressed_json
            except Exception:
                pass

        if hint in ("log", "terminal", "test_output") or "Traceback (most recent call last)" in content or "=== test session starts ===" in content:
            compressed_log = LogCompressor.compress(content, max_lines=max(15, max_tokens // 4))
            if len(compressed_log) // 4 <= max_tokens:
                return compressed_log

        if hint in ("code", "python") or "def " in content or "class " in content or "import " in content:
            c_ws = CodeCompressor.strip_whitespace(content)
            if len(c_ws) // 4 <= max_tokens:
                return c_ws

        norm = CodeCompressor.strip_whitespace(content)
        return norm

