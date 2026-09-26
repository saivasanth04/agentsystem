"""
Executable Compatibility Matrix Validator.
Enforces the pre-execution validation pipeline:
Skill -> Required Tool -> Exists? -> Healthy? -> Authorized? -> Executable?

If any prerequisite fails:
Halts execution and returns a structured diagnostic report.
Never silently substitutes another tool.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .tool_state_machine import ToolStateMachine, ToolLifecycleState

logger = logging.getLogger("runtime.compatibility_matrix")


@dataclass
class ToolRequirementStatus:
    tool_name: str
    skill_name: str
    state: ToolLifecycleState
    exists: bool
    healthy: bool
    authorized: bool
    executable: bool
    diagnostic_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "skill_name": self.skill_name,
            "state": self.state.value,
            "exists": self.exists,
            "healthy": self.healthy,
            "authorized": self.authorized,
            "executable": self.executable,
            "diagnostic_message": self.diagnostic_message,
        }


@dataclass
class CompatibilityValidationReport:
    """Report produced by the pre-execution compatibility validation check."""
    is_compatible: bool
    active_skills: List[str] = field(default_factory=list)
    total_required_tools: int = 0
    passed_tools: List[str] = field(default_factory=list)
    failed_tools: List[str] = field(default_factory=list)
    tool_diagnostics: List[ToolRequirementStatus] = field(default_factory=list)
    failure_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_compatible": self.is_compatible,
            "active_skills": self.active_skills,
            "total_required_tools": self.total_required_tools,
            "passed_tools": self.passed_tools,
            "failed_tools": self.failed_tools,
            "tool_diagnostics": [td.to_dict() for td in self.tool_diagnostics],
            "failure_reasons": self.failure_reasons,
        }


class ExecutableCompatibilityValidator:
    """
    Validates skills and tools against the executable compatibility matrix before runtime begins.
    """

    def __init__(
        self,
        tool_state_machine: ToolStateMachine,
        skill_registry: Optional[Any] = None,
        matrix_path: Optional[Path] = None,
    ):
        self.state_machine = tool_state_machine
        self.skill_registry = skill_registry
        self.matrix_path = matrix_path or (
            Path(__file__).resolve().parents[1] / "tool_audit" / "compatibility_matrix.json"
        )
        self.matrix_data: Dict[str, Any] = {}
        self._load_matrix()

    def _load_matrix(self):
        if self.matrix_path.exists():
            try:
                with open(self.matrix_path, "r", encoding="utf-8") as f:
                    self.matrix_data = json.load(f)
            except Exception as e:
                logger.warning(f"Could not load compatibility_matrix.json: {e}")

    def validate_skills(
        self,
        active_skills: List[str],
        tool_policy: Optional[Any] = None,
    ) -> CompatibilityValidationReport:
        """
        Validates all tools required by active skills:
        Skill -> Required Tool -> Exists? -> Healthy? -> Authorized? -> Executable?
        """
        tool_statuses: List[ToolRequirementStatus] = []
        passed_tools: List[str] = []
        failed_tools: List[str] = []
        failure_reasons: List[str] = []

        seen_tool_skill: Set[tuple] = set()

        for s_name in active_skills:
            req_tools = self._get_tools_for_skill(s_name)
            for t_name in req_tools:
                if (t_name, s_name) in seen_tool_skill:
                    continue
                seen_tool_skill.add((t_name, s_name))

                # Transition tool through lifecycle
                record = self.state_machine.transition_executable(t_name, tool_policy=tool_policy)

                exists = record.state in (
                    ToolLifecycleState.DISCOVERED,
                    ToolLifecycleState.HEALTHY,
                    ToolLifecycleState.AUTHORIZED,
                    ToolLifecycleState.EXECUTABLE,
                )
                healthy = record.state in (
                    ToolLifecycleState.HEALTHY,
                    ToolLifecycleState.AUTHORIZED,
                    ToolLifecycleState.EXECUTABLE,
                )
                authorized = record.state in (
                    ToolLifecycleState.AUTHORIZED,
                    ToolLifecycleState.EXECUTABLE,
                )
                executable = record.is_executable()

                status = ToolRequirementStatus(
                    tool_name=t_name,
                    skill_name=s_name,
                    state=record.state,
                    exists=exists,
                    healthy=healthy,
                    authorized=authorized,
                    executable=executable,
                    diagnostic_message=record.diagnostic_message,
                )
                tool_statuses.append(status)

                if executable:
                    passed_tools.append(t_name)
                else:
                    failed_tools.append(t_name)
                    diag = record.diagnostic_message or f"Tool '{t_name}' in non-executable state: {record.state.value}"
                    failure_reasons.append(f"Skill '{s_name}' requires tool '{t_name}': {diag}")

        is_compatible = len(failed_tools) == 0

        return CompatibilityValidationReport(
            is_compatible=is_compatible,
            active_skills=active_skills,
            total_required_tools=len(tool_statuses),
            passed_tools=passed_tools,
            failed_tools=failed_tools,
            tool_diagnostics=tool_statuses,
            failure_reasons=failure_reasons,
        )

    def _get_tools_for_skill(self, skill_name: str) -> List[str]:
        """Resolves required tools for a skill from SkillRegistry or compatibility matrix."""
        # 1. Try SkillRegistry
        if self.skill_registry and hasattr(self.skill_registry, "get_skill"):
            sk = self.skill_registry.get_skill(skill_name)
            if sk and hasattr(sk, "required_tools") and sk.required_tools:
                return list(sk.required_tools)

        # 2. Try loaded compatibility matrix
        if self.matrix_data:
            # Check by skill name in matrix
            for entry in self.matrix_data.get("matrix", []):
                if entry.get("skill_name") == skill_name:
                    return entry.get("required_tools", [])

        # Default mapping for known core skills
        default_map = {
            "browser-testing-with-devtools": ["browser_console", "browser_inspect", "browser_network"],
            "test-driven-development": ["terminal_execute"],
            "frontend-ui-engineering": ["write_file", "read_file"],
            "git-workflow-and-versioning": ["git_diff"],
        }
        return default_map.get(skill_name, ["read_file"])
