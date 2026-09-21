import unittest
import tempfile
import shutil
from unittest.mock import MagicMock, patch

from agent_orchestrator.runtime.messaging import MessageBus, MessageType, StructuredMessage
from agent_orchestrator.tools.communication_tools import CommunicationToolRegistry
from agent_orchestrator.runtime.task_graph import TaskDAG, ExecutableTask, TaskState
from agent_orchestrator.runtime.dag_scheduler import ConcurrentDAGScheduler
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.state import OrchestratorState


class TestStructuredMessage(unittest.TestCase):
    def test_message_creation_and_serialization(self):
        msg = StructuredMessage(
            sender="CODER",
            recipient="ARCHITECTURE",
            message_type=MessageType.REQUEST,
            content="What is the expected interface for CacheStore?",
            topic="api_contracts",
            payload={"module": "cache.py"},
        )
        self.assertTrue(msg.message_id.startswith("msg-"))
        self.assertEqual(msg.message_type, MessageType.REQUEST)

        d = msg.to_dict()
        self.assertEqual(d["sender"], "CODER")
        self.assertEqual(d["recipient"], "ARCHITECTURE")
        self.assertEqual(d["message_type"], "REQUEST")
        self.assertEqual(d["payload"]["module"], "cache.py")

        # Roundtrip from dict
        restored = StructuredMessage.from_dict(d)
        self.assertEqual(restored.message_id, msg.message_id)
        self.assertEqual(restored.message_type, MessageType.REQUEST)
        self.assertEqual(restored.content, msg.content)


class TestMessageBus(unittest.TestCase):
    def setUp(self):
        self.bus = MessageBus()

    def test_direct_routing_and_inbox(self):
        msg1 = StructuredMessage(
            sender="SPECIFICATION",
            recipient="CODER",
            message_type=MessageType.ARTIFACT,
            content="API contract for cache module",
            payload={"endpoints": ["/get", "/set"]},
        )
        self.bus.send_direct(msg1)

        inbox = self.bus.get_inbox("CODER")
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0].content, "API contract for cache module")

        # Empty inbox for other agents
        self.assertEqual(len(self.bus.get_inbox("TESTER")), 0)

        # Drain inbox
        drained = self.bus.get_inbox("CODER", clear=True)
        self.assertEqual(len(drained), 1)
        self.assertEqual(len(self.bus.get_inbox("CODER")), 0)

    def test_topic_publish_and_subscribe(self):
        received = []

        def callback(msg: StructuredMessage):
            received.append(msg)

        self.bus.subscribe("security_audit", callback)

        msg = StructuredMessage(
            sender="REVIEWER",
            recipient="*",
            message_type=MessageType.FINDING,
            content="Discovered potential SQL injection vector in query parser",
            topic="security_audit",
            payload={"severity": "HIGH"},
        )
        self.bus.publish("security_audit", msg)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].sender, "REVIEWER")
        self.assertEqual(received[0].payload["severity"], "HIGH")

    def test_history_queries(self):
        self.bus.send_direct(StructuredMessage(
            sender="CODER",
            recipient="TESTER",
            message_type=MessageType.TASK_RESULT,
            content="Implemented cache",
            topic="deliverables",
        ))
        self.bus.send_direct(StructuredMessage(
            sender="TESTER",
            recipient="CODER",
            message_type=MessageType.OBSERVATION,
            content="Test suite passed 5/5",
            topic="test_results",
        ))

        coder_msgs = self.bus.get_history(sender="CODER")
        self.assertEqual(len(coder_msgs), 1)
        self.assertEqual(coder_msgs[0].message_type, MessageType.TASK_RESULT)

        obs_msgs = self.bus.get_history(message_type=MessageType.OBSERVATION)
        self.assertEqual(len(obs_msgs), 1)
        self.assertEqual(obs_msgs[0].content, "Test suite passed 5/5")

    def test_sync_peer_query(self):
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Use LRUCache with OrderedDict and TTL expiration."

        resp = self.bus.query_agent_sync(
            sender="CODER",
            target_agent="ARCHITECTURE",
            query="Which data structure is optimal for TTL cache?",
            context={"constraints": ["O(1) lookup", "thread-safe"]},
            llm=mock_llm,
        )

        self.assertEqual(resp.sender, "ARCHITECTURE")
        self.assertEqual(resp.recipient, "CODER")
        self.assertEqual(resp.message_type, MessageType.RESPONSE)
        self.assertIn("LRUCache", resp.content)

        # Check history contains both REQUEST and RESPONSE
        reqs = self.bus.get_history(message_type=MessageType.REQUEST)
        resps = self.bus.get_history(message_type=MessageType.RESPONSE)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(len(resps), 1)
        self.assertEqual(resps[0].in_reply_to, reqs[0].message_id)


