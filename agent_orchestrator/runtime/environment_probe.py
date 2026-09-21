"""
EnvironmentProbeEngine: Dynamic Pre-Flight Host and Runtime Environment Probing Engine.
Probes the live execution environment across 8 vital dimensions:
1. Python Runtime (active version, executable, virtualenv status)
2. Node.js Runtime (installed status, active version, package managers: npm, pnpm, yarn, bun)
3. Java Runtime (installed status, active version, javac, JAVA_HOME)
4. Host OS & Shell (Windows vs POSIX, architecture, path separators, default shell syntax)
5. Installed Dependencies (importlib.metadata distribution verification against declared dependencies)
6. Docker Availability (CLI installed, daemon reachability, container engine status)
7. Database & Service Reachability (low-latency TCP socket probes for Postgres, MySQL, Redis, MongoDB)
8. Environment Variables (resolved keys, missing required keys, placeholder detection without leaking secrets)
"""
from dataclasses import dataclass, field
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set, Tuple, Union


# =============================================================================
# 1. Data Contracts
# =============================================================================

@dataclass
class PythonRuntimeInfo:
    version: str = field(default_factory=lambda: f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    version_tuple: Tuple[int, int, int] = field(default_factory=lambda: (sys.version_info.major, sys.version_info.minor, sys.version_info.micro))
    executable: str = field(default_factory=lambda: sys.executable)
    is_virtualenv: bool = False
    virtualenv_path: Optional[str] = None
    virtualenv_type: Optional[str] = None  # "venv", "virtualenv", "conda", "poetry"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "version_tuple": list(self.version_tuple),
            "executable": self.executable,
            "is_virtualenv": self.is_virtualenv,
            "virtualenv_path": self.virtualenv_path,
            "virtualenv_type": self.virtualenv_type,
        }


@dataclass
class NodeRuntimeInfo:
    is_installed: bool = False
    version: Optional[str] = None
    executable: Optional[str] = None
    package_managers: Dict[str, str] = field(default_factory=dict)  # {"npm": "10.8.2", "pnpm": "...", etc.}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_installed": self.is_installed,
            "version": self.version,
            "executable": self.executable,
            "package_managers": self.package_managers,
        }


@dataclass
class JavaRuntimeInfo:
    is_installed: bool = False
    version: Optional[str] = None
    jdk_path: Optional[str] = None
    java_home: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_installed": self.is_installed,
            "version": self.version,
            "jdk_path": self.jdk_path,
            "java_home": self.java_home,
        }


@dataclass
class OSInfo:
    system: str = field(default_factory=lambda: platform.system().lower())  # "windows", "linux", "darwin"
    release: str = field(default_factory=platform.release)
    architecture: str = field(default_factory=platform.machine)
    path_separator: str = field(default_factory=lambda: os.sep)
    line_ending: str = field(default_factory=lambda: "\\r\\n" if os.name == "nt" else "\\n")
    default_shell: str = "powershell" if os.name == "nt" else "bash"
    is_windows: bool = field(default_factory=lambda: os.name == "nt" or platform.system().lower() == "windows")
    is_posix: bool = field(default_factory=lambda: os.name == "posix" or platform.system().lower() in ("linux", "darwin"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "system": self.system,
            "release": self.release,
            "architecture": self.architecture,
            "path_separator": self.path_separator,
            "line_ending": self.line_ending,
            "default_shell": self.default_shell,
            "is_windows": self.is_windows,
            "is_posix": self.is_posix,
        }


@dataclass
class InstalledDependenciesInfo:
    python_packages: Dict[str, str] = field(default_factory=dict)  # name -> version
    node_packages: Dict[str, str] = field(default_factory=dict)
    missing_declared_dependencies: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_python_packages": len(self.python_packages),
            "python_packages_sample": {k: self.python_packages[k] for k in list(self.python_packages.keys())[:15]},
            "total_node_packages": len(self.node_packages),
            "node_packages_sample": {k: self.node_packages[k] for k in list(self.node_packages.keys())[:15]},
            "missing_declared_dependencies": self.missing_declared_dependencies,
        }


