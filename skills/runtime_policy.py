"""
Authoritative Runtime Policy for Executable Skills.
Defines ToolPolicy, RuntimePolicy, ContextRequirements, and VerificationRequirements
that govern capability-driven execution across the unified execution spine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional, Set

from .policy import (
    DEFAULT_TOOL_ALIASES,
    RuntimePolicy as BaseRuntimePolicy,
    ToolPolicy as BaseToolPolicy,
)

logger = logging.getLogger("skills.runtime_policy")


@dataclass
class ContextRequirements:
    """
    Specifies explicit context constraints and queries demanded by a skill or task.
    Prevents uncontrolled prompt concatenation and ensures context passes strictly
    through ContextCompiler token budgeting.
    """
    required_streams: Set[str] = field(default_factory=lambda: {"task_objective", "working_memory"})
    file_patterns: List[str] = field(default_factory=list)
    symbol_queries: List[str] = field(default_factory=list)
    max_context_tokens: int = 4000
    focal_file_budget: int = 1500
    diff_budget: int = 1000

    def merge(self, other: Optional[ContextRequirements]) -> ContextRequirements:
        if not other:
            return ContextRequirements(
                required_streams=set(self.required_streams),
                file_patterns=list(self.file_patterns),
                symbol_queries=list(self.symbol_queries),
                max_context_tokens=self.max_context_tokens,
                focal_file_budget=self.focal_file_budget,
                diff_budget=self.diff_budget,
            )
        return ContextRequirements(
            required_streams=self.required_streams.union(other.required_streams),
            file_patterns=list(dict.fromkeys(self.file_patterns + other.file_patterns)),
            symbol_queries=list(dict.fromkeys(self.symbol_queries + other.symbol_queries)),
            max_context_tokens=max(self.max_context_tokens, other.max_context_tokens),
            focal_file_budget=max(self.focal_file_budget, other.focal_file_budget),
            diff_budget=max(self.diff_budget, other.diff_budget),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "required_streams": sorted(list(self.required_streams)),
            "file_patterns": self.file_patterns,
            "symbol_queries": self.symbol_queries,
            "max_context_tokens": self.max_context_tokens,
            "focal_file_budget": self.focal_file_budget,
            "diff_budget": self.diff_budget,
        }


@dataclass
class VerificationRequirements:
    """
    Specifies required verification lifecycle gates for a task or skill.
    """
    stages: List[str] = field(default_factory=lambda: ["edit", "build", "lint", "tests"])
    test_runner: Optional[str] = None  # e.g. "pytest", "vitest", "npm test"
    browser_verification_required: bool = False
    security_scan_required: bool = False
    fail_fast: bool = True

    def merge(self, other: Optional[VerificationRequirements]) -> VerificationRequirements:
        if not other:
            return VerificationRequirements(
                stages=list(self.stages),
                test_runner=self.test_runner,
                browser_verification_required=self.browser_verification_required,
                security_scan_required=self.security_scan_required,
                fail_fast=self.fail_fast,
            )
        merged_stages = list(dict.fromkeys(self.stages + other.stages))
        return VerificationRequirements(
            stages=merged_stages,
            test_runner=other.test_runner or self.test_runner,
            browser_verification_required=self.browser_verification_required or other.browser_verification_required,
            security_scan_required=self.security_scan_required or other.security_scan_required,
            fail_fast=self.fail_fast and other.fail_fast,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stages": self.stages,
            "test_runner": self.test_runner,
            "browser_verification_required": self.browser_verification_required,
            "security_scan_required": self.security_scan_required,
            "fail_fast": self.fail_fast,
        }


# Re-export BaseToolPolicy and BaseRuntimePolicy for authoritative single-import access
ToolPolicy = BaseToolPolicy
RuntimePolicy = BaseRuntimePolicy

__all__ = [
    "DEFAULT_TOOL_ALIASES",
    "ToolPolicy",
    "RuntimePolicy",
    "ContextRequirements",
    "VerificationRequirements",
]
