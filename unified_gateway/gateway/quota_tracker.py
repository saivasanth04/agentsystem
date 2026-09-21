"""
Per-Model and Per-Provider Quota & Health Tracker.
Tracks token usage, per-model quota limits, rate limits (429), latency, and dynamic health scores.
"""
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class ModelQuotaState(BaseModel):
    provider_id: str
    model_id: str
    max_daily_tokens: int = 1_000_000
    used_tokens_today: int = 0
    requests_count_today: int = 0
    successful_requests_today: int = 0
    failed_requests_today: int = 0
    consecutive_errors: int = 0
    cooldown_until: float = 0.0
    last_error_message: Optional[str] = None
    last_error_code: Optional[int] = None
    last_used_timestamp: float = Field(default_factory=time.time)
    avg_latency_ms: float = 200.0
    daily_reset_date: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    @property
    def daily_reset_day(self) -> int:
        return int(self.daily_reset_date.split("-")[-1])

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_daily_tokens - self.used_tokens_today)

    @property
    def remaining_pct(self) -> float:
        if self.max_daily_tokens <= 0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - (self.used_tokens_today / self.max_daily_tokens)))

    @property
    def is_in_cooldown(self) -> bool:
        return time.time() < self.cooldown_until

    @property
    def is_exhausted(self) -> bool:
        return self.used_tokens_today >= self.max_daily_tokens

    @property
    def health_status(self) -> str:
        if self.is_in_cooldown:
            return "cooldown"
        if self.is_exhausted:
            return "exhausted"
        if self.consecutive_errors >= 3:
            return "degraded"
        return "healthy"

    def compute_score(self, base_priority: int = 50) -> float:
        """
        Computes composite ranking score for routing.
        Higher score = prioritized for selection.
        """
        if self.is_in_cooldown:
            return -1000.0
        if self.is_exhausted:
            return -500.0

        # Health weight
        health_weight = 100.0 if self.health_status == "healthy" else 20.0
        
        # Remaining Quota weight: favors models with more free tokens remaining
        # so quota across models is used evenly and efficiently!
        quota_weight = self.remaining_pct * 100.0
        
        # Penalty for consecutive errors
        error_penalty = self.consecutive_errors * 25.0
        
        # Latency bonus/penalty (target 500ms)
        latency_penalty = max(0.0, (self.avg_latency_ms - 200.0) / 50.0)
        
        # Provider base priority bonus
        priority_bonus = base_priority * 0.5

        return health_weight + quota_weight + priority_bonus - error_penalty - latency_penalty


class QuotaTracker:
    def __init__(self):
        # Key: f"{provider_id}::{model_id}"
        self.states: Dict[str, ModelQuotaState] = {}

    def _get_key(self, provider_id: str, model_id: str) -> str:
        return f"{provider_id.lower()}::{model_id.strip()}"

    def get_or_create(self, provider_id: str, model_id: str, default_quota: int = 1_000_000) -> ModelQuotaState:
        self._check_daily_reset()
        key = self._get_key(provider_id, model_id)
        if key not in self.states:
            self.states[key] = ModelQuotaState(
                provider_id=provider_id.lower(),
                model_id=model_id.strip(),
                max_daily_tokens=default_quota
            )
        return self.states[key]

    def _check_daily_reset(self):
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for state in self.states.values():
            if state.daily_reset_date != current_date:
                state.used_tokens_today = 0
                state.requests_count_today = 0
                state.successful_requests_today = 0
                state.failed_requests_today = 0
                state.consecutive_errors = 0
                state.cooldown_until = 0.0
                state.daily_reset_date = current_date

    def record_success(
        self,
        provider_id: str,
        model_id: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 200.0
    ):
        state = self.get_or_create(provider_id, model_id)
        total_tokens = max(1, prompt_tokens + completion_tokens)
        
        state.used_tokens_today += total_tokens
        state.requests_count_today += 1
        state.successful_requests_today += 1
        state.consecutive_errors = 0
        state.last_used_timestamp = time.time()
        
        # Exponential moving average for latency
        state.avg_latency_ms = (state.avg_latency_ms * 0.8) + (latency_ms * 0.2)

    def record_error(
        self,
        provider_id: str,
        model_id: str,
        status_code: int,
        error_message: str
    ):
        state = self.get_or_create(provider_id, model_id)
        state.requests_count_today += 1
        state.failed_requests_today += 1
        state.consecutive_errors += 1
        state.last_error_code = status_code
        state.last_error_message = error_message[:200]
        state.last_used_timestamp = time.time()

        now = time.time()
        # Cooldown handling based on HTTP status code
        if status_code == 429:
            # Rate limit or quota exhausted: cooldown for 60 seconds (or more if repeated)
            backoff = min(300.0, 60.0 * (state.consecutive_errors))
            state.cooldown_until = now + backoff
        elif status_code in [400, 404] and any(w in error_message.lower() for w in ["decommissioned", "not found", "deprecated", "does not exist", "invalid_request_error"]):
            # Decommissioned or invalid model: 24-hour cooldown
            state.cooldown_until = now + 86400.0
            state.consecutive_errors = 10
        elif status_code in [401, 403]:
            # Auth or permissions issue: long cooldown
            state.cooldown_until = now + 600.0
        elif status_code >= 500:
            # Server error: short cooldown
            state.cooldown_until = now + 15.0
        else:
            state.cooldown_until = now + 5.0

    def get_ranked_candidates(
        self,
        available_pairs: List[Tuple[str, str, int]],  # List of (provider_id, model_id, base_priority)
    ) -> List[Tuple[str, str, float, ModelQuotaState]]:
        """
        Ranks model-provider pairs by health and remaining quota efficiency.
        Returns list of (provider_id, model_id, score, state) sorted descending.
        """
        self._check_daily_reset()
        results = []
        for prov_id, model_id, priority in available_pairs:
            state = self.get_or_create(prov_id, model_id)
            score = state.compute_score(base_priority=priority)
            results.append((prov_id, model_id, score, state))

        # Sort highest score first
        results.sort(key=lambda x: x[2], reverse=True)
        return results

    def get_summary_stats(self) -> Dict:
        self._check_daily_reset()
        healthy_count = 0
        cooldown_count = 0
        exhausted_count = 0
        degraded_count = 0
        total_tokens_used = 0

        model_details = []
        for state in self.states.values():
            status = state.health_status
            if status == "healthy":
                healthy_count += 1
            elif status == "cooldown":
                cooldown_count += 1
            elif status == "exhausted":
                exhausted_count += 1
            elif status == "degraded":
                degraded_count += 1

            total_tokens_used += state.used_tokens_today
            model_details.append({
                "provider": state.provider_id,
                "model": state.model_id,
                "status": status,
                "remaining_tokens": state.remaining_tokens,
                "remaining_pct": round(state.remaining_pct * 100, 1),
                "used_tokens_today": state.used_tokens_today,
                "consecutive_errors": state.consecutive_errors,
                "avg_latency_ms": round(state.avg_latency_ms, 1),
                "is_in_cooldown": state.is_in_cooldown,
            })

        return {
            "total_monitored_models": len(self.states),
            "healthy": healthy_count,
            "cooldown": cooldown_count,
            "exhausted": exhausted_count,
            "degraded": degraded_count,
            "total_tokens_used_today": total_tokens_used,
            "models": sorted(model_details, key=lambda x: (x["status"] != "healthy", -x["remaining_pct"]))
        }
