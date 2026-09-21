"""
Network Access Policy Engine (Issue #81).
Provides deterministic egress filtering, domain allowlisting/denylisting,
cloud metadata endpoint protection, network-disabled sandboxing, and HTTP permission checks.
"""
from dataclasses import dataclass, field
from enum import Enum
import fnmatch
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urlparse


class NetworkAccessMode(str, Enum):
    """Execution mode for network access."""
    DISABLED = "DISABLED"                # Air-gapped / benchmark mode: zero external network traffic
    ALLOWLIST_ONLY = "ALLOWLIST_ONLY"    # Restricted: only explicitly approved domains/hosts permitted
    UNRESTRICTED = "UNRESTRICTED"        # Open: unrestricted egress (development / permissive mode)


class NetworkAccessDeniedError(PermissionError):
    """Raised when an action violates the active NetworkAccessPolicy."""
    def __init__(self, message: str, target: str = "", mode: Optional[NetworkAccessMode] = None):
        super().__init__(message)
        self.target = target
        self.mode = mode


@dataclass
class NetworkAccessDecision:
    """Result of evaluating a URL, host, or command against NetworkAccessPolicy."""
    allowed: bool
    reason: str = ""
    mode: NetworkAccessMode = NetworkAccessMode.DISABLED
    target: str = ""
    is_cloud_metadata: bool = False
    suggested_action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "mode": self.mode.value if hasattr(self.mode, "value") else str(self.mode),
            "target": self.target,
            "is_cloud_metadata": self.is_cloud_metadata,
            "suggested_action": self.suggested_action,
        }


# High-risk network CLI tools that should be blocked in network-disabled environments
DEFAULT_BLOCKED_NETWORK_COMMANDS = [
    "curl", "wget", "nc", "ncat", "netcat", "telnet",
    "ssh", "scp", "sftp", "rsync", "socat", "ftp",
    "tftp", "nmap", "traceroute", "ping",
]

# Sensitive cloud metadata and private IP endpoints that must ALWAYS be blocked
DEFAULT_BLOCKED_HOSTS = [
    # AWS / GCP / OpenStack link-local metadata
    "169.254.169.254",
    "169.254.*",
    # GCP internal DNS
    "metadata.google.internal",
    "metadata",
    # Azure instance metadata
    "metadata.azure.com",
    # Local loopback illusions
    "127.0.0.1",
    "localhost",
    "::1",
    "0.0.0.0",
]

# Standard developer package registries allowed by default in ALLOWLIST_ONLY mode
DEFAULT_ALLOWED_DOMAINS = [
    "*.pypi.org",
    "pypi.org",
    "*.pythonhosted.org",
    "pythonhosted.org",
    "*.github.com",
    "github.com",
    "*.githubusercontent.com",
    "raw.githubusercontent.com",
    "*.npmjs.org",
    "registry.npmjs.org",
    "registry.yarnpkg.com",
    "*.crates.io",
    "crates.io",
    "proxy.golang.org",
    "*.golang.org",
]


