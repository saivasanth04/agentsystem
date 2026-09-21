"""
Resilient LLM Execution Engine.
Orchestrates error classification, header-aware rate-limiting, full-jitter backoff,
multi-provider endpoint failover, and reactive context overflow compaction.
"""
import copy
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from openai import OpenAI

from .error_classifier import classify_error, LLMErrorCategory
from .retry_policy import LLMRetryPolicy
from .provider_pool import ProviderFailoverPool, ProviderEndpoint

logger = logging.getLogger("orchestrator.resilience")


class ResilientLLMCaller:
    """
    Unified resilience executor for OpenAI-compatible chat completion calls.
    """

    def __init__(
        self,
        throttler: Any,
        retry_policy: Optional[LLMRetryPolicy] = None,
        provider_pool: Optional[ProviderFailoverPool] = None,
        timeout: float = 90.0,
    ):
        self.throttler = throttler
        self.retry_policy = retry_policy or LLMRetryPolicy()
        self.provider_pool = provider_pool
        self.timeout = timeout

    def execute(
        self,
        candidate_models: List[str],
        base_kwargs: Dict[str, Any],
        fallback_client: Optional[OpenAI] = None,
        on_compact_callback: Optional[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]] = None,
    ) -> Any:
        """
        Executes chat completion with full resilience guarantees across candidate models
        and provider endpoints.
        """
        endpoints = self.provider_pool.get_candidate_endpoints() if self.provider_pool else [None]
        last_exception: Optional[Exception] = None

        for endpoint in endpoints:
            if endpoint is None or getattr(endpoint, "name", None) == "primary":
                client = fallback_client or (
                    self.provider_pool.get_client_for_endpoint(endpoint, timeout=self.timeout)
                    if self.provider_pool
                    else None
                )
            else:
                client = (
                    self.provider_pool.get_client_for_endpoint(endpoint, timeout=self.timeout)
                    if self.provider_pool
                    else fallback_client
                )
            if client is None:
                continue

            for model_name in candidate_models:
                # Shallow copy kwargs and set model
                kwargs = copy.copy(base_kwargs)
                kwargs["model"] = model_name

                attempt = 0
                while attempt < self.retry_policy.max_retries:
                    self.throttler.acquire()
                    try:
                        response = client.chat.completions.create(**kwargs)
                        if self.provider_pool and endpoint:
                            self.provider_pool.mark_success(endpoint.name)
                        return response

                    except Exception as exc:
                        last_exception = exc
                        category, retry_after = classify_error(exc)

                        # Record retry event in active tracing span if available
                        try:
                            from ..tracing import get_current_span
                            span = get_current_span()
                            if span:
                                span.add_event(
                                    "llm_retry",
                                    {
                                        "attempt": attempt + 1,
                                        "category": category.value,
                                        "model": model_name,
                                        "endpoint": endpoint.name if endpoint else "default",
                                        "error": str(exc)[:200],
                                    },
                                )
                        except Exception:
                            pass

                        # 1. Reactive Context Overflow Handling
                        if category == LLMErrorCategory.CONTEXT_OVERFLOW:
                            messages = kwargs.get("messages", [])
                            if on_compact_callback:
                                compacted_msgs = on_compact_callback(messages)
                            else:
                                # Fallback built-in reactive compaction: truncate middle observations
                                compacted_msgs = self._reactive_compact(messages)

                            if len(compacted_msgs) < len(messages) or any(
                                len(str(m.get("content", ""))) < len(str(orig.get("content", "")))
                                for m, orig in zip(compacted_msgs, messages)
                            ):
                                kwargs["messages"] = compacted_msgs
                                # Immediately retry without consuming a standard backoff attempt
                                attempt += 1
                                continue

                        # 2. Non-retryable Client Errors: Fail fast to next model/provider or abort
                        if category == LLMErrorCategory.NON_RETRYABLE:
                            break

                        # 3. Calculate Backoff and Sleep
                        attempt += 1
                        if attempt < self.retry_policy.max_retries:
                            delay = self.retry_policy.calculate_delay(
                                attempt=attempt,
                                category=category,
                                retry_after=retry_after,
                            )
                            if delay > 0:
                                time.sleep(delay)

                    finally:
                        self.throttler.release()

            # Mark endpoint as failed if all models failed on this endpoint
            if self.provider_pool and endpoint:
                self.provider_pool.mark_failure(endpoint.name)

        if last_exception:
            raise last_exception
        raise RuntimeError("Chat completion failed across all candidate models and endpoints")

    def _reactive_compact(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Aggressively prunes middle tool outputs and oversized messages when context limit is exceeded.
        """
        if len(messages) <= 2:
            return messages

        head = messages[:1]
        tail = messages[-1:]
        middle = messages[1:-1]

        compacted_middle = []
        for msg in middle:
            if msg.get("role") in ("tool", "assistant", "user"):
                content = str(msg.get("content", ""))
                if len(content) > 200:
                    truncated = content[:200] + "\n... [Remaining output truncated due to context window limit]"
                    compacted_middle.append({**msg, "content": truncated})
                else:
                    compacted_middle.append(msg)
            else:
                compacted_middle.append(msg)

        return head + compacted_middle + tail
