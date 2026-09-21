"""
Tests for Semantic Plan & Cross-Stage Alignment Validator (Issue #57).
Covers:
1. Gate 1: Architecture satisfies Specification (functional requirements -> components)
2. Gate 2: Plan/DAG satisfies Specification & Architecture (tasks cover all components and requirements)
3. Gate 3: Implementation satisfies Architecture (workspace AST inspection for required files and symbols)
4. Gate 4: Tests cover Acceptance Criteria (test suite verification against ACs and edge cases)
5. Compensatory Task Generation
"""

import unittest
from unittest.mock import MagicMock
from agent_orchestrator.contracts import (
    ArchitectureContract,
    ComponentBlueprintContract,
    RequirementContract,
    SpecificationContract,
)
from agent_orchestrator.runtime.semantic_plan_validator import (
    SemanticPlanValidator,
    SemanticAlignmentReport,
)
from agent_orchestrator.runtime.task_graph import ExecutableTask


class TestSemanticPlanValidator(unittest.TestCase):
    """Test suite for SemanticPlanValidator across all four semantic gates."""

    def setUp(self):
        self.spec = SpecificationContract(
            feature_name="User Authentication",
            overview="Secure user authentication with JWT and password hashing",
            functional_requirements=[
                RequirementContract(id="FR-1", description="User registration with email and password"),
                RequirementContract(id="FR-2", description="JWT token issuance upon successful login"),
                RequirementContract(id="FR-3", description="Secure bcrypt password hashing and verification"),
            ],
            acceptance_criteria=[
                "AC-1: Valid credentials return 200 with JWT token",
                "AC-2: Invalid password returns 401 Unauthorized",
            ],
        )

        self.arch = ArchitectureContract(
            system_title="Auth Subsystem",
            design_patterns=["Repository Pattern", "Token Auth"],
            component_structure=[
                ComponentBlueprintContract(
                    module_name="src/auth.py",
                    purpose="Handles user registration and JWT token issuance",
                    classes_or_functions=[
                        {"name": "AuthService", "purpose": "Main authentication service"},
                        {"name": "login", "purpose": "Login endpoint handler"},
                    ],
                ),
                ComponentBlueprintContract(
                    module_name="src/security.py",
                    purpose="Bcrypt password hashing and token encryption",
                    classes_or_functions=[
                        {"name": "PasswordHasher", "purpose": "Hashes and verifies passwords"},
                    ],
                ),
            ],
            file_layout=[{"auth": "src/auth.py"}, {"security": "src/security.py"}],
            data_flow_description="Client requests -> auth.py validates credentials using security.py -> returns JWT",
        )

    # =========================================================================
    # GATE 1: Does Architecture Satisfy Specification?
    # =========================================================================
    def test_architecture_satisfies_specification_success(self):
        report = SemanticPlanValidator.verify_architecture_against_spec(self.spec, self.arch)
        self.assertTrue(report.is_aligned)
        self.assertGreaterEqual(report.coverage_score, 0.75)
        self.assertEqual(len(report.missing_items), 0)
        self.assertIn("SPEC_TO_ARCHITECTURE", report.alignment_type)

    def test_architecture_omits_specification_requirement(self):
        # Architecture omitting security/password hashing component
        incomplete_arch = ArchitectureContract(
            system_title="Incomplete Auth",
            component_structure=[
                ComponentBlueprintContract(
                    module_name="src/auth.py",
                    purpose="Handles user registration and login",
                    classes_or_functions=[{"name": "AuthService"}],
                )
            ],
            data_flow_description="Client sends login request -> auth.py returns token",
        )
        report = SemanticPlanValidator.verify_architecture_against_spec(self.spec, incomplete_arch, min_threshold=0.8)
        self.assertFalse(report.is_aligned)
        self.assertGreater(len(report.missing_items), 0)
        self.assertTrue(any("FR-3" in item or "bcrypt" in item or "hashing" in item for item in report.missing_items))

    # =========================================================================
    # GATE 2: Does Plan/DAG Satisfy Specification & Architecture?
    # =========================================================================
    def test_plan_satisfies_spec_and_arch_success(self):
        tasks = [
            ExecutableTask(
                task_id="T-01",
                objective="Implement AuthService in src/auth.py",
                inputs=["src/auth.py"],
                outputs=["src/auth.py:AuthService"],
            ),
            ExecutableTask(
                task_id="T-02",
                objective="Implement PasswordHasher in src/security.py",
                inputs=["src/security.py"],
                outputs=["src/security.py:PasswordHasher"],
            ),
        ]
        report = SemanticPlanValidator.verify_plan_against_spec_and_arch(self.spec, self.arch, tasks)
        self.assertTrue(report.is_aligned)
        self.assertEqual(len(report.missing_items), 0)

    def test_plan_drops_component_and_triggers_compensation(self):
        # Plan only builds src/auth.py, dropping src/security.py
        partial_tasks = [
            ExecutableTask(
                task_id="T-01",
                objective="Implement AuthService in src/auth.py",
                inputs=["src/auth.py"],
                outputs=["src/auth.py:AuthService"],
            ),
        ]
        report = SemanticPlanValidator.verify_plan_against_spec_and_arch(
            self.spec, self.arch, partial_tasks, min_threshold=0.8
        )
        self.assertFalse(report.is_aligned)
        self.assertTrue(any("security" in item for item in report.missing_items))

        # Test compensatory task generation
        comp_tasks = SemanticPlanValidator.generate_compensatory_tasks(report.missing_items)
        self.assertEqual(len(comp_tasks), len(report.missing_items))
        self.assertEqual(comp_tasks[0]["task_id"], "T-COMP-01")
        self.assertIn("security", comp_tasks[0]["objective"].lower())

    # =========================================================================
    # GATE 3: Does Implementation Satisfy Architecture?
    # =========================================================================
    def test_implementation_satisfies_arch_ast_inspection(self):
        mock_workspace = MagicMock()
        mock_workspace.list_files.return_value = ["src/auth.py", "src/security.py"]

        def fake_read(path):
            if "auth.py" in path:
                return {
                    "content": (
                        "class AuthService:\n"
                        "    def login(self, user, pwd):\n"
                        "        return 'token'\n"
                    )
                }
            elif "security.py" in path:
                return {
                    "content": (
                        "class PasswordHasher:\n"
                        "    def hash(self, pwd):\n"
                        "        return 'hashed'\n"
                    )
                }
            return {"content": ""}

        mock_workspace.read_file.side_effect = fake_read

        report = SemanticPlanValidator.verify_implementation_against_arch(self.arch, mock_workspace)
        self.assertTrue(report.is_aligned)
        self.assertEqual(report.coverage_score, 1.0)
        self.assertEqual(len(report.missing_items), 0)

    def test_implementation_missing_symbols_in_ast(self):
        mock_workspace = MagicMock()
        mock_workspace.list_files.return_value = ["src/auth.py", "src/security.py"]

        def fake_read(path):
            if "auth.py" in path:
                # Missing AuthService class, only has some random function
                return {"content": "def helper(): pass\n"}
            elif "security.py" in path:
                return {"content": "class PasswordHasher: pass\n"}
            return {"content": ""}

        mock_workspace.read_file.side_effect = fake_read

        report = SemanticPlanValidator.verify_implementation_against_arch(self.arch, mock_workspace, min_threshold=0.8)
        self.assertFalse(report.is_aligned)
        self.assertTrue(any("AuthService" in item for item in report.missing_items))

    # =========================================================================
    # GATE 4: Does Test Suite Cover Acceptance Criteria?
    # =========================================================================
    def test_tests_cover_acceptance_criteria_success(self):
        mock_workspace = MagicMock()
        mock_workspace.list_files.return_value = ["tests/test_auth.py"]
        mock_workspace.read_file.return_value = {
            "content": (
                "def test_ac1_valid_credentials_returns_200_jwt():\n"
                "    # AC-1: Valid credentials return 200 with JWT token\n"
                "    assert True\n\n"
                "def test_ac2_invalid_password_returns_401():\n"
                "    # AC-2: Invalid password returns 401 Unauthorized\n"
                "    assert True\n"
            )
        }

        report = SemanticPlanValidator.verify_tests_against_acceptance_criteria(self.spec, mock_workspace)
        self.assertTrue(report.is_aligned)
        self.assertEqual(report.coverage_score, 1.0)
        self.assertEqual(len(report.missing_items), 0)

    def test_tests_omit_acceptance_criteria(self):
        mock_workspace = MagicMock()
        mock_workspace.list_files.return_value = ["tests/test_auth.py"]
        mock_workspace.read_file.return_value = {
            # Only tests AC-1, omits AC-2
            "content": (
                "def test_login():\n"
                "    # AC-1: Valid credentials return 200 with JWT token\n"
                "    assert True\n"
            )
        }

        report = SemanticPlanValidator.verify_tests_against_acceptance_criteria(
            self.spec, mock_workspace, min_threshold=0.8
        )
        self.assertFalse(report.is_aligned)
        self.assertTrue(any("AC-2" in item or "Criterion #2" in item for item in report.missing_items))


if __name__ == "__main__":
    unittest.main()