@dataclass
class DockerInfo:
    is_installed: bool = False
    cli_version: Optional[str] = None
    is_daemon_running: bool = False
    daemon_version: Optional[str] = None
    containers_running_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_installed": self.is_installed,
            "cli_version": self.cli_version,
            "is_daemon_running": self.is_daemon_running,
            "daemon_version": self.daemon_version,
            "containers_running_count": self.containers_running_count,
        }


@dataclass
class ServiceStatus:
    service_name: str
    port: int
    is_reachable: bool
    host: str = "127.0.0.1"
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service_name": self.service_name,
            "port": self.port,
            "is_reachable": self.is_reachable,
            "host": self.host,
            "error": self.error,
        }


@dataclass
class DatabaseReachabilityInfo:
    probed_services: Dict[str, ServiceStatus] = field(default_factory=dict)
    reachable_services: List[str] = field(default_factory=list)
    unreachable_services: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "probed_services": {k: v.to_dict() for k, v in self.probed_services.items()},
            "reachable_services": self.reachable_services,
            "unreachable_services": self.unreachable_services,
        }


@dataclass
class EnvironmentVariablesInfo:
    declared_required: List[str] = field(default_factory=list)
    present: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    placeholder_values: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "declared_required": self.declared_required,
            "present_count": len(self.present),
            "missing": self.missing,
            "placeholder_values": self.placeholder_values,
        }


