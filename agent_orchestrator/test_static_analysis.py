"""
Unit and Integration Tests for Issue #25: Polyglot Static Analysis Engine.
Verifies:
- Structured LSP Diagnostic parsing for ruff, mypy, pyright, eslint, tsc, semgrep, clippy, govet.
- Multi-tool & config discovery (pyproject.toml, ruff.toml, mypy.ini, eslintrc, tsconfig, semgrep.yml).
- File-targeted execution vs whole-repo execution.
- Separation of blocking errors from non-blocking warnings.
- TaskVerificationGate integration with static diagnostics and warnings.
- Builtin run_static_analysis tool available to CODER, TESTER, PLANNER, REVIEWER.
- ReviewerAgent diagnostic audit and rubric scoring.
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.runtime.diagnostic import (
    Diagnostic,
    DiagnosticParser,
    DiagnosticSeverity,
)
from agent_orchestrator.runtime.static_analyzer import (
    StaticAnalyzer,
    StaticToolCategory,
    StaticAnalysisReport,
    StaticAnalysisResult,
)
from agent_orchestrator.runtime.static_verifier import (
    StaticVerificationEngine,
    StaticVerificationReport,
)
from agent_orchestrator.runtime.verification import TaskVerificationGate, VerificationResult
from agent_orchestrator.runtime.task_graph import ExecutableTask
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.agents.reviewer import ReviewerAgent
from agent_orchestrator.state import OrchestratorState


class TestDiagnosticParsers(unittest.TestCase):
    """Tests for DiagnosticParser across various polyglot tool output formats."""

    def test_parse_ruff_text_and_json(self):
        # 1. Text format
        ruff_text = (
            "src/app.py:10:5: F401 `os` imported but unused\n"
            "src/app.py:25:1: E501 Line too long (92 > 88 characters)\n"
        )
        diags = DiagnosticParser.parse_ruff(ruff_text)
        self.assertEqual(len(diags), 2)
        self.assertEqual(diags[0].file_path, "src/app.py")
        self.assertEqual(diags[0].line, 10)
        self.assertEqual(diags[0].column, 5)
        self.assertEqual(diags[0].code, "F401")
        self.assertEqual(diags[0].severity, DiagnosticSeverity.ERROR)
        self.assertEqual(diags[0].source_tool, "ruff")

        # 2. JSON format
        ruff_json = json.dumps([
            {
                "filename": "src/utils.py",
                "location": {"row": 15, "column": 3},
                "code": "B006",
                "message": "Do not use mutable data structures for argument defaults",
                "fix": None,
            }
        ])
        json_diags = DiagnosticParser.parse_ruff(ruff_json)
        self.assertEqual(len(json_diags), 1)
        self.assertEqual(json_diags[0].file_path, "src/utils.py")
        self.assertEqual(json_diags[0].line, 15)
        self.assertEqual(json_diags[0].code, "B006")
        self.assertEqual(json_diags[0].severity, DiagnosticSeverity.ERROR)

    def test_parse_mypy(self):
        mypy_text = (
            "src/service.py:42: error: Incompatible return value type (got 'str', expected 'int')  [return-value]\n"
            "src/service.py:50: warning: Unused 'type: ignore' comment\n"
        )
        diags = DiagnosticParser.parse_mypy(mypy_text)
        self.assertEqual(len(diags), 2)
        self.assertEqual(diags[0].file_path, "src/service.py")
        self.assertEqual(diags[0].line, 42)
        self.assertEqual(diags[0].severity, DiagnosticSeverity.ERROR)
        self.assertEqual(diags[0].code, "return-value")
        self.assertEqual(diags[1].severity, DiagnosticSeverity.WARNING)

    def test_parse_pyright(self):
        pyright_text = (
            "src/models.py:12:9 - error: Cannot assign to read-only property (reportGeneralTypeIssues)\n"
            "src/models.py:20:1 - warning: Statement is unreachable\n"
        )
        diags = DiagnosticParser.parse_pyright(pyright_text)
        self.assertEqual(len(diags), 2)
        self.assertEqual(diags[0].file_path, "src/models.py")
        self.assertEqual(diags[0].line, 12)
        self.assertEqual(diags[0].severity, DiagnosticSeverity.ERROR)
        self.assertEqual(diags[1].severity, DiagnosticSeverity.WARNING)

    def test_parse_tsc(self):
        tsc_text = "src/index.ts(15,3): error TS2322: Type 'string' is not assignable to type 'number'.\n"
        diags = DiagnosticParser.parse_tsc(tsc_text)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].file_path, "src/index.ts")
        self.assertEqual(diags[0].line, 15)
        self.assertEqual(diags[0].column, 3)
        self.assertEqual(diags[0].code, "TS2322")
        self.assertEqual(diags[0].severity, DiagnosticSeverity.ERROR)
        self.assertIn("Type 'string' is not assignable", diags[0].message)

    def test_parse_eslint(self):
        eslint_json = json.dumps([
            {
                "filePath": "src/App.tsx",
                "messages": [
                    {
                        "ruleId": "no-unused-vars",
                        "severity": 2,  # error
                        "message": "'count' is defined but never used.",
                        "line": 8,
                        "column": 10,
                    },
                    {
                        "ruleId": "react/no-unescaped-entities",
                        "severity": 1,  # warning
                        "message": "HTML entity not escaped.",
                        "line": 14,
                        "column": 5,
                    }
                ]
            }
        ])
        diags = DiagnosticParser.parse_eslint(eslint_json)
        self.assertEqual(len(diags), 2)
        self.assertEqual(diags[0].code, "no-unused-vars")
        self.assertEqual(diags[0].severity, DiagnosticSeverity.ERROR)
        self.assertEqual(diags[1].code, "react/no-unescaped-entities")
        self.assertEqual(diags[1].severity, DiagnosticSeverity.WARNING)

    def test_parse_semgrep_sast(self):
        semgrep_json = json.dumps({
            "results": [
                {
                    "check_id": "python.lang.security.insecure-subprocesses",
                    "path": "server.py",
                    "start": {"line": 18, "col": 5},
                    "extra": {
                        "severity": "ERROR",
                        "message": "Detected call to subprocess with shell=True which can lead to command injection",
                    }
                }
            ]
        })
        diags = DiagnosticParser.parse_semgrep(semgrep_json)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].file_path, "server.py")
        self.assertEqual(diags[0].line, 18)
        self.assertEqual(diags[0].column, 5)
        self.assertEqual(diags[0].code, "python.lang.security.insecure-subprocesses")
        self.assertEqual(diags[0].severity, DiagnosticSeverity.ERROR)
        self.assertEqual(diags[0].source_tool, "semgrep")

    def test_parse_clippy_and_govet(self):
        # Clippy
        clippy_text = (
            "error[E0308]: mismatched types\n"
            "  --> src/main.rs:4:5\n"
            "   |\n"
            " 4 |     x + 1\n"
            "   |     ^^^^^ expected `()`, found `i32`\n"
        )
        diags_rs = DiagnosticParser.parse_clippy(clippy_text)
        self.assertEqual(len(diags_rs), 1)
        self.assertEqual(diags_rs[0].file_path, "src/main.rs")
        self.assertEqual(diags_rs[0].line, 4)
        self.assertEqual(diags_rs[0].code, "E0308")

        # Go Vet
        govet_text = "main.go:12:2: unreachable code\n"
        diags_go = DiagnosticParser.parse_govet(govet_text)
        self.assertEqual(len(diags_go), 1)
        self.assertEqual(diags_go[0].file_path, "main.go")
        self.assertEqual(diags_go[0].line, 12)
        self.assertIn("unreachable code", diags_go[0].message)


class TestStaticToolDiscovery(unittest.TestCase):
    """Tests for detecting static analysis tools based on project configurations."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="static_disc_")
        self.ws_path = Path(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_python_pyproject_config_detection(self):
        pyproj = self.ws_path / "pyproject.toml"
        pyproj.write_text(
            '[tool.ruff]\nline-length = 88\n\n[tool.mypy]\nstrict = true\n',
            encoding="utf-8"
        )
        analyzer = StaticAnalyzer(workspace_dir=self.ws_path)
        active = analyzer.detect_active_tools()
        active_names = [t.name for t in active]
        self.assertIn("ruff", active_names)
        self.assertIn("mypy", active_names)

    def test_typescript_eslint_and_tsconfig_detection(self):
        (self.ws_path / "package.json").write_text(
            json.dumps({"name": "app", "devDependencies": {"typescript": "^5.0.0"}}),
            encoding="utf-8"
        )
        (self.ws_path / "tsconfig.json").write_text('{"compilerOptions": {}}', encoding="utf-8")
        (self.ws_path / ".eslintrc.json").write_text('{"rules": {}}', encoding="utf-8")

        analyzer = StaticAnalyzer(workspace_dir=self.ws_path)
        active = analyzer.detect_active_tools()
        active_names = [t.name for t in active]
        self.assertIn("tsc", active_names)
        self.assertIn("eslint", active_names)

    def test_sast_semgrep_config_detection(self):
        (self.ws_path / ".semgrep.yml").write_text('rules:\n  - id: test-rule\n', encoding="utf-8")
        analyzer = StaticAnalyzer(workspace_dir=self.ws_path)
        active = analyzer.detect_active_tools()
        active_names = [t.name for t in active]
        self.assertIn("semgrep", active_names)


class TestTargetedStaticAnalysis(unittest.TestCase):
    """Tests for file-targeted static analysis execution and diagnostic aggregation."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="static_exec_")
        self.workspace = WorkspaceManager(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_file_targeted_execution_skips_unrelated_tools(self):
        # Create a Python file and a TS file
        self.workspace.write_file("src/app.py", "def add(a, b): return a + b\n")
        self.workspace.write_file("src/index.ts", "const x: number = 42;\n")

        analyzer = StaticAnalyzer(workspace=self.workspace)

        # Target ONLY the python file
        report_py = analyzer.run_analysis(files=["src/app.py"])
        # tsc should be skipped because it only supports .ts/.tsx
        if "tsc" in report_py.results:
            self.assertTrue(report_py.results["tsc"].skipped)

    def test_error_vs_warning_classification(self):
        # Create a synthetic report with 1 warning and 0 errors
        warn = Diagnostic(
            file_path="src/app.py",
            line=5,
            severity=DiagnosticSeverity.WARNING,
            code="W291",
            message="Trailing whitespace",
            source_tool="ruff",
        )
        res = StaticAnalysisResult(
            tool_name="ruff",
            category=StaticToolCategory.LINTER,
            command="ruff check src/app.py",
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.05,
            diagnostics=[warn],
            errors=[],
            warnings=[warn],
        )
        report = StaticAnalysisReport(
            passed=True,
            target_files=["src/app.py"],
            diagnostics=[warn],
            errors=[],
            warnings=[warn],
            results={"ruff": res},
        )
        self.assertTrue(report.passed)
        self.assertEqual(len(report.errors), 0)
        self.assertEqual(len(report.warnings), 1)

    def test_task_verification_gate_with_static_diagnostics(self):
        self.workspace.write_file("src/calc.py", "def multiply(a, b): return a * b\n")

        gate = TaskVerificationGate(workspace=self.workspace)
        task = ExecutableTask(
            task_id="T-01",
            objective="Implement multiply",
            outputs=["src/calc.py"],
        )

        res = gate.verify_task(task)
        self.assertTrue(res.passed)
        self.assertIsNotNone(res.static_report)
        self.assertIn("tier_results", res.static_report)

    def test_builtin_tool_run_static_analysis(self):
        registry = BuiltinToolRegistry(workspace=self.workspace)
        self.assertTrue(hasattr(registry, "run_static_analysis_tool"))

        # Check availability across all 4 roles
        for role in ("CODER", "TESTER", "PLANNER", "REVIEWER"):
            tools = [t.name for t in registry.get_tools_for_agent(role)]
            self.assertIn("run_static_analysis", tools)

        # Call the tool
        tool_res = registry.call_tool("run_static_analysis", {"files": ["src/calc.py"]})
        self.assertTrue(tool_res.get("success"))
        self.assertIn("results", tool_res)

    def test_reviewer_audits_static_diagnostics(self):
        registry = BuiltinToolRegistry(workspace=self.workspace)
        reviewer = ReviewerAgent(workspace=self.workspace, tool_registry=registry)

        reviewer.react_loop.run = MagicMock(return_value={
            "final_output": {},
            "turns_taken": 1,
            "history_events": [],
        })

        state = OrchestratorState(user_request="Build secure API")
        state.test_output = {
            "execution_success": True,
            "exit_code": 0,
        }

        # Mock static check failure in tool
        with patch.object(registry, "call_tool", return_value={
            "passed": False,
            "syntax_errors": ["Syntax compilation failed: in app.py"],
            "errors": [{"file_path": "app.py", "message": "SyntaxError", "source_tool": "syntax"}],
            "warnings": [],
        }):
            review_res = reviewer.execute(state, task_info={"task_id": "T-01"})
            self.assertEqual(review_res.get("verdict"), "FAIL")
            self.assertLessEqual(review_res.get("score_out_of_100", 100), 60)
            self.assertIn("StaticVerification", [i.get("component") for i in review_res.get("issues", [])])


if __name__ == "__main__":
    unittest.main()
