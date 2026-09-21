"""
Unit and Integration Tests for Issue #66: Comprehensive Multi-Tier Agent Memory Engine.
Tests Short-Term Working Memory, Task Memory, Episodic Memory, Semantic Memory,
Project Memory, and the unified AgentMemoryEngine facade.
"""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.memory.working_memory import WorkingMemory
from agent_orchestrator.memory.task_memory import TaskMemory, TaskManagerMemoryStore
from agent_orchestrator.memory.episodic_memory import EpisodicMemoryEngine, EpisodeRecord
from agent_orchestrator.memory.semantic_memory import SemanticMemoryStore, SemanticItem
from agent_orchestrator.memory.project_memory import ProjectMemoryManager, ProjectRule
from agent_orchestrator.memory.manager import AgentMemoryEngine
from agent_orchestrator.state import OrchestratorState, AgentMessage


class TestWorkingMemory(unittest.TestCase):
    """Tests for Short-Term Working Memory (STM)."""

    def setUp(self):
        self.wm = WorkingMemory(active_goal="Build auth module")

    def test_set_and_get_kv_store(self):
        """Verify .set() and .get() work with arbitrary keys and defaults."""
        self.wm.set("project_profile", {"language": "python"})
        self.wm.set("env_profile", {"os": "windows"})
        self.assertEqual(self.wm.get("project_profile")["language"], "python")
        self.assertEqual(self.wm.get("env_profile")["os"], "windows")
        self.assertIsNone(self.wm.get("nonexistent"))
        self.assertEqual(self.wm.get("missing_key", "default_val"), "default_val")

    def test_hypothesis_tracking(self):
        """Verify recording active and tested hypotheses."""
        self.wm.record_hypothesis("JWT requires HS256 algorithm parameter", status="PENDING")
        self.assertEqual(self.wm.current_hypothesis, "JWT requires HS256 algorithm parameter")
        self.assertEqual(len(self.wm.tested_hypotheses), 1)

        self.wm.record_hypothesis("JWT requires HS256 algorithm parameter", status="VERIFIED", evidence="PyJWT test passed")
        self.assertEqual(len(self.wm.tested_hypotheses), 2)
        self.assertEqual(self.wm.tested_hypotheses[-1]["status"], "VERIFIED")

    def test_summary_formatting(self):
        """Verify get_summary() produces compact markdown including hypotheses and facts."""
        self.wm.record_fact("Token expiration is 3600 seconds")
        self.wm.record_pitfall("Do not use deprecated jwt.verify()")
        self.wm.record_hypothesis("Claims require sub key", status="VERIFIED")
        self.wm.set("cache_ttl", 300)

        summary = self.wm.get_summary()
        self.assertIn("Active Milestone Goal", summary)
        self.assertIn("Token expiration is 3600 seconds", summary)
        self.assertIn("Do not use deprecated jwt.verify()", summary)
        self.assertIn("Claims require sub key", summary)
        self.assertIn("cache_ttl", summary)

    def test_serialization_roundtrip(self):
        """Verify to_dict and from_dict preserve all fields including kv_store and hypotheses."""
        self.wm.record_fact("Fact 1")
        self.wm.record_hypothesis("Hyp 1", status="PENDING")
        self.wm.set("key1", "val1")

        data = self.wm.to_dict()
        restored = WorkingMemory.from_dict(data)
        self.assertEqual(restored.active_goal, self.wm.active_goal)
        self.assertEqual(restored.verified_facts, ["Fact 1"])
        self.assertEqual(restored.current_hypothesis, "Hyp 1")
        self.assertEqual(restored.get("key1"), "val1")


class TestTaskMemory(unittest.TestCase):
    """Tests for Task Memory and Inter-Task Knowledge Transfer."""

    def setUp(self):
        self.store = TaskManagerMemoryStore()

    def test_task_memory_lifecycle(self):
        """Verify task memory captures constraints, decisions, and takeaways."""
        t_mem = self.store.get_or_create("T-01", goal="Implement JWT Auth")
        t_mem.record_constraint("Must use RS256 or HS256")
        t_mem.record_decision("Adopted PyJWT", rationale="Standard across Python ecosystem")
        t_mem.record_takeaway("Exported generate_token() in auth.py")
        t_mem.record_artifact("auth.py")

        distilled = t_mem.distill_for_dependents()
        self.assertEqual(distilled["task_id"], "T-01")
        self.assertEqual(distilled["discovered_constraints"], ["Must use RS256 or HS256"])
        self.assertEqual(distilled["technical_decisions"], ["Adopted PyJWT"])
        self.assertEqual(distilled["key_takeaways"], ["Exported generate_token() in auth.py"])
        self.assertEqual(distilled["artifacts_created"], ["auth.py"])

    def test_task_store_retrieval_for_dependencies(self):
        """Verify store retrieves memories for multiple dependency tasks."""
        self.store.record_decision("T-01", "Created User model")
        self.store.record_takeaway("T-01", "User table migrated")
        self.store.record_decision("T-02", "Created Payment model")

        mems = self.store.get_memories_for_tasks(["T-01", "T-02", "T-99"])
        self.assertEqual(len(mems), 2)
        task_ids = {m.task_id for m in mems}
        self.assertEqual(task_ids, {"T-01", "T-02"})