@dataclass
class EnvironmentProfile:
    """Comprehensive snapshot of dynamic host and runtime environment reality."""
    python: PythonRuntimeInfo = field(default_factory=PythonRuntimeInfo)
    node: NodeRuntimeInfo = field(default_factory=NodeRuntimeInfo)
    java: JavaRuntimeInfo = field(default_factory=JavaRuntimeInfo)
    os: OSInfo = field(default_factory=OSInfo)
    dependencies: InstalledDependenciesInfo = field(default_factory=InstalledDependenciesInfo)
    docker: DockerInfo = field(default_factory=DockerInfo)
    database: DatabaseReachabilityInfo = field(default_factory=DatabaseReachabilityInfo)
    environment_variables: EnvironmentVariablesInfo = field(default_factory=EnvironmentVariablesInfo)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "python": self.python.to_dict(),
            "node": self.node.to_dict(),
            "java": self.java.to_dict(),
            "os": self.os.to_dict(),
            "dependencies": self.dependencies.to_dict(),
            "docker": self.docker.to_dict(),
            "database": self.database.to_dict(),
            "environment_variables": self.environment_variables.to_dict(),
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "EnvironmentProfile":
        if not data or not isinstance(data, dict):
            return cls()

        py_data = data.get("python", {})
        py_tuple = tuple(py_data.get("version_tuple", (3, 8, 0)))
        py_info = PythonRuntimeInfo(
            version=py_data.get("version", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
            version_tuple=py_tuple if len(py_tuple) == 3 else (sys.version_info.major, sys.version_info.minor, sys.version_info.micro),
            executable=py_data.get("executable", sys.executable),
            is_virtualenv=py_data.get("is_virtualenv", False),
            virtualenv_path=py_data.get("virtualenv_path"),
            virtualenv_type=py_data.get("virtualenv_type"),
        )

        node_data = data.get("node", {})
        node_info = NodeRuntimeInfo(
            is_installed=node_data.get("is_installed", False),
            version=node_data.get("version"),
            executable=node_data.get("executable"),
            package_managers=node_data.get("package_managers", {}),
        )

        java_data = data.get("java", {})
        java_info = JavaRuntimeInfo(
            is_installed=java_data.get("is_installed", False),
            version=java_data.get("version"),
            jdk_path=java_data.get("jdk_path"),
            java_home=java_data.get("java_home"),
        )

        os_data = data.get("os", {})
        os_info = OSInfo(
            system=os_data.get("system", platform.system().lower()),
            release=os_data.get("release", platform.release()),
            architecture=os_data.get("architecture", platform.machine()),
            path_separator=os_data.get("path_separator", os.sep),
            line_ending=os_data.get("line_ending", "\\r\\n" if os.name == "nt" else "\\n"),
            default_shell=os_data.get("default_shell", "powershell" if os.name == "nt" else "bash"),
            is_windows=os_data.get("is_windows", os.name == "nt"),
            is_posix=os_data.get("is_posix", os.name == "posix"),
        )

        dep_data = data.get("dependencies", {})
        dep_info = InstalledDependenciesInfo(
            python_packages=dep_data.get("python_packages", dep_data.get("python_packages_sample", {})),
            node_packages=dep_data.get("node_packages", dep_data.get("node_packages_sample", {})),
            missing_declared_dependencies=dep_data.get("missing_declared_dependencies", []),
        )

        dock_data = data.get("docker", {})
        dock_info = DockerInfo(
            is_installed=dock_data.get("is_installed", False),
            cli_version=dock_data.get("cli_version"),
            is_daemon_running=dock_data.get("is_daemon_running", False),
            daemon_version=dock_data.get("daemon_version"),
            containers_running_count=dock_data.get("containers_running_count", 0),
        )

        db_data = data.get("database", {})
        db_info = DatabaseReachabilityInfo(
            probed_services={},
            reachable_services=db_data.get("reachable_services", []),
            unreachable_services=db_data.get("unreachable_services", []),
        )

        env_vars_data = data.get("environment_variables", {})
        env_vars_info = EnvironmentVariablesInfo(
            declared_required=env_vars_data.get("declared_required", []),
            present=env_vars_data.get("present", []),
            missing=env_vars_data.get("missing", []),
            placeholder_values=env_vars_data.get("placeholder_values", []),
        )

        return cls(
            python=py_info,
            node=node_info,
            java=java_info,
            os=os_info,
            dependencies=dep_info,
            docker=dock_info,
            database=db_info,
            environment_variables=env_vars_info,
            warnings=data.get("warnings", []),
        )

    def summary(self) -> str:
        lines = [
            "Environment Awareness Profile:",
            f"- OS: {self.os.system.capitalize()} ({self.os.architecture}, Shell: {self.os.default_shell})",
            f"- Python: {self.python.version} ({'Virtualenv: ' + (self.python.virtualenv_type or 'active') if self.python.is_virtualenv else 'System interpreter'})",
            f"- Node.js: {'v' + self.node.version if self.node.is_installed else 'Not installed'}" + (f" (npm {self.node.package_managers.get('npm')})" if "npm" in self.node.package_managers else ""),
            f"- Java: {'v' + self.java.version if self.java.is_installed else 'Not installed'}",
            f"- Dependencies: {len(self.dependencies.python_packages)} Python installed" + (f", {len(self.dependencies.missing_declared_dependencies)} missing declared" if self.dependencies.missing_declared_dependencies else ""),
            f"- Docker: {'Daemon running (' + (self.docker.daemon_version or 'active') + ')' if self.docker.is_daemon_running else ('CLI installed but daemon stopped' if self.docker.is_installed else 'Not installed')}",
            f"- Databases: {', '.join(self.database.reachable_services) if self.database.reachable_services else 'No live services detected'}",
            f"- Env Vars: {len(self.environment_variables.present)} present" + (f", {len(self.environment_variables.missing)} missing" if self.environment_variables.missing else "") + (f", {len(self.environment_variables.placeholder_values)} placeholder(s)" if self.environment_variables.placeholder_values else ""),
        ]
        if self.warnings:
            lines.append("- Warnings:")
            for w in self.warnings:
                lines.append(f"  * {w}")
        return "\n".join(lines)

    def to_prompt_context(self) -> str:
        """Formats dynamic runtime reality for direct injection into agent planning prompts."""
        sections = [
            "### Dynamic Host & Runtime Environment (Ground Truth)",
            f"- **Host Platform**: {self.os.system.capitalize()} {self.os.release} ({self.os.architecture})",
            f"  * Shell Syntax: `{self.os.default_shell}` (Path separator: `{self.os.path_separator}`)",
            f"  * Note: Use {'PowerShell / Windows CMD' if self.os.is_windows else 'POSIX / Bash'} commands exclusively. Do NOT emit {'POSIX bash one-liners' if self.os.is_windows else 'Windows CMD syntax'}.",
            f"- **Active Python Runtime**:",
            f"  * Version: `{self.python.version}` (Executable: `{self.python.executable}`)",
            f"  * Virtualenv: {'Active (' + (self.python.virtualenv_type or 'venv') + ')' if self.python.is_virtualenv else 'None (System interpreter)'}",
        ]

        if self.node.is_installed:
            pkg_mgrs = ", ".join([f"{k} v{v}" for k, v in self.node.package_managers.items()])
            sections.append(f"- **Active Node.js Runtime**: v{self.node.version} ({pkg_mgrs or 'node available'})")
        else:
            sections.append("- **Active Node.js Runtime**: Not installed on host.")

        if self.java.is_installed:
            sections.append(f"- **Active Java Runtime**: v{self.java.version} (JAVA_HOME: `{self.java.java_home or 'N/A'}`)")

        if self.dependencies.missing_declared_dependencies:
            sections.append(f"- **Missing Declared Dependencies**: {', '.join([f'`{d}`' for d in self.dependencies.missing_declared_dependencies[:8]])}")
            sections.append("  * Install missing packages or avoid importing them before installation.")

        sections.append(f"- **Docker Runtime**: {'Daemon active (' + (self.docker.daemon_version or 'ready') + ')' if self.docker.is_daemon_running else ('CLI installed but daemon is NOT running' if self.docker.is_installed else 'Docker not installed')}")
        if not self.docker.is_daemon_running:
            sections.append("  * Do NOT invoke `docker` or `docker compose` commands directly.")

        if self.database.reachable_services:
            sections.append(f"- **Reachable Services/Databases**: {', '.join(self.database.reachable_services)}")
        if self.database.unreachable_services:
            sections.append(f"- **Unreachable Probed Ports**: {', '.join(self.database.unreachable_services)} (Use in-memory mocks or SQLite if running tests)")

        if self.environment_variables.missing:
            sections.append(f"- **Missing Required Environment Variables**: {', '.join([f'`{v}`' for v in self.environment_variables.missing])}")
        if self.environment_variables.placeholder_values:
            sections.append(f"- **Environment Variables with Placeholder Values**: {', '.join([f'`{v}`' for v in self.environment_variables.placeholder_values])}")

        return "\n".join(sections)


# =============================================================================
# 2. Environment Probe Engine
# =============================================================================

class EnvironmentProbeEngine:
    """
    Deterministic Dynamic Environment Probe Engine.
    Safely discovers active runtime versions, host OS, installed packages,
    Docker daemon responsiveness, database port reachability, and environment variables.
    """

    # Placeholder patterns in environment variable values
    PLACEHOLDER_REGEX = re.compile(
        r"^(your[-_]?\w+|todo|xxx+|changeme|placeholder|example|insert[-_]?\w+|dummy|test[-_]?secret|<.*>)$",
        re.IGNORECASE
    )

    @classmethod
    def probe(
        cls,
        workspace_or_path: Any = None,
        required_env_vars: Optional[List[str]] = None,
        declared_deps: Optional[List[str]] = None,
        probe_db_ports: Optional[Dict[str, int]] = None,
    ) -> EnvironmentProfile:
        """
        Executes comprehensive dynamic environment probing across all 8 dimensions.
        """
        workspace_path = cls._resolve_path(workspace_or_path)

        # 1. Probe Python
        py_info = cls.probe_python()

        # 2. Probe Node
        node_info = cls.probe_node()

        # 3. Probe Java
        java_info = cls.probe_java()

        # 4. Probe OS and Shell
        os_info = cls.probe_os_and_shell()

        # 5. Probe Installed Dependencies
        dep_info = cls.probe_dependencies(declared_deps=declared_deps, workspace_path=workspace_path)

        # 6. Probe Docker
        docker_info = cls.probe_docker()

        # 7. Probe Database / Service Reachability
        db_info = cls.probe_databases(custom_ports=probe_db_ports)

        # 8. Probe Environment Variables
        env_vars_info = cls.probe_environment_variables(
            required_keys=required_env_vars,
            workspace_path=workspace_path
        )

        warnings: List[str] = []

        if dep_info.missing_declared_dependencies:
            warnings.append(
                f"{len(dep_info.missing_declared_dependencies)} declared dependencies are not installed in active environment: "
                f"{', '.join(dep_info.missing_declared_dependencies[:5])}"
            )

        if docker_info.is_installed and not docker_info.is_daemon_running:
            warnings.append("Docker CLI is installed, but Docker daemon is stopped or unreachable.")

        if env_vars_info.missing:
            warnings.append(f"Missing {len(env_vars_info.missing)} required environment variable(s): {', '.join(env_vars_info.missing[:5])}")

        if env_vars_info.placeholder_values:
            warnings.append(f"{len(env_vars_info.placeholder_values)} environment variable(s) contain placeholder values: {', '.join(env_vars_info.placeholder_values[:5])}")

        return EnvironmentProfile(
            python=py_info,
            node=node_info,
            java=java_info,
            os=os_info,
            dependencies=dep_info,
            docker=docker_info,
            database=db_info,
            environment_variables=env_vars_info,
            warnings=warnings,
        )

    # =========================================================================
    # 1. Python Runtime Probe
    # =========================================================================
    @classmethod
    def probe_python(cls) -> PythonRuntimeInfo:
        """Inspects active Python interpreter, version, and virtual environment status."""
        v_info = sys.version_info
        version_str = f"{v_info.major}.{v_info.minor}.{v_info.micro}"
        executable = sys.executable

        # Virtual environment detection
        is_venv = (
            getattr(sys, "base_prefix", sys.prefix) != sys.prefix or
            hasattr(sys, "real_prefix") or
            "VIRTUAL_ENV" in os.environ or
            "CONDA_PREFIX" in os.environ
        )

        venv_path = None
        venv_type = None

        if is_venv:
            venv_path = os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_PREFIX") or sys.prefix
            if "CONDA_PREFIX" in os.environ:
                venv_type = "conda"
            elif (Path(sys.prefix) / "pyvenv.cfg").is_file():
                venv_type = "venv"
            elif (Path(sys.prefix) / "poetry.lock").is_file() or "pypoetry" in sys.prefix:
                venv_type = "poetry"
            else:
                venv_type = "virtualenv"

        return PythonRuntimeInfo(
            version=version_str,
            version_tuple=(v_info.major, v_info.minor, v_info.micro),
            executable=executable,
            is_virtualenv=is_venv,
            virtualenv_path=venv_path,
            virtualenv_type=venv_type,
        )

    # =========================================================================
    # 2. Node Runtime Probe
    # =========================================================================
    @classmethod
    def probe_node(cls) -> NodeRuntimeInfo:
        """Inspects Node.js installation, version, and available package managers."""
        node_path = shutil.which("node")
        if not node_path:
            return NodeRuntimeInfo(is_installed=False)

        version = None
        try:
            res = subprocess.run(
                [node_path, "--version"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0:
                version = res.stdout.strip().lstrip("v")
        except Exception:
            pass

        # Check npm, pnpm, yarn, bun
        pkg_managers: Dict[str, str] = {}
        for pm in ("npm", "pnpm", "yarn", "bun"):
            pm_path = shutil.which(pm)
            if pm_path:
                try:
                    res = subprocess.run(
                        [pm_path, "--version"],
                        capture_output=True,
                        text=True,
                        timeout=1.5,
                    )
                    if res.returncode == 0:
                        pkg_managers[pm] = res.stdout.strip()
                except Exception:
                    pkg_managers[pm] = "available"

        return NodeRuntimeInfo(
            is_installed=True,
            version=version,
            executable=node_path,
            package_managers=pkg_managers,
        )

    # =========================================================================
    # 3. Java Runtime Probe
    # =========================================================================
    @classmethod
    def probe_java(cls) -> JavaRuntimeInfo:
        """Inspects Java installation, version, and JAVA_HOME."""
        java_path = shutil.which("java")
        java_home = os.environ.get("JAVA_HOME")

        if not java_path:
            if java_home:
                cand = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
                if cand.is_file():
                    java_path = str(cand)

        if not java_path:
            return JavaRuntimeInfo(is_installed=False, java_home=java_home)

        version = None
        try:
            res = subprocess.run(
                [java_path, "-version"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            output = res.stderr or res.stdout
            # Match "17.0.2" or "1.8.0_292"
            m = re.search(r'version\s*["\']([^"\']+)["\']', output)
            if m:
                version = m.group(1)
            elif output:
                first_line = output.splitlines()[0]
                version = first_line.replace("java version", "").replace("openjdk version", "").strip().strip('"\'')
        except Exception:
            pass

        return JavaRuntimeInfo(
            is_installed=True,
            version=version,
            jdk_path=java_path,
            java_home=java_home,
        )

    # =========================================================================
    # 4. Host OS & Shell Probe
    # =========================================================================
    @classmethod
    def probe_os_and_shell(cls) -> OSInfo:
        """Detects OS platform, architecture, path separators, and default shell."""
        sys_name = platform.system().lower()
        is_win = (os.name == "nt" or sys_name == "windows")
        is_posix = (os.name == "posix" or sys_name in ("linux", "darwin"))

        default_shell = "powershell" if is_win else "bash"
        if not is_win:
            shell_env = os.environ.get("SHELL", "")
            if "zsh" in shell_env:
                default_shell = "zsh"
            elif "bash" in shell_env:
                default_shell = "bash"
            elif "sh" in shell_env:
                default_shell = "sh"
        else:
            # Check if PowerShell is available or if CMD is preferred
            if shutil.which("pwsh") or shutil.which("powershell"):
                default_shell = "powershell"
            else:
                default_shell = "cmd"

        return OSInfo(
            system=sys_name,
            release=platform.release(),
            architecture=platform.machine(),
            path_separator=os.sep,
            line_ending="\\r\\n" if is_win else "\\n",
            default_shell=default_shell,
            is_windows=is_win,
            is_posix=is_posix,
        )

    # =========================================================================
    # 5. Installed Dependencies Probe
    # =========================================================================
    @classmethod
    def probe_dependencies(
        cls,
        declared_deps: Optional[List[str]] = None,
        workspace_path: Optional[Path] = None,
    ) -> InstalledDependenciesInfo:
        """
        Inspects active Python distributions via importlib.metadata
        and compares with declared dependencies.
        """
        py_packages: Dict[str, str] = {}
        try:
            for dist in importlib.metadata.distributions():
                name = dist.metadata.get("Name")
                if name:
                    py_packages[name.lower()] = dist.version
        except Exception:
            pass

        node_packages: Dict[str, str] = {}
        if workspace_path:
            # Quick scan of node_modules/.package-lock.json or node_modules directly
            nm = workspace_path / "node_modules"
            if nm.is_dir():
                try:
                    for item in nm.iterdir():
                        if item.is_dir() and not item.name.startswith("."):
                            pkg_json = item / "package.json"
                            if pkg_json.is_file():
                                try:
                                    data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                                    node_packages[data.get("name", item.name)] = data.get("version", "unknown")
                                except Exception:
                                    node_packages[item.name] = "installed"
                except Exception:
                    pass

        missing_deps: List[str] = []
        if declared_deps:
            for dep in declared_deps:
                # Normalize dependency name (e.g. "pytest>=7.0" -> "pytest")
                norm = re.split(r"[><=~!]", dep)[0].strip().lower()
                if norm and norm not in py_packages:
                    # Also check common alias/module names
                    aliases = {
                        "fastapi": "fastapi",
                        "django": "django",
                        "flask": "flask",
                        "pydantic": "pydantic",
                        "pytest": "pytest",
                        "celery": "celery",
                        "sqlalchemy": "sqlalchemy",
                    }
                    if norm in aliases and aliases[norm] not in py_packages:
                        missing_deps.append(norm)
                    elif norm not in aliases:
                        missing_deps.append(norm)

        return InstalledDependenciesInfo(
            python_packages=py_packages,
            node_packages=node_packages,
            missing_declared_dependencies=missing_deps,
        )

    # =========================================================================
    # 6. Docker Availability Probe
    # =========================================================================
    @classmethod
    def probe_docker(cls) -> DockerInfo:
        """Inspects Docker CLI existence and daemon responsiveness."""
        docker_path = shutil.which("docker")
        if not docker_path:
            return DockerInfo(is_installed=False)

        cli_ver = None
        try:
            res = subprocess.run(
                [docker_path, "--version"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0:
                cli_ver = res.stdout.strip().replace("Docker version ", "")
        except Exception:
            pass

        # Probe daemon responsiveness via `docker info --format '{{json .}}'` or quick ping
        daemon_running = False
        daemon_ver = None
        running_count = 0

        try:
            # We use a short 1.5s timeout to prevent hanging if daemon is stopped/unresponsive
            res = subprocess.run(
                [docker_path, "info", "--format", "{{.ServerVersion}} {{.ContainersRunning}}"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                daemon_running = True
                parts = res.stdout.strip().split()
                if parts:
                    daemon_ver = parts[0]
                if len(parts) > 1 and parts[1].isdigit():
                    running_count = int(parts[1])
        except Exception:
            daemon_running = False

        return DockerInfo(
            is_installed=True,
            cli_version=cli_ver,
            is_daemon_running=daemon_running,
            daemon_version=daemon_ver,
            containers_running_count=running_count,
        )

    # =========================================================================
    # 7. Database & Service Reachability Probe
    # =========================================================================
    @classmethod
    def probe_databases(cls, custom_ports: Optional[Dict[str, int]] = None) -> DatabaseReachabilityInfo:
        """
        Performs fast TCP socket connectivity probes to identify live local services.
        Standard ports:
        - Postgres: 5432
        - MySQL: 3306
        - Redis: 6379
        - MongoDB: 27017
        """
        ports_to_check = {
            "postgres": 5432,
            "mysql": 3306,
            "redis": 6379,
            "mongodb": 27017,
        }
        if custom_ports:
            ports_to_check.update(custom_ports)

        results: Dict[str, ServiceStatus] = {}
        reachable: List[str] = []
        unreachable: List[str] = []

        for name, port in ports_to_check.items():
            is_up, err = cls._check_socket("127.0.0.1", port, timeout=0.25)
            status = ServiceStatus(
                service_name=name,
                port=port,
                is_reachable=is_up,
                host="127.0.0.1",
                error=err,
            )
            results[name] = status
            if is_up:
                reachable.append(f"{name}:{port}")
            else:
                unreachable.append(f"{name}:{port}")

        return DatabaseReachabilityInfo(
            probed_services=results,
            reachable_services=reachable,
            unreachable_services=unreachable,
        )

    @classmethod
    def _check_socket(cls, host: str, port: int, timeout: float = 0.25) -> Tuple[bool, Optional[str]]:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True, None
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            return False, str(e)
        except Exception as e:
            return False, str(e)

    # =========================================================================
    # 8. Environment Variables Probe
    # =========================================================================
    @classmethod
    def probe_environment_variables(
        cls,
        required_keys: Optional[List[str]] = None,
        workspace_path: Optional[Path] = None,
    ) -> EnvironmentVariablesInfo:
        """
        Safely inspects environment variables against required keys.
        Detects missing variables and placeholder values WITHOUT exposing secret values.
        """
        all_required: Set[str] = set(required_keys or [])

        # Also inspect .env file in workspace if present
        if workspace_path:
            env_file = workspace_path / ".env"
            if env_file.is_file():
                try:
                    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                        line_s = line.strip()
                        if line_s and not line_s.startswith("#") and "=" in line_s:
                            k = line_s.split("=")[0].strip()
                            if k:
                                all_required.add(k)
                except Exception:
                    pass

        present: List[str] = []
        missing: List[str] = []
        placeholders: List[str] = []

        for k in sorted(list(all_required)):
            val = os.environ.get(k)
            if val is None or val == "":
                missing.append(k)
            else:
                present.append(k)
                # Check for obvious placeholder values
                if cls.PLACEHOLDER_REGEX.match(val.strip()):
                    placeholders.append(k)

        return EnvironmentVariablesInfo(
            declared_required=sorted(list(all_required)),
            present=present,
            missing=missing,
            placeholder_values=placeholders,
        )

    # =========================================================================
    # Helpers
    # =========================================================================
    @classmethod
    def _resolve_path(cls, path_or_ws: Any) -> Optional[Path]:
        if not path_or_ws:
            return None
        if hasattr(path_or_ws, "root_dir"):
            return Path(path_or_ws.root_dir).resolve()
        if isinstance(path_or_ws, (str, Path)):
            p = Path(path_or_ws).resolve()
            return p if p.is_dir() else None
        return None
