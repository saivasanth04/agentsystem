"""
ToolPermissionPolicyEngine: Deterministic Role-Based and Attribute-Based Access Control for Tools.
Mediates every tool invocation and tool availability check by agent role, operation type,
and task-level permission boundaries:
Agent -> Permission Policy -> Tool
"""
from dataclasses import dataclass, field
from enum import Enum
import fnmatch
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Union


class ToolOperationType(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    EXECUTE = "EXECUTE"
    VCS = "VCS"
    CONTROL = "CONTROL"
    ADMIN = "ADMIN"
    NETWORK = "NETWORK"


@dataclass
class PermissionPolicy:
    """Defines the permission envelope for an agent role or specific task."""
    allowed_operations: Set[ToolOperationType] = field(default_factory=lambda: {ToolOperationType.READ, ToolOperationType.CONTROL})
    allowed_write_patterns: List[str] = field(default_factory=list)
    forbidden_write_patterns: List[str] = field(default_factory=list)
    allowed_command_prefixes: List[str] = field(default_factory=list)
    forbidden_command_patterns: List[str] = field(default_factory=list)
    vcs_commit_requires_approval: bool = True
    allowed_paths: List[str] = field(default_factory=lambda: ["*"])
    blocked_paths: List[str] = field(default_factory=list)
    read_only_paths: List[str] = field(default_factory=list)
    sensitive_paths: List[str] = field(default_factory=list)
    file_access_policy: Optional[Any] = None
    network_allowed: bool = False
    allowed_domains: List[str] = field(default_factory=list)
    blocked_domains: List[str] = field(default_factory=list)
    network_policy: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed_operations": [op.value for op in self.allowed_operations],
            "allowed_write_patterns": self.allowed_write_patterns,
            "forbidden_write_patterns": self.forbidden_write_patterns,
            "allowed_command_prefixes": self.allowed_command_prefixes,
            "forbidden_command_patterns": self.forbidden_command_patterns,
            "vcs_commit_requires_approval": self.vcs_commit_requires_approval,
            "allowed_paths": self.allowed_paths,
            "blocked_paths": self.blocked_paths,
            "read_only_paths": self.read_only_paths,
            "sensitive_paths": self.sensitive_paths,
            "network_allowed": self.network_allowed,
            "allowed_domains": self.allowed_domains,
            "blocked_domains": self.blocked_domains,
        }


@dataclass
class PolicyEvaluationResult:
    """Outcome of evaluating a tool invocation against the active permission policy."""
    allowed: bool
    reason: str = ""
    operation_type: ToolOperationType = ToolOperationType.READ
    suggested_action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "operation_type": self.operation_type.value,
            "suggested_action": self.suggested_action,
        }


