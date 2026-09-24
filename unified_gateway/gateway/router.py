"""
True LiteLLM-Powered Intelligent Dynamic Router.
100% metadata-driven: delegates routing, load balancing, latency tracking,
retries, and fallbacks directly to native LiteLLM Router.
Maintains live routing observability traces for the Enterprise Admin Platform.
"""
import asyncio
from datetime import datetime
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
import uuid

import litellm
from litellm import Router
from pydantic import BaseModel, Field

from .config import GatewayConfig
from .discovery import DiscoveryService
from .providers import find_provider_metadata
from .quota_tracker import QuotaTracker
from .registry import LiveModelRegistry, ModelMetadata, model_registry

logger = logging.getLogger("gateway.router")

# Silence noisy external litellm stdout unless error
litellm.suppress_debug_info = True


class RoutingTrace(BaseModel):
    trace_id: str = Field(default_factory=lambda: f"trace-{uuid.uuid4().hex[:8]}")
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    logical_mode: str
    candidates_count: int
    candidates_sample: List[str] = Field(default_factory=list)
    selected_provider: str = "unknown"
    selected_model: str = "unknown"
    status: str = "SUCCESS"  # "SUCCESS", "RETRY", "FALLBACK", "ERROR"
    latency_ms: int = 0
    tokens_used: int = 0
    retries: int = 0
    fallbacks_taken: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class DynamicRouter:
    """
    Production-grade LiteLLM Router manager.
    Dynamic discovery feeds deployments into LiteLLM Router without server restarts.
    """

    def __init__(self, config: GatewayConfig, registry: LiveModelRegistry = model_registry):
        self.config = config
        self.registry = registry
        self.quota_tracker = QuotaTracker()
        self.discovery_service = DiscoveryService(config=config, registry=registry)
        self.traces: List[RoutingTrace] = []
        self._trace_lock = asyncio.Lock()

        # Initialize LiteLLM Router
        self.litellm_router = Router(
            model_list=[],
            routing_strategy="latency-based-routing",  # Native LiteLLM latency-based load balancing
            num_retries=config.max_retries_per_request,
            timeout=config.request_timeout,
            allowed_fails=3,
            cooldown_time=60,
            fallbacks=[
                {"fast": ["auto"]},
                {"coder": ["smart", "auto"]},
                {"smart": ["coder", "auto"]},
            ],
            enable_health_check_routing=True,
        )

        # Connect discovery service callback to sync deployments dynamically
        self.discovery_service.set_router_sync_callback(self.sync_deployments)

    def sync_deployments(self, deployments: List[Dict[str, Any]]):
        """Dynamically registers runtime deployments into LiteLLM Router."""
        if not deployments:
            logger.warning("No deployments provided to LiteLLM router sync.")
            return

        try:
            self.litellm_router.set_model_list(deployments)
            logger.info(f"LiteLLM Router synchronized with {len(deployments)} deployments.")
        except Exception as e:
            logger.error(f"Failed to set model list in LiteLLM router: {e}", exc_info=True)

    async def start_background_discovery(self):
        """Starts discovery service in background."""
        await self.discovery_service.start()

    async def close(self):
        """Shuts down background discovery."""
        await self.discovery_service.stop()

    def get_all_available_models(self) -> List[Dict[str, Any]]:
        """Returns models for /v1/models endpoint."""
        now = int(time.time())
        # First include 4 logical modes
        results = [
            {"id": "auto", "object": "model", "created": now, "owned_by": "gateway", "permission": []},
            {"id": "fast", "object": "model", "created": now, "owned_by": "gateway", "permission": []},
            {"id": "smart", "object": "model", "created": now, "owned_by": "gateway", "permission": []},
            {"id": "coder", "object": "model", "created": now, "owned_by": "gateway", "permission": []},
        ]
        # Then append discovered accessible models
        for m in self.registry.filter(accessible_only=True):
            results.append({
                "id": m.model,
                "object": "model",
                "created": now,
                "owned_by": m.provider,
                "capabilities": m.capabilities,
                "free": m.free,
                "latency_sec": m.latency,
                "health": m.health,
            })
        return results

    async def record_trace(self, trace: RoutingTrace):
        async with self._trace_lock:
            self.traces.insert(0, trace)
            if len(self.traces) > 200:
                self.traces.pop()

    def get_recent_traces(self, limit: int = 50) -> List[RoutingTrace]:
        return self.traces[:limit]

    async def execute_chat_completion(
        self,
        request_data: Dict[str, Any],
        is_streaming: bool = False
    ) -> Tuple[Any, str, str]:
        """
        Executes chat completion via LiteLLM Router.
        Returns: (result_or_stream_generator, routed_provider, routed_model)
        """
        raw_model = request_data.get("model", "auto")
        mode = (raw_model or "auto").strip()

        # Capture candidates from registry for observability
        candidates = self.registry.get_candidates_for_mode(mode)
        candidate_names = [f"{c.provider}/{c.model}" for c in candidates[:6]]

        t0 = time.time()
        routed_provider = "unknown"
        routed_model = "unknown"
        tokens_used = 0
        status = "SUCCESS"
        error_msg = None
        retries = 0

        # Build clean kwargs for LiteLLM
        kwargs = dict(request_data)
        messages = kwargs.pop("messages", [])

        # Clean non-standard LiteLLM parameters if present
        for key in ("provider", "tier", "api_key", "workspace_path"):
            kwargs.pop(key, None)

        if is_streaming:
            # Handle streaming
            try:
                response_stream = await self.litellm_router.acompletion(
                    model=mode,
                    messages=messages,
                    stream=True,
                    **kwargs
                )

                async def stream_generator() -> AsyncIterator[str]:
                    nonlocal routed_provider, routed_model, tokens_used, status, error_msg
                    try:
                        async for chunk in response_stream:
                            # Extract model/provider metadata from chunk if available
                            if hasattr(chunk, "model") and chunk.model:
                                routed_model = str(chunk.model)
                            if hasattr(chunk, "_hidden_params") and isinstance(chunk._hidden_params, dict):
                                routed_provider = chunk._hidden_params.get("custom_llm_provider", routed_provider)

                            chunk_json = chunk.model_dump_json() if hasattr(chunk, "model_dump_json") else json.dumps(chunk)
                            yield f"data: {chunk_json}\n\n"
                        yield "data: [DONE]\n\n"
                    except Exception as stream_err:
                        status = "ERROR"
                        error_msg = str(stream_err)
                        logger.error(f"Streaming error in LiteLLM router: {stream_err}")
                        yield f"data: {json.dumps({'error': {'message': str(stream_err)}})}\n\n"
                    finally:
                        elapsed_ms = int((time.time() - t0) * 1000)
                        await self.record_trace(
                            RoutingTrace(
                                logical_mode=mode,
                                candidates_count=len(candidates),
                                candidates_sample=candidate_names,
                                selected_provider=routed_provider,
                                selected_model=routed_model,
                                status=status,
                                latency_ms=elapsed_ms,
                                tokens_used=tokens_used,
                                error=error_msg,
                            )
                        )

                return stream_generator(), routed_provider, routed_model

            except Exception as e:
                elapsed_ms = int((time.time() - t0) * 1000)
                await self.record_trace(
                    RoutingTrace(
                        logical_mode=mode,
                        candidates_count=len(candidates),
                        candidates_sample=candidate_names,
                        selected_provider=routed_provider,
                        selected_model=routed_model,
                        status="ERROR",
                        latency_ms=elapsed_ms,
                        error=str(e),
                    )
                )
                raise e

        else:
            # Handle non-streaming
            try:
                response = await self.litellm_router.acompletion(
                    model=mode,
                    messages=messages,
                    stream=False,
                    **kwargs
                )

                elapsed_ms = int((time.time() - t0) * 1000)

                # Parse response properties
                if hasattr(response, "model"):
                    routed_model = str(response.model)
                if hasattr(response, "_hidden_params") and isinstance(response._hidden_params, dict):
                    routed_provider = response._hidden_params.get("custom_llm_provider", routed_provider)
                elif "/" in routed_model:
                    routed_provider = routed_model.split("/")[0]

                if hasattr(response, "usage") and response.usage:
                    tokens_used = getattr(response.usage, "total_tokens", 0)

                resp_dict = response.model_dump() if hasattr(response, "model_dump") else dict(response)

                # Update registry and quota metrics
                self.registry.record_call_outcome(
                    provider=routed_provider,
                    model=routed_model,
                    latency=round(elapsed_ms / 1000.0, 3),
                    success=True,
                    tokens_used=tokens_used,
                )
                self.quota_tracker.record_usage(
                    provider_id=routed_provider,
                    tokens=tokens_used,
                    is_error=False,
                )

                # Record inspection trace
                await self.record_trace(
                    RoutingTrace(
                        logical_mode=mode,
                        candidates_count=len(candidates),
                        candidates_sample=candidate_names,
                        selected_provider=routed_provider,
                        selected_model=routed_model,
                        status="SUCCESS",
                        latency_ms=elapsed_ms,
                        tokens_used=tokens_used,
                    )
                )

                return resp_dict, routed_provider, routed_model

            except Exception as e:
                elapsed_ms = int((time.time() - t0) * 1000)
                self.registry.record_call_outcome(
                    provider=routed_provider,
                    model=routed_model,
                    latency=round(elapsed_ms / 1000.0, 3),
                    success=False,
                    error_msg=str(e),
                )
                self.quota_tracker.record_usage(
                    provider_id=routed_provider,
                    is_error=True,
                )
                await self.record_trace(
                    RoutingTrace(
                        logical_mode=mode,
                        candidates_count=len(candidates),
                        candidates_sample=candidate_names,
                        selected_provider=routed_provider,
                        selected_model=routed_model,
                        status="ERROR",
                        latency_ms=elapsed_ms,
                        error=str(e),
                    )
                )
                raise e
