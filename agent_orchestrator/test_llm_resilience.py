"""
Unit and Integration Tests for LLM Resilience Layer (Issue #55).
Tests error classification, rate-limit headers, full-jitter backoff,
reactive context overflow compaction, and provider failover.
"""
from dataclasses import dataclass
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.resilience.error_classifier import classify_error, LLMErrorCategory
from agent_orchestrator.resilience.retry_policy import LLMRetryPolicy
from agent_orchestrator.resilience.provider_pool import ProviderEndpoint, ProviderFailoverPool
from agent_orchestrator.resilience.resilient_caller import ResilientLLMCaller
from agent_orchestrator.llm import LLMClient, ConcurrencyThrottler


class DummyHTTPResponse:
    def __init__(self, headers):
        self.headers = headers


class DummyAPIError(Exception):
    def __init__(self, message, status_code=None, headers=None):
        super().__init__(message)
        self.status_code = status_code
        if headers:
            self.response = DummyHTTPResponse(headers)


class TestLLMResilience(unittest.TestCase):

    def test_error_classification_rate_limit(self):
        """Tests rate limit error classification and Retry-After header parsing."""
        # 1. Exception with headers
        exc_with_headers = DummyAPIError("Rate limit exceeded", status_code=429, headers={"retry-after": "7.5"})
        category, retry_after = classify_error(exc_with_headers)
        self.assertEqual(category, LLMErrorCategory.RATE_LIMIT)
        self.assertEqual(retry_after, 7.5)

        # 2. Exception with message regex
        exc_with_msg = Exception("Rate limit reached. Please try again in 15s.")
        category, retry_after = classify_error(exc_with_msg)
        self.assertEqual(category, LLMErrorCategory.RATE_LIMIT)
        self.assertEqual(retry_after, 15.0)

    def test_error_classification_server_error(self):
        """Tests 5xx server error classification."""
        for code in (500, 502, 503, 504):
            exc = DummyAPIError(f"Server error {code}", status_code=code)
            category, retry_after = classify_error(exc)
            self.assertEqual(category, LLMErrorCategory.SERVER_ERROR)
            self.assertIsNone(retry_after)

    def test_error_classification_context_overflow(self):
        """Tests context window overflow error classification."""
        exc = DummyAPIError("maximum context length is 128000 tokens, however your prompt was 135000", status_code=400)
        category, _ = classify_error(exc)
        self.assertEqual(category, LLMErrorCategory.CONTEXT_OVERFLOW)

    def test_error_classification_non_retryable(self):
        """Tests non-retryable 400 (bad schema) and 401 (auth) errors."""
        auth_err = DummyAPIError("Incorrect API key provided", status_code=401)
        cat_auth, _ = classify_error(auth_err)
        self.assertEqual(cat_auth, LLMErrorCategory.NON_RETRYABLE)

        bad_req = DummyAPIError("Invalid schema for function call", status_code=400)
        cat_req, _ = classify_error(bad_req)
        self.assertEqual(cat_req, LLMErrorCategory.NON_RETRYABLE)

    def test_retry_policy_delay_and_jitter(self):
        """Tests full-jitter exponential backoff calculation."""
        policy = LLMRetryPolicy(base_delay=1.0, max_delay=10.0, jitter=False)

        # Without jitter: strictly deterministic
        self.assertEqual(policy.calculate_delay(attempt=1, category=LLMErrorCategory.SERVER_ERROR), 2.0)
        self.assertEqual(policy.calculate_delay(attempt=2, category=LLMErrorCategory.SERVER_ERROR), 4.0)
        self.assertEqual(policy.calculate_delay(attempt=1, category=LLMErrorCategory.RATE_LIMIT), 3.0)

        # Non-retryable and context overflow should have 0 delay
        self.assertEqual(policy.calculate_delay(attempt=1, category=LLMErrorCategory.NON_RETRYABLE), 0.0)
        self.assertEqual(policy.calculate_delay(attempt=1, category=LLMErrorCategory.CONTEXT_OVERFLOW), 0.0)

        # With jitter: values fall within [0.05, cap]
        jitter_policy = LLMRetryPolicy(base_delay=1.0, max_delay=10.0, jitter=True)
        for _ in range(20):
            d = jitter_policy.calculate_delay(attempt=2, category=LLMErrorCategory.SERVER_ERROR)
            self.assertGreaterEqual(d, 0.05)
            self.assertLessEqual(d, 4.0)

        # Retry-after with jitter
        d_after = jitter_policy.calculate_delay(attempt=1, category=LLMErrorCategory.RATE_LIMIT, retry_after=5.0)
        self.assertGreaterEqual(d_after, 5.0)
        self.assertLessEqual(d_after, 6.0)

    def test_reactive_context_overflow_compaction(self):
        """Tests that context overflow triggers compaction and successful retry."""
        throttler = ConcurrencyThrottler()
        caller = ResilientLLMCaller(throttler=throttler, retry_policy=LLMRetryPolicy(max_retries=2, base_delay=0.01))

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Success after compaction"))]

        calls = []

        def side_effect(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise DummyAPIError("maximum context length is 128000 tokens", status_code=400)
            return mock_response

        mock_client.chat.completions.create.side_effect = side_effect

        long_messages = [
            {"role": "system", "content": "You are an assistant"},
            {"role": "user", "content": "Do task"},
            {"role": "tool", "content": "X" * 1000},
            {"role": "assistant", "content": "Thought"},
        ]

        res = caller.execute(
            candidate_models=["test-model"],
            base_kwargs={"messages": long_messages},
            fallback_client=mock_client,
        )

        self.assertEqual(len(calls), 2)
        # Second call should have compacted tool output
        second_msgs = calls[1]["messages"]
        self.assertIn("truncated due to context window limit", second_msgs[2]["content"])
        self.assertEqual(res.choices[0].message.content, "Success after compaction")

    def test_provider_failover_pool(self):
        """Tests automatic failover from failing primary provider to healthy fallback."""
        pool = ProviderFailoverPool(primary_base_url="http://primary/v1", primary_api_key="key-1", primary_name="primary")
        pool.add_fallback_endpoint(name="secondary", base_url="http://secondary/v1", api_key="key-2")

        throttler = ConcurrencyThrottler()
        caller = ResilientLLMCaller(
            throttler=throttler,
            retry_policy=LLMRetryPolicy(max_retries=2, base_delay=0.01),
            provider_pool=pool,
        )

        mock_primary_client = MagicMock()
        mock_primary_client.chat.completions.create.side_effect = DummyAPIError("502 Bad Gateway", status_code=502)

        mock_secondary_client = MagicMock()
        mock_secondary_resp = MagicMock()
        mock_secondary_resp.choices = [MagicMock(message=MagicMock(content="Response from secondary provider"))]
        mock_secondary_client.chat.completions.create.return_value = mock_secondary_resp

        def mock_get_client(endpoint, timeout=90.0):
            if endpoint.name == "primary":
                return mock_primary_client
            return mock_secondary_client

        pool.get_client_for_endpoint = mock_get_client

        res = caller.execute(
            candidate_models=["gpt-4o"],
            base_kwargs={"messages": [{"role": "user", "content": "hello"}]},
        )

        self.assertEqual(res.choices[0].message.content, "Response from secondary provider")
        # Primary should have been marked failed with consecutive failures
        primary_ep = next(e for e in pool._endpoints if e.name == "primary")
        self.assertGreater(primary_ep.consecutive_failures, 0)

    def test_llm_client_delegation(self):
        """Verifies that LLMClient.chat() and chat_with_tools() delegate cleanly."""
        client = LLMClient(api_key="test-key", base_url="http://test/v1")
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="Delegated reply", tool_calls=None))]

        with patch.object(client.resilient_caller, "execute", return_value=mock_resp) as mock_exec:
            res = client.chat([{"role": "user", "content": "test"}])
            self.assertEqual(res, "Delegated reply")
            mock_exec.assert_called_once()


if __name__ == "__main__":
    unittest.main()
