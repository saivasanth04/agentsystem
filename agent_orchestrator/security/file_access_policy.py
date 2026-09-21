"""
File Access Policy Engine.
Provides deterministic, defense-in-depth access control across the filesystem:
- allowed_paths: Whitelisted directory and file patterns the agent is authorized to access.
- blocked_paths: Blacklisted paths completely forbidden from read, write, delete, grep, or list.
- read_only_paths: Paths that may be read, searched, or analyzed, but never modified or deleted.
- sensitive_paths: Paths containing sensitive data (secrets, credentials, PII) that trigger audit logging.
"""
from dataclasses import dataclass, field
from enum import Enum
import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class FileAccessMode(str, Enum):
    """Operation modes for file access evaluation."""
    READ = "READ"
    WRITE = "WRITE"
    DELETE = "DELETE"
    LIST = "LIST"
    SEARCH = "SEARCH"


class FileAccessDeniedError(PermissionError):
    """Raised when a file operation violates the active FileAccessPolicy."""
    def __init__(self, message: str, path: str = "", mode: Optional[FileAccessMode] = None):
        super().__init__(message)
        self.path = path
        self.mode = mode


@dataclass
class FileAccessDecision:
    """Outcome of evaluating a path against FileAccessPolicy."""
    allowed: bool
    reason: str = ""
    mode: FileAccessMode = FileAccessMode.READ
    is_sensitive: bool = False
    suggested_action: Optional[str] = None
    target_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "mode": self.mode.value if hasattr(self.mode, "value") else str(self.mode),
            "is_sensitive": self.is_sensitive,
            "suggested_action": self.suggested_action,
            "target_path": self.target_path,
        }


