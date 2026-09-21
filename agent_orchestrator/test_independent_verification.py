"""
Unit and integration tests for Independent Verification Mechanisms (Issue #22):
- Multi-tier Static Verification Engine (Syntax, Compiler, Linter, Import Smoke)
- Polyglot toolchain commands in ProjectEnvironment
- Specification-First TesterAgent & Anti-tautology assertion detector
- Adversarial ReviewerAgent with guardrail overrides and rubric scoring
- Builtin static_code_check tool
"""
import ast
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.runtime.static_verifier import (
    StaticVerificationEngine,
    StaticVerificationReport,
    StaticVerificationTier,
)
from agent_orchestrator.runtime.project_detector import (
    ProjectEnvironment,
    ProjectEnvironmentDetector,
)
from agent_orchestrator.runtime.verification import TaskVerificationGate
from agent_orchestrator.agents.tester import TesterAgent
from agent_orchestrator.agents.reviewer import ReviewerAgent
from agent_orchestrator.state import OrchestratorState, ReviewVerdict
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestStaticVerificationEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="static_verif_test_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_python_syntax_valid(self):
        py_file = os.path.join(self.temp_dir, "valid.py")
        with open(py_file, "w", encoding="utf-8") as f:
            f.write("def calculate(x, y):\n    return x + y\n")

        engine = StaticVerificationEngine(workspace_dir=self.temp_dir)
        errors = engine.verify_syntax("valid.py")
        self.assertEqual(len(errors), 0)

    def test_python_syntax_invalid(self):
        py_file = os.path.join(self.temp_dir, "invalid.py")
        with open(py_file, "w", encoding="utf-8") as f:
            f.write("def broken(\n    return 42\n")

        engine = StaticVerificationEngine(workspace_dir=self.temp_dir)
        errors = engine.verify_syntax("invalid.py")
        self.assertGreaterEqual(len(errors), 1)
        self.assertIn("SyntaxError", errors[0])

    def test_json_syntax_valid_and_invalid(self):
        valid_json = os.path.join(self.temp_dir, "valid.json")
        with open(valid_json, "w", encoding="utf-8") as f:
            f.write('{"name": "agent", "enabled": true}')

        invalid_json = os.path.join(self.temp_dir, "invalid.json")
        with open(invalid_json, "w", encoding="utf-8") as f:
            f.write('{"name": "agent", "unclosed": }')

        engine = StaticVerificationEngine(workspace_dir=self.temp_dir)
        self.assertEqual(len(engine.verify_syntax("valid.json")), 0)
        invalid_errs = engine.verify_syntax("invalid.json")
        self.assertGreaterEqual(len(invalid_errs), 1)
        self.assertIn("JSON", invalid_errs[0])

    def test_bracket_matching_for_polyglot_files(self):
        valid_ts = os.path.join(self.temp_dir, "app.ts")
        with open(valid_ts, "w", encoding="utf-8") as f:
            f.write("function greet(name: string) { return `Hello, ${name}`; }")

        invalid_ts = os.path.join(self.temp_dir, "broken.ts")
        with open(invalid_ts, "w", encoding="utf-8") as f:
            f.write("function greet(name: string) { return `Hello, ${name}`; ")

        engine = StaticVerificationEngine(workspace_dir=self.temp_dir)
        self.assertEqual(len(engine.verify_syntax("app.ts")), 0)
        errs = engine.verify_syntax("broken.ts")
        self.assertGreaterEqual(len(errs), 1)
        self.assertIn("Unclosed", errs[0])

    def test_import_smoke_check_success(self):
        mod_file = os.path.join(self.temp_dir, "math_lib.py")
        with open(mod_file, "w", encoding="utf-8") as f:
            f.write("CONSTANT = 100\ndef add(a, b):\n    return a + b\n")

        engine = StaticVerificationEngine(workspace_dir=self.temp_dir)
        errors = engine.verify_import_smoke("math_lib.py")
        self.assertEqual(len(errors), 0)

    def test_import_smoke_check_failure(self):
        mod_file = os.path.join(self.temp_dir, "broken_import.py")
        with open(mod_file, "w", encoding="utf-8") as f:
            f.write("import definitely_non_existent_package_xyz_999\n")

        engine = StaticVerificationEngine(workspace_dir=self.temp_dir)
        errors = engine.verify_import_smoke("broken_import.py")
        self.assertGreaterEqual(len(errors), 1)
        self.assertIn("definitely_non_existent_package_xyz_999", errors[0])

    def test_toolchain_command_graceful_handling(self):
        engine = StaticVerificationEngine(workspace_dir=self.temp_dir)
        # Testing a non-existent binary skips gracefully without failing
        success, errors = engine.run_toolchain_command("fake_non_existent_toolchain_cmd_xyz arg1")
        self.assertTrue(success)
        self.assertEqual(len(errors), 0)

    def test_full_engine_verify_report(self):
        with open(os.path.join(self.temp_dir, "bad.py"), "w", encoding="utf-8") as f:
            f.write("def foo(\n")

        engine = StaticVerificationEngine(workspace_dir=self.temp_dir)
        report = engine.verify(files=["bad.py"])
        self.assertFalse(report.passed)
        self.assertGreaterEqual(len(report.syntax_errors), 1)
        self.assertFalse(report.tier_results["syntax"])


