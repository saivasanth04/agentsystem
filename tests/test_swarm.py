"""
Comprehensive Unit & Integration Test Suite for Swarm Primitives & Multi-Agent Coordination.
Tests all 9 swarm primitives:
1. Agent Discovery
2. Agent Spawning
3. Agent Delegation
4. Agent Handoff
5. Agent Collaboration
6. Agent Result Sharing
7. Agent Conflict Resolution
8. Agent Supervision
9. Agent Termination
"""
import os
import shutil
import tempfile
import time
import unittest
from typing import Any, Dict

from agent_orchestrator.swarm import (
    AgentLifecycleState,
    ResourceLimits,
    SwarmAgentInstance,
    SwarmPool,
    SwarmBlackboard,
    BlackboardEntry,
    DelegationManager,
    DelegationResult,
    HandoffRecord,
    VotingMechanism,
    Vote,
    Proposal,
    ConflictRecord,
    SwarmConsensusEngine,
    HealthStatus,
    WatchdogPolicy,
    AgentHealthReport,
    SupervisorWatchdog,
    SwarmCoordinator,
    SwarmToolRegistry,
)
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.registry.agent_registry import AgentRegistry, AgentManifest


class TestSwarmLifecycle(unittest.TestCase):
    """Tests Agent Lifecycle State Machine, SwarmPool, and Cascading Termination."""

    def setUp(self):
        self.pool = SwarmPool()

    def test_spawn_and_lifecycle_transitions(self):
        agent = self.pool.spawn_subagent(
            parent_id=None,
            role="CODER",
            goal="Implement feature X",
            capabilities=["python", "fastapi"],
            resource_limits=ResourceLimits(max_tokens=5000, max_tool_calls=10, max_time_seconds=60),
        )
        self.assertIsNotNone(agent.instance_id)
        self.assertEqual(agent.role, "CODER")
        self.assertEqual(agent.state, AgentLifecycleState.READY)
        self.assertTrue(agent.is_active())

        # Transition to ACTIVE
        ok = self.pool.transition_state(agent.instance_id, AgentLifecycleState.ACTIVE, reason="Starting task")
        self.assertTrue(ok)
        self.assertEqual(agent.state, AgentLifecycleState.ACTIVE)

        # Record usage
        agent.record_usage(tokens=2000, tool_call=True)
        self.assertEqual(agent.tokens_used, 2000)
        self.assertEqual(agent.tool_calls_count, 1)
        self.assertIsNone(agent.exceeds_limits())

        # Exceed limits
        agent.record_usage(tokens=4000, tool_call=True)
        limit_err = agent.exceeds_limits()
        self.assertIsNotNone(limit_err)
        self.assertIn("Token ceiling exceeded", limit_err)

    def test_hierarchy_and_cascading_termination(self):
        # Create Root -> Child 1 -> Grandchild
        root = self.pool.spawn_subagent(parent_id=None, role="ORCHESTRATOR", goal="Root Goal")
        child = self.pool.spawn_subagent(parent_id=root.instance_id, role="PLANNER", goal="Plan Goal")
        grandchild = self.pool.spawn_subagent(parent_id=child.instance_id, role="RESEARCHER", goal="Research Goal")

        self.assertIn(child.instance_id, root.children_ids)
        self.assertIn(grandchild.instance_id, child.children_ids)

        hierarchy = self.pool.get_hierarchy(root.instance_id)
        self.assertEqual(hierarchy["instance_id"], root.instance_id)
        self.assertEqual(len(hierarchy["children"]), 1)
        self.assertEqual(hierarchy["children"][0]["instance_id"], child.instance_id)
        self.assertEqual(len(hierarchy["children"][0]["children"]), 1)
        self.assertEqual(hierarchy["children"][0]["children"][0]["instance_id"], grandchild.instance_id)

        # Cascading termination from root
        terminated = self.pool.terminate_agent(root.instance_id, reason="Task completed", cascade=True)
        self.assertEqual(len(terminated), 3)
        self.assertIn(root.instance_id, terminated)
        self.assertIn(child.instance_id, terminated)
        self.assertIn(grandchild.instance_id, terminated)

        self.assertEqual(root.state, AgentLifecycleState.TERMINATED)
        self.assertEqual(child.state, AgentLifecycleState.TERMINATED)
        self.assertEqual(grandchild.state, AgentLifecycleState.TERMINATED)
        self.assertFalse(root.is_active())


