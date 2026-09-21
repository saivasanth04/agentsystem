"""
ExistingTestDetector: Repository Test Suite, Convention, Fixture, Mock, and CI Discovery Engine.
Enables coding agents to inspect the repository prior to writing tests:
- Discovers existing test frameworks (pytest, unittest, jest, vitest, junit, cargo test, go test, etc.)
- Discovers existing test files and directory layouts
- Discovers existing test conventions (function-based vs class-based, assertion styles, async markers)
- Discovers declared test fixtures (conftest.py, setupTests.*, test_helper.*)
- Discovers mocking patterns (unittest.mock, pytest-mock, jest.fn, msw, responses, etc.)
- Extracts authoritative CI test commands from .github/workflows/*.yml, Makefile, etc.
- Executes pre-flight baseline test runs to establish ground-truth green/red state.
"""
import ast
from dataclasses import dataclass, field
import fnmatch
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, List, Optional, Union


@dataclass
class RepoTestContext:
    has_existing_tests: bool = False
    test_framework: str = "unknown"
    test_directories: List[str] = field(default_factory=list)
    existing_test_files: List[str] = field(default_factory=list)
    total_test_files: int = 0
    ci_test_commands: List[Dict[str, str]] = field(default_factory=list)
    authoritative_ci_command: Optional[str] = None
    fixtures: List[Dict[str, Any]] = field(default_factory=list)
    mocks: List[Dict[str, Any]] = field(default_factory=list)
    conventions: Dict[str, Any] = field(default_factory=dict)
    sample_tests: List[Dict[str, str]] = field(default_factory=list)
    baseline_execution: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_existing_tests": self.has_existing_tests,
            "test_framework": self.test_framework,
            "test_directories": self.test_directories,
            "existing_test_files": self.existing_test_files,
            "total_test_files": self.total_test_files,
            "ci_test_commands": self.ci_test_commands,
            "authoritative_ci_command": self.authoritative_ci_command,
            "fixtures": self.fixtures,
            "mocks": self.mocks,
            "conventions": self.conventions,
            "sample_tests": self.sample_tests,
            "baseline_execution": self.baseline_execution,
        }

    def format_fixtures_summary(self) -> str:
        if not self.fixtures:
            return "No custom fixtures or setup helpers detected."
        lines = []
        for fix in self.fixtures[:8]:
            src = fix.get("source_file", "")
            name = fix.get("name", "")
            scope = fix.get("scope", "function")
            doc = fix.get("docstring", "").strip().split("\n")[0]
            doc_str = f" - {doc}" if doc else ""
            lines.append(f"  * `{name}` (scope: {scope}, from `{src}`){doc_str}")
        return "\n".join(lines)

    def format_mocks_summary(self) -> str:
        if not self.mocks:
            return "No specific mocking library detected. Use standard framework mocks."
        lines = []
        for m in self.mocks:
            lib = m.get("library", "")
            usage = m.get("pattern", "")
            examples = ", ".join([f"`{ex}`" for ex in m.get("examples", [])[:3]])
            lines.append(f"  * {lib} ({usage}): {examples}")
        return "\n".join(lines)

    def format_conventions_summary(self) -> str:
        if not self.conventions:
            return "Standard conventions apply."
        lines = [
            f"  * Structure: {self.conventions.get('test_style', 'standard')}",
            f"  * Assertion Style: {self.conventions.get('assertion_style', 'standard')}",
            f"  * File Naming: {self.conventions.get('naming_convention', 'test_*.py')}",
            f"  * Async Support: {'Yes' if self.conventions.get('async_test_support') else 'No'}",
        ]
        return "\n".join(lines)

    def to_prompt_context(self) -> str:
        """Formats discovered test context into rich markdown for injection into agent prompts."""
        if not self.has_existing_tests and not self.ci_test_commands:
            return "No existing test suite detected in repository. Create tests according to standard project conventions."

        sections = [
            "### Existing Repository Test Architecture & Conventions",
            f"- **Detected Test Framework**: {self.test_framework}",
            f"- **Existing Test Directories**: {', '.join([f'`{d}`' for d in self.test_directories]) if self.test_directories else 'None'}",
            f"- **Total Existing Test Files**: {self.total_test_files}",
        ]

        if self.authoritative_ci_command:
            sections.append(f"- **Authoritative CI Test Command**: `{self.authoritative_ci_command}`")

        sections.extend([
            "- **Conventions & Style**:",
            self.format_conventions_summary(),
            "- **Available Fixtures & Helpers** (Reuse these! Do NOT reinvent or redeclare them):",
            self.format_fixtures_summary(),
            "- **Mocking Patterns** (Match repository style):",
            self.format_mocks_summary(),
        ])

        if self.sample_tests:
            sections.append("- **Sibling Test Example** (Mirror this exact structure and style):")
            for sample in self.sample_tests[:1]:
                sections.append(f"```\n// File: {sample.get('filepath')}\n{sample.get('content')}\n```")

        if self.baseline_execution:
            status_str = "PASSED" if self.baseline_execution.get("passed") else "FAILED"
            sections.append(
                f"- **Baseline Test Run Status**: {status_str} (Exit code: {self.baseline_execution.get('exit_code')}). "
                "CRITICAL: Your changes and tests MUST NOT break any existing passing tests!"
            )

        return "\n".join(sections)


