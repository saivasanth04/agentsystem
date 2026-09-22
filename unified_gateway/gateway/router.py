"""
Dynamic LLM Routing Engine with Automated LiteLLM Free-Model Discovery,
Live /models Probing, Per-Model Quota Balancing, and Instant Multi-Provider Fallback.
"""
import asyncio
import json
import re
import time
from typing import Dict, List, Optional, Tuple, AsyncIterator, Any, Set
import httpx

try:
    import litellm
except ImportError:
    litellm = None

from .config import GatewayConfig, parse_apikeys_file
from .providers import PROVIDER_SPECS, ProviderSpec, find_provider_spec
from .quota_tracker import QuotaTracker, ModelQuotaState


# Keywords to filter out non-chat models from chat completion routing
NON_CHAT_KEYWORDS = (
    "embed", "embedding", "whisper", "tts", "moderation", "rerank",
    "clip", "dall-e", "stable-diffusion", "flux", "midjourney", "audio", "speech", "bge"
)


class ActiveProvider:
    def __init__(self, spec: ProviderSpec, api_key: Optional[str] = None):
        self.spec = spec
        self.api_key = api_key
        # Models available for this provider (dynamically populated)
        self.models: List[str] = list(spec.default_free_models)
        self.is_active: bool = True

    def get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.spec.extra_headers:
            headers.update(self.spec.extra_headers)

        if self.api_key and self.spec.api_key_header:
            headers[self.spec.api_key_header] = f"{self.spec.api_key_prefix}{self.api_key}"
        return headers

    def get_chat_url(self) -> str:
        base = self.spec.base_url.rstrip("/")
        endpoint = self.spec.chat_endpoint.lstrip("/")
        return f"{base}/{endpoint}"

    def get_models_url(self) -> Optional[str]:
        if not self.spec.models_endpoint:
            return None
        base = self.spec.base_url.rstrip("/")
        endpoint = self.spec.models_endpoint.lstrip("/")
        return f"{base}/{endpoint}"


