"""
Unit and integration tests for Issue #63: Dynamic Environment Awareness.
Validates:
1. Active Python runtime probe (version, executable, virtualenv).
2. Node.js runtime probe (installed status, version, package managers).
3. Java runtime probe (installed status, version, JAVA_HOME).
4. OS and shell detection (Windows vs POSIX, default shell syntax).
5. Installed dependencies probe (importlib.metadata vs declared dependencies).
6. Docker availability probe (CLI vs live daemon status).
7. Database & service reachability probe (fast TCP socket connectivity).
8. Environment variables probe (present, missing, placeholder detection without leaking secrets).
9. Modular detector `detect_environment()` and `BootstrapReport` integration.
10. Orchestrator discovery_node integration populating environment_profile in state.
"""
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_orchestrator.runtime.environment_probe import (
    EnvironmentProbeEngine,
    EnvironmentProfile,
    PythonRuntimeInfo,
    NodeRuntimeInfo,
    JavaRuntimeInfo,
    OSInfo,
    InstalledDependenciesInfo,
    DockerInfo,
    DatabaseReachabilityInfo,
    EnvironmentVariablesInfo,
    ServiceStatus,
)
from agent_orchestrator.runtime.repo_bootstrap import (
    detect_environment,
    RepositoryBootstrapper,
    BootstrapReport,
)
from agent_orchestrator.orchestrator import TaskOrchestrator, OrchestratorGraphState
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestEnvironmentAwareness(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_dir = Path(self.temp_dir)
        self.workspace = WorkspaceManager(root_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Python Runtime Probe
    # -------------------------------------------------------------------------
    def test_probe_python_runtime(self):
        py_info = EnvironmentProbeEngine.probe_python()
        self.assertIsInstance(py_info, PythonRuntimeInfo)
        self.assertTrue(py_info.version)
        self.assertEqual(len(py_info.version_tuple), 3)
        self.assertTrue(os.path.exists(py_info.executable))
        self.assertIsInstance(py_info.is_virtualenv, bool)
        
        # Test serialization
        d = py_info.to_dict()
        self.assertEqual(d["version"], py_info.version)
        self.assertEqual(d["executable"], py_info.executable)

    # -------------------------------------------------------------------------
    # 2. Node Runtime Probe
    # -------------------------------------------------------------------------
    def test_probe_node_runtime(self):
        node_info = EnvironmentProbeEngine.probe_node()
        self.assertIsInstance(node_info, NodeRuntimeInfo)
        self.assertIsInstance(node_info.is_installed, bool)
        if node_info.is_installed:
            self.assertIsNotNone(node_info.version)
            self.assertIsNotNone(node_info.executable)
            self.assertIsInstance(node_info.package_managers, dict)

    def test_probe_node_absent_fallback(self):
        with patch("shutil.which", return_value=None):
            node_info = EnvironmentProbeEngine.probe_node()
            self.assertFalse(node_info.is_installed)
            self.assertIsNone(node_info.version)
            self.assertIsNone(node_info.executable)

    # -------------------------------------------------------------------------
    # 3. Java Runtime Probe
    # -------------------------------------------------------------------------
    def test_probe_java_runtime(self):
        java_info = EnvironmentProbeEngine.probe_java()
        self.assertIsInstance(java_info, JavaRuntimeInfo)
        self.assertIsInstance(java_info.is_installed, bool)
        if java_info.is_installed:
            self.assertIsNotNone(java_info.version)

    def test_probe_java_absent_fallback(self):
        with patch("shutil.which", return_value=None):
            with patch.dict(os.environ, {}, clear=True):
                java_info = EnvironmentProbeEngine.probe_java()
                self.assertFalse(java_info.is_installed)
                self.assertIsNone(java_info.version)

    # -------------------------------------------------------------------------
    # 4. OS and Shell Detection
    # -------------------------------------------------------------------------
    def test_probe_os_and_shell(self):
        os_info = EnvironmentProbeEngine.probe_os_and_shell()
        self.assertIsInstance(os_info, OSInfo)
        self.assertIn(os_info.system, ("windows", "linux", "darwin"))
        self.assertTrue(os_info.architecture)
        self.assertIn(os_info.default_shell, ("powershell", "cmd", "bash", "zsh", "sh"))
        if os.name == "nt":
            self.assertTrue(os_info.is_windows)
            self.assertFalse(os_info.is_posix)
            self.assertIn(os_info.default_shell, ("powershell", "cmd"))
        else:
            self.assertTrue(os_info.is_posix)
            self.assertFalse(os_info.is_windows)
            self.assertIn(os_info.default_shell, ("bash", "zsh", "sh"))

    # -------------------------------------------------------------------------
    # 5. Installed Dependencies Probe
    # -------------------------------------------------------------------------
    def test_probe_dependencies_installed_and_missing(self):
        # We declare a fake package that is guaranteed not to exist
        fake_dep = "nonexistent-super-custom-package-xyz-999"
        dep_info = EnvironmentProbeEngine.probe_dependencies(
            declared_deps=[fake_dep, "pytest>=7.0"],
            workspace_path=self.workspace_dir
        )
        self.assertIsInstance(dep_info, InstalledDependenciesInfo)
        self.assertGreater(len(dep_info.python_packages), 0)
        self.assertIn(fake_dep, dep_info.missing_declared_dependencies)

    # -------------------------------------------------------------------------
    # 6. Docker Availability Probe
    # -------------------------------------------------------------------------
    def test_probe_docker_with_mocked_states(self):
        # Scenario A: Docker CLI not installed
        with patch("shutil.which", return_value=None):
            d_info = EnvironmentProbeEngine.probe_docker()
            self.assertFalse(d_info.is_installed)
            self.assertFalse(d_info.is_daemon_running)

        # Scenario B: Docker CLI installed, daemon running
        with patch("shutil.which", return_value="/usr/bin/docker"):
            mock_proc_ver = MagicMock(returncode=0, stdout="Docker version 24.0.5, build cedb78b")
            mock_proc_info = MagicMock(returncode=0, stdout="24.0.5 3")

            def mock_run(cmd, *args, **kwargs):
                if "--version" in cmd:
                    return mock_proc_ver
                return mock_proc_info

            with patch("subprocess.run", side_effect=mock_run):
                d_info = EnvironmentProbeEngine.probe_docker()
                self.assertTrue(d_info.is_installed)
                self.assertTrue(d_info.is_daemon_running)
                self.assertEqual(d_info.daemon_version, "24.0.5")
                self.assertEqual(d_info.containers_running_count, 3)

        # Scenario C: Docker CLI installed, daemon stopped
        with patch("shutil.which", return_value="/usr/bin/docker"):
            def mock_run_stopped(cmd, *args, **kwargs):
                if "--version" in cmd:
                    return MagicMock(returncode=0, stdout="Docker version 24.0.5")
                return MagicMock(returncode=1, stderr="Cannot connect to the Docker daemon")

            with patch("subprocess.run", side_effect=mock_run_stopped):
                d_info = EnvironmentProbeEngine.probe_docker()
                self.assertTrue(d_info.is_installed)
                self.assertFalse(d_info.is_daemon_running)

    # -------------------------------------------------------------------------
    # 7. Database Reachability Probe
    # -------------------------------------------------------------------------
    def test_probe_databases_socket_checks(self):
        # Bind an ephemeral local socket to simulate an active database
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        active_port = server.getsockname()[1]

        try:
            db_info = EnvironmentProbeEngine.probe_databases(
                custom_ports={"mock_test_db": active_port, "closed_test_db": 59998}
            )
            self.assertIsInstance(db_info, DatabaseReachabilityInfo)
            self.assertIn(f"mock_test_db:{active_port}", db_info.reachable_services)
            self.assertIn("closed_test_db:59998", db_info.unreachable_services)
            self.assertTrue(db_info.probed_services["mock_test_db"].is_reachable)
            self.assertFalse(db_info.probed_services["closed_test_db"].is_reachable)
        finally:
            server.close()

    # -------------------------------------------------------------------------
    # 8. Environment Variables Probe (Safe, No Secret Leaks)
    # -------------------------------------------------------------------------
    def test_probe_environment_variables(self):
        test_env = {
            "ACTIVE_API_KEY": "sk-real-live-secret-key-12345",
            "PLACEHOLDER_TOKEN": "your_api_key_here",
            "DUMMY_SECRET": "todo",
        }
        with patch.dict(os.environ, test_env, clear=False):
            # Create a .env file with declared keys
            env_file = self.workspace_dir / ".env"
            env_file.write_text("ACTIVE_API_KEY=val\nPLACEHOLDER_TOKEN=val\nMISSING_DATABASE_URL=\n", encoding="utf-8")

            res = EnvironmentProbeEngine.probe_environment_variables(
                required_keys=["ACTIVE_API_KEY", "PLACEHOLDER_TOKEN", "MISSING_DATABASE_URL", "UNSET_KEY_XYZ"],
                workspace_path=self.workspace_dir,
            )
            self.assertIn("ACTIVE_API_KEY", res.present)
            self.assertIn("PLACEHOLDER_TOKEN", res.present)
            self.assertIn("MISSING_DATABASE_URL", res.missing)
            self.assertIn("UNSET_KEY_XYZ", res.missing)
            self.assertIn("PLACEHOLDER_TOKEN", res.placeholder_values)

            # Crucial security invariant: actual secret values must NOT appear in output dict
            res_dict = res.to_dict()
            res_str = str(res_dict)
            self.assertNotIn("sk-real-live-secret-key-12345", res_str)

    # -------------------------------------------------------------------------
    # 9. Modular Detector & BootstrapReport Integration
    # -------------------------------------------------------------------------
    def test_detect_environment_modular_and_bootstrap_report(self):
        # Test detect_environment function
        env_profile = detect_environment(self.workspace_dir)
        self.assertIsInstance(env_profile, EnvironmentProfile)
        self.assertTrue(env_profile.python.version)
        self.assertTrue(env_profile.os.system)

        # Test BootstrapReport integration
        report = RepositoryBootstrapper.bootstrap(self.workspace_dir)
        self.assertIsInstance(report, BootstrapReport)
        self.assertIsInstance(report.environment, EnvironmentProfile)
        self.assertEqual(report.environment.python.version, env_profile.python.version)
        
        # Test summary and to_dict
        summary = report.summary()
        self.assertIn("Environment:", summary)
        self.assertIn("Python", summary)
        d = report.to_dict()
        self.assertIn("environment", d)
        self.assertIn("python", d["environment"])

    # -------------------------------------------------------------------------
    # 10. Orchestrator discovery_node Integration
    # -------------------------------------------------------------------------
    def test_orchestrator_discovery_node_populates_environment(self):
        orchestrator = TaskOrchestrator(workspace=self.workspace)
        state: OrchestratorGraphState = {
            "user_request": "Build user auth service",
            "project_profile": None,
            "environment_profile": None,
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

        updates = orchestrator._node_discovery(state)
        self.assertIn("environment_profile", updates)
        env_profile_dict = updates["environment_profile"]
        self.assertIn("python", env_profile_dict)
        self.assertIn("os", env_profile_dict)
        self.assertIn("docker", env_profile_dict)
        self.assertIn("database", env_profile_dict)
        self.assertIn("environment_variables", env_profile_dict)

        # Verify prompt context generation
        env_obj = EnvironmentProfile.from_dict(env_profile_dict)
        prompt_ctx = env_obj.to_prompt_context()
        self.assertIn("Dynamic Host & Runtime Environment", prompt_ctx)
        self.assertIn(env_obj.os.system.capitalize(), prompt_ctx)
        self.assertIn(env_obj.python.version, prompt_ctx)


if __name__ == "__main__":
    unittest.main()
