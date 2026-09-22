"""
Typed LLM Error Classifier.
Categorizes exceptions into actionable categories (Rate Limit, Server Error,
Network Error, Timeout, Context Overflow, Non-retryable) and extracts
provider cooldowns from HTTP headers.
"""
from enum import Enum
import re
from typing import Any, Optional, Tuple


class LLMErrorCategory(str, Enum):
    RATE_LIMIT = "RATE_LIMIT"              # HTTP 429, RateLimitError
    SERVER_ERROR = "SERVER_ERROR"          # HTTP 500, 502, 503, 504, ServiceUnavailable
    NETWORK_ERROR = "NETWORK_ERROR"        # ConnectionReset, RemoteDisconnected, APIConnectionError
    TIMEOUT = "TIMEOUT"                    # APITimeoutError, ReadTimeout
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"  # context_length_exceeded, maximum context length
    AUTH_ERROR = "AUTH_ERROR"              # 401, 403, invalid api key
    NON_RETRYABLE = "NON_RETRYABLE"        # 400 (invalid schema), 404


def _extract_retry_after(exc: Exception) -> Optional[float]:
    """Attempts to extract retry-after duration in seconds from exception headers or message."""
    headers = None
    # OpenAI/httpx exception structures
    if hasattr(exc, "response") and hasattr(exc.response, "headers"):
        headers = exc.response.headers
    elif hasattr(exc, "headers") and isinstance(exc.headers, dict):
        headers = exc.headers

    if headers:
        for header_key in ("retry-after", "Retry-After", "x-ratelimit-reset-requests", "retry-after-ms"):
            val = headers.get(header_key)
            if val is not None:
                try:
                    num = float(val)
                    if "ms" in header_key:
                        num = num / 1000.0
                    return max(0.1, num)
                except (ValueError, TypeError):
                    pass

    # Regex fallback on exception message e.g. "try again in 12.5s" or "Please try again in 5s"
    msg = str(exc)
    match = re.search(r"try again in ([\d\.]+)\s*(s|sec|seconds|ms)?", msg, re.IGNORECASE)
    if match:
        try:
            num = float(match.group(1))
            unit = (match.group(2) or "s").lower()
            if "ms" in unit:
                num = num / 1000.0
            return max(0.1, num)
        except (ValueError, TypeError):
            pass

    return None


def classify_error(exc: Exception) -> Tuple[LLMErrorCategory, Optional[float]]:
    """
    Classifies an exception into an LLMErrorCategory and optional retry_after delay.
    """
    msg = str(exc).lower()
    exc_type_name = type(exc).__name__.lower()
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)

    # 1. Rate Limit (HTTP 429)
    if status_code == 429 or "ratelimit" in exc_type_name or "rate limit" in msg or "429" in msg:
        retry_after = _extract_retry_after(exc)
        return LLMErrorCategory.RATE_LIMIT, retry_after

    # 2. Context Window Overflow (HTTP 400 with context length keywords)
    context_overflow_patterns = [
        "context_length_exceeded",
        "maximum context length",
        "context window",
        "too many tokens",
        "prompt is too long",
        "max_tokens is too large",
        "string too long",
    ]
    if any(pat in msg for pat in context_overflow_patterns):
        return LLMErrorCategory.CONTEXT_OVERFLOW, None

    # 3. Authentication & Permission Errors (401, 403, invalid api key)
    if status_code in (401, 403) or any(
        k in exc_type_name for k in ("authentication", "permissiondenied")
    ) or any(k in msg for k in ("invalid api key", "api_key_invalid", "unauthorized", "forbidden", "permission denied")):
        return LLMErrorCategory.AUTH_ERROR, None

    # 4. Non-retryable Client Errors (404, or 400 without context overflow)
    if status_code == 404 or "notfound" in exc_type_name:
        return LLMErrorCategory.NON_RETRYABLE, None

    if status_code == 400 or "badrequest" in exc_type_name:
        # 400 errors without context overflow are schema/argument errors: non-retryable
        return LLMErrorCategory.NON_RETRYABLE, None

    # 4. Timeout
    if "timeout" in exc_type_name or "timed out" in msg:
        return LLMErrorCategory.TIMEOUT, None

    # 5. Network / Connection Errors
    network_keywords = (
        "connection",
        "apiconnection",
        "connectionreset",
        "remotedisconnected",
        "broken pipe",
        "connection refused",
        "network unreachable",
    )
    if any(k in exc_type_name or k in msg for k in network_keywords):
        return LLMErrorCategory.NETWORK_ERROR, None

    # 6. Server Errors (500, 502, 503, 504)
    if status_code in (500, 502, 503, 504) or any(
        k in exc_type_name for k in ("internalserver", "badgateway", "serviceunavailable", "gatewaytimeout")
    ) or any(k in msg for k in ("500", "502", "503", "504", "internal server error", "service unavailable")):
        return LLMErrorCategory.SERVER_ERROR, None

    # Default fallback: treat general unknown exceptions as server error for retry safety
    return LLMErrorCategory.SERVER_ERROR, None