class DynamicRouter:
    def __init__(self, config: GatewayConfig):
        self.config = config
        self.quota_tracker = QuotaTracker()
        self.active_providers: Dict[str, ActiveProvider] = {}
        # Configure fast connect timeout (2.5s) to bypass offline or slow DNS hosts instantly
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.5, read=config.request_timeout, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=25)
        )
        self._discovery_task: Optional[asyncio.Task] = None
        self._initialize_providers()

    def _initialize_providers(self):
        """Parses apikeys.txt and registers all providers with their initial models."""
        parsed_keys = parse_apikeys_file()
        
        for prov_raw, key in parsed_keys.items():
            spec = find_provider_spec(prov_raw)
            if spec:
                self.active_providers[spec.id] = ActiveProvider(spec=spec, api_key=key)
            else:
                # Fallback generic provider spec if not found in PROVIDER_SPECS
                norm_id = prov_raw.lower().replace(" ", "_").replace(".", "_")
                spec = ProviderSpec(
                    id=norm_id,
                    name=prov_raw,
                    base_url=f"https://api.{norm_id}.com/v1",
                    default_free_models=["default"],
                    base_priority=50
                )
                self.active_providers[norm_id] = ActiveProvider(spec=spec, api_key=key)

        # Ensure any specs that don't require keys are registered
        for spec_id, spec in PROVIDER_SPECS.items():
            if spec_id not in self.active_providers:
                if not spec.api_key_header or spec.api_key_header == "":
                    self.active_providers[spec_id] = ActiveProvider(spec=spec, api_key=None)

        # Pre-seed QuotaTracker with initial models
        for prov_id, provider in self.active_providers.items():
            for model_id in provider.models:
                self.quota_tracker.get_or_create(
                    prov_id, model_id, default_quota=provider.spec.default_model_token_quota
                )

        print(f"[*] Initialized {len(self.active_providers)} providers for dynamic routing.")

    async def start_background_discovery(self):
        """Discovers models from LiteLLM + live endpoints and runs periodic refresh."""
        self._discover_from_litellm()
        await self.discover_all_models()
        self._discovery_task = asyncio.create_task(self._discovery_loop())

    async def _discovery_loop(self):
        while True:
            try:
                await asyncio.sleep(self.config.discovery_interval_seconds)
                self._discover_from_litellm()
                await self.discover_all_models()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[!] Error during model discovery cycle: {e}")

    def _discover_from_litellm(self):
        """
        Dynamically extracts free models and provider-supported models from LiteLLM's database.
        Ensures zero-cost / free-tier models are registered for all matching providers.
        """
        if not litellm or not hasattr(litellm, "model_cost"):
            return

        try:
            model_cost_db = getattr(litellm, "model_cost", {})
            for model_name, info in model_cost_db.items():
                if not isinstance(info, dict):
                    continue

                litellm_provider = str(info.get("litellm_provider", "")).lower()
                input_cost = info.get("input_cost_per_token", 0.0)
                output_cost = info.get("output_cost_per_token", 0.0)
                is_free_tier = (input_cost == 0.0 and output_cost == 0.0) or ":free" in model_name or "free" in model_name

                # Clean model name if prefixed with provider
                clean_model_name = model_name
                if "/" in model_name and not model_name.startswith(("deepseek/", "meta-llama/", "google/", "qwen/", "mistralai/", "microsoft/")):
                    clean_model_name = model_name.split("/", 1)[-1]

                # Match against active providers
                for prov_id, provider in self.active_providers.items():
                    prov_spec = provider.spec
                    # Match if provider id or alias appears in litellm provider tag or model prefix
                    is_match = (
                        prov_id in litellm_provider
                        or any(alias.lower() in litellm_provider for alias in prov_spec.aliases)
                        or model_name.startswith(f"{prov_id}/")
                    )

                    if is_match:
                        # Skip obvious non-chat models
                        if any(kw in clean_model_name.lower() for kw in NON_CHAT_KEYWORDS):
                            continue

                        # Add model if it's free tier or belongs to provider
                        if clean_model_name not in provider.models:
                            provider.models.append(clean_model_name)
                            self.quota_tracker.get_or_create(
                                prov_id, clean_model_name, default_quota=prov_spec.default_model_token_quota
                            )

                    # For OpenRouter: dynamically register all free-tier openrouter models
                    if "openrouter" in prov_id and is_free_tier and ":free" in model_name:
                        if model_name not in provider.models:
                            provider.models.append(model_name)
                            self.quota_tracker.get_or_create(
                                prov_id, model_name, default_quota=prov_spec.default_model_token_quota
                            )

        except Exception as e:
            print(f"[!] Warning: LiteLLM model discovery encountered: {e}")

    async def discover_all_models(self):
        """Probes all active providers supporting discovery at their /models endpoints."""
        tasks = []
        for prov_id, provider in self.active_providers.items():
            if provider.spec.supports_models_discovery and provider.get_models_url():
                tasks.append(self._discover_provider_models(prov_id, provider))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _discover_provider_models(self, prov_id: str, provider: ActiveProvider):
        url = provider.get_models_url()
        if not url:
            return

        try:
            headers = provider.get_headers()
            # Fast timeout probe: 2.5s connect, 5.0s read
            resp = await self.client.get(
                url,
                headers=headers,
                timeout=httpx.Timeout(connect=2.5, read=5.0, write=5.0, pool=2.0)
            )
            if resp.status_code == 200:
                data = resp.json()
                discovered = []
                
                if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                    for item in data["data"]:
                        if isinstance(item, dict) and "id" in item:
                            m_id = str(item["id"]).strip()
                            if self._is_valid_chat_model(prov_id, m_id, item):
                                discovered.append(m_id)
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "id" in item:
                            m_id = str(item["id"]).strip()
                            if self._is_valid_chat_model(prov_id, m_id, item):
                                discovered.append(m_id)
                        elif isinstance(item, str):
                            if self._is_valid_chat_model(prov_id, item, {}):
                                discovered.append(item.strip())

                if discovered:
                    # Merge discovered models without duplicates
                    for model_id in discovered:
                        if model_id not in provider.models:
                            provider.models.append(model_id)
                        # Ensure tracked in QuotaTracker
                        self.quota_tracker.get_or_create(
                            prov_id, model_id, default_quota=provider.spec.default_model_token_quota
                        )
        except Exception:
            # Silent fallback to default models on timeout or connection error during background probe
            pass

    def _is_valid_chat_model(self, prov_id: str, model_id: str, raw_item: Dict[str, Any]) -> bool:
        """Filters out non-chat models (embeddings, whisper, tts, moderation)."""
        m_lower = model_id.lower()
        if any(kw in m_lower for kw in NON_CHAT_KEYWORDS):
            return False

        # OpenRouter filter for free models
        if "openrouter" in prov_id:
            # Check for :free suffix or free pricing in data
            if m_lower.endswith(":free"):
                return True
            pricing = raw_item.get("pricing", {})
            if isinstance(pricing, dict):
                prompt_cost = float(pricing.get("prompt", 1.0))
                if prompt_cost == 0.0:
                    return True
            return False

        return True

    def get_all_available_models(self) -> List[Dict[str, Any]]:
        """Returns list of all discovered and virtual models for the /v1/models endpoint."""
        model_set = set()
        model_list = [
            {"id": "auto", "object": "model", "owned_by": "unified-gateway", "description": "Dynamic Intelligent Auto-Routing (Best Health & Quota Balance)"},
            {"id": "fast", "object": "model", "owned_by": "unified-gateway", "description": "Low-latency Fast Models (Groq / Cerebras / Gemini Flash)"},
            {"id": "smart", "object": "model", "owned_by": "unified-gateway", "description": "High-reasoning Frontier Models (Llama 70B / DeepSeek R1 / Gemini Pro)"},
            {"id": "coder", "object": "model", "owned_by": "unified-gateway", "description": "Code Specialized Models (Qwen Coder / Codestral)"},
        ]
        
        for prov_id, provider in self.active_providers.items():
            for m in provider.models:
                if m not in model_set:
                    model_set.add(m)
                    model_list.append({
                        "id": m,
                        "object": "model",
                        "owned_by": provider.spec.name,
                        "provider_id": prov_id
                    })
        return model_list

    def _get_candidate_pairs(self, requested_model: str) -> List[Tuple[str, str, int]]:
        """
        Determines candidate (provider_id, model_id, base_priority) tuples based on requested model or virtual tier.
        """
        candidates: List[Tuple[str, str, int]] = []
        req = (requested_model or "auto").lower().strip()

        is_auto = req in ["auto", "default", "", "all", "unified"]
        is_fast = req in ["fast", "cheap_fast", "quick"]
        is_smart = req in ["smart", "reasoning", "frontier", "pro"]
        is_coder = req in ["coder", "coding", "code"]

        for prov_id, provider in self.active_providers.items():
            base_prio = provider.spec.base_priority

            for m in provider.models:
                m_lower = m.lower()

                if is_auto:
                    candidates.append((prov_id, m, base_prio))
                elif is_fast:
                    if any(f in m_lower for f in ["flash", "8b", "instant", "small", "mini", "fast", "groq", "cerebras", "lite", "nemo"]):
                        candidates.append((prov_id, m, base_prio + 25))
                    else:
                        candidates.append((prov_id, m, base_prio - 10))
                elif is_smart:
                    if any(s in m_lower for s in ["70b", "r1", "pro", "large", "nemotron", "core", "deepseek", "sonnet", "qwen3-235b"]):
                        candidates.append((prov_id, m, base_prio + 25))
                    else:
                        candidates.append((prov_id, m, base_prio - 10))
                elif is_coder:
                    if any(c in m_lower for c in ["code", "coder", "codestral", "qwen-coder", "deepseek-coder"]):
                        candidates.append((prov_id, m, base_prio + 25))
                    else:
                        candidates.append((prov_id, m, base_prio - 10))
                else:
                    # Direct match or partial match
                    if req == m_lower or req == m:
                        candidates.append((prov_id, m, base_prio + 60))
                    elif req in m_lower or m_lower in req:
                        candidates.append((prov_id, m, base_prio + 20))

        # If specific model requested but not matched anywhere, fallback gracefully to auto pool
        if not candidates and not is_auto:
            for prov_id, provider in self.active_providers.items():
                for m in provider.models:
                    candidates.append((prov_id, m, provider.spec.base_priority))

        return candidates

    async def execute_chat_completion(
        self,
        request_data: Dict[str, Any],
        is_streaming: bool = False
    ) -> Tuple[Any, str, str]:
        """
        Routes the request dynamically across providers & models with automatic fallback
        on quota exhaustion, rate limits (429), or connection errors.
        Returns (response_or_stream_generator, provider_id, model_id).
        """
        requested_model = request_data.get("model", "auto")
        candidates = self._get_candidate_pairs(requested_model)
        ranked = self.quota_tracker.get_ranked_candidates(candidates)

        if not ranked:
            raise RuntimeError("No active providers or models available to handle request.")

        # Interleave ranked list to ensure provider diversity and balanced quota consumption
        diverse_ranked = []
        provider_seen = {}
        deferred = []
        for item in ranked:
            p_id = item[0]
            count = provider_seen.get(p_id, 0)
            if count < 2:  # Up to 2 top models per provider in first pass
                diverse_ranked.append(item)
                provider_seen[p_id] = count + 1
            else:
                deferred.append(item)
        diverse_ranked.extend(deferred)

        last_error = None
        max_attempts = min(len(diverse_ranked), 15)

        for attempt_idx in range(max_attempts):
            prov_id, target_model, score, state = diverse_ranked[attempt_idx]
            provider = self.active_providers.get(prov_id)
            if not provider:
                continue

            # Clone request data and inject target model
            payload = dict(request_data)
            payload["model"] = target_model
            url = provider.get_chat_url()
            headers = provider.get_headers()

            start_time = time.time()
            try:
                if is_streaming:
                    req = self.client.build_request("POST", url, headers=headers, json=payload)
                    resp = await self.client.send(req, stream=True)
                    
                    if resp.status_code == 200:
                        latency_ms = (time.time() - start_time) * 1000.0
                        self.quota_tracker.record_success(prov_id, target_model, latency_ms=latency_ms)
                        return self._stream_response_generator(resp, prov_id, target_model), prov_id, target_model
                    else:
                        error_text = (await resp.aread()).decode("utf-8", errors="ignore")
                        await resp.aclose()
                        self.quota_tracker.record_error(prov_id, target_model, resp.status_code, error_text)
                        last_error = f"Provider {prov_id} (model {target_model}) HTTP {resp.status_code}: {error_text[:200]}"
                        continue
                else:
                    resp = await self.client.post(url, headers=headers, json=payload)
                    latency_ms = (time.time() - start_time) * 1000.0

                    if resp.status_code == 200:
                        data = resp.json()
                        usage = data.get("usage", {})
                        prompt_toks = usage.get("prompt_tokens", 0)
                        comp_toks = usage.get("completion_tokens", 0)
                        
                        # Estimate tokens if provider omitted usage metadata
                        if prompt_toks == 0 and comp_toks == 0:
                            content = ""
                            try:
                                content = data["choices"][0]["message"]["content"]
                            except Exception:
                                pass
                            prompt_str = str(payload.get("messages", ""))
                            prompt_toks = max(1, len(prompt_str) // 4)
                            comp_toks = max(1, len(content) // 4)

                        self.quota_tracker.record_success(
                            prov_id,
                            target_model,
                            prompt_tokens=prompt_toks,
                            completion_tokens=comp_toks,
                            latency_ms=latency_ms
                        )
                        
                        if isinstance(data, dict):
                            data["_gateway"] = {
                                "routed_provider": provider.spec.name,
                                "routed_provider_id": prov_id,
                                "routed_model": target_model,
                                "latency_ms": round(latency_ms, 2)
                            }
                        return data, prov_id, target_model
                    else:
                        error_text = resp.text
                        self.quota_tracker.record_error(prov_id, target_model, resp.status_code, error_text)
                        last_error = f"Provider {prov_id} (model {target_model}) HTTP {resp.status_code}: {error_text[:200]}"
                        continue

            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                # Fast connection failure handling: record connection error and fail over immediately
                self.quota_tracker.record_error(prov_id, target_model, 503, f"Connection/DNS timeout: {str(e)}")
                last_error = f"Provider {prov_id} unreachable: {str(e)}"
                continue
            except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
                self.quota_tracker.record_error(prov_id, target_model, 504, f"Read/Write timeout: {str(e)}")
                last_error = f"Provider {prov_id} timeout: {str(e)}"
                continue
            except Exception as e:
                self.quota_tracker.record_error(prov_id, target_model, 599, str(e))
                last_error = f"Provider {prov_id} error: {str(e)}"
                continue

        raise RuntimeError(f"All routed candidates failed. Last error: {last_error}")

    async def _stream_response_generator(
        self,
        response: httpx.Response,
        prov_id: str,
        model_id: str
    ) -> AsyncIterator[bytes]:
        """Yields SSE chunks from provider stream and ensures connection cleanup."""
        total_chars = 0
        stream_success = False
        try:
            async for chunk in response.aiter_bytes():
                if chunk:
                    total_chars += len(chunk)
                    yield chunk
            stream_success = True
        except Exception as e:
            self.quota_tracker.record_error(prov_id, model_id, 599, f"Streaming error: {str(e)}")
            raise
        finally:
            await response.aclose()
            if stream_success:
                estimated_tokens = max(1, total_chars // 4)
                self.quota_tracker.record_success(
                    prov_id,
                    model_id,
                    prompt_tokens=50,
                    completion_tokens=estimated_tokens,
                    latency_ms=300.0
                )

    async def close(self):
        if self._discovery_task:
            self._discovery_task.cancel()
        await self.client.aclose()
