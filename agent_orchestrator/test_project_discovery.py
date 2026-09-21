"""
Unit and integration tests for Pre-Flight Deterministic Project Discovery Engine (Issue #61).
Tests:
1. Python project discovery (FastAPI, poetry/uv/pip, pytest, ruff, mypy, entrypoints).
2. Node/TypeScript project discovery (Next.js, pnpm, vitest, tsconfig, Docker).
3. Monorepo topology detection (pnpm-workspace.yaml, apps/packages directories).
4. Docker & CI workflow discovery (Dockerfile, docker-compose.yml, GitHub Actions).
5. Entrypoint discovery via AST inspection and manifest scripts.
6. Environment & Secrets template variable discovery (.env.example).
7. Orchestrator workflow integration (discovery_node runs before understand_node and populates state).
8. Graceful fallback on empty workspace.
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.runtime.project_discovery import (
    ProjectProfile,
    ProjectDiscoveryEngine,
)
from agent_orchestrator.orchestrator import TaskOrchestrator, OrchestratorGraphState
from agent_orchestrator.state import OrchestratorState


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

    def register_file_change_listener(self, listener):
        pass

    def unregister_file_change_listener(self, listener):
        pass


class TestProjectDiscovery(unittest.TestCase):

    def setUp(self):
        self._workspaces = []

    def tearDown(self):
        for ws in self._workspaces:
            ws.cleanup()

    def create_workspace(self, files=None):
        ws = MockWorkspace(files=files)
        self._workspaces.append(ws)
        return ws

    def test_python_fastapi_poetry_discovery(self):
        ws = self.create_workspace({
            "pyproject.toml": """
[project]
name = "my-service"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.100.0",
    "pydantic>=2.0.0",
    "pytest>=7.0.0",
]

[project.scripts]
serve = "app.main:run_server"

[tool.ruff]
line-length = 100

[tool.mypy]
strict = true
""",
            "poetry.lock": "# poetry lockfile",
            ".python-version": "3.11.5\n",
            "app/main.py": """
from fastapi import FastAPI
app = FastAPI()

def run_server():
    pass

if __name__ == "__main__":
    run_server()
""",
            "tests/test_api.py": "def test_root(): pass",
            ".env.example": "DATABASE_URL=postgres://localhost:5432/db\nSECRET_KEY=changeme\nPORT=8000\n",
        })

        profile = ProjectDiscoveryEngine.discover(ws)

        self.assertEqual(profile.primary_language, "python")
        self.assertIn("python", profile.detected_languages)
        self.assertEqual(profile.runtime_versions.get("python"), "3.11.5")
        self.assertEqual(profile.framework, "fastapi")
        self.assertEqual(profile.package_manager, "poetry")
        self.assertEqual(profile.lockfile, "poetry.lock")
        self.assertEqual(profile.test_framework, "pytest")
        self.assertIn("ruff", profile.linters_and_formatters)
        self.assertIn("mypy", profile.linters_and_formatters)
        self.assertIn("DATABASE_URL", profile.required_env_vars)
        self.assertIn("SECRET_KEY", profile.required_env_vars)

        # Check entrypoints
        ep_paths = [ep["path"] for ep in profile.entrypoints]
        self.assertTrue(any("app/main.py" in p or "app.main" in p for p in ep_paths))

    def test_typescript_nextjs_pnpm_discovery(self):
        ws = self.create_workspace({
            "package.json": """{
  "name": "my-web-app",
  "version": "1.0.0",
  "main": "src/index.ts",
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "test": "vitest run",
    "lint": "eslint ."
  },
  "dependencies": {
    "next": "14.0.0",
    "react": "18.2.0",
    "react-dom": "18.2.0"
  },
  "devDependencies": {
    "typescript": "^5.0.0",
    "vitest": "^1.0.0",
    "eslint": "^8.0.0"
  },
  "engines": {
    "node": ">=18.17.0"
  }
}""",
            "pnpm-lock.yaml": "lockfileVersion: '6.0'",
            ".nvmrc": "v18.18.0\n",
            "tsconfig.json": '{"compilerOptions": {"target": "ES2022"}}',
            "src/index.ts": "export const app = 'ready';",
            "src/server.ts": "import { createServer } from 'http'; const server = createServer();",
            "Dockerfile": "FROM node:18-alpine\nWORKDIR /app\n",
            "docker-compose.yml": """
version: '3.8'
services:
  web:
    build: .
    ports:
      - "3000:3000"
  postgres:
    image: postgres:15
    environment:
      POSTGRES_PASSWORD: test
