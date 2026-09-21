import json
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.contracts import (
    ArchitectureContract,
    ExecutionPlanContract,
    SpecificationContract,
)
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.runtime.task_graph import ExecutableTask
from agent_orchestrator.runtime.validator import DeliverableValidator, ValidationReport
from agent_orchestrator.runtime.verification import TaskVerificationGate
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestDeliverableValidation(unittest.TestCase):
    def test_reject_arbitrary_garbage_json(self):
        """Valid JSON with arbitrary meaningless keys must be rejected by all contracts."""
        garbage = {"garbage": "but valid JSON"}

        # 1. Plan
        rep_plan = DeliverableValidator.validate_contract(ExecutionPlanContract, garbage)
        self.assertFalse(rep_plan.is_valid)
        self.assertIn("project_title", rep_plan.errors_summary())

        # 2. Spec
        rep_spec = DeliverableValidator.validate_contract(SpecificationContract, garbage)
        self.assertFalse(rep_spec.is_valid)
        self.assertIn("feature_name", rep_spec.errors_summary())

        # 3. Arch
        rep_arch = DeliverableValidator.validate_contract(ArchitectureContract, garbage)
        self.assertFalse(rep_arch.is_valid)
        self.assertIn("system_title", rep_arch.errors_summary())

    def test_reject_missing_required_fields(self):
        """Missing required top-level fields must produce clear schema errors."""
        # Missing project_title
        plan_missing_title = {
            "phases": [{"phase_number": 1, "name": "P1", "deliverables": ["file.py"]}],
        }
        rep = DeliverableValidator.validate_contract(ExecutionPlanContract, plan_missing_title)
        self.assertFalse(rep.is_valid)
        self.assertTrue(any("project_title" in err for err in rep.errors))

        # Missing feature_name
        spec_missing_feature = {
            "functional_requirements": [{"id": "FR-1", "description": "Do something"}],
        }
        rep_spec = DeliverableValidator.validate_contract(SpecificationContract, spec_missing_feature)
        self.assertFalse(rep_spec.is_valid)
        self.assertTrue(any("feature_name" in err for err in rep_spec.errors))

    def test_reject_type_mismatches(self):
        """Types that violate contract definitions must be rejected."""
        # phase_number must be int, not string that cannot coerce
        invalid_phase_type = {
            "project_title": "My Plan",
            "phases": [{"phase_number": "not_an_int", "name": "P1", "deliverables": ["a.py"]}],
        }
        rep = DeliverableValidator.validate_contract(ExecutionPlanContract, invalid_phase_type)
        self.assertFalse(rep.is_valid)
        self.assertTrue(any("phases" in err for err in rep.errors))

        # phases must be a list, not string
        phases_as_str = {
            "project_title": "My Plan",
            "phases": "phase 1 and phase 2",
        }
        rep2 = DeliverableValidator.validate_contract(ExecutionPlanContract, phases_as_str)
        self.assertFalse(rep2.is_valid)
        self.assertTrue(any("phases" in err for err in rep2.errors))

    def test_reject_constraint_violations(self):
        """Blank strings or invalid nested items must fail constraint checks."""
        # Blank project_title
        blank_title = {
            "project_title": "   ",
            "phases": [{"phase_number": 1, "name": "P1", "deliverables": ["d.py"]}],
        }
        rep = DeliverableValidator.validate_contract(ExecutionPlanContract, blank_title)
        self.assertFalse(rep.is_valid)
        self.assertIn("cannot be empty or blank", rep.errors_summary())

        # Empty phase name
        empty_phase_name = {
            "project_title": "Valid Title",
            "phases": [{"phase_number": 1, "name": "", "deliverables": ["d.py"]}],
        }
        rep2 = DeliverableValidator.validate_contract(ExecutionPlanContract, empty_phase_name)
        self.assertFalse(rep2.is_valid)
        self.assertIn("'name' cannot be empty", rep2.errors_summary())

    def test_accept_valid_contract_payloads(self):
        """Fully conformant payloads must validate and instantiate Pydantic models."""
        valid_plan = {
            "project_title": "Distributed Cache",
            "goal_summary": "High performance caching engine",
            "phases": [
                {
                    "phase_number": 1,
                    "name": "Core Implementation",
                    "description": "Build cache core",
                    "deliverables": ["src/cache.py"],
                    "agent_assigned": "CODER",
                }
            ],
            "milestones": ["Core complete"],
            "risk_mitigations": [{"risk": "Memory leak", "mitigation": "Set TTL"}],
            "success_criteria": ["Sub-millisecond latency"],
        }
        rep = DeliverableValidator.validate_contract(ExecutionPlanContract, valid_plan)
        self.assertTrue(rep.is_valid)
        self.assertEqual(len(rep.errors), 0)
        self.assertIsNotNone(rep.validated_model)
        self.assertEqual(rep.validated_model.project_title, "Distributed Cache")

    def test_react_loop_self_correction_on_schema_error(self):
        """
        When the model returns garbage JSON on turn 1, the ReAct loop must catch the schema error,
        prompt the model with specific error feedback, and accept the corrected schema on turn 2.
        """
        mock_llm = MagicMock()
        turn_count = 0

        def mock_chat_with_tools(messages, **kwargs):
            nonlocal turn_count
            turn_count += 1
            if turn_count == 1:
                # Turn 1: Return garbage JSON without required fields
                return {
                    "content": json.dumps({"garbage": "but valid JSON"}),
                    "tool_calls": [],
                }
            else:
                # Turn 2: Inspect message history to verify validation error was received,
                # then return conformant schema
                last_msg = messages[-1]["content"] if messages else ""
                self.assertIn("Deliverable Schema Validation Error", last_msg)
                return {
                    "content": json.dumps({
                        "project_title": "Cache System",
                        "goal_summary": "In-memory LRU cache",
                        "phases": [
                            {"phase_number": 1, "name": "Build", "deliverables": ["cache.py"]}
                        ],
                    }),
                    "tool_calls": [],
                }

        mock_llm.chat_with_tools.side_effect = mock_chat_with_tools

        react_loop = ReActAgentLoop(llm=mock_llm, tool_registry=MagicMock(), max_turns=5)
        result = react_loop.run(
            system_prompt="You are the Planner.",
            user_prompt="Plan the cache project.",
            target_contract=ExecutionPlanContract,
        )

        self.assertEqual(result["turns_taken"], 2)
        val_rep = result.get("validation_report")
        self.assertIsNotNone(val_rep)
        self.assertTrue(val_rep.get("is_valid"))
        self.assertEqual(result["final_output"]["project_title"], "Cache System")

    def test_verification_gate_fails_on_invalid_deliverable(self):
        """TaskVerificationGate must fail verification if task result_data contains an invalid validation report."""
        ws = WorkspaceManager()
        gate = TaskVerificationGate(workspace=ws)

        task = ExecutableTask(
            task_id="T-PLAN",
            objective="Generate Execution Plan",
            outputs=[],
            result_data={
                "validation_report": {
                    "is_valid": False,
                    "contract_name": "ExecutionPlanContract",
                    "errors": ["Field 'project_title': Field required", "Field 'phases': must contain at least 1 phase"],
                }
            },
        )

        v_res = gate.verify_task(task)
        self.assertFalse(v_res.passed)
        self.assertTrue(any("Deliverable Schema Validation Failed" in r for r in v_res.failure_reasons))


if __name__ == "__main__":
    unittest.main()
