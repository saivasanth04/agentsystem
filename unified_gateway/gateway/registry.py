"""
Live Model Registry for Unified LLM Gateway.
Single source of truth for runtime model metadata, accessibility status,
free-model detection, health metrics, and capabilities.
"""
from datetime import datetime
import json
import logging
from pathlib import Path
import threading
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("gateway.registry")


class ModelMetadata(BaseModel):
    provider: str
    model: str
    accessible: bool = True
    free: bool = False
    latency: float = 0.0  # seconds
    health: float = 1.0   # 0.0 to 1.0
    rpmRemaining: Optional[int] = None
    tpmRemaining: Optional[int] = None
    quotaRemaining: Optional[int] = None
    quotaLimit: Optional[int] = None
    resetAt: Optional[str] = None
    capabilities: List[str] = Field(default_factory=lambda: ["chat"])
    contextWindow: Optional[int] = 128000
    lastSuccess: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    lastFailure: Optional[str] = None
    consecutiveFailures: int = 0
    totalRequests: int = 0
    totalSuccesses: int = 0
    totalTokens: int = 0


class LiveModelRegistry:
    """
    Persistent, thread-safe runtime registry for discovered models across all providers.
    """

    def __init__(self, persistence_file: Optional[Path] = None):
        self._lock = threading.RLock()
        self._models: Dict[str, ModelMetadata] = {}  # key: f"{provider}:{model}"
        self.persistence_file = persistence_file or Path(__file__).resolve().parent.parent / ".gateway_model_registry.json"
        self._load_from_disk()

    def _key(self, provider: str, model: str) -> str:
        return f"{provider.lower().strip()}:{model.strip()}"

    def register_or_update(self, meta: ModelMetadata) -> ModelMetadata:
        """Registers or merges model metadata into registry."""
        key = self._key(meta.provider, meta.model)
        with self._lock:
            existing = self._models.get(key)
            if existing:
                # Merge dynamic execution stats with updated discovery metadata
                meta.totalRequests = existing.totalRequests
                meta.totalSuccesses = existing.totalSuccesses
                meta.totalTokens = existing.totalTokens
                meta.consecutiveFailures = existing.consecutiveFailures
                if existing.health < meta.health:
                    meta.health = (existing.health + meta.health) / 2.0
                if existing.latency > 0:
                    meta.latency = (existing.latency + meta.latency) / 2.0
            self._models[key] = meta
            self._save_to_disk()
            return meta

    def record_call_outcome(
        self,
        provider: str,
        model: str,
        latency: float,
        success: bool,
        tokens_used: int = 0,
        error_msg: Optional[str] = None,
        rpm_remaining: Optional[int] = None,
        tpm_remaining: Optional[int] = None,
        reset_at: Optional[str] = None,
    ):
        """Updates runtime performance, health, and quota state after a request."""
        key = self._key(provider, model)
        now_iso = datetime.utcnow().isoformat()

        with self._lock:
            meta = self._models.get(key)
            if not meta:
                # Auto-register if missing
                meta = ModelMetadata(
                    provider=provider,
                    model=model,
                    accessible=success,
                    free=True,
                    latency=latency,
                    health=1.0 if success else 0.5,
                    capabilities=["chat"],
                )
                self._models[key] = meta

            meta.totalRequests += 1
            meta.totalTokens += tokens_used

            if success:
                meta.totalSuccesses += 1
                meta.consecutiveFailures = 0
                meta.accessible = True
                meta.lastSuccess = now_iso
                # Exponential moving average for latency
                if meta.latency <= 0:
                    meta.latency = latency
                else:
                    meta.latency = (meta.latency * 0.7) + (latency * 0.3)
                # Recover health towards 1.0
                meta.health = min(1.0, meta.health + 0.1)
            else:
                meta.consecutiveFailures += 1
                meta.lastFailure = f"{now_iso} - {error_msg or 'Request failed'}"
                # Penalize health
                meta.health = max(0.0, meta.health - 0.25)
                if meta.consecutiveFailures >= 3:
                    meta.accessible = False

            if rpm_remaining is not None:
                meta.rpmRemaining = rpm_remaining
            if tpm_remaining is not None:
                meta.tpmRemaining = tpm_remaining
            if reset_at is not None:
                meta.resetAt = reset_at

            self._save_to_disk()

    def get_model(self, provider: str, model: str) -> Optional[ModelMetadata]:
        with self._lock:
            return self._models.get(self._key(provider, model))

    def get_all(self) -> List[ModelMetadata]:
        with self._lock:
            return list(self._models.values())

    def filter(
        self,
        capability: Optional[str] = None,
        free_only: bool = False,
        accessible_only: bool = True,
        healthy_only: bool = False,
        provider: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[ModelMetadata]:
        """Queries the live model registry using flexible filters."""
        with self._lock:
            results = list(self._models.values())

        if accessible_only:
            results = [m for m in results if m.accessible]
        if free_only:
            results = [m for m in results if m.free]
        if healthy_only:
            results = [m for m in results if m.health >= 0.5]
        if provider:
            results = [m for m in results if m.provider.lower() == provider.lower()]
        if capability:
            cap_norm = capability.lower().strip()
            results = [m for m in results if any(cap_norm in c.lower() for c in m.capabilities)]
        if search:
            s_norm = search.lower().strip()
            results = [
                m for m in results
                if s_norm in m.model.lower()
                or s_norm in m.provider.lower()
                or any(s_norm in c.lower() for c in m.capabilities)
            ]
        return results

    def get_candidates_for_mode(self, mode: str) -> List[ModelMetadata]:
        """
        Resolves candidates for logical modes:
        - 'auto': Best overall (accessible, healthy, free preferred, sorted by health & latency)
        - 'fast': Lowest latency lightweight execution (latency < 1.5s or lightweight architectures)
        - 'smart': Highest reasoning capabilities (reasoning capability, large context, high health)
        - 'coder': Best coding-capable models (coding capability)
        """
        mode_norm = (mode or "auto").lower().strip()
        all_accessible = self.filter(accessible_only=True, healthy_only=False)
        if not all_accessible:
            # Fallback to any registered model if none marked accessible yet
            all_accessible = self.get_all()

        if mode_norm == "fast":
            # Filter for fast models or models with low latency
            candidates = [m for m in all_accessible if "fast" in m.capabilities or m.latency < 1.5 or any(k in m.model.lower() for k in ("flash", "mini", "8b", "instant", "light"))]
            if not candidates:
                candidates = all_accessible
            # Sort by lowest latency, then highest health
            return sorted(candidates, key=lambda m: (m.latency if m.latency > 0 else 999.0, -m.health))

        elif mode_norm == "smart":
            # Filter for reasoning models
            candidates = [m for m in all_accessible if "reasoning" in m.capabilities or any(k in m.model.lower() for k in ("r1", "o1", "o3", "reasoning", "thinking", "70b", "pro", "sonnet"))]
            if not candidates:
                candidates = all_accessible
            # Sort by reasoning score, health, and free preference
            return sorted(candidates, key=lambda m: (-int(m.free), -m.health, m.latency if m.latency > 0 else 999.0))

        elif mode_norm == "coder":
            # Filter for coding models
            candidates = [m for m in all_accessible if "coding" in m.capabilities or any(k in m.model.lower() for k in ("coder", "code", "qwen", "codestral", "deepseek"))]
            if not candidates:
                candidates = all_accessible
            return sorted(candidates, key=lambda m: (-int(m.free), -m.health, m.latency if m.latency > 0 else 999.0))

        else:
            # 'auto': General auto-routing. Prioritize free, high-health, lowest latency
            return sorted(candidates or all_accessible, key=lambda m: (-int(m.free), -m.health, m.latency if m.latency > 0 else 999.0))

    def _save_to_disk(self):
        try:
            data = {k: v.dict() for k, v in self._models.items()}
            self.persistence_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Could not persist model registry to disk: {e}")

    def _load_from_disk(self):
        if not self.persistence_file.exists():
            return
        try:
            content = self.persistence_file.read_text(encoding="utf-8")
            if not content.strip():
                return
            data = json.loads(content)
            for k, v in data.items():
                self._models[k] = ModelMetadata(**v)
        except Exception as e:
            logger.debug(f"Could not load model registry cache: {e}")


# Global registry singleton
model_registry = LiveModelRegistry()
