import unittest
import tempfile
import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_orchestrator.llm import LLMClient, ConcurrencyThrottler
from agent_orchestrator.tools.workspace import WorkspaceManager, SandboxedWorkspace
from agent_orchestrator.runtime.analysis import ParallelDomainAnalyzer, DomainAnalysisMatrix
from agent_orchestrator.runtime.task_graph import TaskDAG, ExecutableTask, TaskState, TaskPermissions
from agent_orchestrator.runtime.dag_scheduler import ConcurrentDAGScheduler
from agent_orchestrator.state import OrchestratorState


class TestConcurrencyThrottler(unittest.TestCase):
    def test_throttler_semaphore_and_backoff(self):
        throttler = ConcurrencyThrottler(max_concurrency=2, requests_per_minute=1000)
        
        with throttler.acquire():
            pass

        self.assertEqual(throttler.max_concurrency, 2)
        
        self.assertAlmostEqual(throttler.get_backoff_delay(0), 1.0, places=1)
        self.assertAlmostEqual(throttler.get_backoff_delay(1), 2.0, places=1)
        self.assertAlmostEqual(throttler.get_backoff_delay(2), 4.0, places=1)

    def test_throttler_token_bucket(self):
        throttler = ConcurrencyThrottler(max_concurrency=5, requests_per_minute=600)
        t0 = time.time()
        with throttler.acquire():
            pass
        t1 = time.time()
        self.assertLess(t1 - t0, 0.5)


