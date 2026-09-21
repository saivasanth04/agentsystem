"""
Secrets Management & Secret Redaction Engine.
Provides CredentialProviders, SecretManager, and universal text/structure secret redaction
to prevent API keys, tokens, and credentials from entering agent context or LLM prompt history.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Union


DEFAULT_SECRET_PATTERNS: List[re.Pattern] = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9\._\-]{20,}", re.IGNORECASE),
    re.compile(r"(AKIA|ASIA)[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[A-Za-z0-9+/=\s\r\n]+?-----END [A-Z ]+ PRIVATE KEY-----"),
    re.compile(r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24,}"),
    re.compile(r"(api[_-]?key|secret|password|token)\s*[:=]\s*['\"]([^'\"]{8,})['\"]", re.IGNORECASE),
]


class CredentialProvider(ABC):
    """Abstract interface for resolving credentials from various backends."""

    @abstractmethod
    def get_credential(self, key_name: str) -> Optional[str]:
        """Resolves a credential by key name, returning None if not found."""
        pass

    def get(self, key_name: str) -> Optional[str]:
        """Convenience alias for get_credential."""
        return self.get_credential(key_name)


class EnvCredentialProvider(CredentialProvider):
    """Resolves credentials from system environment variables with key aliases."""

    ALIASES = {
        "api_key": ["UNIFIED_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "API_KEY"],
        "base_url": ["GATEWAY_BASE_URL", "OPENAI_BASE_URL"],
    }

    def get_credential(self, key_name: str) -> Optional[str]:
        # 1. Direct environment variable lookup
        val = os.getenv(key_name)
        if val:
            return val.strip()

        # 2. Alias lookup
        aliases = self.ALIASES.get(key_name.lower(), [])
        for alias in aliases:
            alias_val = os.getenv(alias)
            if alias_val:
                return alias_val.strip()

        # 3. Uppercase fallback
        upper_val = os.getenv(key_name.upper())
        if upper_val:
            return upper_val.strip()

        return None


class FileCredentialProvider(CredentialProvider):
    """Resolves credentials from local files (e.g. unified_key.txt, .env.local)."""

    def __init__(
        self,
        file_paths: Optional[List[Union[str, Path]]] = None,
        filepath: Optional[Union[str, Path]] = None,
    ):
        paths = list(file_paths or [])
        if filepath:
            paths.append(filepath)
        self.file_paths: List[Path] = [Path(p) for p in paths]

    def add_file_path(self, path: Union[str, Path]):
        self.file_paths.append(Path(path))

    def get_credential(self, key_name: str) -> Optional[str]:
        for fp in self.file_paths:
            if fp.exists() and fp.is_file():
                try:
                    content = fp.read_text(encoding="utf-8", errors="replace").strip()
                    if content:
                        # If single-token key file (like unified_key.txt)
                        if "\n" not in content and "=" not in content and len(content) > 10:
                            if key_name.lower() in ("api_key", "default_key", "unified_key"):
                                return content
                        # If KEY=VALUE formatted file
                        for line in content.splitlines():
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if "=" in line:
                                k, v = line.split("=", 1)
                                if k.strip().lower() == key_name.lower() or k.strip().upper() == key_name.upper():
                                    return v.strip().strip("'\"")
                except Exception:
                    pass
        return None


class SecretManager:
    """
    Central Secret Manager.
    - Chains credential providers to resolve secrets.
    - Registers all known secrets for redaction.
    - Provides universal text and data structure redaction to prevent secret exposure to the LLM.
    """

    def __init__(
        self,
        providers: Optional[List[CredentialProvider]] = None,
        redaction_patterns: Optional[List[re.Pattern]] = None,
    ):
        self.providers: List[CredentialProvider] = (
            providers if providers is not None else [EnvCredentialProvider()]
        )
        self.redaction_patterns: List[re.Pattern] = (
            redaction_patterns if redaction_patterns is not None else list(DEFAULT_SECRET_PATTERNS)
        )
        self._registered_secrets: Set[str] = set()

    def register_provider(self, provider: CredentialProvider):
        """Adds a credential provider to the lookup chain."""
        self.providers.append(provider)

    def register_secret(self, secret_value: Optional[str]):
        """
        Explicitly registers a sensitive secret string so that it will be redacted
        from any text, tool observation, or conversation history.
        """
        if secret_value and isinstance(secret_value, str):
            clean = secret_value.strip()
            # Only register strings with sufficient entropy / length to prevent redacting tiny tokens
            if len(clean) >= 6:
                self._registered_secrets.add(clean)

    def get_secret(self, key_name: str, default: Optional[str] = None) -> Optional[str]:
        """
        Resolves a secret through the registered providers and automatically registers
        the secret value for redaction.
        """
        for provider in self.providers:
            val = provider.get_credential(key_name)
            if val:
                self.register_secret(val)
                return val

        if default:
            # If default is provided, return it (only register if it's an actual credential format)
            if default.startswith("sk-") or len(default) > 20:
                self.register_secret(default)
            return default

        return None

    def redact_text(self, text: str) -> str:
        """
        Redacts all registered secrets and known secret patterns from a string.
        Replaces matched values with '[REDACTED_SECRET]'.
        """
        if not text or not isinstance(text, str):
            return text

        redacted = text

        # 1. Redact explicitly registered secrets
        for secret in self._registered_secrets:
            if secret in redacted:
                redacted = redacted.replace(secret, "[REDACTED_SECRET]")

        # 2. Redact known secret format regex patterns
        for pattern in self.redaction_patterns:
            # For key=value patterns, preserve key name and redact value
            if pattern.pattern.startswith("(api"):
                redacted = pattern.sub(r"\1=\"[REDACTED_SECRET]\"", redacted)
            else:
                redacted = pattern.sub("[REDACTED_SECRET]", redacted)

        return redacted

    def redact_structure(self, data: Any) -> Any:
        """
        Recursively traverses nested dictionaries, lists, tuples, and sets,
        redacting secrets in all string values.
        """
        if isinstance(data, str):
            return self.redact_text(data)
        elif isinstance(data, dict):
            return {self.redact_text(str(k)): self.redact_structure(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.redact_structure(item) for item in data]
        elif isinstance(data, tuple):
            return tuple(self.redact_structure(item) for item in data)
        elif isinstance(data, set):
            return {self.redact_structure(item) for item in data}
        return data


# Global singleton secret manager instance
secret_manager = SecretManager()

# Automatically add unified_key.txt to file provider if it exists
_base_dir = Path(__file__).resolve().parent.parent
_unified_key_file = _base_dir.parent / "unified_gateway" / "unified_key.txt"
if _unified_key_file.exists():
    secret_manager.register_provider(FileCredentialProvider([_unified_key_file]))