class TestEpisodicMemory(unittest.TestCase):
    """Tests for Episodic Memory and Experience Retrieval."""

    def setUp(self):
        self.engine = EpisodicMemoryEngine()

    def test_record_and_retrieve_episode(self):
        """Verify recording an episode and retrieving it by objective keyword."""
        ep = EpisodeRecord(
            task_id="T-10",
            objective="Configure Redis caching layer",
            error_encountered="ConnectionRefusedError: port 6379",
            resolution_strategy="Started Redis service via systemctl or mock client",
            tools_used=["terminal_execute", "write_file"],
            outcome="SUCCESS",
            lessons_learned=["Always check Redis ping before instantiating cache"],
        )
        ep_id = self.engine.record_episode(ep)
        self.assertTrue(ep_id.startswith("ep-"))

        # Retrieve matching episode
        retrieved = self.engine.retrieve_relevant_episodes("Configure Redis caching", top_k=3)
        self.assertGreaterEqual(len(retrieved), 1)
        self.assertEqual(retrieved[0].task_id, "T-10")
        self.assertIn("ConnectionRefusedError", retrieved[0].error_encountered)

    def test_format_episodes_for_prompt(self):
        """Verify format_episodes_for_prompt produces structured markdown."""
        ep = EpisodeRecord(
            task_id="T-11",
            objective="Payment webhook handler",
            error_encountered="Invalid signature",
            resolution_strategy="Passed raw body payload to stripe.Webhook.construct_event",
            outcome="SUCCESS",
            lessons_learned=["Never use request.json() before signature verification"],
        )
        self.engine.record_episode(ep)
        episodes = self.engine.retrieve_relevant_episodes("Payment webhook")
        formatted = self.engine.format_episodes_for_prompt(episodes)
        self.assertIn("Relevant Past Experiences & Trajectories", formatted)
        self.assertIn("Payment webhook handler", formatted)
        self.assertIn("Invalid signature", formatted)
        self.assertIn("Never use request.json()", formatted)


class TestSemanticMemory(unittest.TestCase):
    """Tests for Semantic Knowledge Store."""

    def setUp(self):
        self.store = SemanticMemoryStore()

    def test_store_and_search_semantic_items(self):
        """Verify storing conceptual items and searching by keyword/category."""
        self.store.store(SemanticItem(
            key="FastAPI Dependency Injection",
            category="FRAMEWORK",
            content="Use Depends() for database session injection in route handlers.",
            tags=["fastapi", "db", "dependency-injection"],
        ))
        self.store.store(SemanticItem(
            key="PyJWT Decode Contract",
            category="LIBRARY",
            content="jwt.decode() strictly requires algorithms=['HS256'] in version 2.x+.",
            tags=["jwt", "auth", "pyjwt"],
        ))

        results = self.store.search("FastAPI database", top_k=2)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].key, "FastAPI Dependency Injection")

        jwt_results = self.store.search("JWT decode", category="LIBRARY", top_k=2)
        self.assertEqual(len(jwt_results), 1)
        self.assertEqual(jwt_results[0].key, "PyJWT Decode Contract")

    def test_format_for_prompt(self):
        """Verify semantic memory prompt formatting."""
        item = SemanticItem(
            key="React 19 Hooks",
            category="FRAMEWORK",
            content="useActionState replaces useFormState in React 19.",
        )
        formatted = self.store.format_for_prompt([item])
        self.assertIn("Semantic Knowledge & Contracts", formatted)
        self.assertIn("[FRAMEWORK] **React 19 Hooks**", formatted)


