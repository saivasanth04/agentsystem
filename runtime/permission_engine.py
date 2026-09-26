"""
Runtime Permission Engine.
Enforces runtime security, workspace sandboxing, command safety,
and capability-based authorization before any tool execution occurs.
"""
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from .tool_policy import ToolPolicy

logger = logging.getLogger("runtime.permission_engine")

# Dangerous command patterns prohibited from unconstrained terminal execution
DANGEROUS_COMMAND_PATTERNS = [
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?[a-zA-Z]*\s+[/~]",
    r"\brm\s+-rf\s+/",
    r"\bmkfs\b",
    r"\bdd\s+if=.*of=/dev/[sh]d[a-z]",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # Fork bomb
    r"\bformat\s+[a-z]:",
    r"\bshutdown\b",
    r"\breboot\b",
]

# Sensitive paths requiring strict isolation or approval
SENSITIVE_PATHS = [
    ".git/",
    ".git\\",
    ".env",
    ".ssh",
    "/etc",
    "C:\\Windows",
]


@dataclass
class PermissionEvaluationResult:
    """Outcome of evaluating a tool invocation against active security policies."""
    allowed: bool
    tool_name: str
    reason: str = ""
    requires_approval: bool = False
    sanitized_arguments: Dict[str, Any] = field(default_factory=dict)
    violation_category: Optional[str] = None  # 'UNAUTHORIZED_TOOL' | 'PATH_TRAVERSAL' | 'DANGEROUS_COMMAND' | 'APPROVAL_REQUIRED'

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "tool_name": self.tool_name,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "sanitized_arguments": self.sanitized_arguments,
            "violation_category": self.violation_category,
        }


