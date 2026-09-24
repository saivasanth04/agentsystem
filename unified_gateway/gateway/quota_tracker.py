"""
Provider Quota & Telemetry Metadata Tracker.
Tracks externally exposed provider rate limit headers (reset time, remaining quota)
and accumulates usage metrics for the Admin Platform without custom routing logic.
"""
from datetime import datetime
import threading
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProviderQuotaInfo(BaseModel):
    provider_id: str
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None
    rpm_remaining: Optional[int] = None
    tpm_remaining: Optional[int] = None
    reset_at: Optional[str] = None
    last_updated: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    total_tokens_used: int = 0
    total_requests: int = 0
    total_errors: int = 0


class QuotaTracker:
    """
    Lightweight tracker for externally exposed rate limits and lifetime stats.
    Does NOT implement custom retry, cooldown, or fallback ranking (delegated to LiteLLM).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._providers: Dict[str, ProviderQuotaInfo] = {}

    def update_from_headers(self, provider_id: str, headers: Dict[str, Any]):
        """Parses standard rate-limit headers (e.g. x-ratelimit-reset, retry-after)."""
        norm_id = provider_id.lower().strip()
        with self._lock:
            info = self._providers.setdefault(norm_id, ProviderQuotaInfo(provider_id=norm_id))

            # Look for reset headers
            for k, v in headers.items():
                k_lower = k.lower()
                if "reset-requests" in k_lower or "reset-tokens" in k_lower or "ratelimit-reset" in k_lower:
                    info.reset_at = str(v)
                elif "ratelimit-remaining-requests" in k_lower:
                    try:
                        info.rpm_remaining = int(v)
                    except Exception:
                        pass
                elif "ratelimit-remaining-tokens" in k_lower:
                    try:
                        info.tpm_remaining = int(v)
                    except Exception:
                        pass
                elif "retry-after" in k_lower:
                    info.reset_at = f"Retry-After: {v}s"

            info.last_updated = datetime.utcnow().isoformat()

    def record_usage(self, provider_id: str, tokens: int = 0, is_error: bool = False):
        norm_id = provider_id.lower().strip()
        with self._lock:
            info = self._providers.setdefault(norm_id, ProviderQuotaInfo(provider_id=norm_id))
            info.total_requests += 1
            info.total_tokens_used += tokens
            if is_error:
                info.total_errors += 1
            info.last_updated = datetime.utcnow().isoformat()

    def get_provider_quota(self, provider_id: str) -> Optional[ProviderQuotaInfo]:
        norm_id = provider_id.lower().strip()
        with self._lock:
            return self._providers.get(norm_id)

    def get_all_quotas(self) -> Dict[str, ProviderQuotaInfo]:
        with self._lock:
            return dict(self._providers)

    def get_summary_stats(self) -> Dict[str, Any]:
        """Provides backward-compatible summary stats for /stats endpoint."""
        from .registry import model_registry
        all_models = model_registry.get_all()
        accessible_models = [m for m in all_models if m.accessible]
        healthy_models = [m for m in all_models if m.health >= 0.5]
        free_models = [m for m in all_models if m.free and m.accessible]

        total_reqs = sum(m.totalRequests for m in all_models)
        total_successes = sum(m.totalSuccesses for m in all_models)
        total_tokens = sum(m.totalTokens for m in all_models)
        avg_latency = (
            sum(m.latency for m in accessible_models) / len(accessible_models)
            if accessible_models else 0.0
        )

        return {
            "total_monitored_models": len(all_models),
            "accessible_models": len(accessible_models),
            "healthy": len(healthy_models),
            "free_models": len(free_models),
            "cooldown": len([m for m in all_models if not m.accessible or m.health < 0.5]),
            "exhausted": 0,
            "total_requests": total_reqs,
            "total_successes": total_successes,
            "total_tokens": total_tokens,
            "success_rate_pct": round((total_successes / total_reqs * 100), 1) if total_reqs > 0 else 100.0,
            "average_latency_sec": round(avg_latency, 3),
            "timestamp": datetime.utcnow().isoformat(),
        }
