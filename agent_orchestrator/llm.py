"""
LLM Client Wrapper for Unified LLM Gateway / OpenAI-Compatible Endpoints.
"""
import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Type, Union
from openai import OpenAI
from .config import config
from .routing.model_router import model_router
from .runtime.json_repair import loads_repaired, repair_json
from .runtime.structured_output import StructuredOutputEngine, StructuredOutputResult, StructuredOutputError


class ConcurrencyThrottler:
    """
    Thread-safe concurrency limiter and rate-limit backoff manager.
    Limits simultaneous active requests and handles HTTP 429 backoff gracefully.
    """

    def __init__(self, max_concurrency: int = 5, requests_per_minute: int = 120):
        self.max_concurrency = max_concurrency
        self.requests_per_minute = requests_per_minute
        self.semaphore = threading.Semaphore(max_concurrency)
        self.lock = threading.Lock()
        self._last_request_times: List[float] = []

    def acquire(self):
        return self.__enter__()

    def release(self):
        return self.__exit__(None, None, None)

    def __enter__(self):
        self.semaphore.acquire()
        with self.lock:
            now = time.time()
            self._last_request_times = [t for t in self._last_request_times if now - t < 60.0]
            if len(self._last_request_times) >= self.requests_per_minute:
                sleep_time = 60.0 - (now - self._last_request_times[0]) + 0.1
                if sleep_time > 0:
                    time.sleep(sleep_time)
            self._last_request_times.append(time.time())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.semaphore.release()

    def get_backoff_delay(self, attempt: int) -> float:
        return min(30.0, (2 ** attempt) * 1.0)