class TestSwarmDiscovery(unittest.TestCase):
    """Tests Dynamic Runtime Discovery across the Swarm."""

    def setUp(self):
        self.pool = SwarmPool()
        self.a1 = self.pool.spawn_subagent(None, "CODER", "Code api", capabilities=["python", "fastapi"])
        self.a2 = self.pool.spawn_subagent(None, "TESTER", "Write tests", capabilities=["pytest", "python"])
        self.a3 = self.pool.spawn_subagent(None, "REVIEWER", "Review security", capabilities=["security", "ast"])

    def test_discover_by_role(self):
        coders = self.pool.discover_active(role="CODER")
        self.assertEqual(len(coders), 1)
        self.assertEqual(coders[0].instance_id, self.a1.instance_id)

    def test_discover_by_capability(self):
        python_agents = self.pool.discover_active(capability="python")
        self.assertEqual(len(python_agents), 2)
        inst_ids = [a.instance_id for a in python_agents]
        self.assertIn(self.a1.instance_id, inst_ids)
        self.assertIn(self.a2.instance_id, inst_ids)

    def test_discover_by_state(self):
        self.pool.transition_state(self.a1.instance_id, AgentLifecycleState.SUSPENDED)
        active_agents = self.pool.discover_active()
        self.assertEqual(len(active_agents), 2)
        suspended = self.pool.discover_active(state=AgentLifecycleState.SUSPENDED)
        self.assertEqual(len(suspended), 1)
        self.assertEqual(suspended[0].instance_id, self.a1.instance_id)


class TestSwarmBlackboard(unittest.TestCase):
    """Tests Dynamic Shared Blackboard, Topic Pub/Sub, and Resource Locks."""

    def setUp(self):
        self.blackboard = SwarmBlackboard()

    def test_post_and_read(self):
        entry = self.blackboard.post(
            topic="architecture",
            key="db_schema",
            data={"engine": "sqlite", "tables": ["users", "orders"]},
            author_id="ARCHITECT_1",
            confidence=0.95,
            tags=["database", "schema"],
        )
        self.assertEqual(entry.topic, "architecture")
        self.assertEqual(entry.key, "db_schema")

        read_entry = self.blackboard.read("architecture", "db_schema")
        self.assertIsNotNone(read_entry)
        self.assertEqual(read_entry.author_id, "ARCHITECT_1")
        self.assertEqual(read_entry.data["engine"], "sqlite")

    def test_query_and_filtering(self):
        self.blackboard.post("security", "auth_rule", "JWT required", "SEC_1", confidence=0.9, tags=["auth"])
        self.blackboard.post("security", "cors_rule", "Allow all", "SEC_1", confidence=0.5, tags=["cors"])
        self.blackboard.post("ui", "color_palette", ["#000", "#fff"], "UI_1", confidence=0.8, tags=["theme"])

        sec_high_conf = self.blackboard.query(topic="security", min_confidence=0.8)
        self.assertEqual(len(sec_high_conf), 1)
        self.assertEqual(sec_high_conf[0].key, "auth_rule")

        tag_query = self.blackboard.query(tags=["auth"])
        self.assertEqual(len(tag_query), 1)
        self.assertEqual(tag_query[0].key, "auth_rule")

    def test_topic_subscription(self):
        received = []

        def callback(entry: BlackboardEntry):
            received.append(entry)

        sub_id = self.blackboard.subscribe("code.*", callback)
        self.blackboard.post("code.refactor", "chunk_1", "refactored", "CODER_1")
        self.blackboard.post("docs", "readme", "updated", "WRITER_1")

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].topic, "code.refactor")

        # Unsubscribe
        self.blackboard.unsubscribe(sub_id)
        self.blackboard.post("code.refactor", "chunk_2", "done", "CODER_1")
        self.assertEqual(len(received), 1)

    def test_resource_locks(self):
        # Agent 1 locks models.py
        locked = self.blackboard.reserve_resource("src/models.py", agent_id="CODER_1", lease_seconds=10.0)
        self.assertTrue(locked)
        self.assertTrue(self.blackboard.is_resource_locked("src/models.py"))
        self.assertFalse(self.blackboard.is_resource_locked("src/models.py", by_other_than="CODER_1"))
        self.assertTrue(self.blackboard.is_resource_locked("src/models.py", by_other_than="CODER_2"))

        # Agent 2 tries to lock models.py (should fail)
        locked_by_2 = self.blackboard.reserve_resource("src/models.py", agent_id="CODER_2", lease_seconds=10.0)
        self.assertFalse(locked_by_2)

        # Agent 1 releases lock
        released = self.blackboard.release_resource("src/models.py", agent_id="CODER_1")
        self.assertTrue(released)
        self.assertFalse(self.blackboard.is_resource_locked("src/models.py"))

        # Agent 2 can now acquire lock
        locked_by_2_now = self.blackboard.reserve_resource("src/models.py", agent_id="CODER_2", lease_seconds=10.0)
        self.assertTrue(locked_by_2_now)