TOOL_OPERATION_MAP: Dict[str, ToolOperationType] = {
    # READ tools
    "read_file": ToolOperationType.READ,
    "list_files": ToolOperationType.READ,
    "list_directory": ToolOperationType.READ,
    "list_dir": ToolOperationType.READ,
    "regex_grep": ToolOperationType.READ,
    "grep_search": ToolOperationType.READ,
    "find_symbol": ToolOperationType.READ,
    "find_references": ToolOperationType.READ,
    "get_dependencies": ToolOperationType.READ,
    "semantic_code_search": ToolOperationType.READ,
    "get_call_graph": ToolOperationType.READ,
    "get_impact_radius": ToolOperationType.READ,
    "get_architecture_summary": ToolOperationType.READ,
    "codebase_map": ToolOperationType.READ,
    "get_codebase_map": ToolOperationType.READ,
    "git_status": ToolOperationType.READ,
    "git_diff": ToolOperationType.READ,
    "git_log": ToolOperationType.READ,
    "git_show": ToolOperationType.READ,
    "git_blame": ToolOperationType.READ,
    "detect_environment": ToolOperationType.READ,
    "detect_project_environment": ToolOperationType.READ,
    "inspect_tests": ToolOperationType.READ,
    "inspect_existing_tests": ToolOperationType.READ,
    "search_skills": ToolOperationType.READ,
    "load_skill": ToolOperationType.READ,
    "read_skill_reference": ToolOperationType.READ,
    "read_skill_ref": ToolOperationType.READ,
    "get_workspace_changes": ToolOperationType.READ,
    "get_changes": ToolOperationType.READ,
    "ast_syntax_check": ToolOperationType.READ,
    "syntax_check": ToolOperationType.READ,
    "static_code_check": ToolOperationType.READ,
    "static_verification": ToolOperationType.READ,
    "code_check": ToolOperationType.READ,
    "verify_ground_truth": ToolOperationType.READ,
    "ground_truth_verification": ToolOperationType.READ,
    "ground_truth": ToolOperationType.READ,
    "run_static_analysis": ToolOperationType.READ,
    "static_analysis": ToolOperationType.READ,
    "lint": ToolOperationType.READ,
    "sast_scan": ToolOperationType.READ,
    "query_codebase_graph": ToolOperationType.READ,
    "query_graph": ToolOperationType.READ,
    "get_symbol_neighbors": ToolOperationType.READ,
    "symbol_neighbors": ToolOperationType.READ,
    "get_architecture_slice": ToolOperationType.READ,
    "architecture_slice": ToolOperationType.READ,
    "request_more_evidence": ToolOperationType.READ,
    "search_memory": ToolOperationType.READ,
    "query_symbols": ToolOperationType.READ,
    "index_codebase": ToolOperationType.READ,
    "check_environment": ToolOperationType.READ,
    "security_scan": ToolOperationType.READ,

    # WRITE tools
    "write_file": ToolOperationType.WRITE,
    "replace_file_content": ToolOperationType.WRITE,
    "replace_content": ToolOperationType.WRITE,
    "edit_file": ToolOperationType.WRITE,
    "insert_lines": ToolOperationType.WRITE,
    "delete_lines": ToolOperationType.WRITE,
    "apply_diff_blocks": ToolOperationType.WRITE,
    "diff_blocks": ToolOperationType.WRITE,
    "delete_file": ToolOperationType.WRITE,
    "remove_file": ToolOperationType.WRITE,
    "rename_file": ToolOperationType.WRITE,
    "move_file": ToolOperationType.WRITE,
    "apply_patch": ToolOperationType.WRITE,
    "create_file": ToolOperationType.WRITE,
    "append_file": ToolOperationType.WRITE,
    "copy_file": ToolOperationType.WRITE,
    "filesystem_write": ToolOperationType.WRITE,
    "filesystem_delete": ToolOperationType.WRITE,
    "store_memory": ToolOperationType.WRITE,

    # EXECUTE tools
    "terminal_execute": ToolOperationType.EXECUTE,
    "run_command": ToolOperationType.EXECUTE,
    "bash": ToolOperationType.EXECUTE,
    "execute_skill_script": ToolOperationType.EXECUTE,
    "install_skill": ToolOperationType.EXECUTE,
    "run_build_pipeline": ToolOperationType.EXECUTE,
    "build_pipeline": ToolOperationType.EXECUTE,
    "build_verification": ToolOperationType.EXECUTE,
    "run_tests": ToolOperationType.EXECUTE,
    "run_unit_tests": ToolOperationType.EXECUTE,
    "execute_test": ToolOperationType.EXECUTE,
    "test_runner": ToolOperationType.EXECUTE,
    "pytest": ToolOperationType.EXECUTE,

    # VCS tools
    "git_commit": ToolOperationType.VCS,
    "git_branch": ToolOperationType.VCS,
    "git_checkout": ToolOperationType.VCS,
    "git_restore": ToolOperationType.VCS,
    "git_patch": ToolOperationType.VCS,
    "git_init": ToolOperationType.VCS,
    "rollback_workspace": ToolOperationType.VCS,
    "rollback_to_checkpoint": ToolOperationType.VCS,

    # CONTROL tools
    "complete_task": ToolOperationType.CONTROL,
    "spawn_subtasks": ToolOperationType.CONTROL,
    "state_transition_logger": ToolOperationType.CONTROL,
    "log_transition": ToolOperationType.CONTROL,
    "send_agent_message": ToolOperationType.CONTROL,
    "query_agent": ToolOperationType.CONTROL,
    "publish_finding": ToolOperationType.CONTROL,
    "read_inbox": ToolOperationType.CONTROL,
    "workflow_telemetry": ToolOperationType.CONTROL,
    "save_checkpoint": ToolOperationType.CONTROL,
    "fetch_documentation": ToolOperationType.READ,

    # NETWORK tools
    "http_request": ToolOperationType.NETWORK,
    "http_fetch": ToolOperationType.NETWORK,
    "fetch_url": ToolOperationType.NETWORK,
    "read_url": ToolOperationType.NETWORK,
    "read_url_content": ToolOperationType.NETWORK,
    "web_search": ToolOperationType.NETWORK,
    "search_web": ToolOperationType.NETWORK,
    "download_file": ToolOperationType.NETWORK,
    "download_package": ToolOperationType.NETWORK,
    "curl": ToolOperationType.NETWORK,
    "wget": ToolOperationType.NETWORK,
}