class LLMClient:
    def __init__(
        self,
        config: Optional[Any] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        max_concurrency: int = 5,
        retry_policy: Optional[Any] = None,
        provider_pool: Optional[Any] = None,
        default_seed: Optional[int] = None,
        **kwargs: Any,
    ):
        active_config = config or kwargs.get("cfg") or globals().get("config")
        self.config = active_config
        self.api_key = api_key or (active_config.api_key if active_config else "")
        from .security.secrets import secret_manager
        if self.api_key:
            secret_manager.register_secret(self.api_key)
        self.base_url = base_url or (active_config.base_url if active_config else "http://127.0.0.1:8000/v1")
        self.default_model = default_model or (active_config.default_model if active_config else "auto")
        import httpx
        self.default_seed = default_seed
        timeout_sec = getattr(active_config, "timeout_seconds", 90.0) if active_config else 90.0
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=2.5, read=timeout_sec, write=10.0, pool=5.0)
        )
        self.throttler = ConcurrencyThrottler(max_concurrency=max_concurrency)

        from .resilience import LLMRetryPolicy, ProviderFailoverPool, ResilientLLMCaller
        self.retry_policy = retry_policy or LLMRetryPolicy(
            max_retries=getattr(active_config, "llm_max_retries", 3) if active_config else 3,
            base_delay=getattr(active_config, "llm_base_delay", 0.5) if active_config else 0.5,
            max_delay=getattr(active_config, "llm_max_delay", 30.0) if active_config else 30.0,
        )
        if provider_pool:
            self.provider_pool = provider_pool
        else:
            self.provider_pool = ProviderFailoverPool(
                primary_base_url=self.base_url,
                primary_api_key=self.api_key,
                primary_name="primary",
            )
            fallback_url = getattr(active_config, "fallback_base_url", "") if active_config else ""
            fallback_k = getattr(active_config, "fallback_api_key", "") if active_config else ""
            if fallback_url and fallback_k:
                self.provider_pool.add_fallback_endpoint(
                    name="secondary",
                    base_url=fallback_url,
                    api_key=fallback_k,
                )
            
            # Auto-register direct provider fallback endpoints from apikeys.txt for failover resilience
            try:
                from pathlib import Path
                apikeys_path = Path(__file__).resolve().parent.parent / "unified_gateway" / "apikeys.txt"
                if apikeys_path.exists():
                    from unified_gateway.gateway.config import parse_apikeys_file
                    from unified_gateway.gateway.providers import find_provider_spec
                    keys = parse_apikeys_file(apikeys_path)
                    for prov_name, key_val in keys.items():
                        if not key_val:
                            continue
                        spec = find_provider_spec(prov_name)
                        if spec and spec.id in ("groq", "cerebras", "google", "mistral", "openrouter", "nvidia"):
                            self.provider_pool.add_fallback_endpoint(
                                name=f"direct_{spec.id}",
                                base_url=spec.base_url,
                                api_key=key_val,
                                models=spec.default_free_models
                            )
            except Exception:
                pass

        self.resilient_caller = ResilientLLMCaller(
            throttler=self.throttler,
            retry_policy=self.retry_policy,
            provider_pool=self.provider_pool,
            timeout=timeout_sec,
        )
        self.last_system_fingerprint: Optional[str] = None

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[Union[str, Any]] = None,
        temperature: float = 0.3,
        response_format: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> str:
        """
        Send a chat completion request to the LLM Gateway with concurrency throttling,
        intelligent model resolution, and automatic fallback failover.
        """
        primary_model = model_router.resolve_model(model or self.default_model)
        candidate_models = [primary_model]
        fallback = model_router.get_fallback(primary_model)
        if fallback and fallback not in candidate_models:
            candidate_models.append(fallback)

        kwargs: Dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
        }
        effective_seed = seed if seed is not None else self.default_seed
        if effective_seed is not None:
            kwargs["seed"] = effective_seed

        if isinstance(response_format, dict):
            kwargs["response_format"] = response_format
        elif response_format == "json_object":
            kwargs["response_format"] = {"type": "json_object"}

        response = self.resilient_caller.execute(
            candidate_models=candidate_models,
            base_kwargs=kwargs,
            fallback_client=self.client,
        )
        if hasattr(response, "system_fingerprint"):
            self.last_system_fingerprint = response.system_fingerprint
        content = response.choices[0].message.content or ""
        return content


    def chat_json(
        self,
        messages: List[Dict[str, str]],
        schema: Optional[Type[Any]] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_retries: int = 2,
        response_format: Optional[Union[str, Dict[str, Any]]] = None,
        raise_on_failure: bool = False,
        seed: Optional[int] = None,
    ) -> Union[Dict[str, Any], Any]:
        """
        Send a chat completion request and parse output as JSON or strongly-typed Pydantic model.
        Features 5-stage pipeline:
        1. Schema-constrained generation (optional response_format)
        2. Algorithmic dirty-JSON repair (quotes, trailing commas, literals, unclosed brackets)
        3. Pydantic schema validation & coercion
        4. Corrective feedback retry loop
        5. Explicit failure handling
        """
        rf_dict: Optional[Dict[str, Any]] = None
        if isinstance(response_format, dict):
            rf_dict = response_format
        elif response_format == "json_object":
            rf_dict = {"type": "json_object"}

        def _caller(msgs: List[Dict[str, Any]], kwargs: Dict[str, Any]) -> str:
            call_model = kwargs.get("model") or model
            call_temp = kwargs.get("temperature", temperature)
            call_rf = kwargs.get("response_format") or rf_dict
            call_seed = kwargs.get("seed") if "seed" in kwargs else seed
            return self.chat(msgs, model=call_model, temperature=call_temp, response_format=call_rf, seed=call_seed)

        res = StructuredOutputEngine.execute(
            llm_caller=_caller,
            messages=messages,
            schema=schema,
            model=model,
            temperature=temperature,
            max_retries=max_retries,
            response_format=rf_dict,
            raise_on_failure=raise_on_failure,
        )

        if res.success:
            return res.data

        if raise_on_failure:
            res.unwrap()

        # Backward compatibility fallback
        if schema:
            return {
                "_schema_error": True,
                "_parse_error": True,
                "error": res.error,
                "validation_errors": res.validation_errors,
                "raw_output": res.raw_text,
            }
        return {
            "raw_output": res.raw_text,
            "parse_error": res.error or "Could not parse JSON directly",
            "_parse_error": True,
            "validation_errors": res.validation_errors,
        }

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """
        Extract and parse JSON object from raw response text.
        Applies algorithmic repair for trailing commas, quotes, Python literals, and unclosed brackets.
        """
        if not text:
            return {"raw_output": "", "parse_error": "Empty response", "_parse_error": True}
        try:
            res = loads_repaired(text)
            if isinstance(res, dict):
                return res
            elif isinstance(res, list):
                return res
            return {"data": res}
        except Exception as e:
            return {
                "raw_output": text,
                "parse_error": f"Could not parse JSON directly: {str(e)}",
                "_parse_error": True,
            }


    def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = "auto",
        model: Optional[Union[str, Any]] = None,
        temperature: float = 0.2,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Send a chat completion request with native OpenAI-compatible tool specifications,
        intelligent model resolution, and automatic fallback failover.
        Returns parsed content and structured tool calls.
        """
        primary_model = model_router.resolve_model(model or self.default_model)
        candidate_models = [primary_model]
        fallback = model_router.get_fallback(primary_model)
        if fallback and fallback not in candidate_models:
            candidate_models.append(fallback)

        kwargs: Dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
        }
        effective_seed = seed if seed is not None else self.default_seed
        if effective_seed is not None:
            kwargs["seed"] = effective_seed

        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice

        response = self.resilient_caller.execute(
            candidate_models=candidate_models,
            base_kwargs=kwargs,
            fallback_client=self.client,
        )
        if hasattr(response, "system_fingerprint"):
            self.last_system_fingerprint = response.system_fingerprint


        choice = response.choices[0]
        msg = choice.message

        tool_calls_data = []
        if getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                parsed_args = {}
                raw_args = getattr(tc.function, "arguments", "{}") or "{}"
                try:
                    parsed_args = loads_repaired(raw_args)
                    if not isinstance(parsed_args, dict):
                        parsed_args = {"data": parsed_args}
                except Exception:
                    parsed_args = self._extract_json(raw_args)

                tool_calls_data.append({
                    "id": tc.id,
                    "type": getattr(tc, "type", "function"),
                    "name": tc.function.name,
                    "arguments": parsed_args,
                    "raw_arguments": raw_args,
                })

        ret_message: Dict[str, Any] = {
            "role": "assistant",
            "content": msg.content or "",
        }
        if getattr(msg, "tool_calls", None):
            ret_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": getattr(tc, "type", "function"),
                    "function": {
                        "name": tc.function.name,
                        "arguments": getattr(tc.function, "arguments", "{}") or "{}",
                    }
                }
                for tc in msg.tool_calls
            ]

        return {
            "content": msg.content or "",
            "tool_calls": tool_calls_data,
            "finish_reason": choice.finish_reason,
            "message": ret_message,
        }


# Global default client
default_llm = LLMClient()
