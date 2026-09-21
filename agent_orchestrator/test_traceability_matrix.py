"""
Unit tests for End-to-End Acceptance Criteria & Requirement Traceability Matrix (Issue #59).
Tests:
1. TestExecutionParser (JUnit XML, unittest -v, pytest stdout).
2. TraceabilityEngine building full chain: FR -> Implementation -> Test -> Execution Evidence.
3. Traceability status determination (VERIFIED, IMPLEMENTED_UNTESTED, TEST_FAILED, UNIMPLEMENTED).
4. Backward compatibility with legacy string acceptance criteria.
5. EvidenceSynthesizer integration and markdown rendering.
"""
from pathlib import Path
import tempfile
import unittest

from agent_orchestrator.contracts import (
    RequirementContract,
    SpecificationContract,
    ArchitectureContract,
    ComponentBlueprintContract,
)
from agent_orchestrator.runtime.test_evidence_parser import TestExecutionParser, TestCaseEvidence
from agent_orchestrator.runtime.traceability import (
    TraceabilityEngine,
    TraceabilityMatrix,
    RequirementTraceNode,
    TraceStatus,
)
from agent_orchestrator.runtime.evidence import EvidenceSynthesizer


class MockWorkspace:
    def __init__(self, files=None):
        self.files = files or {}

    def list_files(self):
        return list(self.files.keys())

    def read_file(self, filepath):
        norm = filepath.replace("\\", "/")
        return {"content": self.files.get(norm, "")}


class TestTraceabilityMatrix(unittest.TestCase):

    def test_test_evidence_parser_junit_xml(self):
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="1" tests="3" time="0.123">
    <testcase classname="tests.test_auth.TestAuth" name="test_login_success" time="0.045" file="tests/test_auth.py"/>
    <testcase classname="tests.test_auth.TestAuth" name="test_login_failure" time="0.030" file="tests/test_auth.py">
      <failure message="AssertionError: 401 != 200">Assertion details...</failure>
    </testcase>
    <testcase classname="tests.test_auth.TestAuth" name="test_oauth_skip" time="0.001" file="tests/test_auth.py">
      <skipped message="OAuth provider disabled"/>
    </testcase>
  </testsuite>
</testsuites>
"""
        evidence = TestExecutionParser.parse_junit_xml(xml_content)
        self.assertEqual(len(evidence), 3)

        self.assertEqual(evidence[0].test_name, "test_login_success")
        self.assertEqual(evidence[0].status, "PASSED")
        self.assertTrue(evidence[0].passed)
        self.assertEqual(evidence[0].duration_seconds, 0.045)

        self.assertEqual(evidence[1].test_name, "test_login_failure")
        self.assertEqual(evidence[1].status, "FAILED")
        self.assertFalse(evidence[1].passed)
        self.assertIn("AssertionError", evidence[1].message)

        self.assertEqual(evidence[2].test_name, "test_oauth_skip")
        self.assertEqual(evidence[2].status, "SKIPPED")

    def test_test_evidence_parser_pytest_stdout(self):
        stdout = """
============================= test session starts ==============================
tests/test_auth.py::test_login PASSED                                    [ 33%]
tests/test_auth.py::TestUser::test_create_user PASSED                    [ 66%]
tests/test_auth.py::test_lockout FAILED                                  [100%]
============================== 1 failed, 2 passed in 0.24s ====================
"""
        evidence = TestExecutionParser.parse_pytest_stdout(stdout)
        self.assertEqual(len(evidence), 3)

        self.assertEqual(evidence[0].test_file, "tests/test_auth.py")
        self.assertEqual(evidence[0].test_name, "test_login")
        self.assertEqual(evidence[0].status, "PASSED")

        self.assertEqual(evidence[1].classname, "TestUser")
        self.assertEqual(evidence[1].test_name, "test_create_user")
        self.assertEqual(evidence[1].status, "PASSED")

        self.assertEqual(evidence[2].test_name, "test_lockout")
        self.assertEqual(evidence[2].status, "FAILED")

    def test_test_evidence_parser_unittest_stdout(self):
        stdout = """
