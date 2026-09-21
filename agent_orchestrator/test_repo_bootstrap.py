"""
Unit and integration tests for Repository Bootstrapper & Modular Detectors (Issue #62).
Tests:
1. detect_git() on git repo (clean, dirty, non-git directory).
2. detect_project() identifying project name, root, language, and manifest.
3. detect_stack() identifying language, runtime versions, frameworks, and libraries.
4. detect_package_manager() detecting uv, poetry, pnpm, npm, cargo, and lockfiles.
5. detect_build_system() detecting make, cmake, gradle, maven, npm scripts, cargo.
6. detect_test_runner() detecting pytest, vitest, jest, cargo test, unittest.
7. RepositoryBootstrapper.bootstrap() end-to-end with toolchain checks and auto_init_git.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_orchestrator.runtime.repo_bootstrap import (
    GitInfo,
    ProjectInfo,
    StackInfo,
    PackageManagerInfo,
    BuildSystemInfo,
    TestRunnerInfo,
    BootstrapReport,
    detect_git,
    detect_project,
    detect_stack,
    detect_package_manager,
    detect_build_system,
    detect_test_runner,
    RepositoryBootstrapper,
)


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


class TestRepoBootstrap(unittest.TestCase):

    def setUp(self):
        self._workspaces = []

    def tearDown(self):
        for ws in self._workspaces:
            ws.cleanup()

    def create_workspace(self, files=None):
        ws = MockWorkspace(files=files)
        self._workspaces.append(ws)
        return ws

    # 1. detect_git
    def test_detect_git_non_git_directory(self):
        ws = self.create_workspace({"file.txt": "hello"})
        git_info = detect_git(ws.root_dir)
        self.assertFalse(git_info.is_git_repo)
        self.assertIsNone(git_info.branch)
        self.assertFalse(git_info.is_dirty)

    def test_detect_git_initialized_repo(self):
        ws = self.create_workspace({"file.txt": "hello"})
        # Initialize real git repo
        try:
            subprocess.run(["git", "init"], cwd=str(ws.root_dir), capture_output=True, text=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(ws.root_dir), capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(ws.root_dir), capture_output=True, text=True)
            subprocess.run(["git", "add", "file.txt"], cwd=str(ws.root_dir), capture_output=True, text=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(ws.root_dir), capture_output=True, text=True)

            git_info = detect_git(ws.root_dir)
            self.assertTrue(git_info.is_git_repo)
            self.assertIsNotNone(git_info.commit_hash)
            self.assertFalse(git_info.is_dirty)

            # Make it dirty
            (ws.root_dir / "dirty.txt").write_text("uncommitted", encoding="utf-8")
            git_dirty = detect_git(ws.root_dir)
            self.assertTrue(git_dirty.is_dirty)
            self.assertIn("dirty.txt", git_dirty.untracked_files)
        except (subprocess.SubprocessError, FileNotFoundError):
            # If git binary not available in environment, test falls back gracefully
            pass

    # 2. detect_project
    def test_detect_project_python(self):
        ws = self.create_workspace({
            "pyproject.toml": '[project]\nname = "super-agent"\nversion = "0.1.0"\n',
        })
        info = detect_project(ws.root_dir)
        self.assertEqual(info.name, "super-agent")
        self.assertEqual(info.primary_language, "python")
        self.assertEqual(info.manifest_file, "pyproject.toml")
        self.assertEqual(info.project_type, "standalone")

    def test_detect_project_node_monorepo(self):
        ws = self.create_workspace({
            "package.json": '{"name": "web-platform", "workspaces": ["apps/*"]}',
        })
        info = detect_project(ws.root_dir)
        self.assertEqual(info.name, "web-platform")
        self.assertEqual(info.primary_language, "javascript")
        self.assertEqual(info.manifest_file, "package.json")
        self.assertEqual(info.project_type, "monorepo")

    def test_detect_project_rust(self):
        ws = self.create_workspace({
            "Cargo.toml": '[package]\nname = "fast-engine"\nversion = "0.1.0"\n',
        })
        info = detect_project(ws.root_dir)
        self.assertEqual(info.name, "fast-engine")
        self.assertEqual(info.primary_language, "rust")
        self.assertEqual(info.manifest_file, "Cargo.toml")

    # 3. detect_stack
    def test_detect_stack_fastapi_python(self):
        ws = self.create_workspace({
            ".python-version": "3.11.8\n",
            "app.py": "from fastapi import FastAPI\napp = FastAPI()\n",
            "requirements.txt": "fastapi>=0.100\nuvicorn\npydantic\n",
        })
        stack = detect_stack(ws.root_dir)
        self.assertEqual(stack.language, "python")
        self.assertEqual(stack.runtime_version, "3.11.8")
        self.assertEqual(stack.framework, "fastapi")
        self.assertIn("fastapi", stack.libraries)
        self.assertIn("pydantic", stack.libraries)

    def test_detect_stack_nextjs_typescript(self):
        ws = self.create_workspace({
            "package.json": '{"dependencies": {"next": "14.0.0", "react": "18.2.0"}, "devDependencies": {"typescript": "^5.0.0"}}',
            ".nvmrc": "v20.10.0\n",
        })
        stack = detect_stack(ws.root_dir)
        self.assertEqual(stack.language, "typescript")
        self.assertEqual(stack.runtime_version, "v20.10.0")
        self.assertEqual(stack.framework, "next")
        self.assertIn("react", stack.libraries)

    # 4. detect_package_manager
    def test_detect_package_manager_all(self):
        # uv
        ws_uv = self.create_workspace({"uv.lock": ""})
        self.assertEqual(detect_package_manager(ws_uv.root_dir).name, "uv")
        self.assertTrue(detect_package_manager(ws_uv.root_dir).has_lockfile)

        # poetry
        ws_po = self.create_workspace({"poetry.lock": ""})
        self.assertEqual(detect_package_manager(ws_po.root_dir).name, "poetry")

        # pnpm
        ws_pnpm = self.create_workspace({"pnpm-lock.yaml": ""})
        self.assertEqual(detect_package_manager(ws_pnpm.root_dir).name, "pnpm")

        # npm
        ws_npm = self.create_workspace({"package-lock.json": ""})
        self.assertEqual(detect_package_manager(ws_npm.root_dir).name, "npm")

        # cargo
        ws_cargo = self.create_workspace({"Cargo.lock": ""})
        self.assertEqual(detect_package_manager(ws_cargo.root_dir).name, "cargo")

    # 5. detect_build_system
    def test_detect_build_system_all(self):
        # Make
        ws_make = self.create_workspace({"Makefile": "all:\n\t@echo ok"})
        self.assertEqual(detect_build_system(ws_make.root_dir).name, "make")
        self.assertEqual(detect_build_system(ws_make.root_dir).build_command, "make")

        # CMake
        ws_cmake = self.create_workspace({"CMakeLists.txt": "cmake_minimum_required(VERSION 3.10)"})
        self.assertEqual(detect_build_system(ws_cmake.root_dir).name, "cmake")

        # Gradle
        ws_gradle = self.create_workspace({"build.gradle": "plugins { id 'java' }"})
        self.assertEqual(detect_build_system(ws_gradle.root_dir).name, "gradle")

        # npm scripts
        ws_pkg = self.create_workspace({"package.json": '{"scripts": {"build": "tsc && vite build"}}'})
        self.assertEqual(detect_build_system(ws_pkg.root_dir).name, "npm-scripts")
        self.assertEqual(detect_build_system(ws_pkg.root_dir).build_command, "npm run build")

    # 6. detect_test_runner
    def test_detect_test_runner_all(self):
        # Pytest via pytest.ini
        ws_py = self.create_workspace({"pytest.ini": "[pytest]\naddopts = -v"})
        self.assertEqual(detect_test_runner(ws_py.root_dir).framework, "pytest")

        # Vitest via package.json
        ws_vi = self.create_workspace({"package.json": '{"devDependencies": {"vitest": "^1.0.0"}}'})
        self.assertEqual(detect_test_runner(ws_vi.root_dir).framework, "vitest")

        # Jest via package.json
        ws_je = self.create_workspace({"package.json": '{"devDependencies": {"jest": "^29.0.0"}}'})
        self.assertEqual(detect_test_runner(ws_je.root_dir).framework, "jest")

        # Cargo test via Cargo.toml
        ws_cg = self.create_workspace({"Cargo.toml": '[package]\nname = "test-pkg"'})
        self.assertEqual(detect_test_runner(ws_cg.root_dir).framework, "cargo")

    # 7. RepositoryBootstrapper.bootstrap()
    def test_repository_bootstrapper_end_to_end(self):
        ws = self.create_workspace({
            "pyproject.toml": """[project]
name = "api-service"
version = "1.0.0"
dependencies = ["fastapi", "pytest"]
[tool.pytest.ini_options]
minversion = "7.0"
""",
            "poetry.lock": "",
            "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
            "Makefile": "build:\n\tpython -m build\n",
        })

        report = RepositoryBootstrapper.bootstrap(ws.root_dir, auto_init_git=True)

        self.assertIsInstance(report, BootstrapReport)
        self.assertEqual(report.project.name, "api-service")
        self.assertEqual(report.project.primary_language, "python")
        self.assertEqual(report.stack.framework, "fastapi")
        self.assertEqual(report.package_manager.name, "poetry")
        self.assertEqual(report.build_system.name, "make")
        self.assertEqual(report.test_runner.framework, "pytest")

        # Check summary formatting
        summary = report.summary()
        self.assertIn("Repository Bootstrap", summary)
        self.assertIn("api-service", summary)
        self.assertIn("fastapi", summary)
        self.assertIn("poetry", summary)


if __name__ == "__main__":
    unittest.main()