class TestProjectEnvironmentCommands(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="proj_env_test_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_python_commands_detected(self):
        with open(os.path.join(self.temp_dir, "requirements.txt"), "w") as f:
            f.write("pytest>=7.0.0\n")

        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.language.lower(), "python")
        self.assertIn("ruff", env.linter_command or "")
        self.assertIn("mypy", env.type_checker_command or "")

    def test_node_typescript_commands_detected(self):
        with open(os.path.join(self.temp_dir, "package.json"), "w") as f:
            f.write('{"name": "test-pkg", "scripts": {"test": "jest"}}')
        with open(os.path.join(self.temp_dir, "tsconfig.json"), "w") as f:
            f.write('{"compilerOptions": {}}')

        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.language.lower(), "typescript")
        self.assertEqual(env.compiler_command, "npx tsc --noEmit")
        self.assertEqual(env.linter_command, "npx eslint .")

    def test_rust_commands_detected(self):
        with open(os.path.join(self.temp_dir, "Cargo.toml"), "w") as f:
            f.write('[package]\nname = "rust-test"\nversion = "0.1.0"\n')

        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.language.lower(), "rust")
        self.assertEqual(env.compiler_command, "cargo check")
        self.assertEqual(env.linter_command, "cargo clippy")


class TestTesterAntiTautology(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="tester_anti_tautology_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_detects_assert_true_and_constant_comparisons(self):
        test_file = os.path.join(self.temp_dir, "test_dummy.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("""
import unittest

class DummyTest(unittest.TestCase):
    def test_trivial(self):
        assert True
        assert 1 == 1
        self.assertTrue(True)
        self.assertEqual("a", "a")
""")
        ws = WorkspaceManager(self.temp_dir)
        tester = TesterAgent(workspace=ws)
        warnings = tester._detect_trivial_assertions(test_file)
        self.assertGreaterEqual(len(warnings), 4)
        warning_text = "\n".join(warnings)
        self.assertIn("assert True", warning_text)
        self.assertIn("Trivial tautological comparison", warning_text)
        self.assertIn("self.assertTrue(True)", warning_text)
        self.assertIn("self.assertEqual('a', 'a')", warning_text)

    def test_clean_tests_have_no_tautology_warnings(self):
        test_file = os.path.join(self.temp_dir, "test_clean.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("""
import unittest

class CleanTest(unittest.TestCase):
    def test_genuine_behavior(self):
        x = 5 * 2
        self.assertEqual(x, 10)
        assert x > 0
""")
        ws = WorkspaceManager(self.temp_dir)
        tester = TesterAgent(workspace=ws)
        warnings = tester._detect_trivial_assertions(test_file)
        self.assertEqual(len(warnings), 0)


class TestAdversarialReviewer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="reviewer_test_")
        self.ws = WorkspaceManager(self.temp_dir)
        self.tools = BuiltinToolRegistry(workspace=self.ws)
        self.reviewer = ReviewerAgent(workspace=self.ws, tool_registry=self.tools)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_fails_when_tautological_warnings_exist_even_with_zero_exit_code(self):
        state = OrchestratorState(user_request="Build a math parser")
        state.specification_output = {"requirements": ["Parse addition and subtraction"]}
        state.code_output = {"written_files": ["math_parser.py"]}
        state.test_output = {
            "execution_success": True,
            "exit_code": 0,
            "stdout": "Ran 1 test in 0.001s\n\nOK",
            "stderr": "",
            "tautological_warnings": ["Line 6: Tautological assertion: assert True"],
        }

        # Mock react loop returning lenient output
        self.reviewer.react_loop = MagicMock()
        self.reviewer.react_loop.run.return_value = {
            "final_output": {
                "verdict": "PASS",
                "score_out_of_100": 98,
                "summary": "Looks good from LLM perspective"
            }
        }

        result = self.reviewer.execute(state)
        # Adversarial guardrail should downgrade to FAIL due to tautologies
        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual(state.verdict, ReviewVerdict.FAIL)
        self.assertLessEqual(result["score_out_of_100"], 50)
        self.assertEqual(result.get("target_agent_for_fix"), "TESTER")

    def test_fails_when_tests_failed(self):
        state = OrchestratorState(user_request="Build a string reverser")
        state.code_output = {"written_files": ["reverser.py"]}
        state.test_output = {
            "execution_success": False,
            "exit_code": 1,
            "stdout": "FAILED (failures=1)",
            "stderr": "AssertionError: 'abc' != 'cba'",
            "tautological_warnings": [],
        }

        self.reviewer.react_loop = MagicMock()
        self.reviewer.react_loop.run.return_value = {
            "final_output": {}
        }

        result = self.reviewer.execute(state)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual(state.verdict, ReviewVerdict.FAIL)
        self.assertEqual(result.get("target_agent_for_fix"), "CODER")

    def test_passes_when_clean_tests_and_static_checks_pass(self):
        state = OrchestratorState(user_request="Build an adder")
        state.specification_output = {"requirements": ["Adds two numbers"]}
        state.code_output = {"written_files": ["adder.py"]}
        state.test_output = {
            "execution_success": True,
            "exit_code": 0,
            "stdout": "Ran 2 tests in 0.002s\n\nOK",
            "stderr": "",
            "tautological_warnings": [],
        }

        self.reviewer.react_loop = MagicMock()
        self.reviewer.react_loop.run.return_value = {
            "final_output": {
                "verdict": "PASS",
                "score_out_of_100": 95,
                "summary": "All acceptance criteria verified with genuine unit tests.",
                "strengths": ["Clean modular design", "Proper boundary testing"],
                "issues": [],
            }
        }

        result = self.reviewer.execute(state)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(state.verdict, ReviewVerdict.PASS)


class TestBuiltinStaticCodeCheckTool(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="static_tool_test_")
        self.ws = WorkspaceManager(self.temp_dir)
        self.tools = BuiltinToolRegistry(workspace=self.ws)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tool_presence_for_roles(self):
        coder_tools = [t.name for t in self.tools.get_tools_for_agent("CODER")]
        tester_tools = [t.name for t in self.tools.get_tools_for_agent("TESTER")]
        reviewer_tools = [t.name for t in self.tools.get_tools_for_agent("REVIEWER")]

        self.assertIn("static_code_check", coder_tools)
        self.assertIn("static_code_check", tester_tools)
        self.assertIn("static_code_check", reviewer_tools)

    def test_tool_execution(self):
        with open(os.path.join(self.temp_dir, "module_ok.py"), "w", encoding="utf-8") as f:
            f.write("x = 10\ndef get_x():\n    return x\n")

        res = self.tools.call_tool("static_code_check", {})
        self.assertTrue(res.get("passed", False))
        self.assertTrue(res.get("success", False))
        self.assertEqual(len(res.get("syntax_errors", [])), 0)


class TestTaskVerificationGateStaticIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="gate_static_test_")
        self.ws = WorkspaceManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_gate_detects_syntax_errors(self):
        bad_file = os.path.join(self.temp_dir, "bad_syntax.py")
        with open(bad_file, "w", encoding="utf-8") as f:
            f.write("def foo(\n")

        gate = TaskVerificationGate(workspace=self.ws)
        task = {"outputs": ["bad_syntax.py"]}
        res = gate.verify_task(
            task=task,
            workspace=self.ws,
        )
        self.assertFalse(res.passed)
        self.assertTrue(any("SyntaxError" in r or "Static" in r for r in res.failure_reasons))


if __name__ == "__main__":
    unittest.main()
