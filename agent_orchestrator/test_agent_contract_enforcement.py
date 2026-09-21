"""
Tests for Inter-Agent Contract Enforcement (Issue #58).
Covers:
1. Contract satisfaction when Coder delivers required files and symbols.
2. Contract violation when Coder substitutes unauthorized files (e.g. users.py -> account.py).
3. Missing symbol detection via AST inspection.
4. In-loop deliverable rejection in ReActAgentLoop.
5. Post-task rejection at TaskVerificationGate.
6. Automatic contract derivation from ArchitectureContract and ExecutableTask.
"""

import unittest
from unittest.mock import MagicMock, patch
from agent_orchestrator.contracts import (
    ArchitectureContract,
    ComponentBlueprintContract,
    CodeDeliverableContract,
)
from agent_orchestrator.runtime.agent_contract import (
    InterAgentContract,
    ContractEnforcementReport,
    AgentContractEnforcer,
)
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.runtime.task_graph import ExecutableTask
from agent_orchestrator.runtime.verification import TaskVerificationGate


class TestAgentContractEnforcement(unittest.TestCase):
    """Test suite for Inter-Agent Contract Enforcement."""

    def setUp(self):
        self.contract = InterAgentContract(
            source_agent="ARCHITECTURE",
            target_agent="CODER",
            task_id="T-01",
            required_files=["src/users.py"],
            required_symbols={"src/users.py": ["UserManager", "create_user"]},
            strict_file_matching=True,
        )

    def test_contract_satisfaction(self):
        mock_ws = MagicMock()
        mock_ws.list_files.return_value = ["src/users.py"]
        mock_ws.read_file.return_value = {
            "content": (
                "class UserManager:\n"
                "    pass\n\n"
                "def create_user(name):\n"
                "    return {'name': name}\n"
            )
        }
        candidate = {
            "summary": "Implemented users.py with UserManager and create_user",
            "written_files": ["src/users.py"],
        }
        report = AgentContractEnforcer.validate_deliverable(self.contract, candidate, mock_ws)
        self.assertTrue(report.is_valid)
        self.assertEqual(len(report.violations), 0)
        self.assertIn("src/users.py", report.satisfied_files)

    def test_contract_substitution_violation(self):
        """Architecture says users.py, but coder returns account.py."""
        mock_ws = MagicMock()
        mock_ws.list_files.return_value = ["src/account.py"]
        mock_ws.read_file.return_value = {"content": "class AccountManager: pass\n"}

        # Coder substituted account.py for users.py
        candidate = {
            "summary": "Implemented account management in account.py",
            "written_files": ["src/account.py"],
        }
        report = AgentContractEnforcer.validate_deliverable(self.contract, candidate, mock_ws)
        self.assertFalse(report.is_valid)
        self.assertGreater(len(report.violations), 0)
        # Should flag missing users.py AND unauthorized substitution of account.py
        self.assertIn("src/users.py", report.missing_files)
        self.assertTrue(any("account.py" in v for v in report.violations))
        self.assertTrue(any("users.py" in v for v in report.violations))

    def test_missing_required_symbols(self):
        """Coder creates users.py but omits UserManager class."""
        mock_ws = MagicMock()
        mock_ws.list_files.return_value = ["src/users.py"]
        mock_ws.read_file.return_value = {
            "content": (
                "def create_user(name):\n"
                "    return {'name': name}\n"
            )
        }
        candidate = {
            "summary": "Implemented create_user in users.py",
            "written_files": ["src/users.py"],
        }
        report = AgentContractEnforcer.validate_deliverable(self.contract, candidate, mock_ws)
        self.assertFalse(report.is_valid)
        self.assertTrue(any("UserManager" in v for v in report.violations))

    def test_in_loop_rejection_in_react_loop(self):
        """Verify ReActAgentLoop rejects deliverable that violates InterAgentContract."""
        mock_llm = MagicMock()
        mock_registry = MagicMock()
        mock_ws = MagicMock()
        mock_ws.list_files.return_value = ["src/account.py"]
        mock_registry.workspace = mock_ws

        loop = ReActAgentLoop(llm=mock_llm, tool_registry=mock_registry)
        loop.current_agent_contract = self.contract
        loop.current_workspace = mock_ws

        # Candidate returns account.py instead of users.py
        candidate = {
            "summary": "Created account.py",
            "written_files": ["src/account.py"],
        }
        val_report = loop._validate_candidate_deliverable(
            candidate=candidate,
            target_contract=CodeDeliverableContract,
            agent_name="CODER",
        )
        self.assertIsNotNone(val_report)
        self.assertFalse(val_report.is_valid)
        self.assertTrue(any("users.py" in err for err in val_report.errors))

    def test_verification_gate_fails_on_contract_violation(self):
        """Verify TaskVerificationGate fails task verification when contract is violated."""
        mock_ws = MagicMock()
        mock_ws.root_dir = MagicMock()
        mock_ws.list_files.return_value = ["src/account.py"]
        mock_ws.read_file.return_value = {"content": "class AccountManager: pass\n"}

        gate = TaskVerificationGate(workspace=mock_ws)

        task = ExecutableTask(
            task_id="T-01",
            objective="Implement users.py",
            outputs=["src/users.py"],
        )
        task.agent_contract = self.contract
        task.result_data = {
            "summary": "Done account.py",
            "written_files": ["src/account.py"],
        }

        v_res = gate.verify_task(task)
        self.assertFalse(v_res.passed)
        self.assertTrue(any("Contract Violation" in r or "users.py" in r for r in v_res.failure_reasons))

    def test_from_architecture_and_task_derivation(self):
        """Verify automatic derivation of InterAgentContract from Architecture and Task."""
        arch = ArchitectureContract(
            system_title="User System",
            component_structure=[
                ComponentBlueprintContract(
                    module_name="src/users.py",
                    purpose="User management",
                    classes_or_functions=[{"name": "UserManager"}, {"name": "create_user"}],
                )
            ],
        )
        task = ExecutableTask(
            task_id="T-01",
            objective="Build users module",
            outputs=["src/users.py:UserManager", "src/users.py:create_user"],
        )

        contract = InterAgentContract.from_architecture_and_task(arch, task)
        self.assertIsNotNone(contract)
        self.assertIn("src/users.py", contract.required_files)
        self.assertIn("UserManager", contract.required_symbols["src/users.py"])
        self.assertIn("create_user", contract.required_symbols["src/users.py"])


if __name__ == "__main__":
    unittest.main()
