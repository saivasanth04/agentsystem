"""
Mutation Authorization & Semantic Integrity Engine.
Validates file mutation requests across agents, tool execution, and deliverable persistence.
Enforces task-scoped write boundaries, destructive shrinkage protection, pre-commit syntax validation,
protected file policies, and risk-graded approval escalation.
"""
import ast
from dataclasses import dataclass, field
from enum import Enum
import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union


class MutationType(str, Enum):
    __test__ = False
    CREATE_FILE = "CREATE_FILE"
    UPDATE_FILE = "UPDATE_FILE"
    DELETE_FILE = "DELETE_FILE"
    DIFF_APPLY = "DIFF_APPLY"


class MutationRiskLevel(str, Enum):
    __test__ = False
    SAFE = "SAFE"
    MODERATE = "MODERATE"
    HIGH_RISK = "HIGH_RISK"
    CRITICAL = "CRITICAL"


class MutationDecision(str, Enum):
    __test__ = False
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


class MutationAuthorizationError(PermissionError):
    """Raised when a file mutation violates the mutation authorization policy."""
    def __init__(
        self,
        message: str,
        path: Optional[str] = None,
        risk_level: Optional[MutationRiskLevel] = None,
        suggested_action: Optional[str] = None,
    ):
        super().__init__(message)
        self.path = path
        self.risk_level = risk_level
        self.suggested_action = suggested_action


# Default protected patterns that require elevated risk or approval
DEFAULT_PROTECTED_PATTERNS = [
    ".github/*", ".gitlab-ci.yml", ".circleci/*",
    "migrations/*", "alembic/*",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "pyproject.toml", "poetry.lock", "Pipfile.lock", "requirements.txt",
    "docker-compose.yml", "docker-compose.*.yml", "Dockerfile",
    ".env*", "*.env",
    "webpack.config.*", "vite.config.*", "tsconfig.json",
]

# Prohibited binary or sensitive extensions
DEFAULT_PROHIBITED_EXTENSIONS = [
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".pem", ".key", ".pkcs12", ".pfx",
    ".zip", ".tar", ".gz", ".7z",
]


@dataclass
class MutationPolicy:
    """Policy governing permissible file modifications."""
    __test__ = False
    enforce_task_scope: bool = False
    prevent_destructive_shrinkage: bool = True
    max_allowed_shrinkage_ratio: float = 0.70  # >70% loss of lines flags shrinkage
    min_lines_for_shrinkage_check: int = 15     # Only check shrinkage on files with >=15 lines
    validate_syntax_pre_commit: bool = True
    protected_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_PROTECTED_PATTERNS))
    prohibited_extensions: List[str] = field(default_factory=lambda: list(DEFAULT_PROHIBITED_EXTENSIONS))
    require_approval_for_high_risk: bool = False
    allowed_write_paths: Optional[List[str]] = None
    forbidden_write_paths: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enforce_task_scope": self.enforce_task_scope,
            "prevent_destructive_shrinkage": self.prevent_destructive_shrinkage,
            "max_allowed_shrinkage_ratio": self.max_allowed_shrinkage_ratio,
            "min_lines_for_shrinkage_check": self.min_lines_for_shrinkage_check,
            "validate_syntax_pre_commit": self.validate_syntax_pre_commit,
            "protected_patterns": self.protected_patterns,
            "prohibited_extensions": self.prohibited_extensions,
            "require_approval_for_high_risk": self.require_approval_for_high_risk,
            "allowed_write_paths": self.allowed_write_paths,
            "forbidden_write_paths": self.forbidden_write_paths,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "MutationPolicy":
        if not data:
            return cls()
        return cls(
            enforce_task_scope=bool(data.get("enforce_task_scope", False)),
            prevent_destructive_shrinkage=bool(data.get("prevent_destructive_shrinkage", True)),
            max_allowed_shrinkage_ratio=float(data.get("max_allowed_shrinkage_ratio", 0.70)),
            min_lines_for_shrinkage_check=int(data.get("min_lines_for_shrinkage_check", 15)),
            validate_syntax_pre_commit=bool(data.get("validate_syntax_pre_commit", True)),
            protected_patterns=data.get("protected_patterns", list(DEFAULT_PROTECTED_PATTERNS)),
            prohibited_extensions=data.get("prohibited_extensions", list(DEFAULT_PROHIBITED_EXTENSIONS)),
            require_approval_for_high_risk=bool(data.get("require_approval_for_high_risk", False)),
            allowed_write_paths=data.get("allowed_write_paths"),
            forbidden_write_paths=data.get("forbidden_write_paths"),
        )