class TestSwarmDelegationAndHandoff(unittest.TestCase):
    """Tests Dynamic Delegation and Stateful Handoff."""

    def setUp(self):
        self.pool = SwarmPool()
        self.mgr = DelegationManager(swarm_pool=self.pool)

    def test_subtask_delegation(self):
        parent = self.pool.spawn_subagent(None, "CODER", "Main feature")

        def custom_executor(child: SwarmAgentInstance, ctx: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "status": "SUCCESS",
                "output": f"Tests passed for {ctx.get('module')}",
                "artifacts": ["test_report.json"],
                "tokens_used": 150,
            }

        result = self.mgr.delegate_subtask(
            parent_id=parent.instance_id,
            subtask_objective="Verify module tests",
            target_role="TESTER",
            context={"module": "auth"},
            executor_fn=custom_executor,
        )

        self.assertEqual(result.status, "SUCCESS")
        self.assertIn("Tests passed for auth", result.output)
        self.assertIn("test_report.json", result.artifacts)
        self.assertEqual(result.token_usage, 150)

    def test_stateful_handoff(self):
        coder = self.pool.spawn_subagent(None, "CODER", "Build service")
        reviewer = self.pool.spawn_subagent(None, "REVIEWER", "Review service")

        handoff = self.mgr.handoff_to_agent(
            from_agent_id=coder.instance_id,
            to_agent_id=reviewer.instance_id,
            reason="Implementation complete, ready for security audit",
            working_hypotheses=["JWT expiry handles token refresh safely"],
            active_files=["auth.py", "tokens.py"],
            uncommitted_diffs={"auth.py": "+ def verify_token(): pass"},
            stack_trace=None,
        )

        self.assertIsNotNone(handoff.handoff_id)
        self.assertTrue(handoff.accepted)
        self.assertEqual(reviewer.state, AgentLifecycleState.ACTIVE)
        self.assertIn("JWT expiry handles token refresh safely", handoff.working_hypotheses)
        self.assertIn("auth.py", handoff.active_files)


class TestSwarmConsensusAndConflict(unittest.TestCase):
    """Tests Voting Mechanisms and Arbiter Conflict Resolution."""

    def setUp(self):
        self.consensus = SwarmConsensusEngine()

    def test_majority_voting(self):
        prop = self.consensus.create_proposal(
            proposer_id="CODER_1",
            issue="Choose caching strategy",
            options=["REDIS", "MEMCACHED", "IN_MEMORY"],
            mechanism=VotingMechanism.MAJORITY,
            quorum=3,
        )
        self.consensus.cast_vote(prop.proposal_id, "CODER_1", "REDIS", confidence=0.8)
        self.consensus.cast_vote(prop.proposal_id, "CODER_2", "REDIS", confidence=0.9)
        self.consensus.cast_vote(prop.proposal_id, "CODER_3", "IN_MEMORY", confidence=0.5)

        res = self.consensus.tally_votes(prop.proposal_id)
        self.assertEqual(res["status"], "RESOLVED")
        self.assertEqual(res["winning_option"], "REDIS")

    def test_confidence_weighted_voting(self):
        prop = self.consensus.create_proposal(
            proposer_id="ARCH_1",
            issue="Select database engine",
            options=["POSTGRESQL", "SQLITE"],
            mechanism=VotingMechanism.CONFIDENCE_WEIGHTED,
            quorum=2,
        )
        # Agent 1 has weak preference for SQLITE (0.2), Agent 2 has strong preference for POSTGRESQL (0.95)
        self.consensus.cast_vote(prop.proposal_id, "AGENT_1", "SQLITE", confidence=0.2)
        self.consensus.cast_vote(prop.proposal_id, "AGENT_2", "POSTGRESQL", confidence=0.95)

        res = self.consensus.tally_votes(prop.proposal_id)
        self.assertEqual(res["status"], "RESOLVED")
        self.assertEqual(res["winning_option"], "POSTGRESQL")

    def test_conflict_resolution(self):
        conflict = self.consensus.resolve_conflict(
            agent_a_id="CODER_1",
            agent_b_id="REVIEWER_1",
            issue="Whether to allow raw SQL in migration",
            agent_a_position="Raw SQL needed for custom index creation",
            agent_b_position="Raw SQL violates ORM security boundary",
            arbiter_agent_id="ARCHITECTURE",
            arbiter_decision="Allow raw SQL only inside isolated migration file with parameterization",
        )
        self.assertTrue(conflict.resolved)
        self.assertEqual(conflict.arbiter_agent_id, "ARCHITECTURE")
        self.assertIn("parameterization", conflict.resolution)


