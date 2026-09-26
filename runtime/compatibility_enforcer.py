"""
Compatibility Matrix Enforcer.
Operationalizes tool_audit/compatibility_matrix.json into an executable pre-flight validator.
Enforces the strict rule:
Before runtime starts, validate:
Skill -> Required Tool -> Exists? -> Healthy? -> Authorized? -> Executable?
If any requirement fails: Do not execute. Return structured diagnostic.
Never silently substitute another tool.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .tool_state_machine import ToolLifecycleState, ToolStateMachine

logger = logging.getLogger("runtime.compatibility_enforcer")


@dataclass
class ToolRequirementStatus:
    tool_name: str
    exists: bool
    healthy: bool
    authorized: bool
    executable: bool
    current_state: str
    error_reason: Optional[str] = None

    def is_valid(self) -> bool:
        return self.exists and self.healthy and self.authorized and self.executable


@dataclass
class CompatibilityValidationReport:
    """Structured report returned before executing any skill."""
    valid: bool
    skill_name: str
    required_tools: List[str] = field(default_factory=list)
    tool_statuses: Dict[str, ToolRequirementStatus] = field(default_factory=dict)
    missing_tools: List[str] = field(default_factory=list)
    unhealthy_tools: List[str] = field(default_factory=list)
    unauthorized_tools: List[str] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def compatible(self) -> bool:
        return self.valid

    @property
    def missing_or_blocked(self) -> List[str]:
        return self.missing_tools + self.unhealthy_tools + self.unauthorized_tools

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "compatible": self.compatible,
            "skill_name": self.skill_name,
            "required_tools": self.required_tools,
            "missing_tools": self.missing_tools,
            "unhealthy_tools": self.unhealthy_tools,
            "unauthorized_tools": self.unauthorized_tools,
            "missing_or_blocked": self.missing_or_blocked,
            "tool_statuses": {k: v.__dict__ for k, v in self.tool_statuses.items()},
            "diagnostics": self.diagnostics,
        }

    def format_diagnostic_summary(self) -> str:
        if self.valid:
            return f"All {len(self.required_tools)} required tools for skill '{self.skill_name}' are EXECUTABLE."
        lines = [f"Compatibility verification FAILED for skill '{self.skill_name}':"]
        for diag in self.diagnostics:
            lines.append(f"  - [{diag['stage']}] {diag['tool']}: {diag['reason']}")
        return "\n".join(lines)


# Backward-compatible alias
CompatibilityReport = CompatibilityValidationReport


class CompatibilityMatrixEnforcer:
    """
    Validates skills and task capability requirements against live tool states.
    Aborts runtime execution when any required tool fails the lifecycle chain:
    Exists -> Healthy -> Authorized -> Executable.
    """

    def __init__(
        self,
        matrix_path: Optional[Path] = None,
        tool_state_machine: Optional[ToolStateMachine] = None,
    ):
        if matrix_path is None:
            default_path = Path(__file__).resolve().parents[1] / "tool_audit" / "compatibility_matrix.json"
            self.matrix_path = default_path if default_path.exists() else None
        else:
            self.matrix_path = Path(matrix_path)

        self.tool_state_machine = tool_state_machine or ToolStateMachine()
        self.tool_sm = self.tool_state_machine
        self._matrix_by_skill: Dict[str, List[Dict[str, Any]]] = {}
        self._load_matrix()

    def _load_matrix(self):
        """Loads compatibility matrix json if available."""
        if not self.matrix_path or not self.matrix_path.exists():
            return
        try:
            data = json.loads(self.matrix_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, list):
                for entry in data:
                    s_name = entry.get("skill")
                    if s_name:
                        self._matrix_by_skill.setdefault(s_name, []).append(entry)
        except Exception as e:
            logger.debug("Failed to parse compatibility matrix JSON: %s", e)

    def enforce_skill_compatibility(
        self,
        skill_name: str,
        required_tools: Optional[List[str]] = None,
        allowed_tools: Optional[Set[str]] = None,
    ) -> CompatibilityValidationReport:
        """Enforces skill compatibility against explicitly passed required tools or manifest."""
        return self.validate_skill(skill_name, required_tools=required_tools, allowed_tools=allowed_tools)

    def validate_skill(
        self,
        skill_name: str,
        skill_manifest: Optional[Any] = None,
        allowed_tools: Optional[Set[str]] = None,
        required_tools: Optional[List[str]] = None,
    ) -> CompatibilityValidationReport:
        """
        Validates the complete lifecycle chain for all tools required by a skill:
        Skill -> Required Tool -> Exists? -> Healthy? -> Authorized? -> Executable?
        """
        # Determine required tools from manifest, explicit list, or compatibility matrix
        req_tools: Set[str] = set(required_tools or [])
        if skill_manifest and hasattr(skill_manifest, "required_tools") and skill_manifest.required_tools:
            req_tools.update(skill_manifest.required_tools)
        elif not req_tools and skill_name in self._matrix_by_skill:
            for entry in self._matrix_by_skill[skill_name]:
                rt = entry.get("required_tool")
                if rt:
                    req_tools.add(rt)

        if not req_tools:
            # No required tools defined
            return CompatibilityValidationReport(
                valid=True,
                skill_name=skill_name,
                required_tools=[],
            )

        # Discover tools in state machine
        self.tool_state_machine.discover_tools()

        tool_statuses: Dict[str, ToolRequirementStatus] = {}
        missing: List[str] = []
        unhealthy: List[str] = []
        unauthorized: List[str] = []
        diagnostics: List[Dict[str, Any]] = []

        all_valid = True

        for t_name in sorted(list(req_tools)):
            # 1. Exists?
            managed = self.tool_state_machine._tools.get(t_name)
            if not managed:
                # Try finding under alias
                alias = t_name.replace("filesystem.", "").replace("terminal.", "").replace("browser.", "browser_")
                managed = self.tool_state_machine._tools.get(alias)

            exists = managed is not None and managed.state != ToolLifecycleState.MISSING
            if not exists:
                all_valid = False
                missing.append(t_name)
                diagnostics.append({
                    "stage": "EXISTS",
                    "tool": t_name,
                    "reason": f"Tool '{t_name}' does not exist in any registered builtin or MCP server.",
                })
                tool_statuses[t_name] = ToolRequirementStatus(
                    tool_name=t_name,
                    exists=False,
                    healthy=False,
                    authorized=False,
                    executable=False,
                    current_state=ToolLifecycleState.MISSING.value,
                    error_reason=f"Tool '{t_name}' not found",
                )
                continue

            # 2. Healthy?
            healthy = self.tool_state_machine.verify_health(managed.name)
            if not healthy:
                all_valid = False
                unhealthy.append(t_name)
                diagnostics.append({
                    "stage": "HEALTHY",
                    "tool": t_name,
                    "reason": f"Tool '{t_name}' failed health check: {managed.error_reason or 'unhealthy'}",
                })
                tool_statuses[t_name] = ToolRequirementStatus(
                    tool_name=t_name,
                    exists=True,
                    healthy=False,
                    authorized=False,
                    executable=False,
                    current_state=managed.state.value,
                    error_reason=managed.error_reason,
                )
                continue

            # 3. Authorized?
            authorized = self.tool_state_machine.authorize(managed.name, allowed_tools=allowed_tools)
            if not authorized:
                all_valid = False
                unauthorized.append(t_name)
                diagnostics.append({
                    "stage": "AUTHORIZED",
                    "tool": t_name,
                    "reason": f"Tool '{t_name}' is not authorized under active policy: {managed.error_reason or 'unauthorized'}",
                })
                tool_statuses[t_name] = ToolRequirementStatus(
                    tool_name=t_name,
                    exists=True,
                    healthy=True,
                    authorized=False,
                    executable=False,
                    current_state=managed.state.value,
                    error_reason=managed.error_reason,
                )
                continue

            # 4. Executable?
            if managed.state == ToolLifecycleState.AUTHORIZED:
                self.tool_state_machine.promote_to_executable(managed.name)
            executable = managed.state == ToolLifecycleState.EXECUTABLE
            tool_statuses[t_name] = ToolRequirementStatus(
                tool_name=t_name,
                exists=True,
                healthy=True,
                authorized=True,
                executable=executable,
                current_state=managed.state.value,
            )

        return CompatibilityValidationReport(
            valid=all_valid,
            skill_name=skill_name,
            required_tools=sorted(list(req_tools)),
            tool_statuses=tool_statuses,
            missing_tools=missing,
            unhealthy_tools=unhealthy,
            unauthorized_tools=unauthorized,
            diagnostics=diagnostics,
        )
