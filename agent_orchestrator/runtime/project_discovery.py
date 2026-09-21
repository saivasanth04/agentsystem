"""
ProjectDiscoveryEngine: Deterministic Pre-Flight Repository Discovery Engine across 10 Vital Dimensions.
Inspects the repository BEFORE planning or agent execution begins:
1. Language & Runtimes (.python-version, .nvmrc, go.mod, Cargo.toml, runtime.txt)
2. Frameworks & Core Libraries (FastAPI, Django, Flask, Next.js, Express, React, Spring Boot, Gin, etc.)
3. Package Managers & Lockfiles (uv, poetry, pip, pnpm, npm, yarn, cargo, go mod)
4. Entrypoints & Main Services (main.py, app.py, index.ts, server.ts, [project.scripts], "main"/"bin")
5. Test Frameworks & Conventions (pytest, unittest, vitest, jest, cargo test, naming, test dirs)
6. CI/CD Workflows (.github/workflows/*.yml, .gitlab-ci.yml, Jenkinsfile, Makefile)
7. Docker & Containerization (Dockerfile, docker-compose.yml, declared services, ports)
8. Environment & Secrets (.env.example, .env.template, required env vars, virtualenvs)
9. Configuration & Linters (pyproject.toml, tsconfig.json, ruff.toml, eslint.config.js, etc.)
10. Architecture & Topology (Monorepo vs single package, apps/packages layout, src/ layout)
"""
import ast
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Union

from .project_detector import ProjectEnvironmentDetector, ProjectEnvironment
from .test_detector import ExistingTestDetector, RepoTestContext


