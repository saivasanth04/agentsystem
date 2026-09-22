"""
ApprovalGate: Human-in-the-Loop (HITL) and Policy-Based Approval Gates for Potentially Destructive Actions.
Governs operations such as delete, git reset, git push, database migrations, package installations,
network requests, and secret file accesses.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import fnmatch
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class DestructiveActionType(str, Enum):
    DELETE = "DELETE"
    GIT_RESET = "GIT_RESET"
    GIT_PUSH = "GIT_PUSH"
    DATABASE_MIGRATION = "DATABASE_MIGRATION"
    PACKAGE_INSTALLATION = "PACKAGE_INSTALLATION"
    NETWORK_REQUEST = "NETWORK_REQUEST"
    SECRET_ACCESS = "SECRET_ACCESS"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class ApprovalRequest:
    """Represents a request for authorization to execute a potentially destructive action."""
    action_type: DestructiveActionType
    tool_name: str
    target: str
    command_or_details: str
    risk_level: RiskLevel = RiskLevel.HIGH
    reason: str = ""
    agent_name: Optional[str] = None
    task_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "tool_name": self.tool_name,
            "target": self.target,
            "command_or_details": self.command_or_details,
            "risk_level": self.risk_level.value,
            "reason": self.reason,
            "agent_name": self.agent_name,
            "task_id": self.task_id,
        }


@dataclass
class ApprovalDecision:
    """The outcome of an approval evaluation."""
    approved: bool
    status: str  # "APPROVED" | "DENIED" | "AUTO_APPROVED"
    reason: str = ""
    approved_by: str = "SYSTEM"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "status": self.status,
            "reason": self.reason,
            "approved_by": self.approved_by,
        }


@dataclass
class ApprovalPolicy:
    """
    Configuration rules specifying which destructive actions require approval
    and under what conditions.
    """
    require_approval_for: Set[DestructiveActionType] = field(default_factory=lambda: {
        DestructiveActionType.DELETE,
        DestructiveActionType.GIT_RESET,
        DestructiveActionType.GIT_PUSH,
        DestructiveActionType.DATABASE_MIGRATION,
        DestructiveActionType.PACKAGE_INSTALLATION,
        DestructiveActionType.NETWORK_REQUEST,
        DestructiveActionType.SECRET_ACCESS,
    })
    auto_approve_risk_levels: Set[RiskLevel] = field(default_factory=set)
    auto_approve_command_prefixes: List[str] = field(default_factory=list)
    auto_approve_paths: List[str] = field(default_factory=list)
    strict_deny_without_operator: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "require_approval_for": [a.value for a in self.require_approval_for],
            "auto_approve_risk_levels": [r.value for r in self.auto_approve_risk_levels],
            "auto_approve_command_prefixes": self.auto_approve_command_prefixes,
            "auto_approve_paths": self.auto_approve_paths,
            "strict_deny_without_operator": self.strict_deny_without_operator,
        }


class DestructiveActionClassifier:
    """
    Classifies tool invocations and commands into DestructiveActionType,
    determining risk level and target details.
    """

    # Secret patterns
    SECRET_PATTERNS = [
        ".env*", "*.key", "*.pem", "id_rsa*", "id_ed25519*",
        "*credential*", "*secret*", "*token*", "credentials.json",
    ]

    # Package manager command prefixes
    PACKAGE_INSTALL_PREFIXES = [
        "pip install", "pip3 install", "pipenv install", "poetry add",
        "npm install", "npm i ", "npm add", "yarn add", "pnpm add",
        "gem install", "cargo add", "go get",
    ]

    # Database migration command keywords
    DB_MIGRATION_KEYWORDS = [
        "alembic upgrade", "alembic downgrade", "alembic revision",
        "prisma migrate", "prisma db push", "prisma db execute",
        "manage.py migrate", "manage.py makemigrations",
        "flask db upgrade", "flask db migrate",
        "flyway migrate", "liquibase update",
        "knex migrate", "typeorm migration",
        "drop table", "drop database", "truncate table", "alter table",
    ]

    # Git destructive commands
    GIT_RESET_PATTERNS = [
        "git reset", "git restore", "git clean", "git checkout --",
    ]

    GIT_PUSH_PATTERNS = [
        "git push",
    ]

    # Network commands
    NETWORK_COMMANDS = [
        "curl", "wget", "nc ", "ncat", "telnet", "ssh ", "scp ", "rsync",
    ]

    @classmethod
    def classify_action(
        cls,
        tool_name: str,
        args: Dict[str, Any],
    ) -> Optional[Tuple[DestructiveActionType, RiskLevel, str, str]]:
        """
        Inspects tool_name and args to determine if the invocation is potentially destructive.
        Returns (action_type, risk_level, target, description) or None.
        """
        clean_tool = tool_name.lower().strip()

        # 1. File Deletion Tools
        if clean_tool in ("delete_file", "delete_lines", "filesystem_delete"):
            target = args.get("filepath") or args.get("path") or args.get("file_path") or "unknown_file"
            risk = RiskLevel.CRITICAL if clean_tool == "delete_file" else RiskLevel.MEDIUM
            desc = f"File/Line deletion via {clean_tool} on '{target}'"
            return (DestructiveActionType.DELETE, risk, target, desc)

        # 2. Skill Installation Tool
        if clean_tool == "install_skill":
            skill_name = args.get("skill_name") or args.get("name") or "unknown_skill"
            return (
                DestructiveActionType.PACKAGE_INSTALLATION,
                RiskLevel.HIGH,
                skill_name,
                f"Dynamic installation of skill '{skill_name}'",
            )

        # 3. Secret Access Inspection on Read/Inspect Tools
        if clean_tool in ("read_file", "filesystem_read", "inspect_file"):
            target = args.get("filepath") or args.get("path") or args.get("file_path") or ""
            target_norm = str(target).replace("\\", "/").strip().lstrip("/")
            target_name = Path(target_norm).name
            for pat in cls.SECRET_PATTERNS:
                if fnmatch.fnmatch(target_name.lower(), pat) or fnmatch.fnmatch(target_norm.lower(), f"*/{pat}"):
                    return (
                        DestructiveActionType.SECRET_ACCESS,
                        RiskLevel.HIGH,
                        target,
                        f"Accessing secret/credential file '{target}' via {clean_tool}",
                    )

        # 4. Command Execution Inspection (terminal_execute, run_command, bash)
        if clean_tool in ("terminal_execute", "run_command", "bash", "execute_command"):
            cmd = (args.get("command") or "").strip()
            cmd_lower = cmd.lower()

            # 4a. Shell Deletions (rm -rf, rmdir, del, Remove-Item)
            if any(re.search(p, cmd_lower) for p in [r"\brm\s+(-[rfRF]+\s+|-[rfRF]*\s+)", r"\brmdir\b", r"\bdel\s+/[fFqQsS]", r"\bremove-item\b.*-recurse"]):
                return (
                    DestructiveActionType.DELETE,
                    RiskLevel.CRITICAL,
                    cmd,
                    f"Destructive shell deletion command: '{cmd}'",
                )

            # 4b. Git Reset / Restore
            for p in cls.GIT_RESET_PATTERNS:
                if p in cmd_lower:
                    return (
                        DestructiveActionType.GIT_RESET,
                        RiskLevel.HIGH,
                        cmd,
                        f"Git reset/restore command: '{cmd}'",
                    )

            # 4c. Git Push
            for p in cls.GIT_PUSH_PATTERNS:
                if p in cmd_lower:
                    return (
                        DestructiveActionType.GIT_PUSH,
                        RiskLevel.CRITICAL,
                        cmd,
                        f"Git push to remote: '{cmd}'",
                    )

            # 4d. Database Migrations
            for kw in cls.DB_MIGRATION_KEYWORDS:
                if kw in cmd_lower:
                    return (
                        DestructiveActionType.DATABASE_MIGRATION,
                        RiskLevel.CRITICAL,
                        cmd,
                        f"Database schema migration command: '{cmd}'",
                    )

            # 4e. Package Installations
            for pfx in cls.PACKAGE_INSTALL_PREFIXES:
                if cmd_lower.startswith(pfx) or f" {pfx}" in cmd_lower:
                    return (
                        DestructiveActionType.PACKAGE_INSTALLATION,
                        RiskLevel.HIGH,
                        cmd,
                        f"Package installation command: '{cmd}'",
                    )

            # 4f. Network Requests
            for net_cmd in cls.NETWORK_COMMANDS:
                if cmd_lower.startswith(net_cmd) or f" {net_cmd}" in cmd_lower or f"|{net_cmd}" in cmd_lower:
                    return (
                        DestructiveActionType.NETWORK_REQUEST,
                        RiskLevel.HIGH,
                        cmd,
                        f"External network request command: '{cmd}'",
                    )

            # 4g. Secrets access in shell commands (e.g. cat .env, grep in .env)
            for pat in cls.SECRET_PATTERNS:
                clean_pat = pat.replace("*", "")
                if clean_pat and clean_pat in cmd_lower:
                    return (
                        DestructiveActionType.SECRET_ACCESS,
                        RiskLevel.HIGH,
                        cmd,
                        f"Shell command referencing secret pattern '{pat}': '{cmd}'",
                    )

        return None


class ApprovalGate(ABC):
    """Abstract interface for approval gates."""

    @abstractmethod
    def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        """Evaluates an approval request and returns an ApprovalDecision."""
        pass

    def evaluate_tool_invocation(
        self,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
        agent_name: Optional[str] = None,
        task_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> ApprovalDecision:
        """
        Classifies a tool invocation and checks whether approval is required and granted.
        If non-destructive, returns auto-approved.
        If destructive, delegates to self.request_approval.
        """
        safe_args = args if isinstance(args, dict) else {}
        classified = DestructiveActionClassifier.classify_action(tool_name, safe_args)
        if not classified:
            return ApprovalDecision(
                approved=True,
                status="AUTO_APPROVED",
                reason=f"Action '{tool_name}' is not classified as destructive.",
                approved_by="DestructiveActionClassifier",
            )
        act_type, risk_lvl, target_item, act_desc = classified
        req = ApprovalRequest(
            action_type=act_type,
            tool_name=tool_name,
            target=target_item,
            command_or_details=act_desc,
            risk_level=risk_lvl,
            reason=reason or f"Invocation of {tool_name} requires approval",
            agent_name=agent_name,
            task_id=task_id,
        )
        return self.request_approval(req)


class AutoApprovalGate(ApprovalGate):
    """
    Automated approval gate that approves requests.
    Used in automated test suites and non-interactive environments.
    """

    def __init__(self, default_decision: bool = True, default_reason: str = "Auto-approved in automated/test mode"):
        self.default_decision = default_decision
        self.default_reason = default_reason
        self.history: List[ApprovalRequest] = []

    def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        self.history.append(request)
        status = "AUTO_APPROVED" if self.default_decision else "DENIED"
        return ApprovalDecision(
            approved=self.default_decision,
            status=status,
            reason=self.default_reason,
            approved_by="AutoApprovalGate",
        )


class InteractiveApprovalGate(ApprovalGate):
    """
    Interactive approval gate that prompts an operator for confirmation via CLI or callback.
    """

    def __init__(
        self,
        prompt_callback: Optional[Callable[[ApprovalRequest], bool]] = None,
    ):
        self.prompt_callback = prompt_callback
        self.history: List[Tuple[ApprovalRequest, ApprovalDecision]] = []

    def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        if self.prompt_callback:
            approved = self.prompt_callback(request)
            decision = ApprovalDecision(
                approved=approved,
                status="APPROVED" if approved else "DENIED",
                reason="Operator prompt callback" if approved else "Denied by operator callback",
                approved_by="HUMAN_OPERATOR",
            )
            self.history.append((request, decision))
            return decision

        # Fallback to stdin prompt
        print("\n" + "=" * 60)
        print(f"[APPROVAL GATE] Potentially Destructive Action Requested:")
        print(f"  Action Type : {request.action_type.value}")
        print(f"  Risk Level  : {request.risk_level.value}")
        print(f"  Tool        : {request.tool_name}")
        print(f"  Target      : {request.target}")
        print(f"  Details     : {request.command_or_details}")
        print(f"  Reason      : {request.reason}")
        print("=" * 60)
        try:
            user_input = input("Authorize this action? [y/N]: ").strip().lower()
            approved = user_input in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            approved = False

        decision = ApprovalDecision(
            approved=approved,
            status="APPROVED" if approved else "DENIED",
            reason="Approved by operator via CLI" if approved else "Denied by operator via CLI",
            approved_by="HUMAN_OPERATOR",
        )
        self.history.append((request, decision))
        return decision


class PolicyBasedApprovalGate(ApprovalGate):
    """
    Rule-based approval gate that evaluates requests against an ApprovalPolicy.
    If an action requires explicit operator approval, delegates to a fallback gate (or denies if none).
    """

    def __init__(
        self,
        policy: Optional[ApprovalPolicy] = None,
        operator_gate: Optional[ApprovalGate] = None,
    ):
        self.policy = policy or ApprovalPolicy()
        self.operator_gate = operator_gate
        self.history: List[Tuple[ApprovalRequest, ApprovalDecision]] = []

    def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        # 1. Check if action type requires approval
        if request.action_type not in self.policy.require_approval_for:
            decision = ApprovalDecision(
                approved=True,
                status="AUTO_APPROVED",
                reason=f"Action type '{request.action_type.value}' does not require approval under current policy.",
                approved_by="ApprovalPolicy",
            )
            self.history.append((request, decision))
            return decision

        # 2. Check risk level auto-approval
        if request.risk_level in self.policy.auto_approve_risk_levels:
            decision = ApprovalDecision(
                approved=True,
                status="AUTO_APPROVED",
                reason=f"Risk level '{request.risk_level.value}' is auto-approved under current policy.",
                approved_by="ApprovalPolicy",
            )
            self.history.append((request, decision))
            return decision

        # 3. Check auto-approved command prefixes
        for pfx in self.policy.auto_approve_command_prefixes:
            if request.command_or_details.strip().startswith(pfx):
                decision = ApprovalDecision(
                    approved=True,
                    status="AUTO_APPROVED",
                    reason=f"Command matches auto-approved prefix '{pfx}'.",
                    approved_by="ApprovalPolicy",
                )
                self.history.append((request, decision))
                return decision

        # 4. Check auto-approved paths
        for p in self.policy.auto_approve_paths:
            if fnmatch.fnmatch(request.target, p):
                decision = ApprovalDecision(
                    approved=True,
                    status="AUTO_APPROVED",
                    reason=f"Target path matches auto-approved pattern '{p}'.",
                    approved_by="ApprovalPolicy",
                )
                self.history.append((request, decision))
                return decision

        # 5. Requires Human/Operator Approval
        if self.operator_gate:
            decision = self.operator_gate.request_approval(request)
            self.history.append((request, decision))
            return decision

        # If strict deny or no operator gate available
        decision = ApprovalDecision(
            approved=False,
            status="DENIED",
            reason=f"Action '{request.action_type.value}' requires operator approval, but no operator gate is active.",
            approved_by="ApprovalPolicy",
        )
        self.history.append((request, decision))
        return decision
