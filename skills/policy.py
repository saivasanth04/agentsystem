"""
Policy Engine for Executable Skills.
Defines ToolPolicy and RuntimePolicy that govern autonomous agent execution
when activated by specific skills or tasks.
"""
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("skills.policy")

# Standard canonical tool aliases mapping dot/colon namespaces to runtime tool primitives
DEFAULT_TOOL_ALIASES: Dict[str, str] = {
    # Filesystem operations (mapped to existing Builtin / MCP filesystem primitives)
    "filesystem.read": "read_file",
    "filesystem.write": "write_file",
    "filesystem.list": "list_directory",
    "filesystem.delete": "delete_file",
    "filesystem.rename": "rename_file",
    "filesystem.move": "move_file",
    "filesystem.replace": "replace_file_content",
    "filesystem.diff": "apply_diff_blocks",
    "filesystem.patch": "apply_patch",
    # Git operations (mapped to GitMCPServer)
    "git.status": "git_status",
    "git.diff": "git_diff",
    "git.log": "git_log",
    "git.commit": "git_commit",
    "git.checkout": "git_checkout",
    "git.branch": "git_branch",
    "git.show": "git_show",
    "git.blame": "git_blame",
    # Terminal operations (mapped to Builtin / TerminalMCPServer)
    "terminal.execute": "terminal_execute",
    "terminal.run": "terminal_execute",
    "shell.run": "terminal_execute",
    "run_command": "terminal_execute",
}


@dataclass
class ToolPolicy:
    """
    Enforces tool access, resolution, authorization, and aliasing rules
    for an active skill or task context.
    """
    allowed_tools: Set[str] = field(default_factory=set)
    required_tools: Set[str] = field(default_factory=set)
    capabilities: Set[str] = field(default_factory=set)
    tool_aliases: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TOOL_ALIASES))
    permissions: Set[str] = field(default_factory=set)
    rate_limits: Dict[str, int] = field(default_factory=dict)
    approval_required: bool = False

    def resolve_tool_name(self, tool_name: str) -> str:
        """
        Resolves dot-notation or namespaced tool names into runtime dispatcher names.
        Examples:
            'filesystem.read' -> 'read_file'
            'git.commit' -> 'git_commit'
        """
        if not tool_name:
            return ""
        clean = tool_name.strip()
        # Direct lookup in aliases
        if clean in self.tool_aliases:
            return self.tool_aliases[clean]
        # Normalized lookup (replace ':' with '.')
        normalized = clean.replace(":", ".")
        if normalized in self.tool_aliases:
            return self.tool_aliases[normalized]
        return clean

    def is_tool_allowed(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None, *args: Any, **kwargs: Any) -> bool:
        """
        Checks whether the tool is permitted under this policy.
        If allowed_tools is empty, access is unconstrained by this policy
        (subject to underlying AgentRegistry and UnifiedToolDispatcher authorization).
        """
        if not self.allowed_tools:
            return True
        resolved = self.resolve_tool_name(tool_name)
        return (tool_name in self.allowed_tools) or (resolved in self.allowed_tools)

    def merge(self, other: Optional["ToolPolicy"]) -> "ToolPolicy":
        """
        Merges this policy with another policy.
        """
        if not other:
            return ToolPolicy(
                allowed_tools=set(self.allowed_tools),
                required_tools=set(self.required_tools),
                tool_aliases=dict(self.tool_aliases),
                permissions=set(self.permissions),
                rate_limits=dict(self.rate_limits),
            )

        merged_aliases = dict(self.tool_aliases)
        merged_aliases.update(other.tool_aliases)

        merged_permissions = set(self.permissions).union(other.permissions)
        merged_required = set(self.required_tools).union(other.required_tools)

        if self.allowed_tools and other.allowed_tools:
            # Union of allowed tools from all active skills
            merged_allowed = set(self.allowed_tools).union(other.allowed_tools)
        elif self.allowed_tools:
            merged_allowed = set(self.allowed_tools)
        elif other.allowed_tools:
            merged_allowed = set(other.allowed_tools)
        else:
            merged_allowed = set()

        merged_rate_limits = dict(self.rate_limits)
        for k, v in other.rate_limits.items():
            if k in merged_rate_limits:
                merged_rate_limits[k] = min(merged_rate_limits[k], v)
            else:
                merged_rate_limits[k] = v

        return ToolPolicy(
            allowed_tools=merged_allowed,
            required_tools=merged_required,
            tool_aliases=merged_aliases,
            permissions=merged_permissions,
            rate_limits=merged_rate_limits,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed_tools": sorted(list(self.allowed_tools)),
            "required_tools": sorted(list(self.required_tools)),
            "tool_aliases": self.tool_aliases,
            "permissions": sorted(list(self.permissions)),
            "rate_limits": self.rate_limits,
        }


@dataclass
class RuntimePolicy:
    """
    Defines execution constraints, limits, and runtime parameters for skills.
    """
    timeout_seconds: int = 300
    max_turns: int = 20
    max_retries: int = 3
    approval_required: bool = False
    agent_affinity: Optional[str] = None
    model_tier: str = "default"  # 'default' | 'fast' | 'reasoning'
    sandboxed: bool = True

    def merge(self, other: Optional["RuntimePolicy"]) -> "RuntimePolicy":
        """
        Merges runtime policies taking the most conservative/secure limits.
        """
        if not other:
            return RuntimePolicy(
                timeout_seconds=self.timeout_seconds,
                max_turns=self.max_turns,
                max_retries=self.max_retries,
                approval_required=self.approval_required,
                agent_affinity=self.agent_affinity,
                model_tier=self.model_tier,
                sandboxed=self.sandboxed,
            )

        return RuntimePolicy(
            timeout_seconds=min(self.timeout_seconds, other.timeout_seconds),
            max_turns=min(self.max_turns, other.max_turns),
            max_retries=min(self.max_retries, other.max_retries),
            approval_required=self.approval_required or other.approval_required,
            agent_affinity=self.agent_affinity or other.agent_affinity,
            model_tier="reasoning" if "reasoning" in (self.model_tier, other.model_tier) else (
                "fast" if self.model_tier == "fast" and other.model_tier == "fast" else "default"
            ),
            sandboxed=self.sandboxed or other.sandboxed,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_turns": self.max_turns,
            "max_retries": self.max_retries,
            "approval_required": self.approval_required,
            "agent_affinity": self.agent_affinity,
            "model_tier": self.model_tier,
            "sandboxed": self.sandboxed,
        }
