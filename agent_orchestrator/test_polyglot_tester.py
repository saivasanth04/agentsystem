"""
Tests for Polyglot Tester and Project Environment Detection (Issue #21).
Verifies that the coding agent handles multi-language projects (Java/Spring Boot,
TypeScript/React, Go, Rust, Flutter, C++, and Python/pytest) cleanly,
without hardcoded Python-specific test runners or prompts.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_orchestrator.runtime.project_detector import (
    ProjectEnvironment,
    ProjectEnvironmentDetector,
)
from agent_orchestrator.agents.tester import TesterAgent
from agent_orchestrator.state import OrchestratorState
from agent_orchestrator.runtime.verification import TaskVerificationGate, VerificationResult
from agent_orchestrator.security.sandbox import (
    BaseExecutionSandbox,
    LocalProcessSandbox,
    SandboxResult,
)
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry


class MockLLM:
    """Mock LLM that responds with simple structured task completions."""
    def __init__(self, responses=None):
        self.responses = responses or ["All tests written and verified."]
        self.call_count = 0
        self.last_prompt = None

    def chat_with_tools(self, messages, tools=None, tool_choice=None, model=None, temperature=0.2):
        self.call_count += 1
        self.last_prompt = str(messages)
        content = self.responses.pop(0) if self.responses else "Test run completed."
        return {
            "content": content,
            "tool_calls": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        }

    def chat_json(self, messages, model=None, temperature=0.2):
        self.call_count += 1
        self.last_prompt = str(messages)
        content = self.responses.pop(0) if self.responses else "Test run completed."
        return {
            "thought": "All tests written and verified.",
            "final_output": {"summary": content},
        }

    def _extract_json(self, content: str) -> Dict[str, Any]:
        if not content:
            return {}
        try:
            return json.loads(content)
        except Exception:
            return {"summary": content}


class MockSandbox(BaseExecutionSandbox):
    """Mock sandbox to record test commands dispatched."""
    def __init__(self, root_dir):
        super().__init__(root_dir)
        self.executed_commands = []

    def run_command(self, command, timeout=None, env=None, cwd=None):
        cmd_str = command if isinstance(command, str) else " ".join(command)
        self.executed_commands.append(cmd_str)
        return SandboxResult(exit_code=0, stdout="Tests passed successfully", stderr="", success=True)

    def run_tests(self, pattern="test_*.py", command=None, timeout=None, cwd=None):
        cmd = command or f"python -m unittest discover -s tests -p '{pattern}'"
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        self.executed_commands.append(cmd_str)
        return SandboxResult(exit_code=0, stdout="OK (tests=5)", stderr="", success=True)

    def cleanup(self) -> None:
        pass


class TestProjectEnvironmentDetector(unittest.TestCase):
    """Unit tests for ProjectEnvironmentDetector across various project ecosystems."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="agent_detector_test_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_empty_workspace_returns_python(self):
        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.language, "python")
        self.assertEqual(env.test_runner, "unittest")
        self.assertIn("unittest", env.test_command)

    def test_typescript_react_detection(self):
        pkg_json = {
            "name": "frontend-app",
            "scripts": {
                "test": "vitest run"
            },
            "dependencies": {
                "react": "^18.2.0",
                "react-dom": "^18.2.0"
            },
            "devDependencies": {
                "typescript": "^5.0.0",
                "vitest": "^1.0.0"
            }
        }
        with open(os.path.join(self.temp_dir, "package.json"), "w") as f:
            json.dump(pkg_json, f)

        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.language, "typescript")
        self.assertEqual(env.framework, "react")
        self.assertEqual(env.test_runner, "vitest")
        self.assertEqual(env.package_manager, "npm")
        self.assertEqual(env.test_command, "npm test")
        self.assertIn(".test.", env.test_file_pattern)

    def test_node_pnpm_detection(self):
        pkg_json = {"scripts": {"test": "jest"}}
        with open(os.path.join(self.temp_dir, "package.json"), "w") as f:
            json.dump(pkg_json, f)
        # Create pnpm-lock.yaml
        with open(os.path.join(self.temp_dir, "pnpm-lock.yaml"), "w") as f:
            f.write("lockfileVersion: 5.4\n")

        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.package_manager, "pnpm")
        self.assertEqual(env.test_command, "pnpm test")
        self.assertEqual(env.test_runner, "jest")

    def test_java_maven_spring_boot_detection(self):
        pom_xml = """<project xmlns="http://maven.apache.org/POM/4.0.0">
            <parent>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-starter-parent</artifactId>
                <version>3.1.0</version>
            </parent>
            <dependencies>
                <dependency>
                    <groupId>org.springframework.boot</groupId>
                    <artifactId>spring-boot-starter-web</artifactId>
                </dependency>
            </dependencies>
        </project>"""
        with open(os.path.join(self.temp_dir, "pom.xml"), "w") as f:
            f.write(pom_xml)

        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.language, "java")
        self.assertEqual(env.framework, "spring-boot")
        self.assertEqual(env.build_tool, "maven")
        self.assertEqual(env.test_runner, "maven")
        self.assertIn("mvn test", env.test_command)
        self.assertEqual(env.test_file_pattern, "*Test.java")

    def test_java_gradle_detection(self):
        with open(os.path.join(self.temp_dir, "build.gradle"), "w") as f:
            f.write("plugins { id 'java' }\n")

        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.language, "java")
        self.assertEqual(env.build_tool, "gradle")
        self.assertIn("gradle", env.test_command)

    def test_rust_cargo_detection(self):
        cargo_toml = """[package]
name = "my_rust_crate"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = "1.0"
"""
        with open(os.path.join(self.temp_dir, "Cargo.toml"), "w") as f:
            f.write(cargo_toml)

        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.language, "rust")
        self.assertEqual(env.build_tool, "cargo")
        self.assertEqual(env.test_runner, "cargo")
        self.assertEqual(env.test_command, "cargo test")

    def test_go_mod_detection(self):
        go_mod = """module github.com/example/myservice

go 1.21

require (
    github.com/gin-gonic/gin v1.9.1
)
"""
        with open(os.path.join(self.temp_dir, "go.mod"), "w") as f:
            f.write(go_mod)

        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.language, "go")
        self.assertEqual(env.framework, "gin")
        self.assertEqual(env.test_runner, "go test")
        self.assertEqual(env.test_command, "go test ./...")
        self.assertEqual(env.test_file_pattern, "*_test.go")

    def test_flutter_pubspec_detection(self):
        pubspec = """name: my_mobile_app
description: A new Flutter project.
dependencies:
  flutter:
    sdk: flutter
"""
        with open(os.path.join(self.temp_dir, "pubspec.yaml"), "w") as f:
            f.write(pubspec)

        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.language, "dart")
        self.assertEqual(env.framework, "flutter")
        self.assertEqual(env.test_command, "flutter test")

    def test_python_pytest_detection(self):
        with open(os.path.join(self.temp_dir, "pytest.ini"), "w") as f:
            f.write("[pytest]\nminversion = 6.0\n")

        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.language, "python")
        self.assertEqual(env.test_runner, "pytest")
        self.assertEqual(env.test_command, "pytest")

    def test_nested_subproject_detection(self):
        # Monorepo style: backend in subfolder
        backend_dir = os.path.join(self.temp_dir, "backend")
        os.makedirs(backend_dir)
        with open(os.path.join(backend_dir, "Cargo.toml"), "w") as f:
            f.write('[package]\nname = "core"\nversion = "0.1.0"\n')

        env = ProjectEnvironmentDetector.detect(self.temp_dir)
        self.assertEqual(env.language, "rust")
        self.assertIn("cargo test", env.test_command)
        self.assertIn("backend", env.test_command)


