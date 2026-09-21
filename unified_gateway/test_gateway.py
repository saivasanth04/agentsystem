"""
Automated Test Suite for Unified LLM Gateway & Dynamic Routing Engine.
"""
import unittest
import asyncio
from starlette.testclient import TestClient

from gateway.config import parse_apikeys_file, get_or_create_unified_key, GatewayConfig
from gateway.providers import PROVIDER_SPECS, find_provider_spec
from gateway.quota_tracker import QuotaTracker
from gateway.router import DynamicRouter
from gateway.server import app, lifespan


class TestUnifiedGateway(unittest.TestCase):

    def test_01_parse_apikeys(self):
        """Verify that apikeys.txt is parsed accurately."""
        keys = parse_apikeys_file()
        self.assertGreater(len(keys), 20, "Should load at least 20 providers from apikeys.txt")
        self.assertIn("Google AI Studio", keys)
        self.assertIn("Groq", keys)
        self.assertIn("Mistral", keys)
        self.assertIn("OpenRouter", keys)
        self.assertIn("Kilo Gateway", keys)
        self.assertIsNone(keys["Kilo Gateway"], "Kilo Gateway requires no key")
        print(f"[PASS] Successfully parsed {len(keys)} provider entries from apikeys.txt")

    def test_02_quota_tracker_and_scoring(self):
        """Test per-model quota tracking, cooldowns, and ranking."""
        tracker = QuotaTracker()
        
        # Model A: 0 tokens used, 100% remaining
        state_a = tracker.get_or_create("groq", "llama-3.3-70b-versatile", default_quota=1_000_000)
        # Model B: 600k tokens used, 40% remaining
        state_b = tracker.get_or_create("groq", "llama-3.1-8b-instant", default_quota=1_000_000)
        tracker.record_success("groq", "llama-3.1-8b-instant", prompt_tokens=300_000, completion_tokens=300_000)

        # Score of Model A should be higher than Model B because it has more quota remaining
        score_a = state_a.compute_score(base_priority=90)
        score_b = state_b.compute_score(base_priority=90)
        self.assertGreater(score_a, score_b, "Model with higher remaining quota should score higher")

        # Simulate 429 rate limit error on Model A
        tracker.record_error("groq", "llama-3.3-70b-versatile", 429, "Rate limit reached")
        self.assertTrue(state_a.is_in_cooldown, "Model A should be in cooldown after 429")
        self.assertEqual(state_a.health_status, "cooldown")

        # Now ranked candidates should pick Model B over Model A
        candidates = [
            ("groq", "llama-3.3-70b-versatile", 90),
            ("groq", "llama-3.1-8b-instant", 90)
        ]
        ranked = tracker.get_ranked_candidates(candidates)
        self.assertEqual(ranked[0][1], "llama-3.1-8b-instant", "Healthy model should be ranked first over cooldown model")
        print("[PASS] Quota Tracker and dynamic scoring logic verified")

    def test_03_provider_spec_matching(self):
        """Test provider lookup and alias matching."""
        spec_groq = find_provider_spec("Groq")
        self.assertIsNotNone(spec_groq)
        self.assertEqual(spec_groq.id, "groq")

        spec_google = find_provider_spec("Google AI Studio")
        self.assertIsNotNone(spec_google)
        self.assertEqual(spec_google.id, "google")

        spec_kilo = find_provider_spec("Kilo Gateway")
        self.assertIsNotNone(spec_kilo)
        print("[PASS] Provider specification & alias resolution verified")

    def test_04_server_auth_and_endpoints(self):
        """Test FastAPI server auth middleware, models listing, and stats endpoints."""
        unified_key = get_or_create_unified_key()

        with TestClient(app) as client:
            # 1. Unauthenticated request to /v1/models should fail with 401
            resp = client.get("/v1/models")
            self.assertEqual(resp.status_code, 401)

            # 2. Authenticated request to /v1/models should succeed
            headers = {"Authorization": f"Bearer {unified_key}"}
            resp = client.get("/v1/models", headers=headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("data", data)
            model_ids = [m["id"] for m in data["data"]]
            self.assertIn("auto", model_ids)
            self.assertIn("fast", model_ids)
            self.assertIn("smart", model_ids)

            # 3. Health check (no auth needed)
            resp_health = client.get("/health")
            self.assertEqual(resp_health.status_code, 200)
            self.assertEqual(resp_health.json()["status"], "healthy")

            # 4. Stats endpoint (auth needed)
            resp_stats = client.get("/stats", headers=headers)
            self.assertEqual(resp_stats.status_code, 200)
            stats_data = resp_stats.json()
            self.assertIn("total_monitored_models", stats_data)
            self.assertIn("healthy", stats_data)
            print(f"[PASS] Server endpoints and authentication verified ({len(model_ids)} models available)")

    def test_05_hyphenated_apikeys_and_date_reset(self):
        """Test SEA-LION hyphenated provider parsing and date-based quota reset."""
        keys = parse_apikeys_file()
        self.assertIn("SEA-LION", keys)
        self.assertEqual(keys["SEA-LION"], "sk-z-_AXucb7WTiYkrBbdILEg")
        spec = find_provider_spec("SEA-LION")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.id, "sea_lion")

        tracker = QuotaTracker()
        state = tracker.get_or_create("groq", "llama-3.3-70b-versatile")
        # Simulate an old reset date
        state.daily_reset_date = "2020-01-01"
        state.used_tokens_today = 50000
        tracker._check_daily_reset()
        self.assertEqual(state.used_tokens_today, 0, "Usage should be reset after date change")


if __name__ == "__main__":
    unittest.main()
