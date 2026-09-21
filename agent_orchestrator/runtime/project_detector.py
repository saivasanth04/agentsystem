"""
ProjectEnvironmentDetector: Automatic Project Type, Language, and Test Runner Detection Engine.
Enables polyglot coding agents to identify project ecosystems and dispatch native test runners:
- package.json -> npm / pnpm / yarn / bun (Vitest, Jest, Mocha)
- pyproject.toml / pytest.ini -> pytest (fallback unittest)
- pom.xml -> Java / Maven (JUnit)
- build.gradle / build.gradle.kts -> Java / Kotlin Gradle
- Cargo.toml -> Rust (cargo test)
- go.mod -> Go (go test ./...)
- pubspec.yaml -> Flutter / Dart (flutter test)
- CMakeLists.txt / Makefile -> C / C++ (ctest / make test)
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass
class ProjectEnvironment:
    language: str = "python"
    framework: Optional[str] = None
    build_tool: str = "python"
    package_manager: str = "pip"
    test_runner: str = "unittest"
    test_command: str = 'python -m unittest discover -s . -p "test_*.py"'
    test_file_pattern: str = "test_*.py"
    test_file_example: str = "test_example.py"
    manifest_file: Optional[str] = None
    compiler_command: Optional[str] = None
    type_checker_command: Optional[str] = None
    linter_command: Optional[str] = None
    install_command: Optional[str] = None
    build_command: Optional[str] = None
    integration_test_command: Optional[str] = None
    source_dirs: List[str] = field(default_factory=lambda: ["src", "."])
    test_dirs: List[str] = field(default_factory=lambda: ["tests", "."])
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language,
            "framework": self.framework,
            "build_tool": self.build_tool,
            "package_manager": self.package_manager,
            "test_runner": self.test_runner,
            "test_command": self.test_command,
            "test_file_pattern": self.test_file_pattern,
            "test_file_example": self.test_file_example,
            "manifest_file": self.manifest_file,
            "compiler_command": self.compiler_command,
            "type_checker_command": self.type_checker_command,
            "linter_command": self.linter_command,
            "install_command": self.install_command,
            "build_command": self.build_command,
            "integration_test_command": self.integration_test_command,
            "source_dirs": self.source_dirs,
            "test_dirs": self.test_dirs,
            "metadata": self.metadata,
        }


class ProjectEnvironmentDetector:
    """
    Scans a workspace directory to determine project language, package manager, and test runner.
    """

    @classmethod
    def detect(cls, workspace_or_path: Any) -> ProjectEnvironment:
        """
        Detects project environment from a WorkspaceManager, Path, or str directory.
        """
        root_dir: Path
        if hasattr(workspace_or_path, "root_dir"):
            root_dir = Path(workspace_or_path.root_dir).resolve()
        elif isinstance(workspace_or_path, (str, Path)):
            root_dir = Path(workspace_or_path).resolve()
        else:
            return ProjectEnvironment()

        if not root_dir.exists() or not root_dir.is_dir():
            return ProjectEnvironment()

        # Check candidate manifest paths: root + direct 1-level child directories (e.g. backend/, frontend/)
        candidates = [root_dir]
        try:
            for item in root_dir.iterdir():
                if item.is_dir() and not item.name.startswith(".") and item.name not in ("node_modules", "target", "build", "dist", "__pycache__"):
                    candidates.append(item)
        except Exception:
            pass

        # Priority 1: Node / TypeScript / React (package.json)
        for cand in candidates:
            pkg_path = cand / "package.json"
            if pkg_path.is_file():
                return cls._detect_node_project(cand, root_dir)

        # Priority 2: Rust (Cargo.toml)
        for cand in candidates:
            cargo_path = cand / "Cargo.toml"
            if cargo_path.is_file():
                return cls._detect_rust_project(cand, root_dir)

        # Priority 3: Go (go.mod)
        for cand in candidates:
            go_path = cand / "go.mod"
            if go_path.is_file():
                return cls._detect_go_project(cand, root_dir)

        # Priority 4: Java Maven (pom.xml)
        for cand in candidates:
            pom_path = cand / "pom.xml"
            if pom_path.is_file():
                return cls._detect_maven_project(cand, root_dir)

        # Priority 5: Java/Kotlin Gradle (build.gradle, build.gradle.kts)
        for cand in candidates:
            if (cand / "build.gradle").is_file() or (cand / "build.gradle.kts").is_file():
                return cls._detect_gradle_project(cand, root_dir)

        # Priority 6: Flutter / Dart (pubspec.yaml)
        for cand in candidates:
            pub_path = cand / "pubspec.yaml"
            if pub_path.is_file():
                return cls._detect_flutter_project(cand, root_dir)

        # Priority 7: Python modern or standard (pyproject.toml, pytest.ini, requirements.txt, setup.py)
        for cand in candidates:
            if (cand / "pyproject.toml").is_file() or (cand / "pytest.ini").is_file() or (cand / "setup.py").is_file():
                return cls._detect_python_project(cand, root_dir)

        # Priority 8: C / C++ (CMakeLists.txt, Makefile)
        for cand in candidates:
            if (cand / "CMakeLists.txt").is_file():
                return ProjectEnvironment(
                    language="cpp",
                    build_tool="cmake",
                    test_runner="ctest",
                    test_command="ctest --output-on-failure",
                    test_file_pattern="*test*.cpp",
                    test_file_example="test_main.cpp",
                    manifest_file="CMakeLists.txt",
                    build_command="cmake --build .",
                )
            if (cand / "Makefile").is_file():
                return ProjectEnvironment(
                    language="c",
                    build_tool="make",
                    test_runner="make",
                    test_command="make test",
                    test_file_pattern="*test*.c",
                    test_file_example="test_main.c",
                    manifest_file="Makefile",
                    build_command="make",
                )

        # Priority 9: Extension Heuristics from existing workspace files
        ext_counts: Dict[str, int] = {}
        try:
            for root, dirs, files in os.walk(str(root_dir)):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "target", "build", "__pycache__")]
                for f in files:
                    ext = Path(f).suffix.lower()
                    if ext:
                        ext_counts[ext] = ext_counts.get(ext, 0) + 1
        except Exception:
            pass

        if ext_counts.get(".ts", 0) > 0 or ext_counts.get(".tsx", 0) > 0:
            return ProjectEnvironment(
                language="typescript",
                framework="react" if ext_counts.get(".tsx", 0) > 0 else None,
                build_tool="npm",
                package_manager="npm",
                test_runner="jest",
                test_command="npm test",
                test_file_pattern="*.test.ts",
                test_file_example="example.test.ts",
                install_command="npm install",
                build_command="npm run build",
            )
        if ext_counts.get(".js", 0) > 0 or ext_counts.get(".jsx", 0) > 0:
            return ProjectEnvironment(
                language="javascript",
                framework="react" if ext_counts.get(".jsx", 0) > 0 else None,
                build_tool="npm",
                package_manager="npm",
                test_runner="jest",
                test_command="npm test",
                test_file_pattern="*.test.js",
                test_file_example="example.test.js",
                install_command="npm install",
                build_command="npm run build",
            )
        if ext_counts.get(".java", 0) > 0:
            return ProjectEnvironment(
                language="java",
                build_tool="maven",
                package_manager="maven",
                test_runner="junit",
                test_command="mvn test",
                test_file_pattern="*Test.java",
                test_file_example="ExampleTest.java",
                compiler_command="mvn test-compile -DskipTests",
                install_command="mvn dependency:resolve",
                build_command="mvn compile",
            )
        if ext_counts.get(".rs", 0) > 0:
            return ProjectEnvironment(
                language="rust",
                build_tool="cargo",
                package_manager="cargo",
                test_runner="cargo",
                test_command="cargo test",
                test_file_pattern="*_test.rs",
                test_file_example="example_test.rs",
                compiler_command="cargo check",
                type_checker_command="cargo check",
                linter_command="cargo clippy",
                install_command="cargo fetch",
                build_command="cargo build",
                integration_test_command="cargo test --test '*'",
            )
        if ext_counts.get(".go", 0) > 0:
            return ProjectEnvironment(
                language="go",
                build_tool="go",
                package_manager="go",
                test_runner="go test",
                test_command="go test ./...",
                test_file_pattern="*_test.go",
                test_file_example="example_test.go",
                compiler_command="go vet ./...",
                type_checker_command="go vet ./...",
                linter_command="go vet ./...",
                install_command="go mod download",
                build_command="go build ./...",
                integration_test_command="go test -tags=integration ./...",
            )

        # Default: Standard Python unittest
        return ProjectEnvironment(
            language="python",
            build_tool="python",
            package_manager="pip",
            test_runner="unittest",
            test_command='python -m unittest discover -s . -p "test_*.py"',
            test_file_pattern="test_*.py",
            test_file_example="test_example.py",
            compiler_command="python -m compileall -q .",
            type_checker_command="mypy .",
            linter_command="ruff check .",
            install_command="pip install -r requirements.txt" if (root_dir / "requirements.txt").is_file() else None,

        )

    @classmethod
    def _detect_node_project(cls, cand: Path, root_dir: Path) -> ProjectEnvironment:
        pkg_file = cand / "package.json"
        pkg_data: Dict[str, Any] = {}
        try:
            pkg_data = json.loads(pkg_file.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass

        # Detect package manager
        pkg_mgr = "npm"
        if (cand / "pnpm-lock.yaml").is_file() or (root_dir / "pnpm-lock.yaml").is_file():
            pkg_mgr = "pnpm"
        elif (cand / "yarn.lock").is_file() or (root_dir / "yarn.lock").is_file():
            pkg_mgr = "yarn"
        elif (cand / "bun.lockb").is_file() or (cand / "bun.lock").is_file() or (root_dir / "bun.lock").is_file():
            pkg_mgr = "bun"

        # Detect language (TypeScript vs JavaScript)
        deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
        has_ts = (
            (cand / "tsconfig.json").is_file()
            or (root_dir / "tsconfig.json").is_file()
            or "typescript" in deps
        )
        lang = "typescript" if has_ts else "javascript"

        # Detect framework
        framework = None
        if "next" in deps:
            framework = "next"
        elif "react" in deps:
            framework = "react"
        elif "vue" in deps:
            framework = "vue"
        elif "express" in deps:
            framework = "express"
        elif "nest" in deps or "@nestjs/core" in deps:
            framework = "nest"

        # Detect test runner & command
        scripts = pkg_data.get("scripts", {})
        custom_test_cmd = scripts.get("test")
        test_runner = "npm test"

        if "vitest" in deps or (custom_test_cmd and "vitest" in custom_test_cmd):
            test_runner = "vitest"
        elif "jest" in deps or (custom_test_cmd and "jest" in custom_test_cmd):
            test_runner = "jest"
        elif "mocha" in deps or (custom_test_cmd and "mocha" in custom_test_cmd):
            test_runner = "mocha"

        # Compute execution command
        rel_prefix = ""
        if cand != root_dir:
            rel = str(cand.relative_to(root_dir)).replace("\\", "/")
            rel_prefix = f"cd {rel} && "

        if custom_test_cmd and "no test specified" not in custom_test_cmd.lower():
            exec_cmd = f"{rel_prefix}{pkg_mgr} test"
        elif test_runner == "vitest":
            exec_cmd = f"{rel_prefix}npx vitest run"
        elif test_runner == "jest":
            exec_cmd = f"{rel_prefix}npx jest"
        else:
            exec_cmd = f"{rel_prefix}{pkg_mgr} test"

        test_pat = "*.test.ts" if lang == "typescript" else "*.test.js"
        if framework == "react" and lang == "typescript":
            test_pat = "*.test.tsx"
        elif framework == "react":
            test_pat = "*.test.jsx"

        test_ex = "example.test.tsx" if (framework == "react" and lang == "typescript") else ("example.test.ts" if lang == "typescript" else "example.test.js")

        compiler_cmd = f"{rel_prefix}npx tsc --noEmit" if lang == "typescript" else None
        type_check_cmd = f"{rel_prefix}npx tsc --noEmit" if lang == "typescript" else None
        lint_cmd = f"{rel_prefix}{pkg_mgr} run lint" if ("lint" in scripts) else f"{rel_prefix}npx eslint ."
        install_cmd = f"{rel_prefix}{pkg_mgr} install"
        build_cmd = f"{rel_prefix}{pkg_mgr} run build" if "build" in scripts else None
        integration_cmd = None
        for ik in ("test:e2e", "test:integration", "e2e", "integration"):
            if ik in scripts:
                integration_cmd = f"{rel_prefix}{pkg_mgr} run {ik}"
                break

        return ProjectEnvironment(
            language=lang,
            framework=framework,
            build_tool=pkg_mgr,
            package_manager=pkg_mgr,
            test_runner=test_runner,
            test_command=exec_cmd,
            test_file_pattern=test_pat,
            test_file_example=test_ex,
            manifest_file=str(pkg_file.relative_to(root_dir)).replace("\\", "/"),
            compiler_command=compiler_cmd,
            type_checker_command=type_check_cmd,
            linter_command=lint_cmd,
            install_command=install_cmd,
            build_command=build_cmd,
            integration_test_command=integration_cmd,
            source_dirs=["src", "app", "lib"],
            test_dirs=["tests", "__tests__", "src"],
            metadata={"scripts": scripts, "dependencies": list(deps.keys())[:20]},
        )

    @classmethod
    def _detect_maven_project(cls, cand: Path, root_dir: Path) -> ProjectEnvironment:
        pom_file = cand / "pom.xml"
        content = ""
        try:
            content = pom_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass

        framework = "spring-boot" if "spring-boot" in content.lower() else None
        rel_prefix = ""
        if cand != root_dir:
            rel = str(cand.relative_to(root_dir)).replace("\\", "/")
            rel_prefix = f"cd {rel} && "

        return ProjectEnvironment(
            language="java",
            framework=framework,
            build_tool="maven",
            package_manager="maven",
            test_runner="maven",
            test_command=f"{rel_prefix}mvn test",
            test_file_pattern="*Test.java",
            test_file_example="AppTest.java",
            manifest_file=str(pom_file.relative_to(root_dir)).replace("\\", "/"),
            compiler_command=f"{rel_prefix}mvn test-compile -DskipTests",
            linter_command=f"{rel_prefix}mvn checkstyle:check",
            install_command=f"{rel_prefix}mvn dependency:resolve",
            build_command=f"{rel_prefix}mvn compile",
            integration_test_command=f"{rel_prefix}mvn verify -DskipUnitTests",
            source_dirs=["src/main/java"],
            test_dirs=["src/test/java"],
        )

    @classmethod
    def _detect_gradle_project(cls, cand: Path, root_dir: Path) -> ProjectEnvironment:
        is_kotlin = (cand / "build.gradle.kts").is_file()
        manifest_name = "build.gradle.kts" if is_kotlin else "build.gradle"
        manifest_file = cand / manifest_name
        gradle_cmd = "./gradlew test" if (cand / "gradlew").is_file() or (root_dir / "gradlew").is_file() else "gradle test"

        rel_prefix = ""
        if cand != root_dir:
            rel = str(cand.relative_to(root_dir)).replace("\\", "/")
            rel_prefix = f"cd {rel} && "

        return ProjectEnvironment(
            language="kotlin" if is_kotlin else "java",
            build_tool="gradle",
            package_manager="gradle",
            test_runner="gradle",
            test_command=f"{rel_prefix}{gradle_cmd}",
            test_file_pattern="*Test.kt" if is_kotlin else "*Test.java",
            test_file_example="AppTest.kt" if is_kotlin else "AppTest.java",
            manifest_file=str(manifest_file.relative_to(root_dir)).replace("\\", "/"),
            compiler_command=f"{rel_prefix}{gradle_cmd} testClasses",
            install_command=f"{rel_prefix}{gradle_cmd.replace('test', 'dependencies')}",
            build_command=f"{rel_prefix}{gradle_cmd.replace('test', 'assemble')}",
            integration_test_command=f"{rel_prefix}{gradle_cmd.replace('test', 'integrationTest')}",
            source_dirs=["src/main/java", "src/main/kotlin"],
            test_dirs=["src/test/java", "src/test/kotlin"],
        )

    @classmethod
    def _detect_rust_project(cls, cand: Path, root_dir: Path) -> ProjectEnvironment:
        cargo_file = cand / "Cargo.toml"
        rel_prefix = ""
        if cand != root_dir:
            rel = str(cand.relative_to(root_dir)).replace("\\", "/")
            rel_prefix = f"cd {rel} && "

        return ProjectEnvironment(
            language="rust",
            build_tool="cargo",
            package_manager="cargo",
            test_runner="cargo",
            test_command=f"{rel_prefix}cargo test",
            test_file_pattern="tests/*.rs",
            test_file_example="tests/test_integration.rs",
            manifest_file=str(cargo_file.relative_to(root_dir)).replace("\\", "/"),
            compiler_command=f"{rel_prefix}cargo check",
            type_checker_command=f"{rel_prefix}cargo check",
            linter_command=f"{rel_prefix}cargo clippy",
            install_command=f"{rel_prefix}cargo fetch",
            build_command=f"{rel_prefix}cargo build",
            integration_test_command=f"{rel_prefix}cargo test --test '*'",
            source_dirs=["src"],
            test_dirs=["tests", "src"],
        )

    @classmethod
    def _detect_go_project(cls, cand: Path, root_dir: Path) -> ProjectEnvironment:
        go_file = cand / "go.mod"
        framework = None
        try:
            content = go_file.read_text(encoding="utf-8", errors="replace")
            if "gin-gonic/gin" in content:
                framework = "gin"
            elif "gofiber/fiber" in content:
                framework = "fiber"
            elif "labstack/echo" in content:
                framework = "echo"
        except Exception:
            pass

        rel_prefix = ""
        if cand != root_dir:
            rel = str(cand.relative_to(root_dir)).replace("\\", "/")
            rel_prefix = f"cd {rel} && "

        return ProjectEnvironment(
            language="go",
            framework=framework,
            build_tool="go",
            package_manager="go",
            test_runner="go test",
            test_command=f"{rel_prefix}go test ./...",
            test_file_pattern="*_test.go",
            test_file_example="main_test.go",
            manifest_file=str(go_file.relative_to(root_dir)).replace("\\", "/"),
            compiler_command=f"{rel_prefix}go vet ./...",
            type_checker_command=f"{rel_prefix}go vet ./...",
            linter_command=f"{rel_prefix}go vet ./...",
            install_command=f"{rel_prefix}go mod download",
            build_command=f"{rel_prefix}go build ./...",
            integration_test_command=f"{rel_prefix}go test -tags=integration ./...",
            source_dirs=["."],
            test_dirs=["."],
        )

    @classmethod
    def _detect_flutter_project(cls, cand: Path, root_dir: Path) -> ProjectEnvironment:
        pub_file = cand / "pubspec.yaml"
        content = ""
        try:
            content = pub_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass

        is_flutter = "flutter:" in content
        rel_prefix = ""
        if cand != root_dir:
            rel = str(cand.relative_to(root_dir)).replace("\\", "/")
            rel_prefix = f"cd {rel} && "

        cmd = f"{rel_prefix}flutter test" if is_flutter else f"{rel_prefix}dart test"
        analyze_cmd = f"{rel_prefix}flutter analyze" if is_flutter else f"{rel_prefix}dart analyze"
        return ProjectEnvironment(
            language="dart",
            framework="flutter" if is_flutter else None,
            build_tool="flutter" if is_flutter else "dart",
            package_manager="pub",
            test_runner="flutter test" if is_flutter else "dart test",
            test_command=cmd,
            test_file_pattern="*_test.dart",
            test_file_example="widget_test.dart",
            manifest_file=str(pub_file.relative_to(root_dir)).replace("\\", "/"),
            compiler_command=analyze_cmd,
            type_checker_command=analyze_cmd,
            linter_command=analyze_cmd,
            install_command=f"{rel_prefix}flutter pub get" if is_flutter else f"{rel_prefix}dart pub get",
            build_command=f"{rel_prefix}flutter build" if is_flutter else None,
            integration_test_command=f"{rel_prefix}flutter test integration_test" if is_flutter else None,
            source_dirs=["lib"],
            test_dirs=["test"],
        )

    @classmethod
    def _detect_python_project(cls, cand: Path, root_dir: Path) -> ProjectEnvironment:
        has_pytest = (cand / "pytest.ini").is_file()
        has_pyproject = (cand / "pyproject.toml").is_file()

        framework = None
        if has_pyproject:
            try:
                pyproj_txt = (cand / "pyproject.toml").read_text(encoding="utf-8", errors="replace").lower()
                if "pytest" in pyproj_txt:
                    has_pytest = True
                if "fastapi" in pyproj_txt:
                    framework = "fastapi"
                elif "django" in pyproj_txt:
                    framework = "django"
                elif "flask" in pyproj_txt:
                    framework = "flask"
            except Exception:
                pass

        test_cmd = "pytest" if has_pytest else 'python -m unittest discover -s . -p "test_*.py"'
        manifest = "pyproject.toml" if has_pyproject else ("pytest.ini" if has_pytest else "setup.py")

        rel_prefix = ""
        if cand != root_dir:
            rel = str(cand.relative_to(root_dir)).replace("\\", "/")
            rel_prefix = f"cd {rel} && "

        install_cmd = None
        if (cand / "requirements.txt").is_file():
            install_cmd = f"{rel_prefix}pip install -r requirements.txt"
        elif has_pyproject:
            install_cmd = f"{rel_prefix}pip install -e ."

        build_cmd = f"{rel_prefix}python -m build" if has_pyproject else None
        integration_cmd = None
        if (cand / "tests" / "integration").is_dir() or (root_dir / "tests" / "integration").is_dir():
            integration_cmd = f"{rel_prefix}pytest tests/integration" if has_pytest else f'{rel_prefix}python -m unittest discover -s tests/integration'

        return ProjectEnvironment(
            language="python",
            framework=framework,
            build_tool="pip",
            package_manager="pip",
            test_runner="pytest" if has_pytest else "unittest",
            test_command=f"{rel_prefix}{test_cmd}",
            test_file_pattern="test_*.py",
            test_file_example="test_example.py",
            manifest_file=str((cand / manifest).relative_to(root_dir)).replace("\\", "/") if (cand / manifest).is_file() else manifest,
            compiler_command=f"{rel_prefix}python -m compileall -q .",
            type_checker_command=f"{rel_prefix}mypy .",
            linter_command=f"{rel_prefix}ruff check .",
            install_command=install_cmd,
            build_command=build_cmd,
            integration_test_command=integration_cmd,
            source_dirs=["src", "."],
            test_dirs=["tests", "."],
        )

