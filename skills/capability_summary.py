"""
Minimal Capability Summary for Skills.
Transforms passive SKILL.md markdown files into minimal, bounded runtime capability summaries.
Strictly ensures that full SKILL.md instructions never bleed into LLM prompts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional, Union


@dataclass
class MinimalCapabilitySummary:
    """
    Encapsulates the strictly minimal capability summary of a skill.
    Only this summary and its active constraints may be exposed to the LLM.
    """
    name: str
    summary: str
    required_capabilities: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)

    def to_compact_string(self) -> str:
        """Renders a single-line or two-line bounded representation."""
        caps = f" (Caps: {', '.join(self.required_capabilities)})" if self.required_capabilities else ""
        constr = f" [Rules: {'; '.join(self.constraints[:2])}]" if self.constraints else ""
        return f"{self.name}: {self.summary}{caps}{constr}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "required_capabilities": self.required_capabilities,
            "constraints": self.constraints,
        }


class CapabilitySummaryExtractor:
    """
    Extracts minimal capability summaries from skill objects or manifests.
    Strips raw markdown syntax, lengthy instructions, and prompt-bloat text.
    """

    @classmethod
    def extract(cls, skill: Any) -> MinimalCapabilitySummary:
        """
        Derives a MinimalCapabilitySummary from a skill object, manifest, or dict.
        """
        if isinstance(skill, MinimalCapabilitySummary):
            return skill

        name = ""
        raw_desc = ""
        required_tools: List[str] = []
        constraints: List[str] = []

        if isinstance(skill, dict):
            name = skill.get("name") or skill.get("skill_name") or "unknown_skill"
            raw_desc = skill.get("description") or skill.get("summary") or ""
            required_tools = skill.get("required_tools") or skill.get("tools") or []
            constraints = skill.get("constraints") or skill.get("rules") or []
        elif hasattr(skill, "name"):
            name = getattr(skill, "name", "unknown_skill")
            raw_desc = getattr(skill, "description", "") or getattr(skill, "summary", "")
            required_tools = getattr(skill, "required_tools", []) or []
            constraints = getattr(skill, "constraints", []) or []
        else:
            name = str(skill)
            raw_desc = f"Capability for {name}"

        # Clean description to a single concise sentence (max 120 chars)
        clean_desc = cls._clean_description(raw_desc)

        # Clean constraints to concise statements
        clean_constraints = [cls._clean_constraint(c) for c in constraints if c][:3]

        return MinimalCapabilitySummary(
            name=name,
            summary=clean_desc,
            required_capabilities=[t for t in required_tools if t][:4],
            constraints=[c for c in clean_constraints if c],
        )

    @classmethod
    def _clean_description(cls, text: str) -> str:
        if not text:
            return "Domain engineering capability"
        # Remove markdown headers and formatting
        cleaned = re.sub(r"[#*`_~>\-\[\]]", " ", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        # Take first sentence or first 120 characters
        first_sentence = cleaned.split(". ")[0].strip()
        if len(first_sentence) > 120:
            first_sentence = first_sentence[:117] + "..."
        return first_sentence or "Domain engineering capability"

    @classmethod
    def _clean_constraint(cls, text: str) -> str:
        if not text:
            return ""
        cleaned = re.sub(r"[#*`_~>\-\[\]]", " ", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:80] if len(cleaned) > 80 else cleaned

    @classmethod
    def format_all(cls, summaries: List[MinimalCapabilitySummary]) -> str:
        """Formats multiple capability summaries for LLM context inclusion."""
        if not summaries:
            return ""
        lines = [s.to_compact_string() for s in summaries]
        return "Active Capabilities:\n" + "\n".join(f"- {l}" for l in lines)
