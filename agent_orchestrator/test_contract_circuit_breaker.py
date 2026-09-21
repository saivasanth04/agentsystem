"""
Unit tests for Issue #33: Contract Introspection, Infrastructure Error Classification,
and Infinite Remediation Loop Circuit Breaker.
"""
import unittest
from unittest.mock import MagicMock, patch
from agent_orchestrator.agents.base import BaseAgent
from agent_orchestrator.agents.dynamic_agent import DynamicAgent
from agent_orchestrator.registry.agent_registry import AgentRegistry, AgentManifest
from agent_orchestrator.runtime.approval_gate import AutoApprovalGate
from agent_orchestrator.runtime.diagnostics import FailureDiagnostician
from agent_orchestrator.runtime.task_graph import TaskDAG, ExecutableTask, TaskState, RetryPolicy
from agent_orchestrator.state import OrchestratorState, TaskStatus


class TestContractIntrospection(unittest.TestCase):
    def test_dynamic_agent_accepts_approval_gate_and_kwargs(self):
        gate = AutoApprovalGate()
        agent = DynamicAgent(
            name="TEST_DYNAMIC",
            role_description="Testing dynamic agent",
            approval_gate=gate,
            custom_option="custom_value",
        )
        self.assertEqual(agent.name, "TEST_DYNAMIC")
        self.assertEqual(agent.approval_gate, gate)
        self.assertEqual(agent.extra_kwargs.get("custom_option"), "custom_value")

    def test_base_agent_accepts_approval_gate_and_kwargs(self):
        gate = AutoApprovalGate()
        agent = BaseAgent(
            name="TEST_BASE",
            role_description="Testing base agent",
            approval_gate=gate,
            extra_param=123,
        )
        self.assertEqual(agent.name, "TEST_BASE")
        self.assertEqual(agent.approval_gate, gate)
        self.assertEqual(agent.extra_kwargs.get("extra_param"), 123)

    def test_agent_registry_signature_introspection(self):
        registry = AgentRegistry()

        # Class with rigid constructor (no **kwargs, no approval_gate)
        class RigidAgent(BaseAgent):
            def __init__(self, model=None, llm=None, workspace=None):
                self.model = model

        manifest = AgentManifest(
            name="RIGID",
            role_description="Rigid constructor",
            agent_class=RigidAgent,
        )
        registry.register(manifest)

        # Calling create_agent_instance with approval_gate and unexpected kwargs
        gate = AutoApprovalGate()
        # Must not raise TypeError: unexpected keyword argument
        instance = registry.create_agent_instance(
            "RIGID",
            approval_gate=gate,
            unexpected_arg="should_be_filtered",
        )
        self.assertIsInstance(instance, RigidAgent)


class TestFailureDiagnosticianClassification(unittest.TestCase):
    def setUp(self):
        self.diagnostician = FailureDiagnostician(llm=None)

    def test_unexpected_keyword_argument_classified_as_contract_mismatch(self):
        stderr = (
            "Traceback (most recent call last):\n"
            "  File 'dag_scheduler.py', line 88, in _execute_single_task\n"
            "    agent = orchestrator.select_agent(manifest)\n"
            "  File 'agent_registry.py', line 290, in create_agent_instance\n"
            "    return DynamicAgent(**kwargs)\n"
            "TypeError: DynamicAgent.__init__() got an unexpected keyword argument 'approval_gate'"
        )
        report = self.diagnostician.diagnose_failure(
            task_title="Dynamic Agent Task",
            execution_stderr=stderr,
            execution_stdout="",
        )
        self.assertEqual(report.failure_type, "CONTRACT_MISMATCH")
        self.assertEqual(report.target_agent, "SYSTEM")
        self.assertFalse(report.should_rollback)
        self.assertTrue(any("contract" in s.lower() for s in report.suggested_remediation))

    def test_internal_framework_error_classified_as_infrastructure_error(self):
        stderr = (
            "Traceback (most recent call last):\n"
            "  File 'orchestrator.py', line 124, in execute_ready_wave\n"
            "    res = self.dag_scheduler.execute_ready_wave(...)\n"
            "AttributeError: 'ConcurrentDAGScheduler' object has no attribute 'missing_method'"
        )
        report = self.diagnostician.diagnose_failure(
            task_title="Wave Execution",
            execution_stderr=stderr,
            execution_stdout="",
        )
        self.assertEqual(report.failure_type, "INFRASTRUCTURE_ERROR")
        self.assertEqual(report.target_agent, "SYSTEM")


class TestOrchestratorCircuitBreaker(unittest.TestCase):
    def test_circuit_breaker_stops_normal_remediation(self):
        from agent_orchestrator.orchestrator import TaskOrchestrator, OrchestratorConfig
        from agent_orchestrator.runtime.diagnostics import DiagnosticReport

        cfg = OrchestratorConfig(max_replan_iterations=3)
        orchestrator = TaskOrchestrator(cfg=cfg, llm=MagicMock())

        # Mock diagnostician to return CONTRACT_MISMATCH
        orchestrator.diagnostician.diagnose_failure = MagicMock(return_value=DiagnosticReport(
            root_cause_summary="TypeError: DynamicAgent.__init__() got an unexpected keyword argument 'approval_gate'",
            failure_type="CONTRACT_MISMATCH",
            affected_files=[],
            suggested_remediation=["Reconcile parameter signatures at source."],
            target_agent="SYSTEM",
            should_rollback=False,
        ))

        # Initial state with a failed task
        failed_task = ExecutableTask(
            task_id="T-01",
            objective="Core Implementation",
            state=TaskState.FAILED,
            retry_policy=RetryPolicy(max_retries=2, current_retry=0),
        )
        task_dag = TaskDAG(tasks=[failed_task])

        state = {
            "user_request": "Build feature",
            "iteration": 1,
            "subtasks": task_dag.to_list(),
            "task_decomposition": task_dag.to_list(),
            "review_output": {"summary": "Agent construction failed"},
            "test_output": {"stderr": "TypeError: unexpected keyword argument 'approval_gate'"},
        }

        updates = orchestrator._node_replan(state)

        # Verify that NO T-REM task was injected for CODER!
        subtasks = updates.get("subtasks", [])
        task_ids = [t["task_id"] for t in subtasks]
        self.assertNotIn("T-REM-1", task_ids)
        self.assertEqual(updates.get("target_agent_for_fix"), "SYSTEM")

        # Verify that original task was reconciled and re-queued as READY
        reconciled_task = next(t for t in subtasks if t["task_id"] == "T-01")
        self.assertEqual(reconciled_task["state"], TaskState.READY.value)
        self.assertEqual(reconciled_task["retry_policy"]["current_retry"], 1)


if __name__ == "__main__":
    unittest.main()