@dataclass
class ProjectProfile:
    """Comprehensive 10-dimensional project discovery profile."""
    # 1. Language & Runtimes
    primary_language: str = "python"
    detected_languages: List[str] = field(default_factory=lambda: ["python"])
    runtime_versions: Dict[str, str] = field(default_factory=dict)

    # 2. Frameworks & Libraries
    framework: Optional[str] = None
    detected_frameworks: List[str] = field(default_factory=list)
    key_dependencies: List[str] = field(default_factory=list)

    # 3. Package Managers & Lockfiles
    package_manager: str = "pip"
    lockfile: Optional[str] = None
    install_command: Optional[str] = None
    build_command: Optional[str] = None

    # 4. Entrypoints & Main Services
    entrypoints: List[Dict[str, str]] = field(default_factory=list)
    primary_entrypoint: Optional[str] = None

    # 5. Tests
    test_framework: str = "unittest"
    test_command: str = 'python -m unittest discover -s . -p "test_*.py"'
    test_directories: List[str] = field(default_factory=lambda: ["tests"])
    test_file_pattern: str = "test_*.py"
    total_test_files: int = 0

    # 6. CI/CD Workflows
    ci_workflows: List[Dict[str, Any]] = field(default_factory=list)
    authoritative_ci_command: Optional[str] = None

    # 7. Docker & Containerization
    has_docker: bool = False
    dockerfiles: List[str] = field(default_factory=list)
    compose_files: List[str] = field(default_factory=list)
    container_services: List[str] = field(default_factory=list)

    # 8. Environment & Secrets
    env_templates: List[str] = field(default_factory=list)
    required_env_vars: List[str] = field(default_factory=list)
    has_virtualenv: bool = False

    # 9. Configuration & Linters
    config_files: List[str] = field(default_factory=list)
    linters_and_formatters: List[str] = field(default_factory=list)
    type_checker_command: Optional[str] = None
    linter_command: Optional[str] = None

    # 10. Architecture & Topology
    is_monorepo: bool = False
    monorepo_tool: Optional[str] = None
    workspace_packages: List[str] = field(default_factory=list)
    source_directories: List[str] = field(default_factory=lambda: ["src", "."])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_language": self.primary_language,
            "detected_languages": self.detected_languages,
            "runtime_versions": self.runtime_versions,
            "framework": self.framework,
            "detected_frameworks": self.detected_frameworks,
            "key_dependencies": self.key_dependencies,
            "package_manager": self.package_manager,
            "lockfile": self.lockfile,
            "install_command": self.install_command,
            "build_command": self.build_command,
            "entrypoints": self.entrypoints,
            "primary_entrypoint": self.primary_entrypoint,
            "test_framework": self.test_framework,
            "test_command": self.test_command,
            "test_directories": self.test_directories,
            "test_file_pattern": self.test_file_pattern,
            "total_test_files": self.total_test_files,
            "ci_workflows": self.ci_workflows,
            "authoritative_ci_command": self.authoritative_ci_command,
            "has_docker": self.has_docker,
            "dockerfiles": self.dockerfiles,
            "compose_files": self.compose_files,
            "container_services": self.container_services,
            "env_templates": self.env_templates,
            "required_env_vars": self.required_env_vars,
            "has_virtualenv": self.has_virtualenv,
            "config_files": self.config_files,
            "linters_and_formatters": self.linters_and_formatters,
            "type_checker_command": self.type_checker_command,
            "linter_command": self.linter_command,
            "is_monorepo": self.is_monorepo,
            "monorepo_tool": self.monorepo_tool,
            "workspace_packages": self.workspace_packages,
            "source_directories": self.source_directories,
        }

    def to_markdown(self) -> str:
        """Renders an executive summary of the discovered project profile."""
        lines = [
            "### Project Discovery Profile",
            f"- **Primary Language**: {self.primary_language}" + (f" (Runtime: {', '.join([f'{k}={v}' for k, v in self.runtime_versions.items()])})" if self.runtime_versions else ""),
            f"- **Framework**: {self.framework or 'None detected'}" + (f" (All: {', '.join(self.detected_frameworks)})" if len(self.detected_frameworks) > 1 else ""),
            f"- **Package Manager**: `{self.package_manager}`" + (f" (Lockfile: `{self.lockfile}`)" if self.lockfile else ""),
            f"- **Primary Entrypoint**: `{self.primary_entrypoint}`" if self.primary_entrypoint else "- **Primary Entrypoint**: None detected",
            f"- **Test Suite**: `{self.test_framework}` (Command: `{self.test_command}`, Files: {self.total_test_files})",
            f"- **CI/CD**: {len(self.ci_workflows)} workflow(s)" + (f" (Authoritative: `{self.authoritative_ci_command}`)" if self.authoritative_ci_command else ""),
            f"- **Docker**: {'Yes (' + ', '.join(self.container_services or self.dockerfiles) + ')' if self.has_docker else 'No'}",
            f"- **Environment**: {len(self.required_env_vars)} required var(s)" + (f" (from {', '.join(self.env_templates)})" if self.env_templates else ""),
            f"- **Topology**: {'Monorepo (' + (self.monorepo_tool or 'workspaces') + ')' if self.is_monorepo else 'Single package'} (Sources: {', '.join(self.source_directories)})",
        ]
        return "\n".join(lines)

    def to_prompt_context(self) -> str:
        """Formats discovered project context into rich markdown for injection into agent planning prompts."""
        sections = [
            "### Deterministic Project Discovery Profile (Ground Truth)",
            f"- **Language & Runtimes**: {self.primary_language} (Detected: {', '.join(self.detected_languages)})" + (f", Versions: {json.dumps(self.runtime_versions)}" if self.runtime_versions else ""),
            f"- **Framework & Ecosystem**: {self.framework or 'standard'}" + (f" (Detected frameworks: {', '.join(self.detected_frameworks)})" if self.detected_frameworks else ""),
            f"- **Package Manager & Commands**:",
            f"  * Tool: `{self.package_manager}`",
            f"  * Install: `{self.install_command or 'N/A'}`",
            f"  * Build: `{self.build_command or 'N/A'}`",
            f"- **Application Entrypoints**:",
        ]

        if self.entrypoints:
            for ep in self.entrypoints[:5]:
                sections.append(f"  * `{ep.get('path', '')}` ({ep.get('type', 'main')} - {ep.get('details', '')})")
        else:
            sections.append("  * None explicitly declared. Inspect workspace before adding new entrypoints.")

        sections.extend([
            f"- **Test Suite & Verification**:",
            f"  * Framework: `{self.test_framework}`",
            f"  * Command: `{self.test_command}`",
            f"  * Test Dirs: {', '.join([f'`{d}`' for d in self.test_directories])}",
            f"  * Total Test Files: {self.total_test_files}",
        ])

        if self.ci_workflows:
            sections.append("- **CI/CD Invariants**:")
            if self.authoritative_ci_command:
                sections.append(f"  * Authoritative CI Test: `{self.authoritative_ci_command}`")
            for wf in self.ci_workflows[:3]:
                sections.append(f"  * Workflow `{wf.get('file', '')}`: {len(wf.get('commands', []))} command(s)")

        if self.has_docker:
            sections.append(f"- **Containerization**: Dockerfile present ({', '.join(self.dockerfiles[:2])}); Services: {', '.join(self.container_services) or 'None'}")

        if self.required_env_vars:
            sections.append(f"- **Required Environment Variables**: {', '.join(self.required_env_vars[:10])}")

        if self.linters_and_formatters or self.linter_command or self.type_checker_command:
            sections.append(f"- **Linters & Type Checkers**: {', '.join(self.linters_and_formatters) or 'None'}")
            if self.type_checker_command:
                sections.append(f"  * Type Check: `{self.type_checker_command}`")
            if self.linter_command:
                sections.append(f"  * Lint: `{self.linter_command}`")

        sections.append(f"- **Repository Architecture**: {'Monorepo (' + (self.monorepo_tool or 'workspaces') + ')' if self.is_monorepo else 'Single Package'}")
        if self.workspace_packages:
            sections.append(f"  * Packages: {', '.join([f'`{p}`' for p in self.workspace_packages[:8]])}")
        sections.append(f"  * Source Directories: {', '.join([f'`{d}`' for d in self.source_directories])}")

        return "\n".join(sections)