class PermissionEngine:
    """
    Evaluates and enforces permission boundaries on all tool invocations.
    """

    def __init__(
        self,
        workspace_manager: Optional[Any] = None,
        dispatcher: Optional[Any] = None,
        default_policy: Optional[ToolPolicy] = None,
        **kwargs: Any,
    ):
        self.workspace = workspace_manager
        self.dispatcher = dispatcher or kwargs.get("tool_dispatcher")
        self.default_policy = default_policy or ToolPolicy()

        if self.workspace is None:
            try:
                from agent_orchestrator.tools.workspace import WorkspaceManager
                self.workspace = WorkspaceManager()
            except Exception as e:
                logger.debug(f"WorkspaceManager default initialization deferred: {e}")

    def evaluate(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        parameters: Optional[Dict[str, Any]] = None,
        policy: Optional[ToolPolicy] = None,
        tool_policy: Optional[ToolPolicy] = None,
        **kwargs: Any,
    ) -> PermissionEvaluationResult:
        """Convenience alias for evaluate_tool_call."""
        args = arguments if arguments is not None else (parameters or {})
        pol = policy or tool_policy or self.default_policy
        return self.evaluate_tool_call(tool_name=tool_name, arguments=args, policy=pol, context=kwargs)

    def evaluate_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        policy: Optional[ToolPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> PermissionEvaluationResult:
        """
        Validates tool execution against active capability policy,
        workspace containment, command safety, and approval rules.
        """
        active_policy = policy or self.default_policy
        clean_name = (tool_name or "").strip()
        args = dict(arguments or {})

        # 1. Capability & Tool Authorization Check
        if not active_policy.is_tool_allowed(clean_name, args):
            resolved = active_policy.resolve_tool_name(clean_name)
            return PermissionEvaluationResult(
                allowed=False,
                tool_name=clean_name,
                reason=f"Tool '{clean_name}' (resolved: '{resolved}') is not permitted under the active capability policy.",
                violation_category="UNAUTHORIZED_TOOL",
                sanitized_arguments=args,
            )

        # 2. Workspace Path Containment & Traversal Protection
        path_error = self._validate_path_arguments(clean_name, args)
        if path_error:
            return PermissionEvaluationResult(
                allowed=False,
                tool_name=clean_name,
                reason=path_error,
                violation_category="PATH_TRAVERSAL",
                sanitized_arguments=args,
            )

        # 3. Shell Command Safety Check
        command_error = self._validate_command_safety(clean_name, args)
        if command_error:
            return PermissionEvaluationResult(
                allowed=False,
                tool_name=clean_name,
                reason=command_error,
                violation_category="DANGEROUS_COMMAND",
                sanitized_arguments=args,
            )

        # 4. Approval Requirement Check
        requires_approval = self._check_approval_required(clean_name, args, active_policy, context)
        if requires_approval and not (context and context.get("user_approved", False)):
            return PermissionEvaluationResult(
                allowed=True,
                tool_name=clean_name,
                reason=f"Tool '{clean_name}' involves destructive or state-changing action requiring user approval.",
                requires_approval=True,
                violation_category="APPROVAL_REQUIRED",
                sanitized_arguments=args,
            )

        return PermissionEvaluationResult(
            allowed=True,
            tool_name=clean_name,
            reason="Authorized",
            requires_approval=False,
            sanitized_arguments=args,
        )

    def _validate_path_arguments(self, tool_name: str, args: Dict[str, Any]) -> Optional[str]:
        """Ensures all path arguments reside within workspace boundaries."""
        if not self.workspace:
            return None

        root = Path(self.workspace.root_dir).resolve()
        path_keys = [
            "path", "filepath", "source_filepath", "target_dir",
            "file_path", "old_path", "new_path", "target_path",
        ]

        for k in path_keys:
            if k in args and isinstance(args[k], str) and args[k].strip():
                val = args[k].strip()

                # Sensitive internal path checks for write operations
                if any(tool_name.startswith(p) for p in ["write", "delete", "replace", "apply", "insert", "move", "rename"]):
                    for sp in SENSITIVE_PATHS:
                        if sp in val:
                            return f"Access to sensitive path '{val}' is forbidden for mutating tool '{tool_name}'."

                # Boundary containment check
                try:
                    resolved_path = (root / val).resolve()
                    if not str(resolved_path).startswith(str(root)):
                        return f"Path traversal violation: '{val}' escapes workspace root ({root})."
                except Exception as e:
                    return f"Invalid path argument '{val}': {e}"

        return None

    def _validate_command_safety(self, tool_name: str, args: Dict[str, Any]) -> Optional[str]:
        """Inspects shell commands for hazardous patterns."""
        if tool_name in ["terminal_execute", "run_command", "bash", "terminal.run"]:
            cmd = args.get("command", "")
            if isinstance(cmd, str):
                for pat in DANGEROUS_COMMAND_PATTERNS:
                    if re.search(pat, cmd, re.IGNORECASE):
                        return f"Execution of dangerous command pattern '{pat}' is strictly blocked."
        return None

    def _check_approval_required(
        self,
        tool_name: str,
        args: Dict[str, Any],
        policy: ToolPolicy,
        context: Optional[Dict[str, Any]],
    ) -> bool:
        """Determines if the operation requires explicit confirmation."""
        if policy.approval_required:
            return True

        # Destructive tools inherently requiring approval if not pre-cleared
        destructive_tools = {"delete_file", "git_commit", "git_restore", "rollback_to_checkpoint"}
        resolved = policy.resolve_tool_name(tool_name)
        if resolved in destructive_tools or tool_name in destructive_tools:
            return True

        return False

    def execute_with_permission(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        policy: Optional[ToolPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Evaluates permissions and executes tool via dispatcher if authorized.
        """
        eval_result = self.evaluate_tool_call(tool_name, arguments, policy=policy, context=context)

        if not eval_result.allowed:
            return {
                "success": False,
                "is_error": True,
                "error": eval_result.reason,
                "output": eval_result.reason,
                "violation_category": eval_result.violation_category,
            }

        if eval_result.requires_approval:
            return {
                "success": False,
                "is_error": False,
                "requires_approval": True,
                "reason": eval_result.reason,
                "tool_name": tool_name,
                "arguments": eval_result.sanitized_arguments,
            }

        # Dispatch via UnifiedToolDispatcher
        if self.dispatcher and hasattr(self.dispatcher, "call_tool"):
            return self.dispatcher.call_tool(tool_name, eval_result.sanitized_arguments, **kwargs)
        elif self.dispatcher and hasattr(self.dispatcher, "execute_tool"):
            return self.dispatcher.execute_tool(tool_name, eval_result.sanitized_arguments, **kwargs)

        return {
            "success": True,
            "output": f"Tool '{tool_name}' verified and permitted.",
            "tool_name": tool_name,
            "arguments": eval_result.sanitized_arguments,
        }