class ExistingTestDetector:
    """
    Introspects repository files, test frameworks, fixtures, mocks, and CI workflows.
    """

    IGNORE_DIRS = {
        "node_modules", ".git", "__pycache__", "venv", ".venv", "env", ".env",
        "target", "dist", "build", ".tox", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", "site-packages", ".idea", ".vscode"
    }

    TEST_PATTERNS = [
        "test_*.py", "*_test.py",
        "*.test.ts", "*.test.tsx", "*.test.js", "*.test.jsx",
        "*.spec.ts", "*.spec.tsx", "*.spec.js", "*.spec.jsx",
        "*Test.java", "*Tests.java", "*TestCase.java", "*Test.kt",
        "*_test.go",
        "*_test.rs",
        "*_test.dart",
    ]

    @classmethod
    def scan(cls, workspace_or_path: Any, max_samples: int = 2) -> RepoTestContext:
        """
        Deeply inspects the repository and returns a populated RepoTestContext.
        """
        root_dir = cls._resolve_root_dir(workspace_or_path)
        if not root_dir or not root_dir.is_dir():
            return RepoTestContext()

        # 1. Discover existing test files and directories
        test_files = cls._find_test_files(root_dir)
        rel_test_files = [str(f.relative_to(root_dir)).replace("\\", "/") for f in test_files]

        # Extract test directories
        test_dirs = set()
        for f in rel_test_files:
            parts = f.split("/")
            if len(parts) > 1:
                test_dirs.add(parts[0])
            else:
                test_dirs.add(".")

        # 2. Extract CI test commands
        ci_commands = cls._extract_ci_commands(root_dir)
        authoritative_ci = ci_commands[0]["command"] if ci_commands else None

        # 3. Detect framework
        framework = cls._detect_test_framework(root_dir, test_files, authoritative_ci)

        # 4. Extract fixtures and setup helpers
        fixtures = cls._extract_fixtures(root_dir, test_files)

        # 5. Extract mocking patterns
        mocks = cls._extract_mock_patterns(test_files)

        # 6. Extract conventions (naming, assertion style, class vs function)
        conventions = cls._extract_conventions(test_files)

        # 7. Extract sample sibling test files
        sample_tests = cls._extract_sample_tests(root_dir, test_files, max_samples=max_samples)

        has_tests = len(test_files) > 0

        return RepoTestContext(
            has_existing_tests=has_tests,
            test_framework=framework,
            test_directories=sorted(list(test_dirs)),
            existing_test_files=rel_test_files,
            total_test_files=len(test_files),
            ci_test_commands=ci_commands,
            authoritative_ci_command=authoritative_ci,
            fixtures=fixtures,
            mocks=mocks,
            conventions=conventions,
            sample_tests=sample_tests,
        )

    @classmethod
    def run_baseline(
        cls,
        workspace_or_path: Any,
        command: Optional[str] = None,
        sandbox: Optional[Any] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """
        Executes existing tests before code modification to capture pre-flight baseline state.
        """
        root_dir = cls._resolve_root_dir(workspace_or_path)
        if not root_dir or not root_dir.is_dir():
            return {"passed": True, "exit_code": 0, "skipped": True, "reason": "Invalid workspace path"}

        # If no command provided, inspect and find authoritative CI or detector command
        if not command:
            context = cls.scan(root_dir)
            if not context.has_existing_tests:
                return {
                    "passed": True,
                    "exit_code": 0,
                    "skipped": True,
                    "reason": "No existing test files found in repository."
                }
            if context.authoritative_ci_command:
                command = context.authoritative_ci_command
            else:
                from .project_detector import ProjectEnvironmentDetector
                env = ProjectEnvironmentDetector.detect(root_dir)
                command = env.test_command

        if not command:
            return {"passed": True, "exit_code": 0, "skipped": True, "reason": "No test command available."}

        # Run test execution
        try:
            if sandbox:
                res = sandbox.execute(command, timeout=timeout)
                exit_code = getattr(res, "exit_code", 0)
                stdout = getattr(res, "stdout", "")
                stderr = getattr(res, "stderr", "")
            else:
                proc = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(root_dir),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                exit_code = proc.returncode
                stdout = proc.stdout
                stderr = proc.stderr

            passed = (exit_code == 0)
            return {
                "passed": passed,
                "exit_code": exit_code,
                "command": command,
                "stdout_snippet": stdout[-1000:] if stdout else "",
                "stderr_snippet": stderr[-1000:] if stderr else "",
                "skipped": False,
            }
        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "exit_code": 124,
                "command": command,
                "error": f"Baseline test execution timed out after {timeout} seconds",
                "skipped": False,
            }
        except Exception as e:
            return {
                "passed": False,
                "exit_code": 1,
                "command": command,
                "error": str(e),
                "skipped": False,
            }

    @classmethod
    def _resolve_root_dir(cls, workspace_or_path: Any) -> Optional[Path]:
        if hasattr(workspace_or_path, "root_dir"):
            return Path(workspace_or_path.root_dir).resolve()
        elif isinstance(workspace_or_path, (str, Path)):
            return Path(workspace_or_path).resolve()
        return None

    @classmethod
    def _find_test_files(cls, root_dir: Path) -> List[Path]:
        """Discovers all test files matching standard patterns while ignoring build artifacts."""
        found_files: List[Path] = []
        try:
            for root, dirs, files in os.walk(str(root_dir)):
                # Filter out ignored directories in-place
                dirs[:] = [d for d in dirs if d not in cls.IGNORE_DIRS and not d.startswith(".")]
                for f in files:
                    for pat in cls.TEST_PATTERNS:
                        if fnmatch.fnmatch(f, pat):
                            found_files.append(Path(root) / f)
                            break
        except Exception:
            pass
        return sorted(found_files)

    @classmethod
    def _extract_ci_commands(cls, root_dir: Path) -> List[Dict[str, str]]:
        """Parses CI configuration files (.github/workflows, Makefile, etc.) for test commands."""
        ci_commands: List[Dict[str, str]] = []

        # 1. Inspect .github/workflows/*.yml or *.yaml
        workflows_dir = root_dir / ".github" / "workflows"
        if workflows_dir.is_dir():
            try:
                for yml_file in workflows_dir.iterdir():
                    if yml_file.suffix in (".yml", ".yaml") and yml_file.is_file():
                        rel_path = str(yml_file.relative_to(root_dir)).replace("\\", "/")
                        content = yml_file.read_text(encoding="utf-8", errors="replace")
                        for line in content.splitlines():
                            line_str = line.strip()
                            # Match run: commands involving test runners
                            if line_str.startswith("run:"):
                                cmd = line_str[4:].strip()
                                if any(tok in cmd.lower() for tok in ("pytest", "unittest", "test", "npm test", "vitest", "jest", "cargo test", "go test", "mvn test")):
                                    ci_commands.append({
                                        "source": rel_path,
                                        "command": cmd,
                                        "type": "github_actions",
                                    })
            except Exception:
                pass

        # 2. Inspect Makefile for test targets
        makefile = root_dir / "Makefile"
        if makefile.is_file():
            try:
                content = makefile.read_text(encoding="utf-8", errors="replace")
                in_test_target = False
                for line in content.splitlines():
                    if re.match(r"^(test|tests|check):\s*", line):
                        in_test_target = True
                        continue
                    if in_test_target:
                        if line.startswith("\t") or line.startswith("  "):
                            cmd = line.strip()
                            if cmd and not cmd.startswith("#"):
                                ci_commands.append({
                                    "source": "Makefile",
                                    "command": cmd,
                                    "type": "makefile",
                                })
                                break
                        else:
                            in_test_target = False
            except Exception:
                pass

        # 3. Inspect package.json for test scripts
        pkg_file = root_dir / "package.json"
        if pkg_file.is_file():
            try:
                pkg_data = json.loads(pkg_file.read_text(encoding="utf-8", errors="replace"))
                scripts = pkg_data.get("scripts", {})
                for script_key in ("test:ci", "test:unit", "test"):
                    if script_key in scripts and "no test specified" not in scripts[script_key].lower():
                        ci_commands.append({
                            "source": "package.json",
                            "command": f"npm run {script_key}" if script_key != "test" else "npm test",
                            "type": "npm_scripts",
                        })
                        break
            except Exception:
                pass

        return ci_commands

    @classmethod
    def _detect_test_framework(cls, root_dir: Path, test_files: List[Path], ci_cmd: Optional[str] = None) -> str:
        """Determines the dominant test framework from files, imports, and CI commands."""
        # 1. Check CI command if explicit
        if ci_cmd:
            if "pytest" in ci_cmd:
                return "pytest"
            if "vitest" in ci_cmd:
                return "vitest"
            if "jest" in ci_cmd:
                return "jest"
            if "cargo test" in ci_cmd:
                return "cargo test"
            if "go test" in ci_cmd:
                return "go test"

        # 2. Check conftest.py or pytest.ini
        if (root_dir / "conftest.py").is_file() or (root_dir / "pytest.ini").is_file():
            return "pytest"

        # 3. Inspect Python test file contents
        py_files = [f for f in test_files if f.suffix == ".py"]
        if py_files:
            pytest_score = 0
            unittest_score = 0
            for pf in py_files[:10]:
                try:
                    txt = pf.read_text(encoding="utf-8", errors="replace")
                    if "pytest" in txt or "@pytest." in txt:
                        pytest_score += 2
                    if "unittest.TestCase" in txt or "self.assert" in txt:
                        unittest_score += 2
                    if "def test_" in txt and "TestCase" not in txt:
                        pytest_score += 1
                except Exception:
                    pass
            if pytest_score > unittest_score:
                return "pytest"
            if unittest_score > 0:
                return "unittest"
            return "pytest"

        # 4. Inspect JS/TS test file contents
        js_ts_files = [f for f in test_files if f.suffix in (".ts", ".tsx", ".js", ".jsx")]
        if js_ts_files:
            for jf in js_ts_files[:5]:
                try:
                    txt = jf.read_text(encoding="utf-8", errors="replace")
                    if "vitest" in txt or "vi.fn" in txt:
                        return "vitest"
                    if "jest" in txt or "jest.fn" in txt:
                        return "jest"
                except Exception:
                    pass
            # Check package.json dependencies
            pkg_file = root_dir / "package.json"
            if pkg_file.is_file():
                try:
                    data = json.loads(pkg_file.read_text(encoding="utf-8", errors="replace"))
                    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                    if "vitest" in deps:
                        return "vitest"
                    if "jest" in deps:
                        return "jest"
                except Exception:
                    pass
            return "jest"

        # 5. Go
        if any(f.suffix == ".go" for f in test_files):
            return "go test"

        # 6. Rust
        if any(f.suffix == ".rs" for f in test_files):
            return "cargo test"

        # 7. Java
        if any(f.suffix == ".java" for f in test_files):
            return "junit"

        return "unknown"

    @classmethod
    def _extract_fixtures(cls, root_dir: Path, test_files: List[Path]) -> List[Dict[str, Any]]:
        """Extracts declared fixtures and setup helpers from conftest.py and setup files."""
        fixtures: List[Dict[str, Any]] = []

        # 1. Python conftest.py files
        conftest_files = [f for f in test_files if f.name == "conftest.py"]
        # Also check root or tests/ conftest.py
        for candidate in [root_dir / "conftest.py", root_dir / "tests" / "conftest.py"]:
            if candidate.is_file() and candidate not in conftest_files:
                conftest_files.append(candidate)

        for cf in conftest_files:
            rel_path = str(cf.relative_to(root_dir)).replace("\\", "/")
            try:
                tree = ast.parse(cf.read_text(encoding="utf-8", errors="replace"), filename=str(cf))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # Check decorators for @pytest.fixture
                        is_fixture = False
                        scope = "function"
                        for dec in node.decorator_list:
                            dec_name = ""
                            if isinstance(dec, ast.Name):
                                dec_name = dec.id
                            elif isinstance(dec, ast.Attribute):
                                dec_name = dec.attr
                            elif isinstance(dec, ast.Call):
                                if isinstance(dec.func, ast.Name):
                                    dec_name = dec.func.id
                                elif isinstance(dec.func, ast.Attribute):
                                    dec_name = dec.func.attr
                                # check scope argument
                                for kw in dec.keywords:
                                    if kw.arg == "scope" and isinstance(kw.value, ast.Constant):
                                        scope = str(kw.value.value)

                            if dec_name in ("fixture", "pytest_fixture"):
                                is_fixture = True

                        if is_fixture:
                            doc = ast.get_docstring(node) or ""
                            fixtures.append({
                                "name": node.name,
                                "scope": scope,
                                "source_file": rel_path,
                                "docstring": doc,
                                "kind": "pytest_fixture",
                            })
            except Exception:
                pass

        # 2. JS / TS setup files (setupTests.ts, etc.)
        for setup_name in ("setupTests.ts", "setupTests.js", "jest.setup.js", "vitest.setup.ts"):
            setup_file = root_dir / setup_name
            if not setup_file.is_file():
                setup_file = root_dir / "src" / setup_name
            if setup_file.is_file():
                rel_path = str(setup_file.relative_to(root_dir)).replace("\\", "/")
                fixtures.append({
                    "name": "Global Test Setup",
                    "scope": "suite",
                    "source_file": rel_path,
                    "docstring": "Pre-configured environment, mocks, and DOM extensions.",
                    "kind": "js_setup",
                })

        return fixtures

    @classmethod
    def _extract_mock_patterns(cls, test_files: List[Path]) -> List[Dict[str, Any]]:
        """Identifies mocking patterns and libraries used in test files."""
        mocks: Dict[str, Dict[str, Any]] = {}

        for tf in test_files[:15]:
            try:
                content = tf.read_text(encoding="utf-8", errors="replace")
                # Python unittest.mock
                if "from unittest.mock import" in content or "import unittest.mock" in content:
                    if "unittest.mock" not in mocks:
                        mocks["unittest.mock"] = {
                            "library": "unittest.mock",
                            "pattern": "Standard Python mock library (patch, Mock, MagicMock)",
                            "examples": ["from unittest.mock import patch, MagicMock", "@patch('...')"],
                        }
                # pytest-mock (mocker fixture)
                if "mocker" in content and "def test_" in content:
                    if "pytest-mock" not in mocks:
                        mocks["pytest-mock"] = {
                            "library": "pytest-mock",
                            "pattern": "mocker fixture injection (mocker.patch, mocker.spy)",
                            "examples": ["def test_foo(mocker):", "mocker.patch('...')"],
                        }
                # responses (HTTP mocking)
                if "responses" in content and ("@responses.activate" in content or "responses.add" in content):
                    if "responses" not in mocks:
                        mocks["responses"] = {
                            "library": "responses",
                            "pattern": "HTTP mock library for requests",
                            "examples": ["@responses.activate", "responses.add(...)"],
                        }
                # Vitest vi mocks
                if "vi.fn(" in content or "vi.mock(" in content or "vi.spyOn(" in content:
                    if "vitest_vi" not in mocks:
                        mocks["vitest_vi"] = {
                            "library": "vitest",
                            "pattern": "Native Vitest mocking via `vi` object",
                            "examples": ["vi.fn()", "vi.mock('...')", "vi.spyOn(...)"],
                        }
                # Jest mocks
                if "jest.fn(" in content or "jest.mock(" in content or "jest.spyOn(" in content:
                    if "jest_mock" not in mocks:
                        mocks["jest_mock"] = {
                            "library": "jest",
                            "pattern": "Native Jest mocking via `jest` object",
                            "examples": ["jest.fn()", "jest.mock('...')", "jest.spyOn(...)"],
                        }
            except Exception:
                pass

        return list(mocks.values())

    @classmethod
    def _extract_conventions(cls, test_files: List[Path]) -> Dict[str, Any]:
        """Extracts structural and assertion conventions across discovered test files."""
        if not test_files:
            return {}

        function_based_count = 0
        class_based_count = 0
        bdd_style_count = 0
        assert_stmt_count = 0
        self_assert_count = 0
        expect_count = 0
        async_count = 0
        naming_pattern = "test_*.py"

        for tf in test_files[:20]:
            name = tf.name
            if name.endswith(".py"):
                if name.startswith("test_"):
                    naming_pattern = "test_*.py"
                elif name.endswith("_test.py"):
                    naming_pattern = "*_test.py"
            elif name.endswith((".ts", ".js")):
                if ".test." in name:
                    naming_pattern = "*.test.ts"
                elif ".spec." in name:
                    naming_pattern = "*.spec.ts"

            try:
                txt = tf.read_text(encoding="utf-8", errors="replace")
                # Count styles
                if "class Test" in txt or "(unittest.TestCase)" in txt:
                    class_based_count += 1
                if re.search(r"def test_\w+\(", txt):
                    function_based_count += 1
                if "describe(" in txt or "it(" in txt:
                    bdd_style_count += 1

                # Count assertion styles
                if re.search(r"\bassert\s+", txt):
                    assert_stmt_count += 1
                if "self.assert" in txt:
                    self_assert_count += 1
                if "expect(" in txt:
                    expect_count += 1

                # Async
                if "async def test_" in txt or "@pytest.mark.asyncio" in txt or "async () =>" in txt:
                    async_count += 1
            except Exception:
                pass

        test_style = "function_based (def test_...)"
        if bdd_style_count > function_based_count and bdd_style_count > class_based_count:
            test_style = "bdd_style (describe / it)"
        elif class_based_count > function_based_count:
            test_style = "class_based (class Test... / unittest.TestCase)"

        assertion_style = "assert statement (`assert actual == expected`)"
        if self_assert_count > assert_stmt_count:
            assertion_style = "unittest assertion methods (`self.assertEqual(...)`)"
        elif expect_count > assert_stmt_count:
            assertion_style = "expect assertion (`expect(...).toBe(...)`)"

        return {
            "test_style": test_style,
            "assertion_style": assertion_style,
            "naming_convention": naming_pattern,
            "async_test_support": async_count > 0,
        }

    @classmethod
    def _extract_sample_tests(cls, root_dir: Path, test_files: List[Path], max_samples: int = 2) -> List[Dict[str, str]]:
        """Selects 1-2 representative sibling test files as few-shot style examples."""
        samples: List[Dict[str, str]] = []
        # Filter out conftest.py and setup files from samples
        candidates = [f for f in test_files if f.name not in ("conftest.py", "setupTests.ts", "setupTests.js")]
        if not candidates:
            return samples

        # Sort candidates by file size to pick concise, representative tests
        candidates.sort(key=lambda f: f.stat().st_size if f.is_file() else 999999)

        for cand in candidates[:max_samples]:
            try:
                rel_path = str(cand.relative_to(root_dir)).replace("\\", "/")
                content = cand.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()
                # If file is longer than 50 lines, truncate cleanly
                if len(lines) > 50:
                    truncated = "\n".join(lines[:50]) + "\n... [truncated for brevity]"
                else:
                    truncated = content
                samples.append({
                    "filepath": rel_path,
                    "content": truncated,
                })
            except Exception:
                pass
        return samples
