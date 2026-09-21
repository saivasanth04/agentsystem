"""
Unit and integration tests for Issue #64: Controlled Dependency Installation Strategy.
Validates:
1. Identification of missing Python modules from stderr (including canonical mappings: jwt -> PyJWT, yaml -> PyYAML, PIL -> Pillow).
2. Identification of missing Node modules from stderr (including scoped packages).
3. Resolution of install commands across package managers (pip, uv, poetry, npm, pnpm, yarn).
4. Permission checking (auto-approve vs operator callback).
5. Sandbox installation and execution retry lifecycle.
6. FaultLocalizer classifying MISSING_DEPENDENCY rather than blaming CODER.
7. Diagnostician deterministic gate setting should_rollback=False.
"""
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.runtime.dependency_healer import (
    DependencyHealingEngine,
    MissingDependency,
    HealingResult,
    KNOWN_PYTHON_MAPPINGS,
)
from agent_orchestrator.runtime.fault_localization import EmpiricalFaultLocalizer
from agent_orchestrator.runtime.diagnostics import FailureDiagnostician


class TestDependencyHealer(unittest.TestCase):

    # -------------------------------------------------------------------------
    # 1. Identification: Python Standard & Submodules
    # -------------------------------------------------------------------------
    def test_identify_missing_python_dependency_standard(self):
        stderr = "Traceback (most recent call last):\n  File 'app.py', line 2, in <module>\nModuleNotFoundError: No module named 'requests'"
        dep = DependencyHealingEngine.identify_missing_dependency(stderr)
        self.assertIsNotNone(dep)
        self.assertEqual(dep.module_name, "requests")
        self.assertEqual(dep.package_name, "requests")
        self.assertEqual(dep.ecosystem, "python")

    def test_identify_missing_python_dependency_canonical_mappings(self):
        cases = [
            ("ModuleNotFoundError: No module named 'jwt'", "jwt", "PyJWT"),
            ("ModuleNotFoundError: No module named 'yaml'", "yaml", "PyYAML"),
            ("ModuleNotFoundError: No module named 'PIL'", "PIL", "Pillow"),
            ("ModuleNotFoundError: No module named 'cv2'", "cv2", "opencv-python"),
            ("ModuleNotFoundError: No module named 'sklearn'", "sklearn", "scikit-learn"),
            ("ModuleNotFoundError: No module named 'psycopg2'", "psycopg2", "psycopg2-binary"),
            ("ModuleNotFoundError: No module named 'dotenv'", "dotenv", "python-dotenv"),
        ]
        for error_msg, expected_module, expected_package in cases:
            dep = DependencyHealingEngine.identify_missing_dependency(error_msg)
            self.assertIsNotNone(dep, f"Failed for {error_msg}")
            self.assertEqual(dep.module_name, expected_module)
            self.assertEqual(dep.package_name, expected_package)

    def test_identify_missing_python_submodule(self):
        stderr = "ModuleNotFoundError: No module named 'jwt.algorithms'"
        dep = DependencyHealingEngine.identify_missing_dependency(stderr)
        self.assertIsNotNone(dep)
        self.assertEqual(dep.module_name, "jwt.algorithms")
        self.assertEqual(dep.package_name, "PyJWT")

    def test_identify_cannot_import_name(self):
        stderr = "ImportError: cannot import name 'FastAPI' from 'fastapi'"
        dep = DependencyHealingEngine.identify_missing_dependency(stderr)
        self.assertIsNotNone(dep)
        self.assertEqual(dep.module_name, "fastapi")
        self.assertEqual(dep.package_name, "fastapi")

    # -------------------------------------------------------------------------
    # 2. Identification: Node Modules
    # -------------------------------------------------------------------------
    def test_identify_missing_node_dependency(self):
        stderr = "Error: Cannot find module 'lodash'\nRequire stack:\n- /workspace/index.js"
        dep = DependencyHealingEngine.identify_missing_dependency(stderr)
        self.assertIsNotNone(dep)
        self.assertEqual(dep.module_name, "lodash")
        self.assertEqual(dep.package_name, "lodash")
        self.assertEqual(dep.ecosystem, "node")

    def test_identify_missing_node_scoped_package(self):
        stderr = "Error: Cannot find module '@nestjs/core'\nRequire stack:\n- /workspace/app.js"
        dep = DependencyHealingEngine.identify_missing_dependency(stderr)
        self.assertIsNotNone(dep)
        self.assertEqual(dep.module_name, "@nestjs/core")
        self.assertEqual(dep.package_name, "@nestjs/core")
        self.assertEqual(dep.ecosystem, "node")

    def test_ignore_relative_node_imports(self):
        stderr = "Error: Cannot find module './utils'\nRequire stack:\n- /workspace/app.js"
        dep = DependencyHealingEngine.identify_missing_dependency(stderr)
        self.assertIsNone(dep)

    # -------------------------------------------------------------------------
    # 3. Resolve Install Commands
    # -------------------------------------------------------------------------
    def test_resolve_install_commands_python(self):
        dep = MissingDependency(module_name="jwt", package_name="PyJWT", ecosystem="python")
        
        # pip
        cmd_pip = DependencyHealingEngine.resolve_install_command(dep, package_manager="pip", venv_python="/venv/bin/python")
        self.assertIn("-m pip install PyJWT", cmd_pip)

        # poetry
        cmd_poetry = DependencyHealingEngine.resolve_install_command(dep, package_manager="poetry")
        self.assertEqual(cmd_poetry, "poetry add PyJWT")

        # uv
        cmd_uv = DependencyHealingEngine.resolve_install_command(dep, package_manager="uv")
        self.assertEqual(cmd_uv, "uv add PyJWT")

    def test_resolve_install_commands_node(self):
        dep = MissingDependency(module_name="lodash", package_name="lodash", ecosystem="node")
        
        # npm
        cmd_npm = DependencyHealingEngine.resolve_install_command(dep, package_manager="npm")
        self.assertEqual(cmd_npm, "npm install lodash")

        # pnpm
        cmd_pnpm = DependencyHealingEngine.resolve_install_command(dep, package_manager="pnpm")
        self.assertEqual(cmd_pnpm, "pnpm add lodash")

        # yarn
        cmd_yarn = DependencyHealingEngine.resolve_install_command(dep, package_manager="yarn")
        self.assertEqual(cmd_yarn, "yarn add lodash")

    # -------------------------------------------------------------------------
    # 4. Permission Check
    # -------------------------------------------------------------------------
    def test_permission_check(self):
        dep = MissingDependency(module_name="jwt", package_name="PyJWT")

        # Auto-approve
        self.assertTrue(DependencyHealingEngine.check_permission(dep, auto_approve=True))

        # Operator callback: approved
        self.assertTrue(DependencyHealingEngine.check_permission(dep, operator_callback=lambda d: True))

        # Operator callback: denied
        self.assertFalse(DependencyHealingEngine.check_permission(dep, operator_callback=lambda d: False))

    # -------------------------------------------------------------------------
    # 5. Sandbox Installation & Heal-and-Retry Lifecycle
    # -------------------------------------------------------------------------
    def test_heal_and_retry_success(self):
        sandbox = MagicMock()
        
        # 1st call is install command (pip install PyJWT) -> exit_code 0
        # 2nd call is retry of failed command -> exit_code 0
        sandbox.run_command.side_effect = [
            {"exit_code": 0, "stdout": "Successfully installed PyJWT-2.8.0", "stderr": ""},
            {"exit_code": 0, "stdout": "All tests passed!", "stderr": ""},
        ]

        failed_cmd = "pytest tests/test_auth.py"
        stderr = "ModuleNotFoundError: No module named 'jwt'"

        result = DependencyHealingEngine.heal_and_retry(
            failed_command=failed_cmd,
            stderr=stderr,
            sandbox=sandbox,
            auto_approve=True,
        )

        self.assertTrue(result.success)
        self.assertTrue(result.permission_granted)
        self.assertTrue(result.installed)
        self.assertTrue(result.retry_success)
        self.assertEqual(result.dependency.package_name, "PyJWT")
        self.assertEqual(sandbox.run_command.call_count, 2)

    def test_heal_and_retry_permission_denied(self):
        sandbox = MagicMock()
        failed_cmd = "pytest tests/test_auth.py"
        stderr = "ModuleNotFoundError: No module named 'jwt'"

        result = DependencyHealingEngine.heal_and_retry(
            failed_command=failed_cmd,
            stderr=stderr,
            sandbox=sandbox,
            auto_approve=False,
            operator_callback=lambda d: False,
        )

        self.assertFalse(result.success)
        self.assertFalse(result.permission_granted)
        self.assertFalse(result.installed)
        self.assertIn("Permission denied", result.error)
        sandbox.run_command.assert_not_called()

    # -------------------------------------------------------------------------
    # 6. FaultLocalizer Integration
    # -------------------------------------------------------------------------
    def test_fault_localizer_classifies_missing_dependency(self):
        stderr = "Traceback (most recent call last):\n  File 'app.py', line 1, in <module>\nModuleNotFoundError: No module named 'yaml'"
        locus = EmpiricalFaultLocalizer.localize_fault(
            execution_stderr=stderr,
            execution_stdout="",
        )
        self.assertEqual(locus.locus_type, "MISSING_DEPENDENCY")
        self.assertEqual(locus.ground_truth_attribution, "DEPENDENCY_MANAGER")
        self.assertGreaterEqual(locus.confidence, 0.95)
        self.assertIn("PyYAML", locus.rationale)

    # -------------------------------------------------------------------------
    # 7. Diagnostician Integration (No Destructive Rollback)
    # -------------------------------------------------------------------------
    def test_diagnostician_intercepts_missing_dependency(self):
        diagnostician = FailureDiagnostician(llm=MagicMock())
        stderr = "Traceback (most recent call last):\n  File 'app.py', line 1, in <module>\nModuleNotFoundError: No module named 'jwt'"
        
        report = diagnostician.diagnose_failure(
            task_title="Test Auth",
            execution_stderr=stderr,
            execution_stdout="",
        )
        self.assertEqual(report.failure_type, "MISSING_DEPENDENCY")
        self.assertEqual(report.target_agent, "DEPENDENCY_MANAGER")
        self.assertFalse(report.should_rollback)
        self.assertFalse(report.regression_detected)
        self.assertIn("PyJWT", report.root_cause_summary)


if __name__ == "__main__":
    unittest.main()
