"""
Dynamic LLM Routing Engine with Automated Model Discovery and Multi-Provider Fallback.
"""
import asyncio
import json
import time
from typing import Dict, List, Optional, Tuple, AsyncIterator, Any
import httpx

from .config import GatewayConfig, parse_apikeys_file
from .providers import PROVIDER_SPECS, ProviderSpec, find_provider_spec
from .quota_tracker import QuotaTracker, ModelQuotaState


class ActiveProvider:
    def __init__(self, spec: ProviderSpec, api_key: Optional[str] = None):
        self.spec = spec
        self.api_key = api_key
        # Models available for this provider (initially default free models, expanded via discovery)
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
        self.client = httpx.AsyncClient(timeout=config.request_timeout)
        self._discovery_task: Optional[asyncio.Task] = None
        self._initialize_providers()

    def _initialize_providers(self):
        """Parses apikeys.txt and initializes all 32 providers."""
        parsed_keys = parse_apikeys_file()
        
        for prov_raw, key in parsed_keys.items():
            spec = find_provider_spec(prov_raw)
            if spec:
                self.active_providers[spec.id] = ActiveProvider(spec=spec, api_key=key)
            else:
                # Fallback generic provider spec if not found
                norm_id = prov_raw.lower().replace(" ", "_").replace(".", "_")
                spec = ProviderSpec(
                    id=norm_id,
                    name=prov_raw,
                    base_url=f"https://api.{norm_id}.com/v1",
                    default_free_models=["default"],
                    base_priority=50
                )
                self.active_providers[norm_id] = ActiveProvider(spec=spec, api_key=key)

        # Ensure any specs that don't need keys are included if not present
        for spec_id, spec in PROVIDER_SPECS.items():
            if spec_id not in self.active_providers:
                # Check if spec needs no key or if we should register it
                if not spec.api_key_header:
                    self.active_providers[spec_id] = ActiveProvider(spec=spec, api_key=None)

        print(f"[*] Initialized {len(self.active_providers)} providers for dynamic routing.")

    async def start_background_discovery(self):
        """Starts periodic model discovery in the background."""
        await self.discover_all_models()
        self._discovery_task = asyncio.create_task(self._discovery_loop())

    async def _discovery_loop(self):
        while True:
            try:
                await asyncio.sleep(self.config.discovery_interval_seconds)
                await self.discover_all_models()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[!] Error during model discovery cycle: {e}")

    async def discover_all_models(self):
        """Discovers dynamically available free models from providers' /models endpoints."""
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
            resp = await self.client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                discovered = []
                if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                    for item in data["data"]:
                        if isinstance(item, dict) and "id" in item:
                            model_id = item["id"]
                            # Filter for free/standard models if openrouter or similar
                            if "openrouter" in prov_id:
                                if ":free" in model_id.lower():
                                    discovered.append(model_id)
                            else:
                                discovered.append(model_id)
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "id" in item:
                            discovered.append(item["id"])
                        elif isinstance(item, str):
                            discovered.append(item)

                if discovered:
                    # Merge discovered models with defaults without duplicates
                    combined = list(dict.fromkeys(provider.spec.default_free_models + discovered))
                    provider.models = combined
        except Exception:
            # Silent fallback to default free models on network error during probe
            pass

    def get_all_available_models(self) -> List[Dict[str, Any]]:
        """Returns list of models for the /v1/models endpoint."""
        model_set = set()
        model_list = [
            {"id": "auto", "object": "model", "owned_by": "unified-gateway", "description": "Dynamic Intelligent Auto-Routing (Best Health & Quota)"},
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
        Determines candidate (provider_id, model_id, base_priority) tuples based on requested model.
        """
        candidates: List[Tuple[str, str, int]] = []
        req = requested_model.lower().strip()

        is_auto = req in ["auto", "default", "", "all", "unified"]
        is_fast = req == "fast"
        is_smart = req == "smart"
        is_coder = req == "coder"

        for prov_id, provider in self.active_providers.items():
            base_prio = provider.spec.base_priority

            for m in provider.models:
                m_lower = m.lower()

                if is_auto:
                    candidates.append((prov_id, m, base_prio))
                elif is_fast:
                    if any(f in m_lower for f in ["flash", "8b", "instant", "small", "mini", "fast", "groq", "cerebras"]):
                        candidates.append((prov_id, m, base_prio + 20))
                elif is_smart:
                    if any(s in m_lower for s in ["70b", "r1", "pro", "large", "nemotron", "core"]):
                        candidates.append((prov_id, m, base_prio + 20))
                elif is_coder:
                    if any(c in m_lower for c in ["code", "coder", "codestral"]):
                        candidates.append((prov_id, m, base_prio + 20))
                else:
                    # Direct match or partial match
                    if req == m_lower or req == m:
                        candidates.append((prov_id, m, base_prio + 50))
                    elif req in m_lower or m_lower in req:
                        candidates.append((prov_id, m, base_prio + 10))

        # If specific model requested but not matched anywhere, fallback to auto
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
        Routes the request dynamically across providers & models with automatic fallback on quota exhaustion / errors.
        Returns (response_or_stream_generator, provider_id, model_id).
        """
        requested_model = request_data.get("model", "auto")
        candidates = self._get_candidate_pairs(requested_model)
        ranked = self.quota_tracker.get_ranked_candidates(candidates)

        if not ranked:
            raise RuntimeError("No active providers or models available to handle request.")

        # Interleave ranked list to ensure provider diversity and avoid sticking to one provider
        diverse_ranked = []
        provider_seen = {}
        deferred = []
        for item in ranked:
            p_id = item[0]
            count = provider_seen.get(p_id, 0)
            if count < 2:  # Allow up to 2 top models per provider in primary pass
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

            # Clone request data and inject targeted model
            payload = dict(request_data)
            payload["model"] = target_model
            url = provider.get_chat_url()
            headers = provider.get_headers()

            start_time = time.time()
            try:
                if is_streaming:
                    # Prepare stream request
                    req = self.client.build_request("POST", url, headers=headers, json=payload)
                    resp = await self.client.send(req, stream=True)
                    
                    if resp.status_code == 200:
                        latency_ms = (time.time() - start_time) * 1000.0
                        self.quota_tracker.record_success(prov_id, target_model, latency_ms=latency_ms)
                        
                        # Return streaming iterator generator
                        return self._stream_response_generator(resp, prov_id, target_model), prov_id, target_model
                    else:
                        error_text = (await resp.aread()).decode("utf-8", errors="ignore")
                        await resp.aclose()
                        self.quota_tracker.record_error(prov_id, target_model, resp.status_code, error_text)
                        last_error = f"Provider {prov_id} (model {target_model}) HTTP {resp.status_code}: {error_text}"
                        continue
                else:
                    # Non-streaming request
                    resp = await self.client.post(url, headers=headers, json=payload)
                    latency_ms = (time.time() - start_time) * 1000.0

                    if resp.status_code == 200:
                        data = resp.json()
                        usage = data.get("usage", {})
                        prompt_toks = usage.get("prompt_tokens", 0)
                        comp_toks = usage.get("completion_tokens", 0)
                        
                        # Estimate tokens if provider did not supply usage
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
                        
                        # Decorate response with routed gateway metadata
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
                        last_error = f"Provider {prov_id} (model {target_model}) HTTP {resp.status_code}: {error_text}"
                        continue

            except Exception as e:
                self.quota_tracker.record_error(prov_id, target_model, 599, str(e))
                last_error = f"Provider {prov_id} connection error: {str(e)}"
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
