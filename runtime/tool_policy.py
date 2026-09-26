"""
Capability-Driven Tool Policy.
Shifts tool access from static agent roles to dynamic, capability-based scoping:
Task -> Capabilities -> Skill Runtime -> Allowed Tools -> LLM
"""
from dataclasses import dataclass, field
import fnmatch
import logging
from typing import Any, Dict, List, Optional, Set, Union

logger = logging.getLogger("runtime.tool_policy")

# Canonical aliases mapping dot/colon capability namespaces to concrete tool primitives
DEFAULT_TOOL_ALIASES: Dict[str, str] = {
    # Filesystem operations (mapped to Builtin / FilesystemMCPServer)
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
    "git.show": "git_show",
    "git.blame": "git_blame",
    "git.commit": "git_commit",
    "git.branch": "git_branch",
    "git.checkout": "git_checkout",
    "git.restore": "git_restore",
    "git.patch": "git_patch",
    "git.init": "git_init",
    # Terminal operations (mapped to TerminalMCPServer / Builtin)
    "terminal.execute": "terminal_execute",
    "terminal.run": "terminal_execute",
    "terminal.test": "run_tests",
    "terminal.environment": "check_environment",
    "shell.run": "terminal_execute",
    "run_command": "terminal_execute",
    # Browser DevTools operations (mapped to BrowserMCPAdapter)
    "browser.console": "browser_console",
    "browser.inspect": "browser_inspect",
    "browser.network": "browser_network",
    "browser.snapshot": "browser_snapshot",
    # Codebase & Knowledge Graph operations
    "codebase.symbols": "find_symbol",
    "codebase.references": "find_references",
    "codebase.dependencies": "get_dependencies",
    "codebase.map": "get_codebase_map",
    # Memory operations
    "memory.store": "store_memory",
    "memory.search": "search_memory",
}

# Standard capability mappings to concrete tools
CAPABILITY_TO_TOOLS: Dict[str, List[str]] = {
    "filesystem.read": ["read_file", "list_directory", "get_file_info"],
    "filesystem.write": [
        "write_file",
        "replace_file_content",
        "insert_lines",
        "delete_lines",
        "apply_diff_blocks",
        "rename_file",
        "move_file",
        "delete_file",
        "apply_patch",
    ],
    "git.inspect": ["git_status", "git_diff", "git_log", "git_show", "git_blame"],
    "git.mutate": ["git_commit", "git_branch", "git_checkout", "git_restore", "git_patch", "git_init"],
    "terminal.run": ["terminal_execute", "run_tests", "check_environment"],
    "browser.inspect": ["browser_console", "browser_inspect", "browser_network", "browser_snapshot"],
    "codebase.search": [
        "find_symbol",
        "find_references",
        "get_dependencies",
        "get_codebase_map",
        "search_memory",
        "query_symbols",
    ],
    "memory.read": ["search_memory", "query_symbols"],
    "memory.write": ["store_memory"],
    "verification.test": ["run_tests", "terminal_execute", "ast_syntax_check"],
}