test_login_success (tests.test_auth.TestAuth) ... ok
test_invalid_credentials (tests.test_auth.TestAuth) ... FAIL
test_error_handling (tests.test_auth.TestAuth) ... ERROR

======================================================================
FAIL: test_invalid_credentials (tests.test_auth.TestAuth)
----------------------------------------------------------------------
"""
        evidence = TestExecutionParser.parse_unittest_stdout(stdout)
        self.assertEqual(len(evidence), 3)

        self.assertEqual(evidence[0].test_name, "test_login_success")
        self.assertEqual(evidence[0].status, "PASSED")

        self.assertEqual(evidence[1].test_name, "test_invalid_credentials")
        self.assertEqual(evidence[1].status, "FAILED")

        self.assertEqual(evidence[2].test_name, "test_error_handling")
        self.assertEqual(evidence[2].status, "ERROR")

    def test_full_traceability_chain_verified(self):
        # 1. Spec with FR-1
        spec = SpecificationContract(
            feature_name="Authentication",
            functional_requirements=[
                RequirementContract(
                    id="FR-1",
                    description="User password authentication and token generation",
                    acceptance_criteria=["Return JWT on valid password"],
                )
            ],
        )

        # 2. Workspace with implementation and test
        auth_code = """
class UserManager:
    \"\"\"Manages user authentication [FR-1].\"\"\"
    def authenticate(self, username, password):
        return "token_123"
"""
        test_code = """
from auth import UserManager

def test_authenticate_success():
    \"\"\"Verifies FR-1 password authentication.\"\"\"
    mgr = UserManager()
    token = mgr.authenticate("alice", "secret")
    assert token == "token_123"
"""
        ws = MockWorkspace(
            files={
                "auth.py": auth_code,
                "tests/test_auth.py": test_code,
            }
        )

        # 3. Test execution evidence (PASSED)
        test_results = {
            "execution_success": True,
            "exit_code": 0,
            "stdout": "tests/test_auth.py::test_authenticate_success PASSED\n",
            "stderr": "",
        }

        matrix = TraceabilityEngine.build_matrix(spec=spec, workspace=ws, test_results=test_results)
        self.assertEqual(matrix.total_requirements, 1)
        self.assertEqual(matrix.verified_requirements, 1)
        self.assertEqual(matrix.traceability_score, 100.0)

        node = matrix.nodes["FR-1"]
        self.assertEqual(node.status, TraceStatus.VERIFIED)
        self.assertIn("auth.py", node.implementation_files)
        self.assertIn("tests/test_auth.py::test_authenticate_success", node.test_cases)
        self.assertEqual(len(node.evidence), 1)
        self.assertEqual(node.evidence[0].status, "PASSED")

    def test_traceability_chain_unimplemented(self):
        spec = SpecificationContract(
            feature_name="Billing",
            functional_requirements=[
                RequirementContract(
                    id="FR-2",
                    description="Stripe subscription billing webhooks",
                )
            ],
        )
        ws = MockWorkspace(files={"unrelated.py": "x = 1"})
        matrix = TraceabilityEngine.build_matrix(spec=spec, workspace=ws)

        node = matrix.nodes["FR-2"]
        self.assertEqual(node.status, TraceStatus.UNIMPLEMENTED)
        self.assertIn("No implementation files", node.unverified_reason)

    def test_traceability_chain_implemented_untested(self):
        spec = SpecificationContract(
            feature_name="Profile",
            functional_requirements=[
                RequirementContract(
                    id="FR-3",
                    description="User profile avatar upload service",
                )
            ],
        )
        ws = MockWorkspace(
            files={
                "profile.py": """
class ProfileService:
    \"\"\"Handles profile avatar upload [FR-3].\"\"\"
    def upload_avatar(self, user_id, data):
        pass
