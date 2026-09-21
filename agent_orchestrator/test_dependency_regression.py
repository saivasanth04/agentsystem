"""
Unit tests for Dependency-Aware Regression Detection and Blast-Radius Protection (Issue #60).
Tests:
1. DependencyImpact analysis identifying downstream implementation and test files.
2. Regression detection catching broken dependent files and failing tests.
3. TaskVerificationGate pre-merge protection preventing regressions from merging.
4. GroundTruthVerificationMatrix failing BASELINE_REGRESSIONS on cross-module breakages.
5. EvidenceSynthesizer integrating and formatting the regression report.
"""
from pathlib import Path
import tempfile
import unittest

from agent_orchestrator.runtime.regression_detector import (
    DependencyImpact,
    RegressionReport,
    DependencyRegressionDetector,
)
from agent_orchestrator.runtime.verification import TaskVerificationGate
from agent_orchestrator.runtime.task_graph import ExecutableTask
from agent_orchestrator.runtime.verification_matrix import GroundTruthVerificationMatrix
from agent_orchestrator.runtime.evidence import EvidenceSynthesizer


class MockWorkspace:
    def __init__(self, files=None):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self._temp_dir.name)
        self.files = files or {}
        for rel_path, content in self.files.items():
            p = self.root_dir / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    def cleanup(self):
        try:
            self._temp_dir.cleanup()
        except Exception:
            pass

    def list_files(self):
        return list(self.files.keys())

    def read_file(self, filepath):
        norm = filepath.replace("\\", "/").lstrip("/")
        return {"content": self.files.get(norm, "")}