class TestSandboxedWorkspace(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.main_ws = WorkspaceManager(self.test_dir)
        self.main_ws.write_file("main.py", "print('hello world')")
        self.main_ws.write_file("config.json", '{"version": 1}')

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sandbox_isolation_and_merge(self):
        sandbox = SandboxedWorkspace(self.main_ws, task_id="T-SANDBOX-1")
        
        # 1. Check sandbox synced main files
        self.assertTrue(sandbox.file_exists("main.py"))
        self.assertTrue(sandbox.file_exists("config.json"))
        self.assertEqual(sandbox.read_file("main.py"), "print('hello world')")

        # 2. Modify sandbox file and create new file
        sandbox.write_file("main.py", "print('modified in sandbox')")
        sandbox.write_file("feature.py", "def feature(): return True")

        # Main workspace should NOT be modified yet
        self.assertEqual(self.main_ws.read_file("main.py"), "print('hello world')")
        self.assertFalse(self.main_ws.file_exists("feature.py"))

        # 3. Detect uncommitted changes in sandbox
        changes = sandbox.get_uncommitted_changes()
        self.assertIn("main.py", changes["modified"])
        self.assertIn("feature.py", changes["created"])

        # 4. Merge sandbox into main workspace
        merged = sandbox.merge_into_main()
        self.assertEqual(self.main_ws.read_file("main.py"), "print('modified in sandbox')")
        self.assertTrue(self.main_ws.file_exists("feature.py"))
        self.assertIn("main.py", merged)
        self.assertIn("feature.py", merged)

        # 5. Cleanup
        sandbox.cleanup()
        self.assertFalse(sandbox.sandbox_dir.exists())

    def test_parallel_sandboxes_independent_writes(self):
        sb1 = SandboxedWorkspace(self.main_ws, task_id="T-01")
        sb2 = SandboxedWorkspace(self.main_ws, task_id="T-02")

        sb1.write_file("service_a.py", "class ServiceA: pass")
        sb2.write_file("service_b.py", "class ServiceB: pass")

        sb1.merge_into_main()
        sb1.cleanup()

        sb2.merge_into_main()
        sb2.cleanup()

        self.assertTrue(self.main_ws.file_exists("service_a.py"))
        self.assertTrue(self.main_ws.file_exists("service_b.py"))


class TestParallelDomainAnalyzer(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.test_dir)
        self.mock_llm = MagicMock()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_parallel_analysis_fan_out(self):
        def mock_chat_json(messages, model=None, temperature=0.2):
            content = messages[0]["content"]
            if "Backend" in content:
                return {"proposed_endpoints": ["/api/v1/auth", "/api/v1/data"], "services": ["AuthService"]}
            elif "Frontend" in content:
                return {"components": ["LoginForm", "DashboardView"], "state_management": ["Redux"]}
            elif "Database" in content:
                return {"tables_or_collections": ["users", "sessions"], "schemas": ["UserSchema"]}
            elif "Security" in content:
                return {"auth_boundaries": ["JWT tokens"], "threat_mitigations": ["Rate limiting"]}
            return {}

        self.mock_llm.chat_json.side_effect = mock_chat_json
        analyzer = ParallelDomainAnalyzer(llm=self.mock_llm, workspace=self.workspace)

        matrix = analyzer.analyze_in_parallel("Build a secure multi-user auth and analytics system")

        self.assertIsInstance(matrix, DomainAnalysisMatrix)
        self.assertEqual(matrix.core_goal, "Build a secure multi-user auth and analytics system")
        self.assertIn("/api/v1/auth", matrix.backend_findings.get("proposed_endpoints", []))
        self.assertIn("LoginForm", matrix.frontend_findings.get("components", []))
        self.assertIn("users", matrix.database_findings.get("tables_or_collections", []))
        self.assertIn("JWT tokens", matrix.security_findings.get("auth_boundaries", []))
        self.assertTrue(len(matrix.technical_constraints) > 0)

        d = matrix.to_dict()
        self.assertEqual(d["domain"], "full-stack")
        self.assertIn("backend_findings", d)


class TestConcurrentDAGScheduler(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_wave_execution_in_sandboxes(self):
        scheduler = ConcurrentDAGScheduler(max_workers=2)

        t1 = ExecutableTask(
            task_id="T-01",
            objective="Build Component A",
            required_capabilities=["code-generation"],
            required_tools=["filesystem"],
            dependencies=[],
        )
        t2 = ExecutableTask(
            task_id="T-02",
            objective="Build Component B",
            required_capabilities=["code-generation"],
            required_tools=["filesystem"],
            dependencies=[],
        )
        t3 = ExecutableTask(
            task_id="T-03",
            objective="Integrate Components A & B",
            required_capabilities=["testing"],
            required_tools=["filesystem"],
            dependencies=["T-01", "T-02"],
        )

        dag = TaskDAG()
        dag.add_task(t1)
        dag.add_task(t2)
        dag.add_task(t3)

        mock_orchestrator = MagicMock()
        mock_agent = MagicMock()
        mock_agent.name = "CODER"
        mock_agent.execute.return_value = {"status": "SUCCESS", "artifacts": ["generated_file.py"]}
        mock_orchestrator.agent_registry.discover.return_value = []
        mock_orchestrator.select_agent.return_value = mock_agent
        mock_orchestrator.skill_registry.discover.return_value = []
        mock_orchestrator.workspace = self.workspace
        mock_orchestrator._graph_to_state.return_value = OrchestratorState(user_request="Build components and integrate")
        mock_orchestrator.verification_gate.verify_task.return_value = MagicMock(
            passed=True,
            verified_outputs=["generated_file.py"],
            stdout="OK",
            failure_reasons=[],
        )

        state_data = {
            "user_request": "Build components and integrate",
            "messages": [],
            "step_results": {},
            "subtasks": dag.to_list(),
            "task_decomposition": dag.to_list(),
        }

        res = scheduler.execute_ready_wave(dag, mock_orchestrator, state_data)

        self.assertEqual(t1.state, TaskState.COMPLETED)
        self.assertEqual(t2.state, TaskState.COMPLETED)
        self.assertEqual(t3.state, TaskState.PENDING)
        self.assertEqual(len(res["completed_subtasks"]), 2)


if __name__ == "__main__":
    unittest.main()