@dataclass
class MutationAuthorizationResult:
    """Detailed evaluation result for a proposed file mutation."""
    __test__ = False
    allowed: bool
    decision: MutationDecision
    risk_level: MutationRiskLevel
    reason: str
    path: str
    mutation_type: MutationType
    syntax_valid: bool = True
    shrinkage_ratio: float = 0.0
    suggested_action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "decision": self.decision.value,
            "risk_level": self.risk_level.value,
            "reason": self.reason,
            "path": self.path,
            "mutation_type": self.mutation_type.value,
            "syntax_valid": self.syntax_valid,
            "shrinkage_ratio": self.shrinkage_ratio,
            "suggested_action": self.suggested_action,
        }


class MutationAuthorizer:
    """
    Evaluates and authorizes proposed file mutations against task scope,
    destructive shrinkage, syntax validity, and protected file policies.
    """
    __test__ = False

    def __init__(
        self,
        policy: Optional[MutationPolicy] = None,
        approval_gate: Optional[Any] = None,
    ):
        self.policy = policy or MutationPolicy()
        self.approval_gate = approval_gate

    @staticmethod
    def _validate_syntax(filepath: str, content: str) -> Tuple[bool, Optional[str]]:
        """Performs pre-commit AST and syntax validation."""
        norm = filepath.lower()
        if norm.endswith(".py"):
            try:
                ast.parse(content, filename=filepath)
                return True, None
            except SyntaxError as e:
                return False, f"Python AST SyntaxError at line {e.lineno}: {e.msg}"
        elif norm.endswith(".json"):
            try:
                json.loads(content)
                return True, None
            except json.JSONDecodeError as e:
                return False, f"JSON DecodeError: {e.msg} at line {e.lineno}"
        return True, None

    def authorize_mutation(
        self,
        filepath: str,
        new_content: Optional[str],
        old_content: Optional[str] = None,
        mutation_type: Optional[MutationType] = None,
        agent_role: Optional[str] = None,
        task_scope: Optional[Dict[str, Any]] = None,
        task_permissions: Optional[Any] = None,
        is_approved: bool = False,
    ) -> MutationAuthorizationResult:
        """
        Evaluates whether a proposed mutation is authorized.
        """
        norm_path = str(filepath).replace("\\", "/").strip("/")

        # 1. Determine mutation type if not provided
        if mutation_type is None:
            if new_content is None:
                m_type = MutationType.DELETE_FILE
            elif old_content is None:
                m_type = MutationType.CREATE_FILE
            else:
                m_type = MutationType.UPDATE_FILE
        else:
            m_type = mutation_type

        # 2. Check prohibited file extensions (e.g. .exe, .key, .dll)
        ext = os.path.splitext(norm_path)[1].lower()
        if any(ext == p_ext.lower() for p_ext in self.policy.prohibited_extensions):
            return MutationAuthorizationResult(
                allowed=False,
                decision=MutationDecision.DENIED,
                risk_level=MutationRiskLevel.CRITICAL,
                reason=f"Prohibited file extension '{ext}' on '{norm_path}'. Executable and key files cannot be written.",
                path=norm_path,
                mutation_type=m_type,
                suggested_action="Refrain from writing binary or secret key files to repository.",
            )

        # 3. Check forbidden path patterns from policy or task permissions
        forbidden_patterns = list(self.policy.forbidden_write_paths or [])
        if task_permissions:
            t_forbid = getattr(task_permissions, "forbidden_write_paths", None)
            if isinstance(task_permissions, dict):
                t_forbid = task_permissions.get("forbidden_write_paths")
            if t_forbid:
                forbidden_patterns.extend(t_forbid)

        for pat in forbidden_patterns:
            if fnmatch.fnmatch(norm_path, pat) or fnmatch.fnmatch(norm_path, f"*/{pat}"):
                return MutationAuthorizationResult(
                    allowed=False,
                    decision=MutationDecision.DENIED,
                    risk_level=MutationRiskLevel.HIGH_RISK,
                    reason=f"Path '{norm_path}' matches forbidden write pattern '{pat}'.",
                    path=norm_path,
                    mutation_type=m_type,
                    suggested_action=f"Modify allowed workspace files instead of '{pat}'.",
                )

        # 4. Check task scope authorization (if task_scope or enforce_task_scope is active)
        allowed_paths = self.policy.allowed_write_paths
        if task_permissions:
            t_allow = getattr(task_permissions, "allowed_write_paths", None)
            if isinstance(task_permissions, dict):
                t_allow = task_permissions.get("allowed_write_paths")
            if t_allow is not None:
                allowed_paths = t_allow

        if allowed_paths is not None:
            is_allowed_path = any(
                fnmatch.fnmatch(norm_path, pat) or norm_path.startswith(pat.rstrip("/*"))
                for pat in allowed_paths
            )
            if not is_allowed_path:
                return MutationAuthorizationResult(
                    allowed=False,
                    decision=MutationDecision.DENIED,
                    risk_level=MutationRiskLevel.HIGH_RISK,
                    reason=f"Path '{norm_path}' is outside permitted write scope {allowed_paths}.",
                    path=norm_path,
                    mutation_type=m_type,
                    suggested_action="Request write permissions or restrict changes to allowed scope.",
                )

        if self.policy.enforce_task_scope and task_scope:
            declared_outputs = task_scope.get("outputs", [])
            declared_inputs = task_scope.get("inputs", [])
            allowed_task_files = set()
            for item in (declared_outputs + declared_inputs):
                if item:
                    clean = item.split(":")[0].strip().replace("\\", "/").strip("/")
                    allowed_task_files.add(clean)

            if allowed_task_files and not any(
                norm_path == tf or norm_path.startswith(tf + "/") or fnmatch.fnmatch(norm_path, tf)
                for tf in allowed_task_files
            ):
                return MutationAuthorizationResult(
                    allowed=False,
                    decision=MutationDecision.DENIED,
                    risk_level=MutationRiskLevel.HIGH_RISK,
                    reason=f"File '{norm_path}' is outside the declared task scope {list(allowed_task_files)}.",
                    path=norm_path,
                    mutation_type=m_type,
                    suggested_action="Declare the file in task outputs/inputs or confine edits to active task.",
                )

        # 5. Pre-commit Syntax & Structural Validation
        if self.policy.validate_syntax_pre_commit and new_content is not None:
            is_valid_syntax, syn_err = self._validate_syntax(norm_path, new_content)
            if not is_valid_syntax:
                return MutationAuthorizationResult(
                    allowed=False,
                    decision=MutationDecision.DENIED,
                    risk_level=MutationRiskLevel.HIGH_RISK,
                    reason=f"Pre-commit syntax validation failed for '{norm_path}': {syn_err}",
                    path=norm_path,
                    mutation_type=m_type,
                    syntax_valid=False,
                    suggested_action="Fix syntax errors before persisting code.",
                )

        # 6. Destructive Shrinkage Protection
        shrinkage_ratio = 0.0
        if (
            self.policy.prevent_destructive_shrinkage
            and m_type == MutationType.UPDATE_FILE
            and old_content
            and new_content is not None
        ):
            old_lines = len(old_content.splitlines())
            new_lines = len(new_content.splitlines())
            if old_lines >= self.policy.min_lines_for_shrinkage_check:
                if new_lines < old_lines:
                    shrinkage_ratio = (old_lines - new_lines) / float(old_lines)
                    if shrinkage_ratio >= self.policy.max_allowed_shrinkage_ratio:
                        if not is_approved:
                            return MutationAuthorizationResult(
                                allowed=False,
                                decision=MutationDecision.APPROVAL_REQUIRED if self.policy.require_approval_for_high_risk else MutationDecision.DENIED,
                                risk_level=MutationRiskLevel.HIGH_RISK,
                                reason=(
                                    f"Destructive shrinkage detected on '{norm_path}': "
                                    f"{shrinkage_ratio:.1%} reduction ({old_lines} -> {new_lines} lines) exceeds threshold {self.policy.max_allowed_shrinkage_ratio:.0%}."
                                ),
                                path=norm_path,
                                mutation_type=m_type,
                                shrinkage_ratio=shrinkage_ratio,
                                suggested_action="Use surgical delta tools (replace_file_content) or request explicit refactoring approval.",
                            )

        # 7. Check Protected / High-Risk Files
        is_protected = any(
            fnmatch.fnmatch(norm_path, pat) or fnmatch.fnmatch(os.path.basename(norm_path), pat)
            for pat in self.policy.protected_patterns
        )
        risk = MutationRiskLevel.HIGH_RISK if is_protected else MutationRiskLevel.SAFE

        if is_protected and self.policy.require_approval_for_high_risk and not is_approved:
            return MutationAuthorizationResult(
                allowed=False,
                decision=MutationDecision.APPROVAL_REQUIRED,
                risk_level=MutationRiskLevel.HIGH_RISK,
                reason=f"Path '{norm_path}' is a protected project configuration file requiring explicit approval.",
                path=norm_path,
                mutation_type=m_type,
                suggested_action="Request human or supervisor approval to modify protected system files.",
            )

        # Mutation is Authorized
        return MutationAuthorizationResult(
            allowed=True,
            decision=MutationDecision.ALLOWED,
            risk_level=risk,
            reason="Mutation authorized under active policy.",
            path=norm_path,
            mutation_type=m_type,
            syntax_valid=True,
            shrinkage_ratio=shrinkage_ratio,
        )