@dataclass
class FileAccessPolicy:
    """
    Encapsulates repository and task path boundaries.
    """
    allowed_paths: List[str] = field(default_factory=lambda: ["*"])
    blocked_paths: List[str] = field(default_factory=lambda: [
        ".git",
        ".git/**",
        "**/.git/**",
        ".env",
        ".env.*",
        "**/.env",
        "**/.env.*",
        "*.pem",
        "**/*.pem",
        "*.key",
        "**/*.key",
        ".aws",
        ".aws/**",
        "**/.aws/**",
        ".ssh",
        ".ssh/**",
        "**/.ssh/**",
        "id_rsa*",
        "**/id_rsa*",
        "id_ed25519*",
        "**/id_ed25519*",
    ])
    read_only_paths: List[str] = field(default_factory=list)
    sensitive_paths: List[str] = field(default_factory=lambda: [
        "**/*secret*",
        "**/*credential*",
        "**/*token*",
        "config/production.*",
        "**/production.json",
        "**/production.yaml",
    ])
    audit_sensitive_access: bool = True

    @staticmethod
    def normalize_path(rel_path: Union[str, Path]) -> str:
        """Normalizes path into clean relative POSIX format."""
        p_str = str(rel_path).replace("\\", "/").strip()
        clean = p_str.lstrip("/")
        return clean or "."

    @classmethod
    def matches_pattern(cls, rel_path: Union[str, Path], patterns: List[str]) -> bool:
        """
        Determines whether a normalized path matches any glob/prefix pattern in patterns list.
        Supports fnmatch, directory prefix bounds, and recursive wildcards.
        """
        if not patterns:
            return False

        norm_path = cls.normalize_path(rel_path)
        base_name = Path(norm_path).name

        for pat in patterns:
            if not pat:
                continue
            norm_pat = pat.replace("\\", "/").strip().lstrip("/")
            if norm_pat == "*":
                return True

            # Leading recursive wildcard "**/foo..." matches both at root and within any subpath
            if norm_pat.startswith("**/"):
                sub_pat = norm_pat[3:]
                if cls.matches_pattern(norm_path, [sub_pat]):
                    return True
                parts = norm_path.split("/")
                for i in range(1, len(parts)):
                    sub_path = "/".join(parts[i:])
                    if cls.matches_pattern(sub_path, [sub_pat]):
                        return True

            # 1. Exact match
            if norm_path == norm_pat:
                return True

            # 2. Standard fnmatch on full relative path and basename
            if fnmatch.fnmatch(norm_path, norm_pat):
                return True
            if fnmatch.fnmatch(norm_path, f"*/{norm_pat}"):
                return True
            if fnmatch.fnmatch(base_name, norm_pat):
                return True

            # 3. Recursive directory match: "dir/**" or "dir/*"
            if norm_pat.endswith("/**"):
                prefix = norm_pat[:-3].rstrip("/")
                if norm_path == prefix or norm_path.startswith(prefix + "/"):
                    return True
                if f"/{prefix}/" in f"/{norm_path}/":
                    return True
            elif norm_pat.endswith("/*"):
                prefix = norm_pat[:-2].rstrip("/")
                if norm_path == prefix or norm_path.startswith(prefix + "/"):
                    return True
                if f"/{prefix}/" in f"/{norm_path}/":
                    return True

            # 4. Implicit directory prefix match if pattern is a folder name without wildcards
            if "*" not in norm_pat and "?" not in norm_pat:
                if norm_path == norm_pat or norm_path.startswith(norm_pat + "/"):
                    return True
                if f"/{norm_pat}/" in f"/{norm_path}/":
                    return True

        return False

    def is_blocked(self, rel_path: Union[str, Path]) -> bool:
        """Convenience method checking if a path is blocked by security policy."""
        norm_path = self.normalize_path(rel_path)
        if norm_path.endswith((".example", ".sample", ".template", ".defaults")):
            return False
        return self.matches_pattern(norm_path, self.blocked_paths)

    def is_read_only(self, rel_path: Union[str, Path]) -> bool:
        """Convenience method checking if a path is designated read-only."""
        norm_path = self.normalize_path(rel_path)
        return self.matches_pattern(norm_path, self.read_only_paths)

    def is_sensitive(self, rel_path: Union[str, Path]) -> bool:
        """Convenience method checking if a path is flagged as sensitive."""
        norm_path = self.normalize_path(rel_path)
        return self.matches_pattern(norm_path, self.sensitive_paths)

    def is_allowed(self, rel_path: Union[str, Path], mode: FileAccessMode = FileAccessMode.READ) -> bool:
        """Evaluates whether access is permitted for given path and mode."""
        return self.evaluate(rel_path, mode).allowed

    def evaluate(self, rel_path: Union[str, Path], mode: FileAccessMode) -> FileAccessDecision:
        """
        Evaluates a file access request against blocked, read-only, and allowed path boundaries.
        Order of precedence:
        1. Blocked Paths -> Denied (highest priority)
        2. Sensitive Paths -> Flagged for audit
        3. Write/Delete on Read-Only Paths -> Denied
        4. Allowed Paths Boundary -> Must match if not wildcard
        5. Permitted
        """
        norm_path = self.normalize_path(rel_path)

        # 1. Check blocked_paths (Denies read, write, delete, list, search)
        # Template and example files (e.g. .env.example, .env.template) are permitted documentation
        is_template_file = norm_path.endswith((".example", ".sample", ".template", ".defaults"))
        if not is_template_file and self.matches_pattern(norm_path, self.blocked_paths):
            return FileAccessDecision(
                allowed=False,
                reason=f"Access Denied: Path '{norm_path}' is explicitly blocked by security policy.",
                mode=mode,
                is_sensitive=True,
                suggested_action="Access to internal metadata, environment secrets, and credentials is prohibited.",
                target_path=norm_path,
            )

        # 2. Check sensitive_paths (informational / audit flag)
        is_sensitive = self.matches_pattern(norm_path, self.sensitive_paths)

        # 3. Check read_only_paths on mutation attempts (WRITE or DELETE)
        if mode in (FileAccessMode.WRITE, FileAccessMode.DELETE):
            if self.matches_pattern(norm_path, self.read_only_paths):
                action_str = "modified" if mode == FileAccessMode.WRITE else "deleted"
                return FileAccessDecision(
                    allowed=False,
                    reason=f"Permission Denied: Path '{norm_path}' is designated read-only and cannot be {action_str}.",
                    mode=mode,
                    is_sensitive=is_sensitive,
                    suggested_action="To change this file, remove it from read-only protection or modify an authorized mutable target.",
                    target_path=norm_path,
                )

        # 4. Check allowed_paths scope (if restricted)
        if self.allowed_paths and "*" not in self.allowed_paths:
            if not self.matches_pattern(norm_path, self.allowed_paths):
                return FileAccessDecision(
                    allowed=False,
                    reason=f"Access Denied: Path '{norm_path}' is outside authorized scope {self.allowed_paths}.",
                    mode=mode,
                    is_sensitive=is_sensitive,
                    suggested_action=f"Confine file operations to authorized paths: {self.allowed_paths}.",
                    target_path=norm_path,
                )

        # 5. Access Granted
        return FileAccessDecision(
            allowed=True,
            reason="Access permitted by file access policy.",
            mode=mode,
            is_sensitive=is_sensitive,
            target_path=norm_path,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed_paths": self.allowed_paths,
            "blocked_paths": self.blocked_paths,
            "read_only_paths": self.read_only_paths,
            "sensitive_paths": self.sensitive_paths,
            "audit_sensitive_access": self.audit_sensitive_access,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileAccessPolicy":
        if not data:
            return cls()
        return cls(
            allowed_paths=list(data.get("allowed_paths", ["*"])),
            blocked_paths=list(data.get("blocked_paths", [
                ".git", ".git/**", "**/.git/**",
                ".env", ".env.*", "**/.env", "**/.env.*",
                "**/*.pem", "**/*.key", "**/.aws/**", "**/.ssh/**",
                "**/id_rsa*", "**/id_ed25519*",
            ])),
            read_only_paths=list(data.get("read_only_paths", [])),
            sensitive_paths=list(data.get("sensitive_paths", [
                "**/*secret*", "**/*credential*", "**/*token*", "config/production.*"
            ])),
            audit_sensitive_access=bool(data.get("audit_sensitive_access", True)),
        )