""",
                "tests/test_other.py": "def test_unrelated(): assert True",
            }
        )
        matrix = TraceabilityEngine.build_matrix(spec=spec, workspace=ws)

        node = matrix.nodes["FR-3"]
        self.assertEqual(node.status, TraceStatus.IMPLEMENTED_UNTESTED)
        self.assertIn("profile.py", node.implementation_files)
        self.assertEqual(node.test_cases, [])

    def test_traceability_chain_test_failed(self):
        spec = SpecificationContract(
            feature_name="Orders",
            functional_requirements=[
                RequirementContract(
                    id="FR-4",
                    description="Order checkout and inventory deduction",
                )
            ],
        )
        ws = MockWorkspace(
            files={
                "orders.py": """
class OrderService:
    def checkout(self, order_id):
        return False
""",
                "tests/test_orders.py": """
from orders import OrderService

def test_checkout_inventory():
    \"\"\"Tests FR-4 order checkout.\"\"\"
    svc = OrderService()
    assert svc.checkout("order_1") is True
""",
            }
        )
        test_results = {
            "execution_success": False,
            "exit_code": 1,
            "stdout": "tests/test_orders.py::test_checkout_inventory FAILED\n",
            "stderr": "AssertionError: False is not True",
        }

        matrix = TraceabilityEngine.build_matrix(spec=spec, workspace=ws, test_results=test_results)
        node = matrix.nodes["FR-4"]
        self.assertEqual(node.status, TraceStatus.TEST_FAILED)
        self.assertIn("failed execution", node.unverified_reason)

    def test_backward_compatibility_with_string_criteria(self):
        spec = {
            "feature_name": "Legacy Feature",
            "acceptance_criteria": [
                "AC-1: Valid user signup",
                "AC-2: Duplicate email rejection",
            ],
        }
        ws = MockWorkspace(
            files={
                "signup.py": """
def signup(email):
    return True
""",
                "tests/test_signup.py": """
from signup import signup

def test_ac1_valid_signup():
    assert signup("test@example.com") is True
""",
            }
        )
        test_results = {
            "execution_success": True,
            "exit_code": 0,
            "stdout": "tests/test_signup.py::test_ac1_valid_signup PASSED\n",
        }

        matrix = TraceabilityEngine.build_matrix(spec=spec, workspace=ws, test_results=test_results)
        self.assertEqual(matrix.total_requirements, 2)
        self.assertIn("AC-1", matrix.nodes)
        self.assertIn("AC-2", matrix.nodes)

        self.assertEqual(matrix.nodes["AC-1"].status, TraceStatus.VERIFIED)
        self.assertEqual(matrix.nodes["AC-2"].status, TraceStatus.UNIMPLEMENTED)

    def test_evidence_synthesizer_integrates_traceability_matrix(self):
        spec = {
            "feature_name": "Search",
            "functional_requirements": [
                {
                    "id": "FR-1",
                    "description": "Fuzzy full text search index",
                    "acceptance_criteria": ["Return top 10 results"],
                }
            ],
        }
        ws = MockWorkspace(
            files={
                "search.py": "def search(query): return ['res1']",
                "tests/test_search.py": "from search import search\ndef test_search_fr1(): assert len(search('q')) > 0",
            }
        )
        test_info = {
            "execution_success": True,
            "exit_code": 0,
            "stdout": "tests/test_search.py::test_search_fr1 PASSED\n",
        }

        evidence = EvidenceSynthesizer.synthesize(
            state={"specification_output": spec, "workspace": ws},
            test_info=test_info,
            workspace=ws,
        )

        self.assertIsNotNone(evidence.traceability_matrix)
        self.assertEqual(evidence.acceptance_criteria.get("FR-1"), "verified")
        md = evidence.to_markdown()
        self.assertIn("Requirements & Acceptance Criteria Traceability", md)
        self.assertIn("FR-1", md)
        self.assertIn("🟢 VERIFIED", md)


if __name__ == "__main__":
    unittest.main()