@dataclass
class NetworkAccessPolicy:
    """
    Encapsulates network egress and HTTP permission boundaries.
    """
    mode: NetworkAccessMode = NetworkAccessMode.ALLOWLIST_ONLY
    allowed_domains: List[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_DOMAINS))
    blocked_domains: List[str] = field(default_factory=lambda: list(DEFAULT_BLOCKED_HOSTS))
    blocked_ports: List[int] = field(default_factory=lambda: [22, 23, 25, 445, 3389])
    blocked_network_commands: List[str] = field(default_factory=lambda: list(DEFAULT_BLOCKED_NETWORK_COMMANDS))
    resource_budget: Optional[Any] = None
    resource_tracker: Optional[Any] = None
    allowlist: Optional[List[str]] = None

    def __post_init__(self):
        if self.allowlist is not None:
            self.allowed_domains = list(self.allowlist)

    @staticmethod
    def extract_host_and_port(url_or_host: str) -> Tuple[str, Optional[int]]:
        """Extracts canonical lowercase hostname and optional port from a URL or host string."""
        clean = str(url_or_host).strip()
        if not clean:
            return "", None

        # If it doesn't have scheme, prepend dummy scheme for urlparse
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+-.]*://", clean):
            parsed = urlparse(f"http://{clean}")
        else:
            parsed = urlparse(clean)

        hostname = (parsed.hostname or "").lower().strip()
        port = parsed.port
        return hostname, port

    @classmethod
    def matches_domain_pattern(cls, host: str, patterns: List[str]) -> bool:
        """
        Determines whether a host matches any domain wildcard pattern.
        Supports exact match ('pypi.org'), wildcard prefix ('*.pypi.org'), and IP patterns ('169.254.*').
        """
        if not host or not patterns:
            return False

        clean_host = host.lower().strip()

        for pat in patterns:
            if not pat:
                continue
            clean_pat = pat.lower().strip()
            if clean_pat == "*":
                return True
            # Exact match
            if clean_host == clean_pat:
                return True
            # Subdomain match with leading wildcard (e.g. *.github.com matches api.github.com and github.com)
            if clean_pat.startswith("*."):
                root_domain = clean_pat[2:]
                if clean_host == root_domain or clean_host.endswith(f".{root_domain}"):
                    return True
            # Standard fnmatch
            if fnmatch.fnmatch(clean_host, clean_pat):
                return True

        return False

    def is_cloud_metadata(self, host: str) -> bool:
        """Checks if a host matches known cloud metadata endpoints."""
        clean_host = host.lower().strip()
        metadata_indicators = [
            "169.254.169.254",
            "metadata.google.internal",
            "metadata.azure.com",
        ]
        return any(clean_host == ind or clean_host.startswith("169.254.") for ind in metadata_indicators)

    def evaluate_host(
        self,
        host: str,
        port: Optional[int] = None,
        bytes_transferred: int = 0,
    ) -> NetworkAccessDecision:
        """Evaluates a target host and optional port against the active policy."""
        clean_host = (host or "").lower().strip()

        # 1. Mode DISABLED -> complete air-gap
        if self.mode == NetworkAccessMode.DISABLED:
            return NetworkAccessDecision(
                allowed=False,
                reason=f"Network Access Denied: Execution environment is air-gapped (mode={self.mode.value}). Outbound connection to '{clean_host}' is prohibited.",
                mode=self.mode,
                target=clean_host,
                suggested_action="Execution requires network isolation. Enable network permissions on the task contract or run in offline mode.",
            )

        # 2. Blocked ports check
        if port is not None and port in self.blocked_ports:
            return NetworkAccessDecision(
                allowed=False,
                reason=f"Network Access Denied: Port {port} is explicitly blocked by security policy.",
                mode=self.mode,
                target=f"{clean_host}:{port}",
                suggested_action=f"Outbound connections to port {port} are prohibited for security.",
            )

        # 3. Check blocked_domains (Denies cloud metadata, private IP SSRF, etc.)
        if self.matches_domain_pattern(clean_host, self.blocked_domains):
            is_meta = self.is_cloud_metadata(clean_host)
            meta_desc = " (Cloud Instance Metadata Service)" if is_meta else ""
            return NetworkAccessDecision(
                allowed=False,
                reason=f"Network Access Denied: Host '{clean_host}'{meta_desc} is explicitly blocked to prevent SSRF and credential exfiltration.",
                mode=self.mode,
                target=clean_host,
                is_cloud_metadata=is_meta,
                suggested_action="Access to instance metadata, localhost, and private endpoints is forbidden.",
            )

        # Check quantitative network budget before permitting egress
        if self.resource_tracker:
            net_dec = self.resource_tracker.record_network_request(clean_host, bytes_transferred=bytes_transferred)
            if not net_dec.allowed:
                return NetworkAccessDecision(
                    allowed=False,
                    reason=f"Network Access Denied: {net_dec.reason}",
                    mode=self.mode,
                    target=clean_host,
                    suggested_action=net_dec.suggested_action,
                )
        elif self.resource_budget:
            rb = self.resource_budget
            max_reqs = getattr(rb, "max_network_requests", 0)
            if max_reqs > 0:
                self._temp_req_count = getattr(self, "_temp_req_count", 0) + 1
                if self._temp_req_count > max_reqs:
                    return NetworkAccessDecision(
                        allowed=False,
                        reason=f"Network Access Denied: Network request budget exceeded: {self._temp_req_count} > {max_reqs} requests.",
                        mode=self.mode,
                        target=clean_host,
                        suggested_action="Cache remote requests or raise max_network_requests on the task budget.",
                    )

        # 4. Mode UNRESTRICTED -> allow all non-blocked hosts
        if self.mode == NetworkAccessMode.UNRESTRICTED:
            return NetworkAccessDecision(
                allowed=True,
                reason=f"Network access to '{clean_host}' permitted under unrestricted policy.",
                mode=self.mode,
                target=clean_host,
            )

        # 5. Mode ALLOWLIST_ONLY -> host must match allowed_domains
        if self.mode == NetworkAccessMode.ALLOWLIST_ONLY:
            if self.matches_domain_pattern(clean_host, self.allowed_domains):
                return NetworkAccessDecision(
                    allowed=True,
                    reason=f"Host '{clean_host}' is in the authorized network allowlist.",
                    mode=self.mode,
                    target=clean_host,
                )
            return NetworkAccessDecision(
                allowed=False,
                reason=f"Network Access Denied: Host '{clean_host}' is not in the authorized egress allowlist.",
                mode=self.mode,
                target=clean_host,
                suggested_action=f"To access '{clean_host}', add it to task allowed_domains or configure network policy.",
            )

        # Fallback denial
        return NetworkAccessDecision(
            allowed=False,
            reason=f"Network access to '{clean_host}' denied by default.",
            mode=self.mode,
            target=clean_host,
        )

    def evaluate_url(self, url: str) -> NetworkAccessDecision:
        """Evaluates a complete URL against the active network policy."""
        host, port = self.extract_host_and_port(url)
        if not host:
            return NetworkAccessDecision(
                allowed=False,
                reason=f"Invalid URL: could not parse hostname from '{url}'.",
                mode=self.mode,
                target=url,
            )
        decision = self.evaluate_host(host, port)
        decision.target = url
        return decision

    def evaluate_command(self, cmd: Union[str, List[str]]) -> NetworkAccessDecision:
        """
        Evaluates a shell command string or token list for network access risks.
        Detects blocked network binaries and extracts remote targets where possible.
        """
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        cmd_clean = cmd_str.strip()
        cmd_lower = cmd_clean.lower()

        # 1. Mode DISABLED -> Check for any network binaries or network flags
        if self.mode == NetworkAccessMode.DISABLED:
            for net_cmd in self.blocked_network_commands:
                pattern = rf"\b{re.escape(net_cmd.strip())}\b"
                if re.search(pattern, cmd_lower):
                    return NetworkAccessDecision(
                        allowed=False,
                        reason=f"Command '{cmd_clean}' invokes network utility '{net_cmd.strip()}' while network is disabled.",
                        mode=self.mode,
                        target=cmd_clean,
                        suggested_action="Task execution is configured with network_allowed=False. Remove network commands or enable network permissions.",
                    )

        # 2. Check for URLs or IP addresses in the command
        urls = re.findall(r"https?://[^\s'\"\`]+", cmd_str)
        for u in urls:
            dec = self.evaluate_url(u)
            if not dec.allowed:
                return dec

        # 3. Check for standalone cloud metadata IP (169.254.169.254) in command string
        if "169.254.169.254" in cmd_str or "metadata.google.internal" in cmd_str:
            return NetworkAccessDecision(
                allowed=False,
                reason="Command attempts to access cloud instance metadata endpoint (SSRF prevention).",
                mode=self.mode,
                target="169.254.169.254",
                is_cloud_metadata=True,
                suggested_action="Accessing cloud metadata endpoints is strictly forbidden.",
            )

        return NetworkAccessDecision(
            allowed=True,
            reason="Command does not violate network access policy.",
            mode=self.mode,
            target=cmd_clean,
        )

    def get_disabled_proxy_env(self) -> Dict[str, str]:
        """
        Returns environment variables that route HTTP/HTTPS traffic through a non-listening local port,
        ensuring local subprocesses (pip, requests, urllib, curl) fail fast if attempting egress.
        """
        return {
            "HTTP_PROXY": "http://127.0.0.1:0",
            "HTTPS_PROXY": "http://127.0.0.1:0",
            "http_proxy": "http://127.0.0.1:0",
            "https_proxy": "http://127.0.0.1:0",
            "ALL_PROXY": "socks5://127.0.0.1:0",
            "all_proxy": "socks5://127.0.0.1:0",
            "NO_PROXY": "",
            "no_proxy": "",
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value if hasattr(self.mode, "value") else str(self.mode),
            "allowed_domains": self.allowed_domains,
            "blocked_domains": self.blocked_domains,
            "blocked_ports": self.blocked_ports,
            "blocked_network_commands": self.blocked_network_commands,
            "resource_budget": self.resource_budget.to_dict() if hasattr(self.resource_budget, "to_dict") else self.resource_budget,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NetworkAccessPolicy":
        if not data:
            return cls()
        raw_mode = data.get("mode", NetworkAccessMode.ALLOWLIST_ONLY.value)
        mode = NetworkAccessMode(raw_mode) if raw_mode in NetworkAccessMode.__members__ or raw_mode in [m.value for m in NetworkAccessMode] else NetworkAccessMode.ALLOWLIST_ONLY
        rb_data = data.get("resource_budget")
        rb = None
        if rb_data:
            from .resource_budget import ResourceBudget
            rb = ResourceBudget.from_dict(rb_data) if isinstance(rb_data, dict) else rb_data
        return cls(
            mode=mode,
            allowed_domains=list(data.get("allowed_domains", DEFAULT_ALLOWED_DOMAINS)),
            blocked_domains=list(data.get("blocked_domains", DEFAULT_BLOCKED_HOSTS)),
            blocked_ports=list(data.get("blocked_ports", [22, 23, 25, 445, 3389])),
            blocked_network_commands=list(data.get("blocked_network_commands", DEFAULT_BLOCKED_NETWORK_COMMANDS)),
            resource_budget=rb,
        )
