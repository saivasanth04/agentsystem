"""
Repository Bootstrapper & Modular Detection Engine.
Provides standalone, modular detection functions and pre-flight bootstrap routines:
1. detect_git(path) -> GitInfo
2. detect_project(path) -> ProjectInfo
3. detect_stack(path) -> StackInfo
4. detect_package_manager(path) -> PackageManagerInfo
5. detect_build_system(path) -> BuildSystemInfo
6. detect_test_runner(path) -> TestRunnerInfo
7. RepositoryBootstrapper.bootstrap(workspace_or_path) -> BootstrapReport
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    from .environment_probe import EnvironmentProfile, EnvironmentProbeEngine
except (ImportError, ValueError):
    from agent_orchestrator.runtime.environment_probe import EnvironmentProfile, EnvironmentProbeEngine


# =============================================================================
# 1. Data Contracts
# =============================================================================

@dataclass
class GitInfo:
    is_git_repo: bool = False
    branch: Optional[str] = None
    commit_hash: Optional[str] = None
    is_dirty: bool = False
    uncommitted_files: List[str] = field(default_factory=list)
    untracked_files: List[str] = field(default_factory=list)
    remote_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_git_repo": self.is_git_repo,
            "branch": self.branch,
            "commit_hash": self.commit_hash,
            "is_dirty": self.is_dirty,
            "uncommitted_files": self.uncommitted_files,
            "untracked_files": self.untracked_files,
            "remote_url": self.remote_url,
        }


@dataclass
class ProjectInfo:
    name: str = "unknown"
    root_dir: str = ""
    primary_language: str = "python"
    manifest_file: Optional[str] = None
    project_type: str = "standalone"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "root_dir": self.root_dir,
            "primary_language": self.primary_language,
            "manifest_file": self.manifest_file,
            "project_type": self.project_type,
        }


@dataclass
class StackInfo:
    language: str = "python"
    runtime_version: Optional[str] = None
    framework: Optional[str] = None
    libraries: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language,
            "runtime_version": self.runtime_version,
            "framework": self.framework,
            "libraries": self.libraries,
        }


@dataclass
class PackageManagerInfo:
    name: str = "pip"
    lockfile: Optional[str] = None
    install_command: Optional[str] = None
    has_lockfile: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "lockfile": self.lockfile,
            "install_command": self.install_command,
            "has_lockfile": self.has_lockfile,
        }


@dataclass
class BuildSystemInfo:
    name: str = "standard"
    config_file: Optional[str] = None
    build_command: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "config_file": self.config_file,
            "build_command": self.build_command,
        }


@dataclass
class TestRunnerInfo:
    framework: str = "unittest"
    command: str = 'python -m unittest discover -s . -p "test_*.py"'
    test_dir: str = "tests"
    pattern: str = "test_*.py"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "framework": self.framework,
            "command": self.command,
            "test_dir": self.test_dir,
            "pattern": self.pattern,
        }


@dataclass
class BootstrapReport:
    project: ProjectInfo = field(default_factory=ProjectInfo)
    git: GitInfo = field(default_factory=GitInfo)
    stack: StackInfo = field(default_factory=StackInfo)
    package_manager: PackageManagerInfo = field(default_factory=PackageManagerInfo)
    build_system: BuildSystemInfo = field(default_factory=BuildSystemInfo)
    test_runner: TestRunnerInfo = field(default_factory=TestRunnerInfo)
    environment: EnvironmentProfile = field(default_factory=EnvironmentProfile)
    tools_available: Dict[str, bool] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    is_ready: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project": self.project.to_dict(),
            "git": self.git.to_dict(),
            "stack": self.stack.to_dict(),
            "package_manager": self.package_manager.to_dict(),
            "build_system": self.build_system.to_dict(),
            "test_runner": self.test_runner.to_dict(),
            "environment": self.environment.to_dict(),
            "tools_available": self.tools_available,
            "warnings": self.warnings,
            "is_ready": self.is_ready,
        }

    def summary(self) -> str:
        lines = [
            f"Repository Bootstrap: {'READY' if self.is_ready else 'ATTENTION REQUIRED'}",
            f"- Project: {self.project.name} ({self.project.primary_language})",
            f"- Git: {'Initialized' if self.git.is_git_repo else 'Not a git repository'}" + (f" on branch '{self.git.branch}'" if self.git.branch else "") + (f" (DIRTY: {len(self.git.uncommitted_files)} uncommitted)" if self.git.is_dirty else " (Clean)"),
            f"- Stack: {self.stack.language}" + (f" {self.stack.runtime_version}" if self.stack.runtime_version else "") + (f" with {self.stack.framework}" if self.stack.framework else ""),
            f"- Package Manager: {self.package_manager.name}" + (f" (Lockfile: {self.package_manager.lockfile})" if self.package_manager.lockfile else ""),
            f"- Build System: {self.build_system.name}" + (f" (Command: `{self.build_system.build_command}`)" if self.build_system.build_command else ""),
            f"- Test Runner: {self.test_runner.framework} (`{self.test_runner.command}`)",
            f"- Environment: {self.environment.os.system.capitalize()} | Python {self.environment.python.version}" + (f" (Virtualenv: {self.environment.python.virtualenv_type})" if self.environment.python.is_virtualenv else "") + (f" | Node v{self.environment.node.version}" if self.environment.node.is_installed else ""),
        ]
        if self.warnings:
            lines.append("- Warnings:")
            for w in self.warnings:
                lines.append(f"  * {w}")
        return "\n".join(lines)


# =============================================================================
# 2. Modular Detection Functions
# =============================================================================

def _normalize_path(path_or_ws: Any) -> Path:
    if hasattr(path_or_ws, "root_dir"):
        return Path(path_or_ws.root_dir).resolve()
    return Path(path_or_ws).resolve()


def detect_git(path: Union[str, Path, Any]) -> GitInfo:
    """
    Detects Git repository status, branch, commit HEAD, dirty files, and remote URL.
    """
    root = _normalize_path(path)
    git_dir = root / ".git"

    # Check if .git exists or if root is the git repository toplevel
    is_git = git_dir.is_dir() or git_dir.is_file()
    if not is_git:
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                toplevel = Path(res.stdout.strip()).resolve()
                is_git = (toplevel == root.resolve())
        except Exception:
            is_git = False

    if not is_git:
        return GitInfo(is_git_repo=False)

    branch = None
    commit = None
    remote_url = None
    is_dirty = False
    uncommitted_files: List[str] = []
    untracked_files: List[str] = []

    try:
        # Branch
        b_res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if b_res.returncode == 0:
            branch = b_res.stdout.strip()

        # Commit Hash
        c_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if c_res.returncode == 0:
            commit = c_res.stdout.strip()

        # Status porcelain
        s_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if s_res.returncode == 0 and s_res.stdout.strip():
            for line in s_res.stdout.splitlines():
                if not line.strip():
                    continue
                status_code = line[:2]
                file_name = line[3:].strip()
                if "??" in status_code:
                    untracked_files.append(file_name)
                else:
                    uncommitted_files.append(file_name)
            is_dirty = bool(uncommitted_files or untracked_files)

        # Remote URL
        r_res = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r_res.returncode == 0:
            remote_url = r_res.stdout.strip()
    except Exception:
        pass

    return GitInfo(
        is_git_repo=True,
        branch=branch,
        commit_hash=commit,
        is_dirty=is_dirty,
        uncommitted_files=uncommitted_files,
        untracked_files=untracked_files,
        remote_url=remote_url,
    )


def detect_project(path: Union[str, Path, Any]) -> ProjectInfo:
    """
    Detects project identity, name, primary language, and manifest path.
    """
    root = _normalize_path(path)
    name = root.name
    lang = "python"
    manifest: Optional[str] = None
    project_type = "standalone"

    # Python: pyproject.toml
    if (root / "pyproject.toml").is_file():
        manifest = "pyproject.toml"
        lang = "python"
        try:
            content = (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
            m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
            if m:
                name = m.group(1)
        except Exception:
            pass
    # Node: package.json
    elif (root / "package.json").is_file():
        manifest = "package.json"
        lang = "javascript"
        try:
            data = json.loads((root / "package.json").read_text(encoding="utf-8", errors="replace"))
            name = data.get("name", name)
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "typescript" in deps or (root / "tsconfig.json").is_file():
                lang = "typescript"
            if "workspaces" in data or (root / "pnpm-workspace.yaml").is_file():
                project_type = "monorepo"
        except Exception:
            pass
    # Rust: Cargo.toml
    elif (root / "Cargo.toml").is_file():
        manifest = "Cargo.toml"
        lang = "rust"
        try:
            content = (root / "Cargo.toml").read_text(encoding="utf-8", errors="replace")
            m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
            if m:
                name = m.group(1)
            if "[workspace]" in content:
                project_type = "monorepo"
        except Exception:
            pass
    # Go: go.mod
    elif (root / "go.mod").is_file():
        manifest = "go.mod"
        lang = "go"
        try:
            for line in (root / "go.mod").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("module "):
                    name = line.strip().split()[1].split("/")[-1]
                    break
        except Exception:
            pass
    # Java: pom.xml
    elif (root / "pom.xml").is_file():
        manifest = "pom.xml"
        lang = "java"
        try:
            content = (root / "pom.xml").read_text(encoding="utf-8", errors="replace")
            m = re.search(r'<artifactId>([^<]+)</artifactId>', content)
            if m:
                name = m.group(1)
        except Exception:
            pass
    # Python setup.py / requirements.txt fallback
    elif (root / "setup.py").is_file():
        manifest = "setup.py"
        lang = "python"
    elif (root / "requirements.txt").is_file():
        manifest = "requirements.txt"
        lang = "python"

    return ProjectInfo(
        name=name,
        root_dir=str(root),
        primary_language=lang,
        manifest_file=manifest,
        project_type=project_type,
    )


def detect_stack(path: Union[str, Path, Any]) -> StackInfo:
    """
    Detects language, runtime version, framework, and core libraries.
    """
    root = _normalize_path(path)
    lang = "python"
    runtime_ver = None
    framework = None
    libs: Set[str] = set()

    # Runtime versions
    for fn, l_name in ((".python-version", "python"), ("runtime.txt", "python")):
        p = root / fn
        if p.is_file():
            try:
                runtime_ver = p.read_text(encoding="utf-8", errors="replace").strip()
                lang = l_name
                break
            except Exception:
                pass

    if not runtime_ver:
        for fn in (".nvmrc", ".node-version"):
            p = root / fn
            if p.is_file():
                try:
                    runtime_ver = p.read_text(encoding="utf-8", errors="replace").strip()
                    lang = "javascript"
                    break
                except Exception:
                    pass

    # Inspect Python manifests
    for fn in ("pyproject.toml", "requirements.txt", "Pipfile"):
        p = root / fn
        if p.is_file():
            lang = "python"
            try:
                txt = p.read_text(encoding="utf-8", errors="replace").lower()
                for fw in ("fastapi", "django", "flask", "celery", "sqlalchemy", "pydantic", "pytest"):
                    if fw in txt:
                        libs.add(fw)
                        if fw in ("fastapi", "django", "flask") and not framework:
                            framework = fw
            except Exception:
                pass

    # Inspect candidate app files if framework not found yet
    if not framework:
        for cand in ("app.py", "main.py", "server.py", "src/app.py", "src/main.py"):
            p = root / cand
            if p.is_file():
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace").lower()
                    if "fastapi" in txt:
                        framework = "fastapi"
                    elif "flask" in txt:
                        framework = "flask"
                    elif "django" in txt:
                        framework = "django"
                    if framework:
                        libs.add(framework)
                        break
                except Exception:
                    pass

    # Inspect Node manifests
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            has_ts = "typescript" in all_deps or (root / "tsconfig.json").is_file()
            lang = "typescript" if has_ts else "javascript"
            for fw, match in [
                ("next", "next"),
                ("react", "react"),
                ("vue", "vue"),
                ("express", "express"),
                ("nest", "@nestjs/core"),
                ("tailwind", "tailwindcss"),
                ("prisma", "@prisma/client"),
                ("vitest", "vitest"),
                ("jest", "jest"),
            ]:
                if match in all_deps:
                    libs.add(fw)
                    if fw in ("next", "react", "vue", "express", "nest") and not framework:
                        framework = fw
        except Exception:
            pass

    return StackInfo(
        language=lang,
        runtime_version=runtime_ver,
        framework=framework,
        libraries=sorted(list(libs)),
    )


def detect_package_manager(path: Union[str, Path, Any]) -> PackageManagerInfo:
    """
    Detects package manager (uv, poetry, pip, pnpm, npm, yarn, cargo, go), lockfile, and install command.
    """
    root = _normalize_path(path)

    if (root / "uv.lock").is_file():
        return PackageManagerInfo(name="uv", lockfile="uv.lock", install_command="uv sync", has_lockfile=True)
    if (root / "poetry.lock").is_file():
        return PackageManagerInfo(name="poetry", lockfile="poetry.lock", install_command="poetry install", has_lockfile=True)
    if (root / "pnpm-lock.yaml").is_file():
        return PackageManagerInfo(name="pnpm", lockfile="pnpm-lock.yaml", install_command="pnpm install", has_lockfile=True)
    if (root / "yarn.lock").is_file():
        return PackageManagerInfo(name="yarn", lockfile="yarn.lock", install_command="yarn install", has_lockfile=True)
    if (root / "package-lock.json").is_file():
        return PackageManagerInfo(name="npm", lockfile="package-lock.json", install_command="npm install", has_lockfile=True)
    if (root / "bun.lockb").is_file() or (root / "bun.lock").is_file():
        lf = "bun.lockb" if (root / "bun.lockb").is_file() else "bun.lock"
        return PackageManagerInfo(name="bun", lockfile=lf, install_command="bun install", has_lockfile=True)
    if (root / "Cargo.lock").is_file():
        return PackageManagerInfo(name="cargo", lockfile="Cargo.lock", install_command="cargo fetch", has_lockfile=True)
    if (root / "go.sum").is_file():
        return PackageManagerInfo(name="go", lockfile="go.sum", install_command="go mod download", has_lockfile=True)

    # Manifest based without lockfile
    if (root / "package.json").is_file():
        return PackageManagerInfo(name="npm", lockfile=None, install_command="npm install", has_lockfile=False)
    if (root / "Cargo.toml").is_file():
        return PackageManagerInfo(name="cargo", lockfile=None, install_command="cargo fetch", has_lockfile=False)
    if (root / "go.mod").is_file():
        return PackageManagerInfo(name="go", lockfile=None, install_command="go mod download", has_lockfile=False)
    if (root / "requirements.txt").is_file():
        return PackageManagerInfo(name="pip", lockfile=None, install_command="pip install -r requirements.txt", has_lockfile=False)
    if (root / "pyproject.toml").is_file():
        return PackageManagerInfo(name="pip", lockfile=None, install_command="pip install -e .", has_lockfile=False)

    return PackageManagerInfo(name="pip", lockfile=None, install_command=None, has_lockfile=False)


def detect_build_system(path: Union[str, Path, Any]) -> BuildSystemInfo:
    """
    Detects build tool (make, cmake, gradle, maven, cargo, npm, tsc, setuptools) and build command.
    """
    root = _normalize_path(path)

    if (root / "CMakeLists.txt").is_file():
        return BuildSystemInfo(name="cmake", config_file="CMakeLists.txt", build_command="cmake --build .")
    if (root / "Makefile").is_file():
        return BuildSystemInfo(name="make", config_file="Makefile", build_command="make")
    if (root / "build.gradle.kts").is_file():
        return BuildSystemInfo(name="gradle", config_file="build.gradle.kts", build_command="./gradlew assemble")
    if (root / "build.gradle").is_file():
        return BuildSystemInfo(name="gradle", config_file="build.gradle", build_command="./gradlew assemble")
    if (root / "pom.xml").is_file():
        return BuildSystemInfo(name="maven", config_file="pom.xml", build_command="mvn compile")
    if (root / "Cargo.toml").is_file():
        return BuildSystemInfo(name="cargo", config_file="Cargo.toml", build_command="cargo build")
    if (root / "go.mod").is_file():
        return BuildSystemInfo(name="go", config_file="go.mod", build_command="go build ./...")

    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            scripts = data.get("scripts", {})
            if "build" in scripts:
                return BuildSystemInfo(name="npm-scripts", config_file="package.json", build_command="npm run build")
        except Exception:
            pass

    if (root / "pyproject.toml").is_file():
        return BuildSystemInfo(name="python-build", config_file="pyproject.toml", build_command="python -m build")

    return BuildSystemInfo(name="none", config_file=None, build_command=None)


def detect_test_runner(path: Union[str, Path, Any]) -> TestRunnerInfo:
    """
    Detects test framework (pytest, unittest, vitest, jest, cargo test, go test) and test command.
    """
    root = _normalize_path(path)

    # 1. Check CI for authoritative command first
    gh_dir = root / ".github" / "workflows"
    if gh_dir.is_dir():
        for wf_file in gh_dir.glob("*.yml"):
            try:
                for line in wf_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    line_s = line.strip()
                    if line_s.startswith("run:"):
                        cmd = line_s.replace("run:", "").strip()
                        if "pytest" in cmd:
                            return TestRunnerInfo(framework="pytest", command=cmd, test_dir="tests", pattern="test_*.py")
                        elif "vitest" in cmd:
                            return TestRunnerInfo(framework="vitest", command=cmd, test_dir="tests", pattern="*.test.ts")
                        elif "jest" in cmd:
                            return TestRunnerInfo(framework="jest", command=cmd, test_dir="tests", pattern="*.test.js")
                        elif "cargo test" in cmd:
                            return TestRunnerInfo(framework="cargo", command=cmd, test_dir="tests", pattern="*.rs")
                        elif "go test" in cmd:
                            return TestRunnerInfo(framework="go", command=cmd, test_dir=".", pattern="*_test.go")
            except Exception:
                pass

    # 2. Python checks
    if (root / "pytest.ini").is_file():
        return TestRunnerInfo(framework="pytest", command="pytest", test_dir="tests", pattern="test_*.py")
    if (root / "pyproject.toml").is_file():
        try:
            txt = (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace").lower()
            if "pytest" in txt or "[tool.pytest" in txt:
                return TestRunnerInfo(framework="pytest", command="pytest", test_dir="tests", pattern="test_*.py")
        except Exception:
            pass

    # 3. Node checks
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            scripts = data.get("scripts", {})
            test_cmd = scripts.get("test", "npm test")
            if "vitest" in deps:
                return TestRunnerInfo(framework="vitest", command="npx vitest run", test_dir="tests", pattern="*.test.ts")
            elif "jest" in deps:
                return TestRunnerInfo(framework="jest", command="npx jest", test_dir="tests", pattern="*.test.js")
            elif "test" in scripts and "no test specified" not in test_cmd.lower():
                return TestRunnerInfo(framework="npm-test", command="npm test", test_dir="tests", pattern="*.test.*")
        except Exception:
            pass

    # 4. Rust
    if (root / "Cargo.toml").is_file():
        return TestRunnerInfo(framework="cargo", command="cargo test", test_dir="tests", pattern="*.rs")

    # 5. Go
    if (root / "go.mod").is_file():
        return TestRunnerInfo(framework="go", command="go test ./...", test_dir=".", pattern="*_test.go")

    # Default fallback
    return TestRunnerInfo(
        framework="unittest",
        command='python -m unittest discover -s . -p "test_*.py"',
        test_dir="tests" if (root / "tests").is_dir() else ".",
        pattern="test_*.py",
    )


def detect_environment(
    path: Union[str, Path, Any],
    required_vars: Optional[List[str]] = None,
    declared_deps: Optional[List[str]] = None,
) -> EnvironmentProfile:
    """
    Probes dynamic host and runtime environment (Python, Node, Java, OS, dependencies, Docker, DBs, env vars).
    """
    root = _normalize_path(path)
    return EnvironmentProbeEngine.probe(
        workspace_or_path=root,
        required_env_vars=required_vars,
        declared_deps=declared_deps,
    )


# =============================================================================
# 3. Repository Bootstrapper Coordinator
# =============================================================================

class RepositoryBootstrapper:
    """
    Coordinates modular repository detectors, validates host toolchains, and bootstraps the workspace.
    """

    @classmethod
    def bootstrap(cls, workspace_or_path: Any, auto_init_git: bool = False) -> BootstrapReport:
        root = _normalize_path(workspace_or_path)

        # Run 7 Detectors (6 static + 1 dynamic environment)
        git_info = detect_git(root)
        proj_info = detect_project(root)
        stack_info = detect_stack(root)
        pkg_info = detect_package_manager(root)
        build_info = detect_build_system(root)
        test_info = detect_test_runner(root)
        env_info = detect_environment(root, declared_deps=stack_info.libraries)

        warnings: List[str] = []

        # Merge environment warnings
        if env_info.warnings:
            warnings.extend(env_info.warnings)

        # Auto-init git if requested and missing
        if auto_init_git and not git_info.is_git_repo:
            try:
                res = subprocess.run(["git", "init"], cwd=str(root), capture_output=True, text=True, timeout=5)
                if res.returncode == 0:
                    git_info = detect_git(root)
                else:
                    warnings.append(f"Failed to auto-init git: {res.stderr.strip()}")
            except Exception as e:
                warnings.append(f"Failed to auto-init git: {e}")

        # Check for uncommitted changes
        if git_info.is_dirty:
            total_dirty = len(git_info.uncommitted_files) + len(git_info.untracked_files)
            warnings.append(f"Repository has {total_dirty} uncommitted/untracked files. Agent changes may overwrite uncommitted work.")

        # Toolchain availability checks on PATH
        tools_to_check = ["git"]
        if stack_info.language in ("python", "unknown"):
            tools_to_check.extend(["python"])
        if stack_info.language in ("javascript", "typescript") or pkg_info.name in ("npm", "pnpm", "yarn", "bun"):
            tools_to_check.extend(["node", pkg_info.name if pkg_info.name != "pip" else "npm"])
        if stack_info.language == "rust":
            tools_to_check.extend(["cargo", "rustc"])
        if stack_info.language == "go":
            tools_to_check.append("go")

        tools_available: Dict[str, bool] = {}
        for tool in tools_to_check:
            available = shutil.which(tool) is not None
            tools_available[tool] = available
            if not available:
                warnings.append(f"Detected tool '{tool}' is not installed or not found on system PATH.")

        is_ready = not any(not avail for t, avail in tools_available.items() if t in ("git", "python", "node"))

        return BootstrapReport(
            project=proj_info,
            git=git_info,
            stack=stack_info,
            package_manager=pkg_info,
            build_system=build_info,
            test_runner=test_info,
            environment=env_info,
            tools_available=tools_available,
            warnings=warnings,
            is_ready=is_ready,
        )