@dataclass
class ToolPolicy:
    """
    Enforces capability-driven tool authorization.
    Limits tool availability strictly to the capabilities required for the task.
    """
    allowed_tools: Set[str] = field(default_factory=set)
    forbidden_tools: Set[str] = field(default_factory=set)
    capabilities: Set[str] = field(default_factory=set)
    tool_aliases: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TOOL_ALIASES))
    required_parameters: Dict[str, List[str]] = field(default_factory=dict)
    parameter_constraints: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    approval_required: bool = False
    read_only: bool = False
    rate_limits: Dict[str, int] = field(default_factory=dict)

    def resolve_tool_name(self, tool_name: str) -> str:
        """
        Resolves capability/aliased names to concrete runtime tool names.
        Examples:
            'filesystem.read' -> 'read_file'
            'browser.inspect' -> 'browser_inspect'
        """
        if not tool_name:
            return ""
        clean = tool_name.strip()
        if clean in self.tool_aliases:
            return self.tool_aliases[clean]
        normalized = clean.replace(":", ".")
        if normalized in self.tool_aliases:
            return self.tool_aliases[normalized]
        return clean

    def is_tool_allowed(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> bool:
        """
        Evaluates whether a tool is permitted under this capability policy.
        """
        if not tool_name:
            return False

        resolved = self.resolve_tool_name(tool_name)

        # Check explicit forbidden list
        if tool_name in self.forbidden_tools or resolved in self.forbidden_tools:
            return False

        # Read-only enforcement
        if self.read_only:
            write_tools = set(CAPABILITY_TO_TOOLS.get("filesystem.write", [])) | set(CAPABILITY_TO_TOOLS.get("git.mutate", []))
            if resolved in write_tools or tool_name in write_tools:
                return False

        # If allowed_tools is empty and no capabilities specified, allow by default
        if not self.allowed_tools and not self.capabilities:
            return True

        # Check direct allowed membership (original or resolved name)
        if tool_name in self.allowed_tools or resolved in self.allowed_tools:
            return True

        # Check capability namespace prefix
        for cap in self.capabilities:
            cap_tools = CAPABILITY_TO_TOOLS.get(cap, [])
            if resolved in cap_tools or tool_name in cap_tools:
                return True

        return False

    def filter_tools(self, tools: List[Any]) -> List[Any]:
        """
        Filters a collection of tool objects, dicts, or strings to only allowed tools.
        """
        filtered = []
        for t in tools:
            name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else str(t))
            if self.is_tool_allowed(name):
                filtered.append(t)
        return filtered

    def merge(self, other: Optional["ToolPolicy"]) -> "ToolPolicy":
        """
        Merges this policy with another, choosing the most restrictive security constraints.
        """
        if not other:
            return ToolPolicy(
                allowed_tools=set(self.allowed_tools),
                forbidden_tools=set(self.forbidden_tools),
                capabilities=set(self.capabilities),
                tool_aliases=dict(self.tool_aliases),
                required_parameters=dict(self.required_parameters),
                parameter_constraints=dict(self.parameter_constraints),
                approval_required=self.approval_required,
                read_only=self.read_only,
                rate_limits=dict(self.rate_limits),
            )

        merged_aliases = dict(self.tool_aliases)
        merged_aliases.update(other.tool_aliases)

        merged_constraints = dict(self.parameter_constraints)
        merged_constraints.update(other.parameter_constraints)

        return ToolPolicy(
            allowed_tools=self.allowed_tools.union(other.allowed_tools),
            forbidden_tools=self.forbidden_tools.union(other.forbidden_tools),
            capabilities=self.capabilities.union(other.capabilities),
            tool_aliases=merged_aliases,
            required_parameters={**self.required_parameters, **other.required_parameters},
            parameter_constraints=merged_constraints,
            approval_required=self.approval_required or other.approval_required,
            read_only=self.read_only or other.read_only,
            rate_limits={**self.rate_limits, **other.rate_limits},
        )

    @classmethod
    def from_capabilities(
        cls,
        capabilities: List[str],
        read_only: bool = False,
        approval_required: bool = False,
        extra_allowed: Optional[List[str]] = None,
        extra_forbidden: Optional[List[str]] = None,
    ) -> "ToolPolicy":
        """
        Constructs a ToolPolicy directly from high-level capabilities.
        """
        allowed: Set[str] = set(extra_allowed or [])
        caps_set: Set[str] = set(capabilities)

        for cap in capabilities:
            norm_cap = cap.strip()
            # If direct group exists in CAPABILITY_TO_TOOLS
            if norm_cap in CAPABILITY_TO_TOOLS:
                allowed.update(CAPABILITY_TO_TOOLS[norm_cap])
            # If alias exists
            if norm_cap in DEFAULT_TOOL_ALIASES:
                allowed.add(DEFAULT_TOOL_ALIASES[norm_cap])

        return cls(
            allowed_tools=allowed,
            forbidden_tools=set(extra_forbidden or []),
            capabilities=caps_set,
            read_only=read_only,
            approval_required=approval_required,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed_tools": sorted(list(self.allowed_tools)),
            "forbidden_tools": sorted(list(self.forbidden_tools)),
            "capabilities": sorted(list(self.capabilities)),
            "approval_required": self.approval_required,
            "read_only": self.read_only,
            "rate_limits": self.rate_limits,
        }


class ToolPolicyEngine:
    """
    Authoritative Tool Policy Engine.
    Ensures that every tool exposure path delegates to this engine,
    and that ONLY tools in EXECUTABLE lifecycle state reach model schemas.
    """
    _state_machine: Optional[Any] = None

    @classmethod
    def get_state_machine(cls) -> Any:
        if cls._state_machine is None:
            from .tool_state_machine import ToolStateMachine
            cls._state_machine = ToolStateMachine()
        return cls._state_machine

    @classmethod
    def set_state_machine(cls, sm: Any) -> None:
        cls._state_machine = sm

    @classmethod
    def get_executable_tools(
        cls,
        allowed_tools: Optional[Set[str]] = None,
        task: Optional[str] = None,
        state_machine: Optional[Any] = None,
    ) -> List[Any]:
        """
        Returns only tools that have reached EXECUTABLE state in the authoritative lifecycle.
        """
        sm = state_machine or cls.get_state_machine()
        if allowed_tools:
            for t_name in allowed_tools:
                if sm.authorize(t_name, allowed_tools=allowed_tools):
                    sm.promote_to_executable(t_name)
        return sm.get_executable_tools()

    @classmethod
    def get_executable_schemas(
        cls,
        allowed_tools: Optional[Set[str]] = None,
        task: Optional[str] = None,
        state_machine: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        Strict invariant: Only EXECUTABLE tools may be exposed to the model.
        Returns OpenAI-compatible schemas for all executable tools.
        """
        sm = state_machine or cls.get_state_machine()
        if allowed_tools:
            for t_name in allowed_tools:
                if sm.authorize(t_name, allowed_tools=allowed_tools):
                    sm.promote_to_executable(t_name)
        return sm.get_executable_schemas()

