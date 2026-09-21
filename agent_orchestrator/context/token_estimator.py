"""
Token Estimation Utility.
Accurately estimates token counts using tiktoken when available, with calibrated 3.3 chars/token multiplier fallback.
"""
import math
from typing import Any

try:
    import tiktoken
    _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    _TIKTOKEN_ENC = None


def estimate_tokens(text: Any) -> int:
    """Accurately calculates or estimates token count using tiktoken or calibrated 3.3 chars/token multiplier."""
    if text is None:
        return 0
    str_val = str(text) if not isinstance(text, str) else text
    if not str_val:
        return 0
    if _TIKTOKEN_ENC is not None:
        try:
            return len(_TIKTOKEN_ENC.encode(str_val, disallowed_special=()))
        except Exception:
            pass
    return max(1, math.ceil(len(str_val) / 3.3))