# Role policies defining the baseline permissions of each agent role
DEFAULT_ROLE_POLICIES: Dict[str, PermissionPolicy] = {
    "REVIEWER": PermissionPolicy(
        allowed_operations={ToolOperationType.READ, ToolOperationType.CONTROL},
        allowed_write_patterns=[],  # Strictly NO writes
        allowed_command_prefixes=[],  # Strictly NO terminal executions
        vcs_commit_requires_approval=True,
    ),
    "TESTER": PermissionPolicy(
        allowed_operations={ToolOperationType.READ, ToolOperationType.WRITE, ToolOperationType.EXECUTE, ToolOperationType.CONTROL},
        allowed_write_patterns=[
            "tests/**", "test/**", "test_*.py", "*_test.py", "*_test.*", "*.test.*", "*.spec.*",
            "conftest.py", "pytest.ini", "setup.cfg", "tests/*", "test/*",
        ],
        forbidden_write_patterns=[
            "src/**", "app/**", "lib/**", "core/**", ".env*", ".git/*",
        ],
        allowed_command_prefixes=[
            "pytest", "python -m unittest", "python -m pytest", "npm test", "npm run test",
            "npx jest", "jest", "vitest", "cargo test", "go test", "coverage", "python", "node",
        ],
        forbidden_command_patterns=[
            "rm -rf*", "rmdir*", "mkfs*", "curl*", "wget*", "git push*",
        ],
        vcs_commit_requires_approval=True,
    ),
    "CODER": PermissionPolicy(
        allowed_operations={ToolOperationType.READ, ToolOperationType.WRITE, ToolOperationType.EXECUTE, ToolOperationType.CONTROL, ToolOperationType.VCS},
        allowed_write_patterns=["*"],  # Scoped by task allowed_write_paths
        forbidden_write_patterns=[".git/*", ".env*", "*.key", "*.pem"],
        allowed_command_prefixes=[
            "pytest", "python", "npm", "node", "npx", "ruff", "mypy", "pyright", "tsc",
            "cargo", "go", "git status", "git diff", "git log", "git show",
        ],
        forbidden_command_patterns=[
            "rm -rf /", "rm -rf ~", "mkfs*", "curl*|*sh*", "wget*|*sh*", "git push*",
        ],
        vcs_commit_requires_approval=True,
    ),
    "PLANNER": PermissionPolicy(
        allowed_operations={ToolOperationType.READ, ToolOperationType.WRITE, ToolOperationType.CONTROL},
        allowed_write_patterns=["*.md", "*.json", "plans/**", "specs/**", "docs/**"],
        allowed_command_prefixes=[],  # Read-only execution
        vcs_commit_requires_approval=True,
    ),
    "SPECIFICATION": PermissionPolicy(
        allowed_operations={ToolOperationType.READ, ToolOperationType.WRITE, ToolOperationType.CONTROL},
        allowed_write_patterns=["*.md", "*.json", "specs/**", "docs/**", "requirements/**"],
        allowed_command_prefixes=[],
        vcs_commit_requires_approval=True,
    ),
    "ARCHITECTURE": PermissionPolicy(
        allowed_operations={ToolOperationType.READ, ToolOperationType.WRITE, ToolOperationType.CONTROL},
        allowed_write_patterns=["*.md", "*.json", "architecture/**", "docs/**", "diagrams/**"],
        allowed_command_prefixes=[],
        vcs_commit_requires_approval=True,
    ),
    "TASKORCHESTRATOR": PermissionPolicy(
        allowed_operations={ToolOperationType.READ, ToolOperationType.CONTROL, ToolOperationType.VCS},
        allowed_write_patterns=["*.json", ".orchestrator/**"],
        allowed_command_prefixes=[],
        vcs_commit_requires_approval=False,
    ),
    "UNKNOWN": PermissionPolicy(
        allowed_operations={ToolOperationType.READ},
        allowed_write_patterns=[],
        allowed_command_prefixes=[],
        vcs_commit_requires_approval=True,
    ),
    "ANONYMOUS": PermissionPolicy(
        allowed_operations={ToolOperationType.READ},
        allowed_write_patterns=[],
        allowed_command_prefixes=[],
        vcs_commit_requires_approval=True,
    ),
}


