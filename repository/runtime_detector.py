"""
Runtime Detector.
Detects programming languages, frameworks, package managers, test runners,
entrypoints, and CI/Docker infrastructure from repository manifests.
"""
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("repository.runtime_detector")


@dataclass
class RuntimeProfile:
    """Detected runtime environment and tech stack profile."""
    primary_language: str
    framework: Optional[str] = None
    package_manager: str = "unknown"
    test_framework: str = "unknown"
    entrypoints: List[str] = field(default_factory=list)
    is_monorepo: bool = False
    ci_workflows: List[str] = field(default_factory=list)
    docker_present: bool = False
    total_files: int = 0
    key_dependencies: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_language": self.primary_language,
            "framework": self.framework,
            "package_manager": self.package_manager,
            "test_framework": self.test_framework,
            "entrypoints": self.entrypoints,
            "is_monorepo": self.is_monorepo,
            "ci_workflows": self.ci_workflows,
            "docker_present": self.docker_present,
            "total_files": self.total_files,
            "key_dependencies": self.key_dependencies,
        }


class RuntimeDetector:
    """
    Analyzes project configuration files (pyproject.toml, package.json, requirements.txt, Dockerfile, CI)
    to build a comprehensive runtime profile.
    """

    def __init__(self, root_dir: Union[str, Path], db_conn: Optional[sqlite3.Connection] = None):
        self.root_dir = Path(root_dir).resolve()
        self.db = db_conn

    def detect(self) -> RuntimeProfile:
        """Executes full environment detection across workspace manifests."""
        primary_lang = "python"
        framework = None
        pkg_manager = "unknown"
        test_framework = "unknown"
        entrypoints: List[str] = []
        is_monorepo = False
        ci_workflows: List[str] = []
        docker_present = False
        key_deps: Dict[str, str] = {}

        # 1. Inspect Python environment
        req_txt = self.root_dir / "requirements.txt"
        pyproject = self.root_dir / "pyproject.toml"

        if req_txt.exists():
            pkg_manager = "pip"
            try:
                for line in req_txt.read_text(encoding="utf-8", errors="replace").splitlines():
                    clean = line.strip().split("#")[0].strip()
                    if clean:
                        parts = clean.replace(">=", "==").replace("~=", "==").split("==")
                        pkg = parts[0].strip()
                        ver = parts[1].strip() if len(parts) > 1 else "*"
                        key_deps[pkg] = ver
                        if pkg.lower() in ("fastapi", "flask", "django", "tornado"):
                            framework = pkg.capitalize()
                        if pkg.lower() in ("pytest", "unittest"):
                            test_framework = pkg.lower()
            except Exception as e:
                logger.debug(f"Error reading requirements.txt: {e}")

        if pyproject.exists():
            pkg_manager = "poetry" if "tool.poetry" in pyproject.read_text(encoding="utf-8", errors="replace") else "pip/uv"

        # 2. Inspect Node.js / TypeScript environment
        pkg_json = self.root_dir / "package.json"
        frontend_pkg_json = self.root_dir / "frontend" / "package.json"

        target_pkg = pkg_json if pkg_json.exists() else (frontend_pkg_json if frontend_pkg_json.exists() else None)
        if target_pkg:
            if not req_txt.exists():
                primary_lang = "typescript" if (self.root_dir / "tsconfig.json").exists() else "javascript"
            try:
                data = json.loads(target_pkg.read_text(encoding="utf-8", errors="replace"))
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                for k, v in deps.items():
                    key_deps[k] = v
                if "next" in deps:
                    framework = "Next.js"
                elif "react" in deps and not framework:
                    framework = "React"
                elif "vue" in deps:
                    framework = "Vue"
                elif "express" in deps and not framework:
                    framework = "Express"

                if "vitest" in deps:
                    test_framework = "vitest"
                elif "jest" in deps and test_framework == "unknown":
                    test_framework = "jest"

                pkg_manager = "pnpm" if (self.root_dir / "pnpm-lock.yaml").exists() else (
                    "yarn" if (self.root_dir / "yarn.lock").exists() else (
                        "bun" if (self.root_dir / "bun.lockb").exists() else "npm"
                    )
                )
            except Exception as e:
                logger.debug(f"Error reading package.json: {e}")

        # Check monorepo structure
        subdirs = [p for p in self.root_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
        manifest_dirs = [
            d.name for d in subdirs
            if (d / "package.json").exists() or (d / "requirements.txt").exists() or (d / "pyproject.toml").exists()
        ]
        if len(manifest_dirs) > 1 or (self.root_dir / "frontend").is_dir() and (self.root_dir / "agent_orchestrator").is_dir():
            is_monorepo = True

        # 3. Detect standard Entrypoints
        candidate_entrypoints = [
            "main.py",
            "server.py",
            "app.py",
            "start.bat",
            "agent_orchestrator/main.py",
            "agent_orchestrator/server.py",
            "unified_gateway/gateway/server.py",
            "frontend/package.json",
            "src/index.ts",
            "src/index.js",
            "src/main.tsx",
        ]
        for ep in candidate_entrypoints:
            if (self.root_dir / ep).exists():
                entrypoints.append(ep)

        # 4. Detect CI Workflows
        ci_dir = self.root_dir / ".github" / "workflows"
        if ci_dir.exists():
            for f in ci_dir.glob("*.yml"):
                ci_workflows.append(f.name)
            for f in ci_dir.glob("*.yaml"):
                ci_workflows.append(f.name)

        # 5. Detect Docker
        if (self.root_dir / "Dockerfile").exists() or (self.root_dir / "docker-compose.yml").exists():
            docker_present = True

        # Default test framework if still unknown
        if test_framework == "unknown" and (self.root_dir / "tests").exists():
            test_framework = "pytest"

        profile = RuntimeProfile(
            primary_language=primary_lang,
            framework=framework,
            package_manager=pkg_manager,
            test_framework=test_framework,
            entrypoints=entrypoints,
            is_monorepo=is_monorepo,
            ci_workflows=ci_workflows,
            docker_present=docker_present,
            key_dependencies=key_deps,
        )

        if self.db:
            self._persist_profile(profile)

        return profile

    def _persist_profile(self, p: RuntimeProfile):
        """Persists the runtime profile to the SQLite database."""
        if not self.db:
            return
        self.db.execute("DELETE FROM runtime_profiles WHERE id = 1")
        self.db.execute(
            """
            INSERT INTO runtime_profiles
            (id, primary_language, framework, package_manager, test_framework, is_monorepo, docker_present, entrypoints_json, dependencies_json)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                p.primary_language,
                p.framework,
                p.package_manager,
                p.test_framework,
                1 if p.is_monorepo else 0,
                1 if p.docker_present else 0,
                json.dumps(p.entrypoints),
                json.dumps(p.key_dependencies),
            ),
        )
        try:
            self.db.commit()
        except Exception:
            pass
