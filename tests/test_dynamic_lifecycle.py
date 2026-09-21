"""
Unit & Integration Test Suite for Dynamic Agent Lifecycle Engine (Issue #68).
Tests:
- spawn_agent()
- pause_agent()
- resume_agent()
- terminate_agent()
- delegate()
- handoff()
- AgentExecutionFrame serialization
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.runtime.execution_frame import AgentExecutionFrame
from agent_orchestrator.runtime.lifecycle_manager import AgentLifecycleManager
from agent_orchestrator.agents.base import BaseAgent
from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.config import OrchestratorConfig
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestExecutionFrame(unittest.TestCase):
    """Tests AgentExecutionFrame serialization and restoration."""

    def test_frame_serialization(self):
        frame = AgentExecutionFrame(
            agent_id="agent-coder-123",
            role="CODER",
            session_id="sess-001",
            task_id="T-01",
            turn=3,
            max_turns=10,
            messages=[
                {"role": "system", "content": "You are a coder."},
                {"role": "user", "content": "Implement auth.py"},
                {"role": "assistant", "content": "Thought: inspect auth.py"},
            ],
            observations=[{"tool": "read_file", "output": "file not found"}],
            working_hypotheses=["Need to create new auth module"],
            scratchpad="auth.py needs jwt",
            pause_reason="Awaiting user approval for dependencies",
        )

        data = frame.to_dict()
        self.assertEqual(data["agent_id"], "agent-coder-123")
        self.assertEqual(data["turn"], 3)
        self.assertEqual(len(data["messages"]), 3)

        # JSON roundtrip
        json_str = frame.to_json()
        restored = AgentExecutionFrame.from_json(json_str)
        self.assertEqual(restored.agent_id, frame.agent_id)
        self.assertEqual(restored.turn, 3)
        self.assertEqual(restored.pause_reason, "Awaiting user approval for dependencies")
        self.assertEqual(len(restored.messages), 3)


class TestDynamicAgentLifecycle(unittest.TestCase):
    """Tests dynamic lifecycle operations on TaskOrchestrator and AgentLifecycleManager."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)
        self.cfg = OrchestratorConfig(workspace_dir=self.temp_dir)

        # Mock LLM
        self.mock_llm = MagicMock()
        self.mock_llm.chat.return_value = '{"thought": "done", "status": "COMPLETED", "summary": "Task complete"}'
        self.mock_llm.chat_json.return_value = {"status": "COMPLETED", "summary": "Task complete"}

        self.orchestrator = TaskOrchestrator(
            cfg=self.cfg,
            workspace=self.workspace,
            llm=self.mock_llm,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_spawn_agent(self):
        coder = self.orchestrator.spawn_agent(
            role="CODER",
            goal="Implement user authentication",
            capabilities=["python", "fastapi", "security"],
        )
        self.assertIsNotNone(coder)
        self.assertTrue(coder.agent_id.startswith("agent-coder-"))
        self.assertEqual(coder.lifecycle_state, "READY")

        live_agents = self.orchestrator.list_live_agents()
        self.assertEqual(len(live_agents), 1)
        self.assertEqual(live_agents[0]["agent_id"], coder.agent_id)

    def test_pause_and_resume_agent(self):
        tester = self.orchestrator.spawn_agent(
            role="TESTER",
            goal="Execute integration tests",
        )

        # Pause agent
        frame = self.orchestrator.pause_agent(
            agent_id=tester.agent_id,
            reason="Waiting for database container to start",
        )
        self.assertIsNotNone(frame)
        self.assertEqual(tester.lifecycle_state, "PAUSED")
        self.assertEqual(frame.pause_reason, "Waiting for database container to start")

        # Resume agent
        res = self.orchestrator.resume_agent(
            agent_id=tester.agent_id,
            feedback="Database container is now healthy.",
        )
        self.assertEqual(res.get("status"), "SUCCESS")
        self.assertEqual(tester.lifecycle_state, "READY")

    def test_terminate_agent_with_cascade(self):
        parent = self.orchestrator.spawn_agent(role="LEAD", goal="Lead development")
        child = self.orchestrator.spawn_agent(
            role="RESEARCHER",
            goal="Research libraries",
            parent_id=parent.agent_id,
        )

        self.assertEqual(len(self.orchestrator.list_live_agents()), 2)

        # Terminate parent with cascading
        terminated = self.orchestrator.terminate_agent(parent.agent_id, cascade=True)
        self.assertEqual(len(terminated), 2)
        self.assertIn(parent.agent_id, terminated)
        self.assertIn(child.agent_id, terminated)

        self.assertEqual(len(self.orchestrator.list_live_agents()), 0)

    def test_orchestrator_delegate(self):
        lead = self.orchestrator.spawn_agent(role="CODER", goal="Build system")
        del_res = self.orchestrator.delegate(
            from_agent_id=lead.agent_id,
            to_agent_id="TESTER",
            subtask_objective="Verify login endpoint",
            context={"endpoint": "/login"},
        )
        self.assertIn(del_res.get("status"), ("SUCCESS", "DELEGATED"))

    def test_orchestrator_handoff(self):
        coder = self.orchestrator.spawn_agent(role="CODER", goal="Build auth")
        reviewer = self.orchestrator.spawn_agent(role="REVIEWER", goal="Audit auth")

        handoff_res = self.orchestrator.handoff(
            from_agent_id=coder.agent_id,
            to_agent_id=reviewer.agent_id,
            reason="Implementation finished, review needed",
            state_transfer={
                "hypotheses": ["Password hashing uses bcrypt"],
                "active_files": ["src/auth.py"],
            },
        )
        self.assertIn(handoff_res.get("status"), ("HANDOFF_COMPLETED",)) or self.assertIn("handoff_id", handoff_res)


if __name__ == "__main__":
    unittest.main()