class ToolPermissionPolicyEngine:
    """
    Evaluates tool availability and tool invocations against role-based and
    attribute-based access control policies.
    """

    def __init__(self, policy: Optional[PermissionPolicy] = None):
        self.policy = policy
        if policy is not None:
            def _bound_eval(*b_args, **b_kwargs):
                if "policy" not in b_kwargs and not (b_args and isinstance(b_args[0], PermissionPolicy)):
                    b_kwargs.setdefault("policy", self.policy)
                return ToolPermissionPolicyEngine.evaluate_tool_invocation(*b_args, **b_kwargs)
            self.evaluate_tool_invocation = _bound_eval

    @classmethod
    def get_tool_operation_type(cls, tool_name: str) -> ToolOperationType:
        """Determines the operation classification of a tool."""
        clean = (tool_name or "").lower().strip()
        if clean in TOOL_OPERATION_MAP:
            return TOOL_OPERATION_MAP[clean]
        bare = clean
        if "__" in bare:
            bare = bare.split("__")[-1]
        prefixes = (
            "mcp_", "builtin_", "native_",
            "mcp-server-filesystem_", "mcp-server-git_", "mcp-server-terminal_", "mcp-server-memory_", "mcp-server-fetch_", "mcp-server-sqlite_",
            "filesystem_", "git_", "terminal_", "memory_", "fetch_", "sqlite_",
        )
        changed = True
        while changed:
            changed = False
            if bare in TOOL_OPERATION_MAP:
                break
            for pfx in prefixes:
                if bare.startswith(pfx) and len(bare) > len(pfx) and bare not in TOOL_OPERATION_MAP:
                    bare = bare[len(pfx):]
                    changed = True
                    break

        if bare in TOOL_OPERATION_MAP:
            return TOOL_OPERATION_MAP[bare]
        return ToolOperationType.READ

    @classmethod
    def get_policy_for_role(
        cls,
        agent_role: Optional[Union[str, "PermissionPolicy"]],
        agent_registry: Optional[Any] = None,
    ) -> PermissionPolicy:
        """Retrieves the permission policy for a given agent role, manifest, or policy object."""
        if isinstance(agent_role, PermissionPolicy):
            return agent_role

        if not agent_role:
            # Default read-only policy for unassigned/anonymous roles
            return PermissionPolicy(
                allowed_operations={ToolOperationType.READ},
                allowed_write_patterns=[],
                allowed_command_prefixes=[],
                vcs_commit_requires_approval=True,
            )

        role_clean = str(agent_role).upper().strip()

        # 1. Check direct role policies
        if role_clean in DEFAULT_ROLE_POLICIES:
            return DEFAULT_ROLE_POLICIES[role_clean]

        # 2. Check agent registry if provided
        if agent_registry:
            manifest = agent_registry.get(str(agent_role))
            if manifest:
                return manifest.get_effective_permissions()

        # 3. Check capability registry if agent_role is a capability ID
        try:
            from ..capabilities.model import default_capability_registry
            cap = default_capability_registry.get(str(agent_role))
            if cap and cap.permission_policy:
                return cap.permission_policy
        except Exception:
            pass

        # Default read-only policy for unknown roles
        return PermissionPolicy(
            allowed_operations={ToolOperationType.READ},
            allowed_write_patterns=[],
            allowed_command_prefixes=[],
            vcs_commit_requires_approval=True,
        )

    @classmethod
    def evaluate_tool_invocation(
        cls,
        agent_role: Optional[Union[str, "PermissionPolicy"]] = None,
        tool_name: str = "",
        args: Optional[Dict[str, Any]] = None,
        task_permissions: Optional[Any] = None,
        workspace: Optional[Any] = None,
        policy: Optional[PermissionPolicy] = None,
        agent_registry: Optional[Any] = None,
        operation_type: Optional[ToolOperationType] = None,
        arguments: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> PolicyEvaluationResult:
        """
        Evaluates whether an agent with `agent_role` is authorized to invoke `tool_name` with `args`.
        """
        args = args if args is not None else (arguments or {})
        op_type = operation_type or cls.get_tool_operation_type(tool_name)
        if policy is None:
            if isinstance(agent_role, PermissionPolicy):
                policy = agent_role
            else:
                policy = cls.get_policy_for_role(agent_role, agent_registry=agent_registry)
        role_display = getattr(agent_role, "name", str(agent_role or "UNKNOWN")).upper() if not isinstance(agent_role, PermissionPolicy) else "CUSTOM_POLICY"

        # 1. Operation Category Check (RBAC)
        if op_type == ToolOperationType.NETWORK:
            task_net_allowed = getattr(task_permissions, "network_allowed", None)
            if task_net_allowed is None and isinstance(task_permissions, dict):
                task_net_allowed = task_permissions.get("network_allowed")
            effective_net_allowed = (task_net_allowed is True) or policy.network_allowed or (op_type in policy.allowed_operations)
            if not effective_net_allowed:
                return PolicyEvaluationResult(
                    allowed=False,
                    operation_type=op_type,
                    reason=f"Permission Denied: Network access is disabled for agent role [{role_display}]. Network operations ('{tool_name}') require explicit authorization.",
                    suggested_action="Enable network_allowed=True on task permissions or configure allowed_domains.",
                )
        elif op_type not in policy.allowed_operations:
            return PolicyEvaluationResult(
                allowed=False,
                operation_type=op_type,
                reason=f"Permission Denied: Agent role [{role_display}] is forbidden from performing '{op_type.value}' operations (invoking '{tool_name}').",
                suggested_action=f"Delegate '{op_type.value}' tasks to an authorized agent (e.g. CODER for writing, TESTER for testing).",
            )

        # 1.5 File Access Policy (ABAC for paths across READ and WRITE)
        target_path = args.get("filepath") or args.get("file_path") or args.get("path") or args.get("rel_path") or args.get("filename") or ""
        if target_path and op_type in (ToolOperationType.READ, ToolOperationType.WRITE):
            from ..security.file_access_policy import FileAccessPolicy, FileAccessMode
            task_allowed_paths = getattr(task_permissions, "allowed_paths", None) or (task_permissions.get("allowed_paths") if isinstance(task_permissions, dict) else None)
            task_blocked_paths = getattr(task_permissions, "blocked_paths", None) or (task_permissions.get("blocked_paths") if isinstance(task_permissions, dict) else None)
            task_read_only_paths = getattr(task_permissions, "read_only_paths", None) or (task_permissions.get("read_only_paths") if isinstance(task_permissions, dict) else None)
            task_sensitive_paths = getattr(task_permissions, "sensitive_paths", None) or (task_permissions.get("sensitive_paths") if isinstance(task_permissions, dict) else None)

            fap = policy.file_access_policy
            if not fap:
                eff_allowed = task_allowed_paths or policy.allowed_paths or ["*"]
                eff_blocked = list(policy.blocked_paths or []) + list(task_blocked_paths or [])
                eff_read_only = list(policy.read_only_paths or []) + list(task_read_only_paths or [])
                eff_sensitive = list(policy.sensitive_paths or []) + list(task_sensitive_paths or [])
                fap = FileAccessPolicy(
                    allowed_paths=eff_allowed,
                    blocked_paths=eff_blocked if eff_blocked else FileAccessPolicy().blocked_paths,
                    read_only_paths=eff_read_only,
                    sensitive_paths=eff_sensitive if eff_sensitive else FileAccessPolicy().sensitive_paths,
                )

            access_mode = FileAccessMode.WRITE if op_type == ToolOperationType.WRITE else FileAccessMode.READ
            decision = fap.evaluate(target_path, access_mode)
            if not decision.allowed:
                return PolicyEvaluationResult(
                    allowed=False,
                    operation_type=op_type,
                    reason=f"Permission Denied: Agent role [{role_display}] blocked by file access policy: {decision.reason}",
                    suggested_action=decision.suggested_action,
                )

        # 1.6 Network Access Policy (ABAC for network tools)
        if op_type == ToolOperationType.NETWORK:
            target_url = args.get("url") or args.get("uri") or args.get("endpoint") or args.get("host") or args.get("domain") or ""
            from ..security.network_policy import NetworkAccessPolicy, NetworkAccessMode
            net_pol = policy.network_policy
            if not net_pol:
                task_allowed_doms = getattr(task_permissions, "allowed_domains", None) or (task_permissions.get("allowed_domains") if isinstance(task_permissions, dict) else None)
                task_blocked_doms = getattr(task_permissions, "blocked_domains", None) or (task_permissions.get("blocked_domains") if isinstance(task_permissions, dict) else None)
                eff_allowed_doms = task_allowed_doms or policy.allowed_domains
                eff_blocked_doms = list(policy.blocked_domains or []) + list(task_blocked_doms or [])
                mode = NetworkAccessMode.ALLOWLIST_ONLY if eff_allowed_doms else NetworkAccessMode.UNRESTRICTED
                net_pol = NetworkAccessPolicy(
                    mode=mode,
                    allowed_domains=eff_allowed_doms if eff_allowed_doms else NetworkAccessPolicy().allowed_domains,
                    blocked_domains=eff_blocked_doms if eff_blocked_doms else NetworkAccessPolicy().blocked_domains,
                )
            if target_url:
                target_str = str(target_url).strip()
                dec = net_pol.evaluate_url(target_str) if ("://" in target_str or "/" in target_str) else net_pol.evaluate_host(target_str)
                if not dec.allowed:
                    return PolicyEvaluationResult(
                        allowed=False,
                        operation_type=op_type,
                        reason=f"Permission Denied: Agent role [{role_display}] blocked by network policy: {dec.reason}",
                        suggested_action=dec.suggested_action,
                    )

        # 2. WRITE Operation Scoping (ABAC)
        if op_type == ToolOperationType.WRITE:
            target_path = args.get("filepath") or args.get("file_path") or args.get("path") or args.get("rel_path") or ""
            if target_path:
                norm_target = str(target_path).replace("\\", "/").strip().lstrip("/")

                # Check role's forbidden write patterns
                for for_pat in policy.forbidden_write_patterns:
                    norm_for = for_pat.replace("\\", "/").strip().lstrip("/")
                    if fnmatch.fnmatch(norm_target, norm_for) or fnmatch.fnmatch(norm_target, f"*/{norm_for}"):
                        return PolicyEvaluationResult(
                            allowed=False,
                            operation_type=op_type,
                            reason=f"Permission Denied: Agent role [{role_display}] cannot modify protected path '{target_path}' (matches forbidden rule '{for_pat}').",
                            suggested_action="Ensure file modification matches your authorized domain (e.g. TESTER writes to tests/ only).",
                        )
                    if norm_for.endswith("/**"):
                        prefix = norm_for[:-3]
                        if norm_target == prefix or norm_target.startswith(prefix + "/"):
                            return PolicyEvaluationResult(
                                allowed=False,
                                operation_type=op_type,
                                reason=f"Permission Denied: Agent role [{role_display}] cannot modify protected directory '{target_path}' (forbidden prefix '{prefix}').",
                                suggested_action="Confine file modifications to your assigned scope.",
                            )

                # Check role's allowed write patterns (if restricted)
                if policy.allowed_write_patterns and "*" not in policy.allowed_write_patterns:
                    matched = False
                    for allow_pat in policy.allowed_write_patterns:
                        norm_allow = allow_pat.replace("\\", "/").strip().lstrip("/")
                        if fnmatch.fnmatch(norm_target, norm_allow) or fnmatch.fnmatch(norm_target, f"*/{norm_allow}"):
                            matched = True
                            break
                        if norm_allow.endswith("/**"):
                            prefix = norm_allow[:-3]
                            if norm_target == prefix or norm_target.startswith(prefix + "/"):
                                matched = True
                                break
                    if not matched:
                        return PolicyEvaluationResult(
                            allowed=False,
                            operation_type=op_type,
                            reason=f"Permission Denied: Agent role [{role_display}] can only write to authorized paths {policy.allowed_write_patterns}. Attempted: '{target_path}'.",
                            suggested_action="Confine your changes to your role's allowed paths.",
                        )

                # Check task-level allowed_write_paths (if task_permissions provided)
                if task_permissions:
                    task_allowed = getattr(task_permissions, "allowed_write_paths", None)
                    if isinstance(task_permissions, dict):
                        task_allowed = task_permissions.get("allowed_write_paths")
                    if task_allowed and "*" not in task_allowed:
                        task_matched = False
                        for tap in task_allowed:
                            norm_tap = str(tap).replace("\\", "/").strip().lstrip("/")
                            if fnmatch.fnmatch(norm_target, norm_tap) or fnmatch.fnmatch(norm_target, f"*/{norm_tap}"):
                                task_matched = True
                                break
                            if norm_tap.endswith("/*") or norm_tap.endswith("/**"):
                                pfx = norm_tap.rstrip("/*").rstrip("/")
                                if norm_target == pfx or norm_target.startswith(pfx + "/"):
                                    task_matched = True
                                    break
                            elif norm_target == norm_tap or norm_target.startswith(norm_tap + "/"):
                                task_matched = True
                                break
                        if not task_matched:
                            return PolicyEvaluationResult(
                                allowed=False,
                                operation_type=op_type,
                                reason=f"Permission Denied: Write operation on '{target_path}' is outside task allowed paths {task_allowed}.",
                                suggested_action="Only modify files explicitly authorized for this task.",
                            )

        # 3. EXECUTE Operation Scoping (ABAC)
        if op_type == ToolOperationType.EXECUTE:
            cmd = args.get("command") or ""
            if cmd:
                cmd_stripped = cmd.strip()

                # Check forbidden command patterns
                for for_cmd in policy.forbidden_command_patterns:
                    if fnmatch.fnmatch(cmd_stripped, for_cmd):
                        return PolicyEvaluationResult(
                            allowed=False,
                            operation_type=op_type,
                            reason=f"Permission Denied: Destructive or unauthorized command '{cmd}' blocked by security policy.",
                            suggested_action="Use safe development, build, or test commands.",
                        )

                # Check network policy on command execution
                task_net_allowed = getattr(task_permissions, "network_allowed", None)
                if task_net_allowed is None and isinstance(task_permissions, dict):
                    task_net_allowed = task_permissions.get("network_allowed")

                from ..security.network_policy import NetworkAccessPolicy, NetworkAccessMode
                net_pol = policy.network_policy
                if not net_pol:
                    if task_net_allowed is False or (task_net_allowed is None and not policy.network_allowed and ToolOperationType.NETWORK not in policy.allowed_operations):
                        net_pol = NetworkAccessPolicy(mode=NetworkAccessMode.DISABLED)
                    else:
                        task_allowed_doms = getattr(task_permissions, "allowed_domains", None) or (task_permissions.get("allowed_domains") if isinstance(task_permissions, dict) else None)
                        task_blocked_doms = getattr(task_permissions, "blocked_domains", None) or (task_permissions.get("blocked_domains") if isinstance(task_permissions, dict) else None)
                        eff_allowed_doms = task_allowed_doms or policy.allowed_domains
                        eff_blocked_doms = list(policy.blocked_domains or []) + list(task_blocked_doms or [])
                        mode = NetworkAccessMode.ALLOWLIST_ONLY if eff_allowed_doms else NetworkAccessMode.UNRESTRICTED
                        net_pol = NetworkAccessPolicy(
                            mode=mode,
                            allowed_domains=eff_allowed_doms if eff_allowed_doms else NetworkAccessPolicy().allowed_domains,
                            blocked_domains=eff_blocked_doms if eff_blocked_doms else NetworkAccessPolicy().blocked_domains,
                        )

                cmd_dec = net_pol.evaluate_command(cmd)
                if not cmd_dec.allowed:
                    return PolicyEvaluationResult(
                        allowed=False,
                        operation_type=op_type,
                        reason=f"Permission Denied: Command '{cmd}' blocked by network policy: {cmd_dec.reason}",
                        suggested_action=cmd_dec.suggested_action,
                    )

                # Check role's allowed command prefixes (if restricted)
                if policy.allowed_command_prefixes and "*" not in policy.allowed_command_prefixes:
                    matched_cmd = any(cmd_stripped.startswith(p) for p in policy.allowed_command_prefixes)
                    if not matched_cmd:
                        return PolicyEvaluationResult(
                            allowed=False,
                            operation_type=op_type,
                            reason=f"Permission Denied: Agent role [{role_display}] is restricted to command prefixes {policy.allowed_command_prefixes}. Attempted: '{cmd}'.",
                            suggested_action=f"Run an authorized test or build command from: {policy.allowed_command_prefixes[:4]}.",
                        )

                # Check task-level allowed_commands
                if task_permissions:
                    task_cmds = getattr(task_permissions, "allowed_commands", None)
                    if isinstance(task_permissions, dict):
                        task_cmds = task_permissions.get("allowed_commands")
                    if task_cmds and "*" not in task_cmds:
                        matched_task = any(cmd_stripped.startswith(c) for c in task_cmds)
                        if not matched_task:
                            return PolicyEvaluationResult(
                                allowed=False,
                                operation_type=op_type,
                                reason=f"Permission Denied: Command '{cmd}' is outside task allowed command prefixes {task_cmds}.",
                                suggested_action="Only execute commands authorized for this task.",
                            )

        # 4. VCS Operation Authorization
        if op_type == ToolOperationType.VCS:
            if tool_name == "git_commit" and policy.vcs_commit_requires_approval:
                # Check if task_permissions explicitly authorizes git commit
                authorized = False
                if task_permissions:
                    if getattr(task_permissions, "allow_git_commit", False):
                        authorized = True
                    elif isinstance(task_permissions, dict) and task_permissions.get("allow_git_commit"):
                        authorized = True
                    task_cmds = getattr(task_permissions, "allowed_commands", []) or []
                    if isinstance(task_permissions, dict):
                        task_cmds = task_permissions.get("allowed_commands", [])
                    if "git commit" in task_cmds or "git" in task_cmds or "*" in task_cmds:
                        authorized = True

                if not authorized:
                    return PolicyEvaluationResult(
                        allowed=False,
                        operation_type=op_type,
                        reason=f"Permission Denied: VCS commit requires explicit authorization or review approval. [{role_display}] cannot commit directly.",
                        suggested_action="Request commit authorization or submit deliverables through complete_task for orchestrator review.",
                    )

        return PolicyEvaluationResult(allowed=True, operation_type=op_type)

    @classmethod
    def filter_tools_for_role(cls, agent_role: str, tool_names: List[str]) -> List[str]:
        """Filters a list of tool names, returning only those permitted for the given agent role."""
        policy = cls.get_policy_for_role(agent_role)
        filtered = []
        for t in tool_names:
            op_type = cls.get_tool_operation_type(t)
            if op_type in policy.allowed_operations:
                # If tool is git_commit, only include if not blocked
                if t == "git_commit" and ToolOperationType.VCS not in policy.allowed_operations:
                    continue
                filtered.append(t)
        return filtered