""",
        })

        profile = ProjectDiscoveryEngine.discover(ws)

        self.assertIn("typescript", profile.detected_languages)
        self.assertEqual(profile.runtime_versions.get("node"), "v18.18.0")
        self.assertEqual(profile.framework, "next")
        self.assertEqual(profile.package_manager, "pnpm")
        self.assertEqual(profile.lockfile, "pnpm-lock.yaml")
        self.assertEqual(profile.test_framework, "vitest")
        self.assertTrue(profile.has_docker)
        self.assertIn("web", profile.container_services)
        self.assertIn("postgres", profile.container_services)
        self.assertIn("eslint", profile.linters_and_formatters)

    def test_monorepo_topology_discovery(self):
        ws = self.create_workspace({
            "pnpm-workspace.yaml": "packages:\n  - 'apps/*'\n  - 'packages/*'\n",
            "package.json": '{"name": "root-repo", "private": true}',
            "apps/web/package.json": '{"name": "web"}',
            "apps/api/package.json": '{"name": "api"}',
            "packages/ui/package.json": '{"name": "ui"}',
        })

        profile = ProjectDiscoveryEngine.discover(ws)

        self.assertTrue(profile.is_monorepo)
        self.assertEqual(profile.monorepo_tool, "pnpm-workspaces")
        self.assertIn("apps/web", profile.workspace_packages)
        self.assertIn("apps/api", profile.workspace_packages)
        self.assertIn("packages/ui", profile.workspace_packages)

    def test_ci_workflow_discovery(self):
        ws = self.create_workspace({
            ".github/workflows/ci.yml": """
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Run Tests
        run: pytest -v --cov=src
""",
            "Makefile": """
test:
\tpytest
lint:
\truff check .
""",
        })

        profile = ProjectDiscoveryEngine.discover(ws)

        self.assertEqual(len(profile.ci_workflows), 2)
        ci_files = [w["file"] for w in profile.ci_workflows]
        self.assertTrue(any("ci.yml" in f for f in ci_files))
        self.assertIn("Makefile", ci_files)
        self.assertEqual(profile.authoritative_ci_command, "pytest -v --cov=src")

    def test_prompt_context_formatting(self):
        profile = ProjectProfile(
            primary_language="python",
            detected_languages=["python"],
            runtime_versions={"python": "3.11"},
            framework="fastapi",
            package_manager="poetry",
            install_command="poetry install",
            test_framework="pytest",
            test_command="pytest",
            has_docker=True,
            container_services=["postgres", "redis"],
            required_env_vars=["DATABASE_URL", "SECRET_KEY"],
            is_monorepo=False,
            source_directories=["app"],
        )

        ctx = profile.to_prompt_context()
        self.assertIn("Deterministic Project Discovery Profile", ctx)
        self.assertIn("fastapi", ctx)
        self.assertIn("poetry", ctx)
        self.assertIn("pytest", ctx)
        self.assertIn("postgres", ctx)
        self.assertIn("DATABASE_URL", ctx)

    def test_orchestrator_discovery_node_populates_state(self):
        ws = self.create_workspace({
            "pyproject.toml": '[project]\nname = "test-pkg"\n',
            "app.py": "from flask import Flask\napp = Flask(__name__)\n",
        })

        orch = TaskOrchestrator(workspace=ws)
        state = {
            "user_request": "Add user authentication",
            "project_profile": None,
            "task_understanding": None,
            "task_decomposition": None,
            "subtasks": [],
            "current_subtask_index": 0,
            "completed_subtasks": [],
            "step_results": {},
            "plan_output": None,
            "specification_output": None,
            "architecture_output": None,
            "code_output": None,
            "test_output": None,
            "review_output": None,
            "verdict": "UNDECIDED",
            "iteration": 0,
            "max_iterations": 3,
            "remediation_plan": [],
            "replan_history": [],
            "target_agent_for_fix": "CODER",
            "status": "PENDING",
            "messages": [],
            "baseline_test_info": None,
            "rollback_executed": False,
            "last_rollback": None,
        }

        # Run discovery node directly
        updates = orch._node_discovery(state)
        self.assertIn("project_profile", updates)
        prof_dict = updates["project_profile"]
        self.assertEqual(prof_dict["primary_language"], "python")
        self.assertEqual(prof_dict["framework"], "flask")
        self.assertEqual(updates["status"], "IN_PROGRESS")

    def test_empty_workspace_fallback(self):
        ws = self.create_workspace({})
        profile = ProjectDiscoveryEngine.discover(ws)

        self.assertIsNotNone(profile)
        self.assertEqual(profile.primary_language, "python")
        self.assertFalse(profile.has_docker)
        self.assertFalse(profile.is_monorepo)
        self.assertEqual(len(profile.entrypoints), 0)


if __name__ == "__main__":
    unittest.main()