class TestSupervisorWatchdog(unittest.TestCase):
    """Tests Supervisor Watchdog Sentinel, Loop Detection, and Circuit Breakers."""

    def setUp(self):
        self.pool = SwarmPool()
        self.watchdog = SupervisorWatchdog(
            swarm_pool=self.pool,
            policy=WatchdogPolicy(
                heartbeat_timeout_seconds=0.5,
                max_consecutive_errors=3,
                loop_repetition_threshold=3,
            ),
        )

    def test_loop_detection(self):
        agent = self.pool.spawn_subagent(None, "CODER", "Refactor loop")

        # Call same tool with same arguments 3 times
        s1 = self.watchdog.record_action(agent.instance_id, "read_file", {"filepath": "main.py"})
        self.assertEqual(s1, HealthStatus.HEALTHY)
        s2 = self.watchdog.record_action(agent.instance_id, "read_file", {"filepath": "main.py"})
        self.assertEqual(s2, HealthStatus.HEALTHY)
        s3 = self.watchdog.record_action(agent.instance_id, "read_file", {"filepath": "main.py"})
        self.assertEqual(s3, HealthStatus.LOOPING)

        report = self.watchdog.check_health(agent.instance_id)
        self.assertEqual(report.status, HealthStatus.LOOPING)
        self.assertTrue(any("Loop detected" in w for w in report.warnings))

    def test_consecutive_errors_and_intervention(self):
        agent = self.pool.spawn_subagent(None, "TESTER", "Run failing test")
        for i in range(3):
            self.watchdog.record_action(agent.instance_id, "run_test", {}, error=f"Fail {i}")

        report = self.watchdog.check_health(agent.instance_id)
        self.assertEqual(report.status, HealthStatus.CRITICAL)

        # Supervisor terminates agent via circuit breaker
        int_res = self.watchdog.intervene(agent.instance_id, action="TERMINATE")
        self.assertEqual(int_res["action"], "TERMINATE")
        self.assertEqual(agent.state, AgentLifecycleState.TERMINATED)


class TestSwarmCoordinatorAndTools(unittest.TestCase):
    """Tests SwarmCoordinator Facade and SwarmToolRegistry integration with BuiltinToolRegistry."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)
        self.coordinator = SwarmCoordinator()
        self.swarm_tools = SwarmToolRegistry(coordinator=self.coordinator, current_agent_id="LEAD_AGENT")
        self.builtin_registry = BuiltinToolRegistry(
            workspace=self.workspace,
            swarm_coordinator=self.coordinator,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_swarm_tool_schemas(self):
        schemas = self.swarm_tools.get_tool_definitions()
        names = [s["function"]["name"] for s in schemas]
        self.assertIn("spawn_subagent", names)
        self.assertIn("delegate_subtask", names)
        self.assertIn("handoff_to_agent", names)
        self.assertIn("discover_swarm_agents", names)
        self.assertIn("post_to_blackboard", names)
        self.assertIn("read_from_blackboard", names)
        self.assertIn("request_consensus", names)
        self.assertIn("terminate_subagent", names)

    def test_swarm_tools_via_builtin_registry(self):
        # Verify builtin_registry exposed tools
        all_tool_names = [t.name for t in self.builtin_registry.get_all_tools()]
        self.assertIn("spawn_subagent", all_tool_names)
        self.assertIn("post_to_blackboard", all_tool_names)
        self.assertIn("request_consensus", all_tool_names)

        # Test spawning subagent through builtin_tools
        spawn_res = self.builtin_registry.call_tool(
            "spawn_subagent",
            {"role": "RESEARCHER", "goal": "Find optimal algorithm"},
        )
        self.assertEqual(spawn_res["status"], "SPAWNED")
        subagent_id = spawn_res["subagent_id"]

        # Post to blackboard through builtin_tools
        post_res = self.builtin_registry.call_tool(
            "post_to_blackboard",
            {"topic": "research", "key": "algo_choice", "data": "QuickSort"},
        )
        self.assertEqual(post_res["status"], "POSTED")

        # Read from blackboard through builtin_tools
        read_res = self.builtin_registry.call_tool(
            "read_from_blackboard",
            {"topic": "research", "key": "algo_choice"},
        )
        self.assertEqual(read_res["status"], "FOUND")
        self.assertEqual(read_res["entry"]["data"], "QuickSort")

        # Terminate subagent
        term_res = self.builtin_registry.call_tool(
            "terminate_subagent",
            {"subagent_id": subagent_id},
        )
        self.assertEqual(term_res["status"], "TERMINATED")


if __name__ == "__main__":
    unittest.main()