class ProjectDiscoveryEngine:
    """
    Deterministic Project Discovery Engine.
    Inspects all 10 project dimensions across the workspace filesystem before planning starts.
    """

    @classmethod
    def discover(cls, workspace_or_path: Any) -> ProjectProfile:
        """
        Performs comprehensive pre-flight discovery across the workspace.
        """
        root_dir: Path
        if hasattr(workspace_or_path, "root_dir"):
            root_dir = Path(workspace_or_path.root_dir).resolve()
        elif isinstance(workspace_or_path, (str, Path)):
            root_dir = Path(workspace_or_path).resolve()
        else:
            return ProjectProfile()

        if not root_dir.exists() or not root_dir.is_dir():
            return ProjectProfile()

        # Step 1: Base Environment & Language/Framework via ProjectEnvironmentDetector
        base_env: ProjectEnvironment = ProjectEnvironmentDetector.detect(root_dir)

        # Step 2: Test Context via ExistingTestDetector
        test_ctx: RepoTestContext = ExistingTestDetector.scan(root_dir)

        # Step 3: Inspect 10 Dimensions
        languages, runtime_versions = cls._discover_languages_and_runtimes(root_dir, base_env)
        frameworks, key_deps = cls._discover_frameworks_and_deps(root_dir, base_env)
        pkg_mgr, lockfile, install_cmd, build_cmd = cls._discover_package_manager(root_dir, base_env)
        entrypoints, primary_ep = cls._discover_entrypoints(root_dir, languages)
        ci_workflows, ci_cmd = cls._discover_ci_workflows(root_dir, test_ctx)
        has_docker, dockerfiles, compose_files, services = cls._discover_docker(root_dir)
        env_templates, required_vars, has_venv = cls._discover_environment(root_dir)
        config_files, linters, type_cmd, lint_cmd = cls._discover_configuration(root_dir, base_env)
        is_monorepo, monorepo_tool, packages, source_dirs = cls._discover_architecture(root_dir, base_env)

        primary_lang = base_env.language or (languages[0] if languages else "python")
        primary_framework = frameworks[0] if frameworks else base_env.framework

        test_framework = test_ctx.test_framework if test_ctx.has_existing_tests else base_env.test_runner
        test_command = test_ctx.authoritative_ci_command or base_env.test_command
        test_directories = test_ctx.test_directories or base_env.test_dirs
        total_test_files = test_ctx.total_test_files

        return ProjectProfile(
            primary_language=primary_lang,
            detected_languages=languages,
            runtime_versions=runtime_versions,
            framework=primary_framework,
            detected_frameworks=frameworks,
            key_dependencies=key_deps,
            package_manager=pkg_mgr,
            lockfile=lockfile,
            install_command=install_cmd,
            build_command=build_cmd,
            entrypoints=entrypoints,
            primary_entrypoint=primary_ep,
            test_framework=test_framework,
            test_command=test_command,
            test_directories=test_directories,
            test_file_pattern=base_env.test_file_pattern,
            total_test_files=total_test_files,
            ci_workflows=ci_workflows,
            authoritative_ci_command=ci_cmd or test_ctx.authoritative_ci_command,
            has_docker=has_docker,
            dockerfiles=dockerfiles,
            compose_files=compose_files,
            container_services=services,
            env_templates=env_templates,
            required_env_vars=required_vars,
            has_virtualenv=has_venv,
            config_files=config_files,
            linters_and_formatters=linters,
            type_checker_command=type_cmd or base_env.type_checker_command,
            linter_command=lint_cmd or base_env.linter_command,
            is_monorepo=is_monorepo,
            monorepo_tool=monorepo_tool,
            workspace_packages=packages,
            source_directories=source_dirs or base_env.source_dirs,
        )

    # =========================================================================
    # 1. Language & Runtimes
    # =========================================================================
    @classmethod
    def _discover_languages_and_runtimes(cls, root: Path, base_env: ProjectEnvironment) -> tuple[List[str], Dict[str, str]]:
        languages: Set[str] = set()
        runtimes: Dict[str, str] = {}

        if base_env.language:
            languages.add(base_env.language)

        # Python versions
        for fn in (".python-version", "runtime.txt"):
            p = root / fn
            if p.is_file():
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace").strip()
                    if txt:
                        runtimes["python"] = txt
                        languages.add("python")
                except Exception:
                    pass

        # Node versions
        for fn in (".nvmrc", ".node-version"):
            p = root / fn
            if p.is_file():
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace").strip()
                    if txt:
                        runtimes["node"] = txt
                        languages.add("javascript")
                except Exception:
                    pass

        # Check pyproject.toml python version
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            languages.add("python")
            if "python" not in runtimes:
                try:
                    content = pyproject.read_text(encoding="utf-8", errors="replace")
                    m = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', content)
                    if m:
                        runtimes["python"] = m.group(1)
                except Exception:
                    pass

        # Check package.json node version / typescript
        pkg = root / "package.json"
        if pkg.is_file():
            languages.add("javascript")
            try:
                data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
                engines = data.get("engines", {})
                if "node" in engines and "node" not in runtimes:
                    runtimes["node"] = str(engines["node"])
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                if "typescript" in deps or (root / "tsconfig.json").is_file():
                    languages.add("typescript")
            except Exception:
                pass

        # Check go.mod
        gomod = root / "go.mod"
        if gomod.is_file():
            languages.add("go")
            try:
                for line in gomod.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.strip().startswith("go "):
                        runtimes["go"] = line.strip().split()[1]
                        break
            except Exception:
                pass

        # Check Cargo.toml
        cargo = root / "Cargo.toml"
        if cargo.is_file():
            languages.add("rust")
            try:
                for line in cargo.read_text(encoding="utf-8", errors="replace").splitlines():
                    if "rust-version" in line:
                        m = re.search(r'rust-version\s*=\s*["\']([^"\']+)["\']', line)
                        if m:
                            runtimes["rust"] = m.group(1)
                            break
            except Exception:
                pass

        if not languages:
            languages.add("python")

        return sorted(list(languages)), runtimes

    # =========================================================================
    # 2. Frameworks & Core Libraries
    # =========================================================================
    @classmethod
    def _discover_frameworks_and_deps(cls, root: Path, base_env: ProjectEnvironment) -> tuple[List[str], List[str]]:
        frameworks: Set[str] = set()
        deps: Set[str] = set()

        if base_env.framework:
            frameworks.add(base_env.framework)

        # Python: pyproject.toml, requirements.txt, setup.py
        for fn in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "Pipfile"):
            p = root / fn
            if p.is_file():
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace").lower()
                    for fw, match in [
                        ("fastapi", "fastapi"),
                        ("django", "django"),
                        ("flask", "flask"),
                        ("celery", "celery"),
                        ("sqlalchemy", "sqlalchemy"),
                        ("pydantic", "pydantic"),
                        ("pytest", "pytest"),
                        ("langchain", "langchain"),
                    ]:
                        if match in txt:
                            if fw in ("fastapi", "django", "flask"):
                                frameworks.add(fw)
                            deps.add(fw)
                except Exception:
                    pass

        # Check candidate app/entrypoint files for framework imports
        for cand in ("app.py", "main.py", "server.py", "src/app.py", "src/main.py"):
            p = root / cand
            if p.is_file():
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace").lower()
                    if "fastapi" in txt:
                        frameworks.add("fastapi")
                    elif "flask" in txt:
                        frameworks.add("flask")
                    elif "django" in txt:
                        frameworks.add("django")
                except Exception:
                    pass

        # Node: package.json
        pkg = root / "package.json"
        if pkg.is_file():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
                all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
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
                        if fw in ("next", "react", "vue", "express", "nest"):
                            frameworks.add(fw)
                        deps.add(fw)
            except Exception:
                pass

        return sorted(list(frameworks)), sorted(list(deps))

    # =========================================================================
    # 3. Package Managers & Lockfiles
    # =========================================================================
    @classmethod
    def _discover_package_manager(cls, root: Path, base_env: ProjectEnvironment) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
        pkg_mgr = base_env.package_manager or "pip"
        lockfile = None

        # Lockfile priority checks
        if (root / "uv.lock").is_file():
            pkg_mgr = "uv"
            lockfile = "uv.lock"
        elif (root / "poetry.lock").is_file():
            pkg_mgr = "poetry"
            lockfile = "poetry.lock"
        elif (root / "pnpm-lock.yaml").is_file():
            pkg_mgr = "pnpm"
            lockfile = "pnpm-lock.yaml"
        elif (root / "yarn.lock").is_file():
            pkg_mgr = "yarn"
            lockfile = "yarn.lock"
        elif (root / "package-lock.json").is_file():
            pkg_mgr = "npm"
            lockfile = "package-lock.json"
        elif (root / "bun.lockb").is_file() or (root / "bun.lock").is_file():
            pkg_mgr = "bun"
            lockfile = "bun.lockb" if (root / "bun.lockb").is_file() else "bun.lock"
        elif (root / "Cargo.lock").is_file():
            pkg_mgr = "cargo"
            lockfile = "Cargo.lock"
        elif (root / "go.sum").is_file():
            pkg_mgr = "go"
            lockfile = "go.sum"

        install_cmd = base_env.install_command
        if pkg_mgr == "uv":
            install_cmd = "uv sync"
        elif pkg_mgr == "poetry":
            install_cmd = "poetry install"
        elif pkg_mgr == "pnpm":
            install_cmd = "pnpm install"
        elif pkg_mgr == "yarn":
            install_cmd = "yarn install"

        build_cmd = base_env.build_command
        return pkg_mgr, lockfile, install_cmd, build_cmd

    # =========================================================================
    # 4. Entrypoints & Main Services
    # =========================================================================
    @classmethod
    def _discover_entrypoints(cls, root: Path, languages: List[str]) -> tuple[List[Dict[str, str]], Optional[str]]:
        entrypoints: List[Dict[str, str]] = []

        # 1. Inspect manifests (pyproject.toml [project.scripts], package.json "main"/"bin")
        pyproj = root / "pyproject.toml"
        if pyproj.is_file():
            try:
                txt = pyproj.read_text(encoding="utf-8", errors="replace")
                scripts_section = False
                for line in txt.splitlines():
                    if "[project.scripts]" in line or "[tool.poetry.scripts]" in line:
                        scripts_section = True
                        continue
                    if scripts_section:
                        if line.startswith("["):
                            break
                        if "=" in line:
                            parts = line.split("=", 1)
                            cmd_name = parts[0].strip()
                            target = parts[1].strip().strip('"\'')
                            entrypoints.append({
                                "path": target.split(":")[0],
                                "symbol": target.split(":")[1] if ":" in target else "",
                                "type": "cli_script",
                                "details": f"CLI command: {cmd_name}",
                            })
            except Exception:
                pass

        pkg = root / "package.json"
        if pkg.is_file():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
                if "main" in data:
                    entrypoints.append({
                        "path": data["main"],
                        "type": "library_main",
                        "details": "package.json main",
                    })
                bins = data.get("bin", {})
                if isinstance(bins, str):
                    entrypoints.append({
                        "path": bins,
                        "type": "cli_binary",
                        "details": "package.json bin",
                    })
                elif isinstance(bins, dict):
                    for b_name, b_path in bins.items():
                        entrypoints.append({
                            "path": b_path,
                            "type": "cli_binary",
                            "details": f"package.json bin: {b_name}",
                        })
            except Exception:
                pass

        # 2. Inspect standard candidate files on disk
        candidates = [
            "main.py", "app.py", "server.py", "run.py", "manage.py",
            "src/main.py", "src/app.py", "app/main.py",
            "index.ts", "server.ts", "src/index.ts", "src/server.ts", "src/main.ts",
            "index.js", "server.js", "src/index.js", "src/server.js",
            "main.go", "cmd/main.go", "src/main.rs",
        ]

        for cand in candidates:
            p = root / cand
            if p.is_file():
                ep_type = "application"
                details = "Standard entrypoint file"

                # If python, quick AST check for __main__ or app = FastAPI/Flask
                if cand.endswith(".py"):
                    try:
                        content = p.read_text(encoding="utf-8", errors="replace")
                        if '__name__ == "__main__"' in content or "__name__ == '__main__'" in content:
                            details = "Contains __main__ block"
                            ep_type = "executable_script"
                        if "fastapi" in content.lower() and "app =" in content:
                            details = "FastAPI application instance"
                            ep_type = "web_app"
                        elif "flask" in content.lower() and "app =" in content:
                            details = "Flask application instance"
                            ep_type = "web_app"
                    except Exception:
                        pass

                # Avoid duplicate paths
                norm_cand = cand.replace("\\", "/")
                if not any(ep.get("path") == norm_cand for ep in entrypoints):
                    entrypoints.append({
                        "path": norm_cand,
                        "type": ep_type,
                        "details": details,
                    })

        primary = entrypoints[0]["path"] if entrypoints else None
        return entrypoints, primary

    # =========================================================================
    # 5. CI/CD Workflows
    # =========================================================================
    @classmethod
    def _discover_ci_workflows(cls, root: Path, test_ctx: RepoTestContext) -> tuple[List[Dict[str, Any]], Optional[str]]:
        workflows: List[Dict[str, Any]] = []
        auth_cmd: Optional[str] = test_ctx.authoritative_ci_command

        gh_dir = root / ".github" / "workflows"
        if gh_dir.is_dir():
            for wf_file in gh_dir.glob("*.yml"):
                try:
                    content = wf_file.read_text(encoding="utf-8", errors="replace")
                    cmds = []
                    for line in content.splitlines():
                        line_s = line.strip()
                        if line_s.startswith("run:"):
                            cmd = line_s.replace("run:", "").strip()
                            cmds.append(cmd)
                            if not auth_cmd and any(kw in cmd for kw in ("pytest", "npm test", "pnpm test", "cargo test", "go test", "mvn test")):
                                auth_cmd = cmd
                    workflows.append({
                        "file": str(wf_file.relative_to(root)).replace("\\", "/"),
                        "commands": cmds,
                    })
                except Exception:
                    pass

        # Also check Makefile
        makefile = root / "Makefile"
        if makefile.is_file():
            try:
                targets = []
                for line in makefile.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line and line[0].isalnum() and ":" in line and not line.startswith("."):
                        targets.append(line.split(":")[0].strip())
                if targets:
                    workflows.append({
                        "file": "Makefile",
                        "commands": [f"make {t}" for t in targets[:6]],
                    })
            except Exception:
                pass

        return workflows, auth_cmd

    # =========================================================================
    # 6. Docker & Containerization
    # =========================================================================
    @classmethod
    def _discover_docker(cls, root: Path) -> tuple[bool, List[str], List[str], List[str]]:
        dockerfiles: List[str] = []
        compose_files: List[str] = []
        services: Set[str] = set()

        for df in ("Dockerfile", "Dockerfile.dev", "docker/Dockerfile"):
            if (root / df).is_file():
                dockerfiles.append(df)

        for cf in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            p = root / cf
            if p.is_file():
                compose_files.append(cf)
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                    # Naive service extractor: under services: key
                    in_services = False
                    for line in txt.splitlines():
                        if line.startswith("services:"):
                            in_services = True
                            continue
                        if in_services:
                            if line and not line.startswith(" ") and not line.startswith("\t"):
                                break
                            # Indented by 2 spaces is service name
                            if re.match(r"^  [a-zA-Z0-9_-]+:\s*$", line):
                                s_name = line.strip().rstrip(":")
                                services.add(s_name)
                except Exception:
                    pass

        has_docker = bool(dockerfiles or compose_files)
        return has_docker, dockerfiles, compose_files, sorted(list(services))

    # =========================================================================
    # 7. Environment & Secrets
    # =========================================================================
    @classmethod
    def _discover_environment(cls, root: Path) -> tuple[List[str], List[str], bool]:
        env_templates: List[str] = []
        required_vars: Set[str] = set()

        for ef in (".env.example", ".env.template", ".env.sample", ".env.defaults", "env.example"):
            p = root / ef
            if p.is_file():
                env_templates.append(ef)
                try:
                    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                        line_s = line.strip()
                        if line_s and not line_s.startswith("#") and "=" in line_s:
                            var_name = line_s.split("=")[0].strip()
                            if var_name:
                                required_vars.add(var_name)
                except Exception:
                    pass

        has_venv = (root / ".venv").is_dir() or (root / "venv").is_dir() or ("VIRTUAL_ENV" in os.environ)
        return env_templates, sorted(list(required_vars)), has_venv

    # =========================================================================
    # 8. Configuration & Linters
    # =========================================================================
    @classmethod
    def _discover_configuration(cls, root: Path, base_env: ProjectEnvironment) -> tuple[List[str], List[str], Optional[str], Optional[str]]:
        config_files: List[str] = []
        linters: Set[str] = set()

        known_configs = [
            ("pyproject.toml", "pyproject"),
            ("ruff.toml", "ruff"),
            (".ruff.toml", "ruff"),
            ("tsconfig.json", "typescript"),
            (".eslintrc", "eslint"),
            (".eslintrc.js", "eslint"),
            (".eslintrc.json", "eslint"),
            ("eslint.config.js", "eslint"),
            ("eslint.config.mjs", "eslint"),
            (".prettierrc", "prettier"),
            ("biome.json", "biome"),
            ("pytest.ini", "pytest"),
            ("mypy.ini", "mypy"),
            (".flake8", "flake8"),
        ]

        for cf, tool in known_configs:
            if (root / cf).is_file():
                config_files.append(cf)
                if tool in ("ruff", "eslint", "prettier", "biome", "mypy", "flake8"):
                    linters.add(tool)

        # Check pyproject.toml tools
        pyproj = root / "pyproject.toml"
        if pyproj.is_file():
            try:
                txt = pyproj.read_text(encoding="utf-8", errors="replace").lower()
                for tool in ("ruff", "mypy", "black", "isort", "pylint", "pytest"):
                    if f"[tool.{tool}" in txt:
                        linters.add(tool)
            except Exception:
                pass

        # Check package.json tools
        pkg = root / "package.json"
        if pkg.is_file():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                scripts = data.get("scripts", {})
                for tool in ("eslint", "prettier", "biome"):
                    if tool in deps or any(tool in str(s) for s in scripts.values()):
                        linters.add(tool)
            except Exception:
                pass

        type_cmd = base_env.type_checker_command
        lint_cmd = base_env.linter_command
        if "ruff" in linters and not lint_cmd:
            lint_cmd = "ruff check ."
        if "mypy" in linters and not type_cmd:
            type_cmd = "mypy ."
        if "eslint" in linters and not lint_cmd:
            lint_cmd = "npx eslint ."

        return config_files, sorted(list(linters)), type_cmd, lint_cmd

    # =========================================================================
    # 9. Architecture & Workspace Topology
    # =========================================================================
    @classmethod
    def _discover_architecture(cls, root: Path, base_env: ProjectEnvironment) -> tuple[bool, Optional[str], List[str], List[str]]:
        is_monorepo = False
        monorepo_tool = None
        packages: List[str] = []
        source_dirs: Set[str] = set(base_env.source_dirs or ["src", "."])

        # Monorepo signals
        if (root / "pnpm-workspace.yaml").is_file():
            is_monorepo = True
            monorepo_tool = "pnpm-workspaces"
        elif (root / "turbo.json").is_file():
            is_monorepo = True
            monorepo_tool = "turborepo"
        elif (root / "nx.json").is_file():
            is_monorepo = True
            monorepo_tool = "nx"
        elif (root / "lerna.json").is_file():
            is_monorepo = True
            monorepo_tool = "lerna"

        # Check package.json workspaces
        pkg = root / "package.json"
        if pkg.is_file() and not is_monorepo:
            try:
                data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
                if "workspaces" in data:
                    is_monorepo = True
                    monorepo_tool = "npm/yarn-workspaces"
            except Exception:
                pass

        # Scan for packages or apps subdirectories
        for p_dir in ("packages", "apps", "libs", "modules"):
            pd = root / p_dir
            if pd.is_dir():
                is_monorepo = True
                source_dirs.add(p_dir)
                try:
                    for child in pd.iterdir():
                        if child.is_dir() and not child.name.startswith("."):
                            packages.append(f"{p_dir}/{child.name}")
                except Exception:
                    pass

        # Check standard source directories
        for sd in ("src", "app", "lib", "internal", "pkg"):
            if (root / sd).is_dir():
                source_dirs.add(sd)

        return is_monorepo, monorepo_tool, sorted(packages), sorted(list(source_dirs))