class TestTesterAgentPolyglot(unittest.TestCase):
    """Unit tests for language-agnostic TesterAgent execution and prompts."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="agent_tester_polyglot_")
        self.workspace = WorkspaceManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tester_agent_polyglot_prompt_generation(self):
        # Setup React/TS project manifest
        pkg = {
            "name": "web-client",
            "scripts": {"test": "vitest run"},
            "devDependencies": {"vitest": "^1.0.0", "typescript": "^5.0.0"}
        }
        with open(os.path.join(self.temp_dir, "package.json"), "w") as f:
            json.dump(pkg, f)

        mock_llm = MockLLM(["Implemented unit tests in App.test.tsx."])
        sandbox = MockSandbox(self.temp_dir)
        tools = BuiltinToolRegistry(self.workspace, sandbox=sandbox)

        agent = TesterAgent(
            llm=mock_llm,
            workspace=self.workspace,
            tool_registry=tools,
        )

        state = OrchestratorState(user_request="Write unit tests for UI components")
        task = {
            "task_id": "T-TEST-01",
            "title": "Write unit tests for UI components",
            "description": "Create test suites for React buttons and inputs",
            "capabilities": ["TESTING"],
            "inputs": {},
        }

        result = agent.execute(state, task_info=task)
        self.assertTrue(result["execution_success"])

        # Check prompt sent to LLM
        prompt = mock_llm.last_prompt
        self.assertIn("Primary Language: Typescript", prompt)
        self.assertIn("Test Runner: vitest", prompt)
        self.assertIn("Test Execution Command: `npm test`", prompt)
        self.assertIn("Target Test File Pattern: `*.test.ts`", prompt)

        # Result summary contains environment metadata
        self.assertEqual(result["language"], "typescript")
        self.assertEqual(result["test_runner"], "vitest")
        self.assertEqual(result["test_command"], "npm test")

    def test_tester_agent_fallback_runs_detected_test_command(self):
        # Rust crate
        with open(os.path.join(self.temp_dir, "Cargo.toml"), "w") as f:
            f.write('[package]\nname = "rust_core"\nversion = "0.1.0"\n')

        mock_llm = MockLLM(["No tool calls made, just text."])
        sandbox = MockSandbox(self.temp_dir)
        tools = BuiltinToolRegistry(self.workspace, sandbox=sandbox)

        agent = TesterAgent(
            llm=mock_llm,
            workspace=self.workspace,
            tool_registry=tools,
        )

        state = OrchestratorState(user_request="Run tests")
        task = {
            "task_id": "T-TEST-02",
            "title": "Run cargo test",
            "capabilities": ["TESTING"],
        }

        result = agent.execute(state, task_info=task)
        self.assertTrue(result["execution_success"])
        # Sandbox should have been called with 'cargo test'
        self.assertIn("cargo test", sandbox.executed_commands)


class TestTaskVerificationGateMultiLanguage(unittest.TestCase):
    """Unit tests for TaskVerificationGate with multi-language files and test runners."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="agent_verify_polyglot_")
        self.workspace = WorkspaceManager(self.temp_dir)
        self.sandbox = MockSandbox(self.temp_dir)
        self.gate = TaskVerificationGate(workspace=self.workspace, sandbox=self.sandbox)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_syntax_checks_across_languages(self):
        # Valid Python
        self.workspace.write_file("app.py", "def add(a, b):\n    return a + b\n")
        # Invalid Python
        self.workspace.write_file("bad.py", "def add(a, b)\n    return a + b\n")
        # Valid JSON
        self.workspace.write_file("data.json", '{"status": "ok", "count": 42}')
        # Invalid JSON
        self.workspace.write_file("bad.json", '{"status": "ok", "count": }')
        # Valid TypeScript
        self.workspace.write_file("component.tsx", "export function Comp() { return (<div>Hello</div>); }")
        # Invalid TypeScript (unmatched bracket)
        self.workspace.write_file("bad.tsx", "export function Comp() { return (<div>Hello</div>); ")

        task_valid = {
            "task_id": "T-01",
            "outputs": ["app.py", "data.json", "component.tsx"],
            "capabilities": ["CODE_GENERATION"],
        }
        res_valid = self.gate.verify_task(task_valid)
        self.assertTrue(res_valid.passed)

        task_invalid_py = {
            "task_id": "T-02",
            "outputs": ["bad.py"],
            "capabilities": ["CODE_GENERATION"],
        }
        res_invalid_py = self.gate.verify_task(task_invalid_py)
        self.assertFalse(res_invalid_py.passed)
        self.assertTrue(any("Syntax error" in f or "syntax" in f.lower() for f in res_invalid_py.failure_reasons))

        task_invalid_json = {
            "task_id": "T-03",
            "outputs": ["bad.json"],
            "capabilities": ["CODE_GENERATION"],
        }
        res_invalid_json = self.gate.verify_task(task_invalid_json)
        self.assertFalse(res_invalid_json.passed)

        task_invalid_tsx = {
            "task_id": "T-04",
            "outputs": ["bad.tsx"],
            "capabilities": ["CODE_GENERATION"],
        }
        res_invalid_tsx = self.gate.verify_task(task_invalid_tsx)
        self.assertFalse(res_invalid_tsx.passed)
        self.assertTrue(any("bracket" in f.lower() or "syntax" in f.lower() or "delimiter" in f.lower() for f in res_invalid_tsx.failure_reasons))

    def test_verify_task_dispatches_detected_test_command(self):
        # Create Go project manifest
        with open(os.path.join(self.temp_dir, "go.mod"), "w") as f:
            f.write("module testmod\ngo 1.21\n")

        task = {
            "task_id": "T-GO-01",
            "capabilities": ["TESTING"],
            "outputs": ["go.mod"],
        }
        res = self.gate.verify_task(task)
        self.assertTrue(res.passed)
        # Sandbox should have executed 'go test ./...'
        self.assertIn("go test ./...", self.sandbox.executed_commands)


class TestBuiltinToolRegistryPolyglot(unittest.TestCase):
    """Unit tests for detect_project_environment tool in BuiltinToolRegistry."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="agent_registry_polyglot_")
        self.workspace = WorkspaceManager(self.temp_dir)
        self.registry = BuiltinToolRegistry(self.workspace)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_detect_project_environment_tool(self):
        # Setup Cargo.toml
        with open(os.path.join(self.temp_dir, "Cargo.toml"), "w") as f:
            f.write('[package]\nname = "test_rust"\nversion = "0.1.0"\n')

        tool = self.registry.detect_env_tool
        result = tool.invoke({})
        self.assertTrue(result["success"])
        self.assertEqual(result["language"], "rust")
        self.assertEqual(result["test_runner"], "cargo")
        self.assertEqual(result["test_command"], "cargo test")

    def test_tool_available_for_agent_roles(self):
        for role in ["PLANNER", "CODER", "TESTER", "REVIEWER", "TASKORCHESTRATOR"]:
            tools = self.registry.get_tools_for_agent(role)
            tool_names = [t.name for t in tools]
            self.assertIn("detect_project_environment", tool_names, f"Missing in {role}")


if __name__ == "__main__":
    unittest.main()
