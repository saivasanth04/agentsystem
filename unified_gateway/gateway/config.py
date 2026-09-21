"""
Unified Gateway Configuration & API Key Parser
"""
import os
import secrets
from pathlib import Path
from typing import Dict, Optional
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
APIKEYS_FILE = BASE_DIR / "apikeys.txt"
UNIFIED_KEY_FILE = BASE_DIR / "unified_key.txt"


class GatewayConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    unified_api_key: str = ""
    discovery_interval_seconds: int = 300
    request_timeout: float = 60.0
    max_retries_per_request: int = 4
    enable_stream_fallback: bool = True
    track_tokens_usage: bool = True


def get_or_create_unified_key() -> str:
    """Reads existing unified key from env/file or generates a secure new one."""
    env_key = os.environ.get("UNIFIED_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()

    if UNIFIED_KEY_FILE.exists():
        try:
            content = UNIFIED_KEY_FILE.read_text(encoding="utf-8").strip()
            if content:
                return content
        except Exception:
            pass

    # Generate a new random unified key
    new_key = f"sk-unified-{secrets.token_hex(20)}"
    try:
        UNIFIED_KEY_FILE.write_text(new_key, encoding="utf-8")
    except Exception as e:
        print(f"Warning: Could not write {UNIFIED_KEY_FILE}: {e}")
    return new_key


import re

def parse_apikeys_file(filepath: Optional[Path] = None) -> Dict[str, Optional[str]]:
    """
    Parses the apikeys.txt file.
    Format: '<Provider Name> - <Key or "no key needed">'
    Returns a dictionary mapping normalized provider names to API keys (or None/empty).
    """
    file_to_read = filepath or APIKEYS_FILE
    provider_keys: Dict[str, Optional[str]] = {}

    if not file_to_read.exists():
        print(f"Warning: API keys file not found at {file_to_read}")
        return provider_keys

    with open(file_to_read, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            
            # Match Provider and Key with space-hyphen-space or trailing hyphen
            match = re.match(r"^(.+?)\s+-\s+(.+)$", line)
            if not match:
                match = re.match(r"^(.+?)\s*-\s*(.+)$", line)

            if match:
                provider_raw = match.group(1).strip()
                key_raw = match.group(2).strip()
                
                # Check for "no key needed"
                if key_raw.lower() in ["no key needed", "none", "no key", "null", "false"]:
                    key_val = None
                else:
                    key_val = key_raw
                provider_keys[provider_raw] = key_val
            else:
                # Key without dash
                provider_keys[line] = None

    return provider_keys
