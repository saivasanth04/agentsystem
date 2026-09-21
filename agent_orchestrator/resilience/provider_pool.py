"""
Multi-Provider Failover Pool.
Manages primary and fallback provider endpoints (e.g. OpenAI, OpenRouter, Azure),
monitoring health, consecutive failures, and cooldown periods.
"""
from dataclasses import dataclass, field
import threading
import time
from typing import Dict, List, Optional
from openai import OpenAI


@dataclass
class ProviderEndpoint:
    name: str
    base_url: str
    api_key: str
    is_active: bool = True
    models: Optional[List[str]] = None
    consecutive_failures: int = 0
    cooldown_until: float = 0.0

    def is_available(self) -> bool:
        return self.is_active and time.time() >= self.cooldown_until


class ProviderFailoverPool:
    """
    Manages an ordered failover pool of provider endpoints.
    """

    def __init__(self, primary_base_url: str, primary_api_key: str, primary_name: str = "primary"):
        self._endpoints: List[ProviderEndpoint] = [
            ProviderEndpoint(name=primary_name, base_url=primary_base_url, api_key=primary_api_key)
        ]
        self._clients: Dict[str, OpenAI] = {}
        self._lock = threading.RLock()

    def add_fallback_endpoint(
        self,
        name: str,
        base_url: str,
        api_key: str,
        models: Optional[List[str]] = None,
    ) -> None:
        """Adds a fallback endpoint to the pool."""
        with self._lock:
            # Avoid duplicate endpoint names
            self._endpoints = [e for e in self._endpoints if e.name != name]
            self._endpoints.append(
                ProviderEndpoint(name=name, base_url=base_url, api_key=api_key, models=models)
            )

    def get_candidate_endpoints(self) -> List[ProviderEndpoint]:
        """Returns available endpoints in priority order."""
        with self._lock:
            available = [e for e in self._endpoints if e.is_available()]
            # If all are cooling down, return all active endpoints as fallback
            return available if available else [e for e in self._endpoints if e.is_active]

    def get_client_for_endpoint(self, endpoint: ProviderEndpoint, timeout: float = 90.0) -> OpenAI:
        """Retrieves or creates an OpenAI client for a given provider endpoint."""
        with self._lock:
            if endpoint.name not in self._clients:
                self._clients[endpoint.name] = OpenAI(
                    api_key=endpoint.api_key,
                    base_url=endpoint.base_url,
                    timeout=timeout,
                )
            return self._clients[endpoint.name]

    def mark_success(self, endpoint_name: str) -> None:
        """Resets consecutive failure count on successful call."""
        with self._lock:
            for ep in self._endpoints:
                if ep.name == endpoint_name:
                    ep.consecutive_failures = 0
                    ep.cooldown_until = 0.0

    def mark_failure(self, endpoint_name: str, cooldown_seconds: float = 30.0) -> None:
        """Records a failure and triggers cooldown if threshold exceeded."""
        with self._lock:
            for ep in self._endpoints:
                if ep.name == endpoint_name:
                    ep.consecutive_failures += 1
                    if ep.consecutive_failures >= 2:
                        ep.cooldown_until = time.time() + cooldown_seconds