class TestDependencyRegression(unittest.TestCase):

    def setUp(self):
        self._workspaces = []

    def tearDown(self):
        for ws in self._workspaces:
            ws.cleanup()

    def create_workspace(self, files=None):
        ws = MockWorkspace(files=files)
        self._workspaces.append(ws)
        return ws

    def test_impact_analysis_identifies_dependents(self):
        ws = self.create_workspace(
            files={
                "users.py": "def get_user(uid): return {'id': uid}",
                "payments.py": "from users import get_user\ndef charge(uid, amount): user = get_user(uid)",
                "unrelated.py": "def foo(): return 42",
                "tests/test_users.py": "import users\ndef test_u(): pass",
                "tests/test_payments.py": "import payments\ndef test_p(): pass",
            }
        )

        impact = DependencyRegressionDetector.analyze_impact(
            modified_files=["users.py"],
            workspace=ws,
        )

        self.assertIn("users.py", impact.modified_files)
        self.assertIn("payments.py", impact.impacted_impl_files)
        self.assertNotIn("unrelated.py", impact.impacted_impl_files)
        self.assertIn("tests/test_payments.py", impact.impacted_test_files)
        self.assertGreater(impact.blast_radius_score, 0)

    def test_verify_regressions_clean_passes(self):
        ws = self.create_workspace(
            files={
                "users.py": "def get_user(uid): return {'id': uid}",
                "payments.py": "from users import get_user\ndef charge(uid): return get_user(uid)",
                "tests/test_payments.py": "import payments\ndef test_p(): pass",
            }
        )

        report = DependencyRegressionDetector.verify_regressions(
            modified_files=["users.py"],
            workspace=ws,
            test_info={"execution_success": True, "exit_code": 0},
        )

        self.assertTrue(report.passed)
        self.assertEqual(len(report.broken_dependents), 0)
        self.assertIn("Zero regressions", report.details)

    def test_verify_regressions_detects_broken_syntax_in_dependent(self):
        ws = self.create_workspace(
            files={
                "users.py": "def get_user(uid): return {'id': uid}",
                "payments.py": "from users import get_user\ndef charge(uid): return get_user(uid) syntax error here !!!",
            }
        )

        report = DependencyRegressionDetector.verify_regressions(
            modified_files=["users.py"],
            workspace=ws,
        )

        self.assertFalse(report.passed)
        self.assertGreater(len(report.broken_dependents), 0)
        self.assertTrue(any("payments.py" in bd for bd in report.broken_dependents))

    def test_verify_regressions_detects_failing_dependent_test(self):
        ws = self.create_workspace(
            files={
                "users.py": "def get_user(uid): return {'id': uid}",
                "payments.py": "from users import get_user\ndef charge(uid): return get_user(uid)",
                "tests/test_payments.py": "import payments\ndef test_p(): assert False",
            }
        )

        report = DependencyRegressionDetector.verify_regressions(
            modified_files=["users.py"],
            workspace=ws,
            test_info={
                "execution_success": False,
                "exit_code": 1,
                "stdout": "tests/test_payments.py::test_p FAILED",
                "stderr": "AssertionError",
            },
        )

        self.assertFalse(report.passed)
        self.assertGreater(len(report.failing_tests), 0)
        self.assertTrue(any("test_payments.py" in ft for ft in report.failing_tests))

    def test_task_verification_gate_catches_dependent_regression(self):
        ws = self.create_workspace(
            files={
                "users.py": "def get_user(uid): return {'id': uid}",
                "payments.py": "from users import get_user\ndef charge(uid): return get_user(uid) def invalid",
            }
        )

        gate = TaskVerificationGate(workspace=ws)
        task = ExecutableTask(
            task_id="T-1",
            objective="Update users module",
            outputs=["users.py"],
        )

        v_res = gate.verify_task(task, workspace=ws)
        self.assertFalse(v_res.passed)
        self.assertIsNotNone(v_res.regression_report)
        self.assertFalse(v_res.regression_report["passed"])
        self.assertTrue(any("payments.py" in str(r) for r in v_res.failure_reasons))

    def test_task_verification_gate_passes_clean_dependent(self):
        ws = self.create_workspace(
            files={
                "users.py": "def get_user(uid): return {'id': uid}",
                "payments.py": "from users import get_user\ndef charge(uid): return get_user(uid)",
            }
        )

        gate = TaskVerificationGate(workspace=ws)
        task = ExecutableTask(
            task_id="T-1",
            objective="Update users module",
            outputs=["users.py"],
        )

        v_res = gate.verify_task(task, workspace=ws)
        self.assertTrue(v_res.passed)
        self.assertIsNotNone(v_res.regression_report)
        self.assertTrue(v_res.regression_report["passed"])

    def test_eval_regression_gate_fails_on_broken_dependent(self):
        ws = self.create_workspace(
            files={
                "users.py": "def get_user(uid): return {'id': uid}",
                "payments.py": "from users import get_user\ndef charge(uid): return get_user(uid) broken syntax",
            }
        )

        matrix = GroundTruthVerificationMatrix(workspace=ws)
        gt_report = matrix.evaluate(
            state={
                "test_output": {"execution_success": True, "exit_code": 0},
                "baseline_test_info": {"execution_success": True, "exit_code": 0},
            },
            modified_files=["users.py"],
        )

        self.assertFalse(gt_report.passed)
        reg_gate = gt_report.gates.get("BASELINE_REGRESSIONS")
        self.assertIsNotNone(reg_gate)
        self.assertFalse(reg_gate.passed)
        self.assertIn("Regression detected", reg_gate.details)

    def test_evidence_synthesizer_integrates_regression_report(self):
        ws = self.create_workspace(
            files={
                "users.py": "def get_user(uid): return {'id': uid}",
                "payments.py": "from users import get_user\ndef charge(uid): return get_user(uid)",
            }
        )

        ev = EvidenceSynthesizer.synthesize(
            state={"code_output": {"written_files": ["users.py"]}},
            test_info={"execution_success": True, "exit_code": 0},
            workspace=ws,
            modified_files=["users.py"],
        )

        self.assertIsNotNone(ev.regression_report)
        self.assertTrue(ev.regression_report["passed"])
        md = ev.to_markdown()
        self.assertIn("Regression Protection", md)
        self.assertIn("✅ CLEAN", md)


if __name__ == "__main__":
    unittest.main()
