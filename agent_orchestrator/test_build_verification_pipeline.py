"""
Unit and Integration Tests for Issue #24: Build Verification Pipeline in Multi-Agent Coding Orchestrator.
Verifies:
- Staged execution: INSTALL_DEPENDENCIES -> BUILD -> COMPILE_TYPECHECK -> LINT -> UNIT_TEST -> INTEGRATION_TEST
- Fail-fast mechanics: downstream stages skipped when an earlier stage fails
- Polyglot detection of install_command, build_command, and integration_test_command
- TaskVerificationGate integration with BuildVerificationPipeline
- run_build_pipeline tool in BuiltinToolRegistry
- TesterAgent and ReviewerAgent build pipeline awareness
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.runtime.build_pipeline import (
    BuildVerificationPipeline,
    PipelineStage,
    PipelineReport,
    StageResult,
)
from agent_orchestrator.runtime.project_detector import ProjectEnvironment, ProjectEnvironmentDetector
from agent_orchestrator.runtime.verification import TaskVerificationGate
from agent_orchestrator.runtime.task_graph import ExecutableTask
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.agents.tester import TesterAgent
from agent_orchestrator.agents.reviewer import ReviewerAgent
from agent_orchestrator.state import OrchestratorState


class TestBuildVerificationPipeline(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_build_pipeline_")
        self.workspace_path = Path(self.test_dir)
        self.workspace = WorkspaceManager(self.workspace_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_pipeline_stages_and_fail_fast(self):
        """Verify pipeline executes sequentially and halts downstream stages on failure."""
        pipeline = BuildVerificationPipeline(workspace=self.workspace)

        custom_cmds = {
            PipelineStage.INSTALL_DEPENDENCIES: 'python -c "print(\'installing\')"',
            PipelineStage.BUILD: 'python -c "import sys; sys.exit(2)"',  # FAILS
            PipelineStage.COMPILE_TYPECHECK: 'python -c "print(\'compiling\')"',
            PipelineStage.UNIT_TEST: 'python -c "print(\'testing\')"',
        }

        report = pipeline.execute(
            stages=[
                PipelineStage.INSTALL_DEPENDENCIES,
                PipelineStage.BUILD,
                PipelineStage.COMPILE_TYPECHECK,
                PipelineStage.UNIT_TEST,
            ],
            fail_fast=True,
            custom_commands=custom_cmds,
        )

        self.assertFalse(report.passed)
        self.assertEqual(report.failed_stage, PipelineStage.BUILD)

        # Stage 1: Install succeeded
        self.assertTrue(report.stage_results["INSTALL_DEPENDENCIES"].passed)
        self.assertEqual(report.stage_results["INSTALL_DEPENDENCIES"].exit_code, 0)

        # Stage 2: Build failed
        self.assertFalse(report.stage_results["BUILD"].passed)
        self.assertEqual(report.stage_results["BUILD"].exit_code, 2)

        # Stage 3 & 4: Skipped due to fail_fast
        self.assertTrue(report.stage_results["COMPILE_TYPECHECK"].skipped)
        self.assertIn("BUILD", report.stage_results["COMPILE_TYPECHECK"].skip_reason)
        self.assertTrue(report.stage_results["UNIT_TEST"].skipped)

        # Summary output
        summary = report.summary()
        self.assertIn("FAILED", summary)
        self.assertIn("Failed Stage: BUILD", summary)

    def test_pipeline_all_stages_pass(self):
        """Verify pipeline reports complete pass when all stages exit 0."""
        pipeline = BuildVerificationPipeline(workspace=self.workspace)
        custom_cmds = {
            PipelineStage.BUILD: 'python -c "print(\'build ok\')"',
            PipelineStage.UNIT_TEST: 'python -c "print(\'tests ok\')"',
        }
        report = pipeline.execute(
            stages=[PipelineStage.BUILD, PipelineStage.UNIT_TEST],
            custom_commands=custom_cmds,
        )
        self.assertTrue(report.passed)
        self.assertIsNone(report.failed_stage)
        self.assertTrue(report.stage_results["BUILD"].passed)
        self.assertTrue(report.stage_results["UNIT_TEST"].passed)

    def test_polyglot_build_commands_detection(self):
        """Verify ProjectEnvironmentDetector detects install, build, and integration commands."""
        # 1. Node project with build script
        node_dir = self.workspace_path / "node_proj"
        node_dir.mkdir()
        pkg_json = {
            "name": "my-app",
            "scripts": {
                "build": "vite build",
                "test": "vitest",
                "test:e2e": "playwright test",
            },
            "dependencies": {"vite": "^5.0.0", "vitest": "^1.0.0"}
        }
        (node_dir / "package.json").write_text(json.dumps(pkg_json), encoding="utf-8")
        env_node = ProjectEnvironmentDetector.detect(node_dir)

        self.assertEqual(env_node.language, "javascript")
        self.assertEqual(env_node.install_command, "npm install")
        self.assertEqual(env_node.build_command, "npm run build")
        self.assertEqual(env_node.integration_test_command, "npm run test:e2e")

        # 2. Rust project
        rust_dir = self.workspace_path / "rust_proj"
        rust_dir.mkdir()
        (rust_dir / "Cargo.toml").write_text('[package]\nname = "test_rust"\nversion = "0.1.0"\n', encoding="utf-8")
        env_rust = ProjectEnvironmentDetector.detect(rust_dir)

        self.assertEqual(env_rust.language, "rust")
        self.assertEqual(env_rust.install_command, "cargo fetch")
        self.assertEqual(env_rust.build_command, "cargo build")
        self.assertEqual(env_rust.integration_test_command, "cargo test --test '*'")

        # 3. Go project
        go_dir = self.workspace_path / "go_proj"
        go_dir.mkdir()
        (go_dir / "go.mod").write_text("module example.com/app\n\ngo 1.21\n", encoding="utf-8")
        env_go = ProjectEnvironmentDetector.detect(go_dir)

        self.assertEqual(env_go.language, "go")
        self.assertEqual(env_go.install_command, "go mod download")
        self.assertEqual(env_go.build_command, "go build ./...")
        self.assertEqual(env_go.integration_test_command, "go test -tags=integration ./...")

    def test_task_verification_gate_with_build_pipeline(self):
        """Verify TaskVerificationGate uses BuildVerificationPipeline during task verification."""
        # Create output file
        self.workspace.write_file("src/math.py", "def add(a, b): return a + b\n")

        gate = TaskVerificationGate(workspace=self.workspace)
        task = ExecutableTask(
            task_id="T-01",
            objective="Implement math add",
            required_capabilities=["code-generation"],
            outputs=["src/math.py"],
            acceptance_tests=['python -c "from src.math import add; assert add(1, 2) == 3"'],
        )

        res = gate.verify_task(task)
        self.assertTrue(res.passed)
        self.assertIsNotNone(res.pipeline_report)
        self.assertTrue(res.pipeline_report["passed"])

    def test_builtin_tool_run_build_pipeline(self):
        """Verify BuiltinToolRegistry exposes run_build_pipeline tool."""
        registry = BuiltinToolRegistry(workspace=self.workspace)
        self.assertTrue(hasattr(registry, "run_build_pipeline_tool"))

        # Verify tool is available to CODER, TESTER, PLANNER, REVIEWER
        for role in ("CODER", "TESTER", "PLANNER", "REVIEWER"):
            tools = [t.name for t in registry.get_tools_for_agent(role)]
            self.assertIn("run_build_pipeline", tools)

        # Call the tool
        tool_res = registry.call_tool("run_build_pipeline", {"stages": ["INSTALL_DEPENDENCIES"]})
        self.assertTrue(tool_res.get("success"))
        self.assertIn("stage_results", tool_res)

    def test_reviewer_agent_with_failing_build_pipeline(self):
        """Verify ReviewerAgent issues a FAIL verdict when build pipeline fails."""
        registry = BuiltinToolRegistry(workspace=self.workspace)
        reviewer = ReviewerAgent(workspace=self.workspace, tool_registry=registry)

        reviewer.react_loop.run = MagicMock(return_value={
            "final_output": {},
            "turns_taken": 1,
            "history_events": [],
        })

        state = OrchestratorState(user_request="Build microservice")
        state.test_output = {
            "execution_success": True,
            "exit_code": 0,
            "build_pipeline_report": {
                "passed": False,
                "failed_stage": "BUILD",
                "stage_results": {
                    "BUILD": {"stage": "BUILD", "passed": False, "exit_code": 1, "command": "npm run build"}
                }
            }
        }

        review_res = reviewer.execute(state, task_info={"task_id": "T-01"})
        self.assertEqual(review_res.get("verdict"), "FAIL")
        self.assertIn("BuildPipeline", [i.get("component") for i in review_res.get("issues", [])])

    def test_tester_agent_with_build_command(self):
        """Verify TesterAgent triggers build pipeline when build_command is present."""
        registry = BuiltinToolRegistry(workspace=self.workspace)
        tester = TesterAgent(workspace=self.workspace, tool_registry=registry)

        tester.react_loop.run = MagicMock(return_value={
            "final_output": {"summary": "Ran tests successfully"},
            "turns_taken": 1,
            "history_events": [],
        })

        state = OrchestratorState(user_request="Build and test service")
        # Create dummy test file
        self.workspace.write_file(
            "test_dummy.py",
            "import unittest\nclass DummyTest(unittest.TestCase):\n    def test_d(self): self.assertTrue(True)\n"
        )

        # Mock project detector to return build command
        from agent_orchestrator.runtime.project_detector import ProjectEnvironment
        mock_env = ProjectEnvironment(
            language="python",
            build_command='python -c "print(\'building\')"',
            test_command='python -m unittest discover -s . -p "test_dummy.py"',
        )

        with unittest.mock.patch("agent_orchestrator.runtime.project_detector.ProjectEnvironmentDetector.detect", return_value=mock_env):
            res = tester.execute(state, task_info={"task_id": "T-01"})
            self.assertIn("build_pipeline_report", res)
            self.assertIsNotNone(res["build_pipeline_report"])
            self.assertTrue(res["build_pipeline_report"]["passed"])


if __name__ == "__main__":
    unittest.main()

