"""
Unit and Integration Tests for Evidence-Based Stopping Criteria.
Tests EvidenceRecord, EvidenceLedger, ReAct loop evidence enforcement,
and ConcurrentDAGScheduler dynamic DAG pruning upon early goal satisfaction.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.runtime.evidence_stopping import (
    EvidenceType,
    EvidenceRecord,
    EvidenceLedger,
)
from agent_orchestrator.runtime.task_graph import (
    TaskDAG,
    ExecutableTask,
    TaskState,
)
from agent_orchestrator.runtime.dag_scheduler import ConcurrentDAGScheduler
from agent_orchestrator.runtime.verification import TaskVerificationGate, VerificationResult
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.workspace import WorkspaceManager


class MockLLMReAct:
    """Mock LLM that returns a planned sequence of responses via chat_with_tools."""
    def __init__(self, turns):
        self.turns = list(turns)
        self.turn_idx = 0
        self.prompts_received = []

    def chat_with_tools(self, messages, tools=None, tool_choice=None, model=None, temperature=0.2):
        self.prompts_received.append(messages)
        if self.turn_idx < len(self.turns):
            resp = self.turns[self.turn_idx]
            self.turn_idx += 1
            return resp
        return {"content": "Final finished", "tool_calls": []}


class TestEvidenceStoppingCriteria(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)
        self.tools = BuiltinToolRegistry(self.workspace)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_evidence_record_and_ledger(self):
        """Tests EvidenceRecord and EvidenceLedger scoring and serialization."""
        r1 = EvidenceRecord(
            evidence_type=EvidenceType.TEST_PASS,
            proof_citation="pytest passed 12/12 unit tests in 0.23s",
            confidence_score=0.98,
        )
        r2 = EvidenceRecord(
            evidence_type=EvidenceType.GOAL_SATISFIED_EARLY,
            proof_citation="Already satisfied in module.py",
            confidence_score=0.90,
            redundant_tasks=["T-02", "T-03"],
        )

        ledger = EvidenceLedger()
        self.assertEqual(ledger.cumulative_confidence(), 0.0)
        self.assertFalse(ledger.is_sufficient())

        ledger.add_evidence(r1)
        ledger.add_evidence(r2)

        conf = ledger.cumulative_confidence()
        self.assertGreater(conf, 0.90)
        self.assertTrue(ledger.is_sufficient(0.85))
        self.assertEqual(ledger.get_redundant_tasks(), ["T-02", "T-03"])

        summary = ledger.summary()
        self.assertIn("pytest passed", summary)
        self.assertIn("GOAL_SATISFIED_EARLY", summary)

        l_dict = ledger.to_dict()
        self.assertEqual(l_dict["total_records"], 2)
        self.assertTrue(l_dict["is_sufficient"])
        self.assertEqual(l_dict["redundant_tasks"], ["T-02", "T-03"])

        # Round-trip serialization of EvidenceRecord
        r1_dict = r1.to_dict()
        r1_restored = EvidenceRecord.from_dict(r1_dict)
        self.assertEqual(r1_restored.evidence_type, EvidenceType.TEST_PASS)
        self.assertEqual(r1_restored.proof_citation, r1.proof_citation)
        self.assertAlmostEqual(r1_restored.confidence_score, 0.98, places=2)

    def test_complete_task_with_evidence_contract(self):
        """Tests BuiltinToolRegistry._complete_task with evidence stopping parameters."""
        res_default = self.tools._complete_task(
            summary="Refactoring completed successfully",
            deliverables={"file": "test.py"},
        )
        self.assertEqual(res_default["status"], "COMPLETED")
        self.assertEqual(res_default["evidence"]["evidence_type"], "INSPECTION_CONFIRMED")
        self.assertEqual(res_default["confidence_score"], 1.0)

        res_early = self.tools._complete_task(
            summary="Feature already present in base codebase",
            deliverables={},
            evidence_type="GOAL_SATISFIED_EARLY",
            proof_citation="grep_search found exact class in base.py",
            confidence_score=0.95,
            redundant_tasks=["T-04", "T-05"],
        )
        self.assertEqual(res_early["status"], "COMPLETED")
        self.assertEqual(res_early["evidence"]["evidence_type"], "GOAL_SATISFIED_EARLY")
        self.assertEqual(res_early["proof_citation"], "grep_search found exact class in base.py")
        self.assertEqual(res_early["redundant_tasks"], ["T-04", "T-05"])

    def test_react_loop_rejects_unverified_complete_task(self):
        """
        When enforce_react is active and files were mutated, calling complete_task without
        verification (e.g. ast check or test run) and without proof citation is rejected.
        """
        mock_turns = [
            # Turn 1: write_file
            {
                "content": "Thought: I will write the file.",
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"filepath": "calc.py", "content": "def add(a, b): return a + b\n"}),
                    },
                }],
            },
            # Turn 2: complete_task unverified (should be rejected)
            {
                "content": "Thought: I am done without verification.",
                "tool_calls": [{
                    "id": "c2",
                    "type": "function",
                    "function": {
                        "name": "complete_task",
                        "arguments": json.dumps({"summary": "Done adding add function", "evidence_type": "INSPECTION_CONFIRMED"}),
                    },
                }],
            },
            # Turn 3: ast_syntax_check (verification)
            {
                "content": "Thought: Let me verify the code syntax.",
                "tool_calls": [{
                    "id": "c3",
                    "type": "function",
                    "function": {
                        "name": "ast_syntax_check",
                        "arguments": json.dumps({"filepath": "calc.py"}),
                    },
                }],
            },
            # Turn 4: complete_task verified
            {
                "content": "Thought: Now task is complete.",
                "tool_calls": [{
                    "id": "c4",
                    "type": "function",
                    "function": {
                        "name": "complete_task",
                        "arguments": json.dumps({"summary": "Done and verified", "proof_citation": "ast_syntax_check passed"}),
                    },
                }],
            },
        ]

        llm = MockLLMReAct(mock_turns)
        events = []
        loop = ReActAgentLoop(
            llm=llm,
            tool_registry=self.tools,
            on_step_callback=lambda action, payload: events.append((action, payload)),
        )

        result = loop.run(
            system_prompt="You are an expert coder.",
            user_prompt="Implement add function in calc.py",
            agent_name="CoderAgent",
            enforce_react=True,
            require_verification=True,
            max_turns=5,
        )

        event_names = [e[0] for e in events]
        self.assertIn("VERIFICATION_PROOF_REQUIRED", event_names)
        self.assertIn("FINISH", event_names)
        self.assertEqual(result["turns_taken"], 4)
        self.assertIn("write_file", result["tools_used"])
        self.assertIn("ast_syntax_check", result["tools_used"])
        self.assertIn("complete_task", result["tools_used"])

    def test_react_loop_accepts_complete_task_with_proof_citation(self):
        """
        Agent mutates file and then immediately calls complete_task with empirical proof citation
        and confidence score >= 0.70; this is accepted without requiring extra ast tools.
        """
        mock_turns = [
            {
                "content": "Thought: Writing file.",
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"filepath": "utils.py", "content": "x = 10\n"}),
                    },
                }],
            },
            {
                "content": "Thought: Completing with proof citation.",
                "tool_calls": [{
                    "id": "c2",
                    "type": "function",
                    "function": {
                        "name": "complete_task",
                        "arguments": json.dumps({
                            "summary": "Assigned x = 10",
                            "evidence_type": "INSPECTION_CONFIRMED",
                            "proof_citation": "Verified variable assignment directly in workspace state",
                            "confidence_score": 0.95,
                        }),
                    },
                }],
            },
        ]

        llm = MockLLMReAct(mock_turns)
        events = []
        loop = ReActAgentLoop(
            llm=llm,
            tool_registry=self.tools,
            on_step_callback=lambda action, payload: events.append((action, payload)),
        )

        result = loop.run(
            system_prompt="You are an expert coder.",
            user_prompt="Assign x = 10 in utils.py",
            agent_name="CoderAgent",
            enforce_react=True,
            require_verification=True,
            max_turns=4,
        )

        event_names = [e[0] for e in events]
        self.assertNotIn("VERIFICATION_PROOF_REQUIRED", event_names)
        self.assertIn("FINISH", event_names)
        self.assertEqual(result["turns_taken"], 2)
        self.assertIsNotNone(result.get("evidence"))
        self.assertEqual(result["evidence"]["evidence_type"], "INSPECTION_CONFIRMED")

    def test_dynamic_dag_pruning_early_goal_satisfaction(self):
        """
        Tests that when a task reports GOAL_SATISFIED_EARLY with redundant downstream tasks,
        ConcurrentDAGScheduler prunes those tasks (transitioning them to SKIPPED_REDUNDANT)
        and successfully concludes the workflow.
        """
        task_1 = ExecutableTask(
            task_id="T1",
            objective="Inspect if auth service is already complete",
            outputs=["auth_done"],
        )
        task_2 = ExecutableTask(
            task_id="T2",
            objective="Implement auth service",
            dependencies=["T1"],
            outputs=["auth_module"],
        )
        task_3 = ExecutableTask(
            task_id="T3",
            objective="Write redundant auth integration tests",
            dependencies=["T2"],
            outputs=["auth_tests"],
        )

        dag = TaskDAG([task_1, task_2, task_3])

        mock_orchestrator = MagicMock()
        mock_agent = MagicMock()
        mock_agent.name = "PLANNER"
        mock_agent.execute.return_value = {
            "status": "SUCCESS",
            "final_output": {
                "status": "COMPLETED",
                "evidence": {
                    "evidence_type": "GOAL_SATISFIED_EARLY",
                    "proof_citation": "Auth service is already implemented in core/auth.py",
                    "confidence_score": 1.0,
                    "redundant_tasks": ["T2", "T3"],
                },
                "redundant_tasks": ["T2", "T3"],
            },
            "evidence": {
                "evidence_type": "GOAL_SATISFIED_EARLY",
                "proof_citation": "Auth service is already implemented in core/auth.py",
                "confidence_score": 1.0,
                "redundant_tasks": ["T2", "T3"],
            },
            "redundant_tasks": ["T2", "T3"],
        }
        mock_orchestrator.agent_registry.discover.return_value = [(MagicMock(name="PLANNER"), 1.0)]
        mock_orchestrator.select_agent.return_value = mock_agent
        mock_orchestrator.skill_registry.discover.return_value = []
        mock_orchestrator.workspace = self.workspace
        mock_orchestrator.working_memory = None
        mock_orchestrator.tool_registry = self.tools
        mock_orchestrator.state_store = None
        mock_orchestrator.session_id = "test-session"
        mock_orchestrator._graph_to_state.return_value = MagicMock(user_request="Inspect auth service")
        mock_orchestrator.verification_gate.verify_task.return_value = MagicMock(
            passed=True,
            verified_outputs=["auth_done"],
            stdout="OK",
            failure_reasons=[],
        )
        mock_orchestrator.message_bus = None

        scheduler_events = []
        scheduler = ConcurrentDAGScheduler(
            max_workers=2,
            on_event_callback=lambda stage, msg, payload=None: scheduler_events.append((stage, msg)),
        )

        state_data = {
            "user_request": "Inspect auth service",
            "messages": [],
            "step_results": {},
            "subtasks": dag.to_list(),
            "task_decomposition": dag.to_list(),
        }

        # Wave 1 executes T1, which prunes T2 and T3
        res = scheduler.execute_ready_wave(dag, mock_orchestrator, state_data)

        self.assertTrue(dag.is_all_completed())
        self.assertEqual(dag.get_task("T1").state, TaskState.COMPLETED)
        self.assertEqual(dag.get_task("T2").state, TaskState.SKIPPED_REDUNDANT)
        self.assertEqual(dag.get_task("T3").state, TaskState.SKIPPED_REDUNDANT)

        pruned_events = [e for e in scheduler_events if e[0] == "DAG PRUNED"]
        self.assertEqual(len(pruned_events), 1)
        self.assertIn("T2", pruned_events[0][1])
        self.assertIn("T3", pruned_events[0][1])


if __name__ == "__main__":
    unittest.main()