class TestCommunicationToolRegistry(unittest.TestCase):
    def setUp(self):
        self.bus = MessageBus()
        self.mock_llm = MagicMock()
        self.registry = CommunicationToolRegistry(
            message_bus=self.bus,
            llm=self.mock_llm,
            current_agent_name="CODER",
        )

    def test_send_and_read_tools(self):
        # 1. Send tool
        res = self.registry.send_agent_message(
            recipient="TESTER",
            content="Ready for test verification",
            message_type="TASK_RESULT",
            payload={"files": ["src/main.py"]},
        )
        self.assertEqual(res["status"], "SENT")

        # 2. Read tool from TESTER's perspective
        tester_view = CommunicationToolRegistry(
            message_bus=self.bus,
            current_agent_name="TESTER",
        )
        inbox_res = tester_view.read_inbox()
        self.assertEqual(inbox_res["count"], 1)
        self.assertEqual(inbox_res["messages"][0]["content"], "Ready for test verification")

    def test_query_agent_tool(self):
        self.mock_llm.chat.return_value = "The return type is Dict[str, Any]."
        ans = self.registry.query_agent(
            target_agent="SPECIFICATION",
            query="What is the return type for get_metrics()?",
            context={"function": "get_metrics"},
        )
        self.assertEqual(ans["status"], "ANSWERED")
        self.assertEqual(ans["from_agent"], "SPECIFICATION")
        self.assertIn("Dict[str, Any]", ans["response"])

    def test_publish_finding_tool(self):
        res = self.registry.publish_finding(
            topic="architecture_seams",
            title="Separated LRU cache core from storage adapter",
            details={"pattern": "Adapter"},
        )
        self.assertEqual(res["status"], "PUBLISHED")
        self.assertEqual(res["topic"], "architecture_seams")

        history = self.bus.get_history(topic="architecture_seams")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].message_type, MessageType.FINDING)


class TestSchedulerMessageBusIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)
        self.bus = MessageBus()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scheduler_publishes_task_results_to_bus(self):
        scheduler = ConcurrentDAGScheduler(max_workers=2)
        task = ExecutableTask(
            task_id="T-01",
            objective="Implement core service",
            required_capabilities=["code-generation"],
            required_tools=["filesystem"],
            dependencies=[],
        )
        dag = TaskDAG()
        dag.add_task(task)

        mock_orchestrator = MagicMock()
        mock_agent = MagicMock()
        mock_agent.name = "CODER"
        mock_agent.execute.return_value = {"status": "SUCCESS", "artifacts": ["service.py"]}
        mock_orchestrator.agent_registry.discover.return_value = []
        mock_orchestrator.select_agent.return_value = mock_agent
        mock_orchestrator.skill_registry.discover.return_value = []
        mock_orchestrator.workspace = self.workspace
        mock_orchestrator.message_bus = self.bus
        mock_orchestrator._graph_to_state.return_value = OrchestratorState(user_request="Implement service")
        mock_orchestrator.verification_gate.verify_task.return_value = MagicMock(
            passed=True,
            verified_outputs=["service.py"],
            stdout="Verification passed",
            failure_reasons=[],
        )

        state_data = {
            "user_request": "Implement service",
            "messages": [],
            "step_results": {},
            "subtasks": dag.to_list(),
            "task_decomposition": dag.to_list(),
        }

        scheduler.execute_ready_wave(dag, mock_orchestrator, state_data)

        # Verify message bus received TASK_RESULT
        results = self.bus.get_history(topic="task_results")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].message_type, MessageType.TASK_RESULT)
        self.assertIn("T-01", results[0].content)
        self.assertEqual(results[0].payload["task_id"], "T-01")


if __name__ == "__main__":
    unittest.main()