class TestProjectMemory(unittest.TestCase):
    """Tests for Project Memory Manager."""

    def setUp(self):
        self.pm = ProjectMemoryManager()

    def test_seed_from_environment(self):
        """Verify auto-seeding from project and environment profiles."""
        project_profile = {
            "primary_language": "python",
            "framework": "fastapi",
            "package_manager": "uv",
            "test_runner": "pytest",
        }
        env_profile = {
            "os": {"system": "windows", "default_shell": "powershell"},
        }
        self.pm.seed_from_environment(project_profile=project_profile, env_profile=env_profile)

        summary = self.pm.get_project_context_summary()
        self.assertIn("Project Conventions & Architecture Rules", summary)
        self.assertIn("Primary Language: Python", summary)
        self.assertIn("Package Manager: uv", summary)
        self.assertIn("Test Runner: pytest", summary)
        self.assertIn("Host OS: Windows", summary)

    def test_seed_from_workspace_files(self):
        """Verify auto-seeding from guideline files like CLAUDE.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            claude_md = tmppath / "CLAUDE.md"
            claude_md.write_text("# Project Rules\nAlways use snake_case for python functions.", encoding="utf-8")

            self.pm.seed_from_workspace_files(tmppath)
            summary = self.pm.get_project_context_summary()
            self.assertIn("Guidelines from CLAUDE.md", summary)
            self.assertIn("snake_case", summary)


class TestAgentMemoryEngineFacade(unittest.TestCase):
    """Tests for the unified AgentMemoryEngine facade."""

    def setUp(self):
        self.engine = AgentMemoryEngine()

    def test_active_pre_flight_priming(self):
        """Verify prime_context_for_task produces primed context across memory tiers."""
        self.engine.working.record_fact("Database runs on port 5432")
        self.engine.project.record_rule("CONVENTION", "Async Handlers", "All API routes must be async def.")
        self.engine.semantic.store(SemanticItem(key="Stripe API", category="LIBRARY", content="Use stripe.Charge.create()"))

        task_info = {
            "task_id": "T-05",
            "objective": "Implement Stripe payment route",
            "dependencies": [],
        }
        primed = self.engine.prime_context_for_task(task_info)

        self.assertIn("Database runs on port 5432", primed["working_memory"])
        self.assertIn("Async Handlers", primed["project_memory"])
        self.assertIn("Stripe API", primed["semantic_knowledge"])

    def test_post_task_automatic_distillation_success(self):
        """Verify successful task distill updates working, task, and episodic memory."""
        task_info = {
            "task_id": "T-01",
            "objective": "Build User Model",
            "session_id": "sess-123",
            "tools_used": ["write_file"],
        }
        result_data = {
            "summary": "Implemented User model in models/user.py",
            "written_files": ["models/user.py"],
        }
        self.engine.distill_task_outcome("T-01", task_info, result_data, success=True)

        # 1. Working memory updated
        self.assertTrue(any("Task [T-01] 'Build User Model' succeeded" in f for f in self.engine.working.verified_facts))
        self.assertIn("models/user.py", self.engine.working.modified_symbols)

        # 2. Task memory updated
        t_mem = self.engine.task_store.get("T-01")
        self.assertIsNotNone(t_mem)
        self.assertIn("Implemented User model in models/user.py", t_mem.key_takeaways[0])

        # 3. Episodic memory updated
        episodes = self.engine.episodic.retrieve_relevant_episodes("Build User Model")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].outcome, "SUCCESS")

    def test_post_task_automatic_distillation_failure(self):
        """Verify failed task distill records pitfall and failure episode."""
        task_info = {
            "task_id": "T-02",
            "objective": "Payment Processing",
            "session_id": "sess-123",
            "tools_used": ["terminal_execute"],
        }
        result_data = {"summary": "Execution crashed"}
        self.engine.distill_task_outcome(
            "T-02",
            task_info,
            result_data,
            success=False,
            error_message="ZeroDivisionError: division by zero",
        )

        self.assertTrue(any("ZeroDivisionError" in p for p in self.engine.working.discovered_pitfalls))
        episodes = self.engine.episodic.retrieve_relevant_episodes("Payment Processing")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].outcome, "FAILURE")
        self.assertIn("ZeroDivisionError", episodes[0].error_encountered)


class TestStateMessageQueries(unittest.TestCase):
    """Tests for upgrading OrchestratorState messages into a queryable trace."""

    def setUp(self):
        self.state = OrchestratorState(user_request="Build microservice")

    def test_message_queries(self):
        """Verify get_recent_messages, get_messages_by_role, and get_trace_summary."""
        self.state.add_message("PLANNER", "PLANNING", "Generated execution plan")
        self.state.add_message("CODER", "CODING", "Created app.py")
        self.state.add_message("TESTER", "TESTING", "Ran pytest suite (PASSED)")

        recent = self.state.get_recent_messages(2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0].agent_name, "CODER")
        self.assertEqual(recent[1].agent_name, "TESTER")

        coder_msgs = self.state.get_messages_by_role("CODER")
        self.assertEqual(len(coder_msgs), 1)
        self.assertEqual(coder_msgs[0].content, "Created app.py")

        testing_msgs = self.state.get_messages_by_stage("TESTING")
        self.assertEqual(len(testing_msgs), 1)
        self.assertEqual(testing_msgs[0].agent_name, "TESTER")

        trace_summary = self.state.get_trace_summary()
        self.assertIn("**Execution Message Trace**:", trace_summary)
        self.assertIn("[PLANNER | PLANNING]: Generated execution plan", trace_summary)
        self.assertIn("[CODER | CODING]: Created app.py", trace_summary)


class TestThreadSafety(unittest.TestCase):
    """Tests verifying thread safety under concurrent reads and writes."""

    def test_concurrent_working_memory(self):
        wm = WorkingMemory()
        errors = []

        def worker(idx):
            try:
                for i in range(50):
                    wm.set(f"key_{idx}_{i}", i)
                    wm.record_fact(f"fact_{idx}_{i}")
                    wm.record_pitfall(f"pitfall_{idx}_{i}")
                    wm.record_hypothesis(f"hyp_{idx}_{i}")
                    _ = wm.get(f"key_{idx}_{i}")
                    _ = wm.get_summary()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread safety errors: {errors}")
        self.assertGreater(len(wm.verified_facts), 0)


if __name__ == "__main__":
    unittest.main()
