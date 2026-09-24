"""
Dynamic Provider Discovery Engine for Unified LLM Gateway.
Continuously probes provider endpoints, discovers models dynamically,
verifies credentials, detects free tier models & capabilities, updates LiveModelRegistry,
and registers deployments dynamically into LiteLLM Router.
"""
import asyncio
from datetime import datetime
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

from .config import GatewayConfig, parse_apikeys_file
from .providers import PROVIDER_METADATA_REGISTRY, ProviderMetadata, find_provider_metadata
from .registry import LiveModelRegistry, ModelMetadata, model_registry

logger = logging.getLogger("gateway.discovery")


class ProviderStatus(BaseModel if "BaseModel" in globals() else object):
    pass


class DiscoveryService:
    """
    Asynchronous discovery service that auto-discovers models across 34+ providers,
    verifies accessibility, identifies free tiers, and syncs deployments with LiteLLM Router.
    """

    def __init__(self, config: GatewayConfig, registry: LiveModelRegistry = model_registry):
        self.config = config
        self.registry = registry
        self.provider_statuses: Dict[str, Dict[str, Any]] = {}
        self._is_running = False
        self._background_task: Optional[asyncio.Task] = None
        self._router_sync_callback: Optional[Any] = None
        self.last_sync_time: Optional[str] = None
        self.total_discovered_models: int = 0
        self.total_free_models: int = 0
        self.total_accessible_models: int = 0

    def set_router_sync_callback(self, callback: Any):
        """Sets callback to notify DynamicRouter when deployments are updated."""
        self._router_sync_callback = callback

    async def start(self):
        """Starts background periodic discovery loop."""
        if self._is_running:
            return
        self._is_running = True
        # Run initial discovery immediately
        asyncio.create_task(self.discover_all())
        # Schedule recurring background discovery
        self._background_task = asyncio.create_task(self._periodic_loop())

    async def stop(self):
        """Stops background discovery."""
        self._is_running = False
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass

    async def _periodic_loop(self):
        while self._is_running:
            try:
                await asyncio.sleep(self.config.discovery_interval_seconds)
                await self.discover_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic discovery loop: {e}", exc_info=True)
                await asyncio.sleep(30)

    async def discover_all(self) -> Dict[str, Any]:
        """
        Executes a complete discovery cycle across all configured providers.
        """
        logger.info("Starting unified dynamic model discovery cycle...")
        start_time = time.time()
        parsed_keys = parse_apikeys_file()

        # Merge environment variables for providers
        env_mappings = {
            "google": os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            "groq": os.getenv("GROQ_API_KEY"),
            "cerebras": os.getenv("CEREBRAS_API_KEY"),
            "openrouter": os.getenv("OPENROUTER_API_KEY"),
            "mistral": os.getenv("MISTRAL_API_KEY"),
            "nvidia": os.getenv("NVIDIA_API_KEY"),
            "openai": os.getenv("OPENAI_API_KEY"),
            "anthropic": os.getenv("ANTHROPIC_API_KEY"),
            "deepseek": os.getenv("DEEPSEEK_API_KEY"),
            "together": os.getenv("TOGETHER_API_KEY"),
        }
        for p_id, env_k in env_mappings.items():
            if env_k and (p_id not in parsed_keys or not parsed_keys[p_id]):
                parsed_keys[p_id] = env_k

        # Discover providers concurrently
        tasks = []
        timeout = httpx.Timeout(connect=3.5, read=12.0, write=5.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for prov_name, api_key in parsed_keys.items():
                meta = find_provider_metadata(prov_name)
                if not meta:
                    # Dynamically create metadata for unknown provider
                    norm_id = re.sub(r"[^a-z0-9_]+", "_", prov_name.lower().strip())
                    meta = ProviderMetadata(
                        id=norm_id,
                        name=prov_name,
                        base_url=f"https://api.{norm_id}.com/v1",
                        litellm_prefix="openai/",
                    )
                tasks.append(self._discover_single_provider(client, meta, api_key))

            # Include keyless providers from registry
            for spec_id, spec in PROVIDER_METADATA_REGISTRY.items():
                if spec.auth_type == "none" and spec.id not in [find_provider_metadata(k).id for k in parsed_keys if find_provider_metadata(k)]:
                    tasks.append(self._discover_single_provider(client, spec, None))

            results = await asyncio.gather(*tasks, return_exceptions=True)

        # Update summary counters
        all_models = self.registry.get_all()
        self.total_discovered_models = len(all_models)
        self.total_accessible_models = len([m for m in all_models if m.accessible])
        self.total_free_models = len([m for m in all_models if m.free and m.accessible])
        self.last_sync_time = datetime.utcnow().isoformat()
        elapsed = time.time() - start_time

        logger.info(
            f"Discovery complete in {elapsed:.2f}s: {self.total_discovered_models} total models, "
            f"{self.total_accessible_models} accessible, {self.total_free_models} free."
        )

        # Generate deployments and notify router
        deployments = self.build_litellm_deployments()
        if self._router_sync_callback:
            try:
                self._router_sync_callback(deployments)
            except Exception as e:
                logger.error(f"Error syncing deployments with LiteLLM Router: {e}")

        return {
            "timestamp": self.last_sync_time,
            "elapsed_seconds": round(elapsed, 2),
            "total_discovered": self.total_discovered_models,
            "total_accessible": self.total_accessible_models,
            "total_free": self.total_free_models,
            "provider_statuses": self.provider_statuses,
        }

    async def _discover_single_provider(
        self,
        client: httpx.AsyncClient,
        meta: ProviderMetadata,
        api_key: Optional[str]
    ):
        """Probes a single provider's /models endpoint to discover live models."""
        prov_id = meta.id
        status_info = {
            "id": prov_id,
            "name": meta.name,
            "base_url": meta.base_url,
            "has_key": bool(api_key) or meta.auth_type == "none",
            "auth_type": meta.auth_type,
            "status": "pending",
            "model_count": 0,
            "free_count": 0,
            "last_sync": datetime.utcnow().isoformat(),
            "error": None,
            "latency_ms": 0,
        }

        # Build auth headers
        headers = {
            "Accept": "application/json",
            "User-Agent": "Unified-Gateway-Discovery/2.0",
        }
        if meta.extra_headers:
            headers.update(meta.extra_headers)

        if api_key and meta.auth_type != "none":
            headers[meta.auth_header_name] = f"{meta.auth_header_prefix}{api_key}"

        models_url = f"{meta.base_url.rstrip('/')}/{meta.models_endpoint.lstrip('/')}" if meta.models_endpoint else None

        if not models_url:
            status_info["status"] = "no_endpoint"
            self.provider_statuses[prov_id] = status_info
            return

        t0 = time.time()
        try:
            resp = await client.get(models_url, headers=headers)
            latency_sec = time.time() - t0
            status_info["latency_ms"] = int(latency_sec * 1000)

            if resp.status_code in (200, 201):
                data = resp.json()
                discovered_models = self._extract_model_ids(prov_id, data)
                status_info["status"] = "connected"
                status_info["model_count"] = len(discovered_models)

                free_count = 0
                for model_id, model_raw in discovered_models:
                    is_free = self._detect_if_free(prov_id, model_id, model_raw)
                    if is_free:
                        free_count += 1
                    caps = self._detect_capabilities(prov_id, model_id, model_raw)
                    context_win = self._detect_context_window(model_raw)

                    # Register into live model registry
                    self.registry.register_or_update(
                        ModelMetadata(
                            provider=prov_id,
                            model=model_id,
                            accessible=True,
                            free=is_free,
                            latency=round(latency_sec, 3),
                            health=1.0,
                            capabilities=caps,
                            contextWindow=context_win,
                            lastSuccess=datetime.utcnow().isoformat(),
                        )
                    )

                status_info["free_count"] = free_count
            elif resp.status_code in (401, 403):
                status_info["status"] = "auth_failed"
                status_info["error"] = f"HTTP {resp.status_code}: Invalid or unauthorized API key"
            else:
                status_info["status"] = "http_error"
                status_info["error"] = f"HTTP {resp.status_code}: {resp.text[:120]}"

        except httpx.ConnectTimeout:
            status_info["status"] = "timeout"
            status_info["error"] = "Connection timed out"
        except httpx.ConnectError:
            status_info["status"] = "unreachable"
            status_info["error"] = "Provider host unreachable"
        except Exception as e:
            status_info["status"] = "error"
            status_info["error"] = str(e)[:150]

        self.provider_statuses[prov_id] = status_info

    def _extract_model_ids(self, provider_id: str, data: Any) -> List[Tuple[str, Dict[str, Any]]]:
        """Extracts model IDs and raw metadata from provider response."""
        results: List[Tuple[str, Dict[str, Any]]] = []

        if isinstance(data, dict):
            # Standard OpenAI format: {"data": [...]}
            if "data" in data and isinstance(data["data"], list):
                for item in data["data"]:
                    if isinstance(item, dict) and "id" in item:
                        results.append((str(item["id"]), item))
                    elif isinstance(item, str):
                        results.append((item, {}))
            # Ollama format: {"models": [...]}
            elif "models" in data and isinstance(data["models"], list):
                for item in data["models"]:
                    if isinstance(item, dict) and "name" in item:
                        results.append((str(item["name"]), item))
                    elif isinstance(item, str):
                        results.append((item, {}))
            # AI Horde / custom
            elif "models" in data and isinstance(data["models"], dict):
                for m_name, meta in data["models"].items():
                    results.append((str(m_name), meta if isinstance(meta, dict) else {}))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "id" in item:
                    results.append((str(item["id"]), item))
                elif isinstance(item, str):
                    results.append((item, {}))

        return results

    def _detect_if_free(self, provider_id: str, model_id: str, raw_meta: Dict[str, Any]) -> bool:
        """Determines if a model is accessible on free tier without per-token charges."""
        p_lower = provider_id.lower()
        m_lower = model_id.lower()

        # OpenRouter free models end with :free
        if ":free" in m_lower:
            return True

        # Check pricing in raw metadata if provided
        pricing = raw_meta.get("pricing", {})
        if isinstance(pricing, dict):
            prompt_cost = float(pricing.get("prompt", 0) or 0)
            completion_cost = float(pricing.get("completion", 0) or 0)
            if prompt_cost == 0.0 and completion_cost == 0.0:
                return True

        # Naturally free or community providers
        if p_lower in ("kilo", "ovh", "aihorde", "pollinations"):
            return True

        # Groq free tier
        if p_lower == "groq":
            return True

        # Cerebras free tier
        if p_lower == "cerebras":
            return True

        # Google Gemini AI Studio generous free tier
        if p_lower == "google" and any(k in m_lower for k in ("flash", "gemini-1.5", "gemini-2.0", "gemini-2.5")):
            return True

        # Mistral free tier / open-mistral
        if p_lower == "mistral" and ("open-mistral" in m_lower or "mistral-small" in m_lower):
            return True

        return False

    def _detect_capabilities(self, provider_id: str, model_id: str, raw_meta: Dict[str, Any]) -> List[str]:
        """Classifies capabilities: chat, coding, reasoning, vision, embedding, audio, image, fast."""
        caps: Set[str] = set()
        m_lower = model_id.lower()

        # Embedding filter
        if any(k in m_lower for k in ("embed", "embedding", "bge", "text-embedding")):
            caps.add("embedding")
            return list(caps)

        # Image generation filter
        if any(k in m_lower for k in ("dall-e", "flux", "stable-diffusion", "midjourney", "sdxl")):
            caps.add("image")
            return list(caps)

        # Audio filter
        if any(k in m_lower for k in ("whisper", "tts", "speech", "audio")):
            caps.add("audio")
            return list(caps)

        # Default for conversational models is chat
        caps.add("chat")

        # Coding capability
        if any(k in m_lower for k in ("coder", "code", "qwen-2.5-coder", "codestral", "deepseek-coder", "llama-code", "dev")):
            caps.add("coding")

        # Reasoning capability
        if any(k in m_lower for k in ("r1", "o1", "o3", "reasoning", "thinking", "qwq", "deepseek-r1", "70b", "pro")):
            caps.add("reasoning")

        # Fast capability (sub-second or lightweight)
        if any(k in m_lower for k in ("flash", "mini", "8b", "instant", "light", "haiku", "small", "turbo")):
            caps.add("fast")

        # Vision capability
        if any(k in m_lower for k in ("vision", "vl", "flash", "gpt-4o", "gemini", "claude-3")):
            caps.add("vision")

        return list(caps)

    def _detect_context_window(self, raw_meta: Dict[str, Any]) -> int:
        """Extracts context window from raw metadata or defaults to 128k."""
        if "context_length" in raw_meta:
            try:
                return int(raw_meta["context_length"])
            except Exception:
                pass
        if "context_window" in raw_meta:
            try:
                return int(raw_meta["context_window"])
            except Exception:
                pass
        return 128000

    def build_litellm_deployments(self) -> List[Dict[str, Any]]:
        """
        Builds deployment configuration dictionaries for LiteLLM Router.
        Registers candidates for logical modes:
        - 'auto'
        - 'fast'
        - 'smart'
        - 'coder'
        plus physical model passthroughs.
        """
        parsed_keys = parse_apikeys_file()
        deployments: List[Dict[str, Any]] = []

        # Map providers to api keys & base urls
        for mode in ("auto", "fast", "smart", "coder"):
            candidates = self.registry.get_candidates_for_mode(mode)
            for c in candidates[:15]:  # Top 15 candidates per logical mode
                meta = find_provider_metadata(c.provider)
                if not meta:
                    continue
                key = parsed_keys.get(c.provider) or parsed_keys.get(meta.name) or os.getenv(f"{c.provider.upper()}_API_KEY") or ""
                if not key and meta.auth_type != "none":
                    continue

                litellm_model = f"{meta.litellm_prefix}{c.model}" if not meta.litellm_prefix.endswith("/") else f"{meta.litellm_prefix}{c.model}"
                if meta.litellm_prefix == "openai/":
                    # Custom OpenAI compatible base
                    litellm_model = f"openai/{c.model}"

                deployments.append({
                    "model_name": mode,  # Logical alias (e.g. "auto", "coder")
                    "litellm_params": {
                        "model": litellm_model,
                        "api_key": key or "none",
                        "api_base": meta.base_url,
                        "rpm": meta.default_rpm_limit,
                        "tpm": meta.default_tpm_limit,
                        "timeout": self.config.request_timeout,
                    },
                    "model_info": {
                        "id": f"{c.provider}:{c.model}",
                        "mode": mode,
                        "provider": c.provider,
                        "physical_model": c.model,
                        "free": c.free,
                        "latency": c.latency,
                        "health": c.health,
                    }
                })

        # Also register accessible models by their direct names for passthrough
        for c in self.registry.filter(accessible_only=True)[:40]:
            meta = find_provider_metadata(c.provider)
            if not meta:
                continue
            key = parsed_keys.get(c.provider) or parsed_keys.get(meta.name) or os.getenv(f"{c.provider.upper()}_API_KEY") or ""
            if not key and meta.auth_type != "none":
                continue

            litellm_model = f"openai/{c.model}"
            deployments.append({
                "model_name": c.model,
                "litellm_params": {
                    "model": litellm_model,
                    "api_key": key or "none",
                    "api_base": meta.base_url,
                    "rpm": meta.default_rpm_limit,
                    "timeout": self.config.request_timeout,
                },
                "model_info": {
                    "id": f"{c.provider}:{c.model}",
                    "provider": c.provider,
                    "physical_model": c.model,
                    "free": c.free,
                }
            })

        logger.info(f"Generated {len(deployments)} LiteLLM deployment mappings.")
        return deployments
