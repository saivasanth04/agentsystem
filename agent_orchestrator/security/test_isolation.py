"""
Test Execution & Workspace Isolation Engine.
Provides deterministic isolation between untrusted test execution, test code,
the authoritative workspace repository, and the host environment.
Prevents generated tests from mutating, corrupting, or polluting project code.
"""
from dataclasses import dataclass, field
from enum import Enum
import fnmatch
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from .sandbox import BaseExecutionSandbox, SandboxResult


class TestIsolationMode(str, Enum):
    """Modes of test execution workspace isolation."""
    __test__ = False
    AUTO = "AUTO"
    SHADOW_WORKSPACE = "SHADOW_WORKSPACE"      # Ephemeral clone of workspace, discarded after run
    SNAPSHOT_ROLLBACK = "SNAPSHOT_ROLLBACK"    # Snapshot before test, detect drift, cleanup & rollback
    READ_ONLY_DOCKER = "READ_ONLY_DOCKER"      # Docker container with read-only workspace volume


# Default file and directory patterns to ignore when cloning shadow workspace
DEFAULT_IGNORE_PATTERNS = [
    ".git", ".hg", ".svn",
    "node_modules", "venv", ".venv", "env", ".env_dir",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", "dist", "build", ".next", ".nuxt",
]

# Patterns of temporary pollution files created by test runners that should be cleaned up
DEFAULT_POLLUTION_PATTERNS = [
    "*.pyc", "*.pyo", "*.pyd",
    ".coverage", ".coverage.*", "coverage.xml", "htmlcov",
    ".pytest_cache", "__pycache__",
    "*.log", "test.log", "tests.log",
    "test.db", "test.sqlite", "test.sqlite3", "test_*.db",
    ".nyc_output", "coverage",
    ".turbo", ".cache",
]


@dataclass
class TestIsolationPolicy:
    """Configuration governing test execution isolation."""
    __test__ = False
    mode: TestIsolationMode = TestIsolationMode.AUTO
    ignore_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))
    pollution_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_POLLUTION_PATTERNS))
    cleanup_test_artifacts: bool = True
    restore_modified_files: bool = True
    allowed_mutation_paths: List[str] = field(default_factory=lambda: ["tmp", "temp", ".tmp", "test_scratch"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value if isinstance(self.mode, TestIsolationMode) else str(self.mode),
            "ignore_patterns": self.ignore_patterns,
            "pollution_patterns": self.pollution_patterns,
            "cleanup_test_artifacts": self.cleanup_test_artifacts,
            "restore_modified_files": self.restore_modified_files,
            "allowed_mutation_paths": self.allowed_mutation_paths,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "TestIsolationPolicy":
        if not data:
            return cls()
        raw_mode = data.get("mode", "AUTO")
        try:
            mode = TestIsolationMode(raw_mode)
        except ValueError:
            mode = TestIsolationMode.AUTO
        return cls(
            mode=mode,
            ignore_patterns=data.get("ignore_patterns", list(DEFAULT_IGNORE_PATTERNS)),
            pollution_patterns=data.get("pollution_patterns", list(DEFAULT_POLLUTION_PATTERNS)),
            cleanup_test_artifacts=bool(data.get("cleanup_test_artifacts", True)),
            restore_modified_files=bool(data.get("restore_modified_files", True)),
            allowed_mutation_paths=data.get("allowed_mutation_paths", ["tmp", "temp", ".tmp", "test_scratch"]),
        )


@dataclass
class FileMutationRecord:
    """Record of a file created, modified, or deleted by a test run."""
    path: str
    mutation_type: str  # "CREATED" | "MODIFIED" | "DELETED"
    restored: bool = False
    cleaned_up: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "mutation_type": self.mutation_type,
            "restored": self.restored,
            "cleaned_up": self.cleaned_up,
        }


@dataclass
class IsolatedExecutionResult:
    """Result of running a test or command under test isolation."""
    success: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    command: Union[str, List[str]] = ""
    mode_used: str = "SHADOW_WORKSPACE"
    mutations_detected: List[FileMutationRecord] = field(default_factory=list)
    restored_files: List[str] = field(default_factory=list)
    pollution_cleaned: List[str] = field(default_factory=list)

    @property
    def mutations(self) -> List[FileMutationRecord]:
        return self.mutations_detected

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "command": self.command,
            "mode_used": self.mode_used,
            "mutations_detected": [m.to_dict() for m in self.mutations_detected],
            "restored_files": self.restored_files,
            "pollution_cleaned": self.pollution_cleaned,
        }


class TestIsolationEngine:
    """
    Orchestrates isolated test and build execution.
    Protects the authoritative workspace from destructive test code and untracked file pollution.
    """
    __test__ = False

    @staticmethod
    def _compute_sha256(filepath: Path) -> Optional[str]:
        """Computes SHA-256 hash of a file."""
        if not filepath.is_file():
            return None
        try:
            h = hashlib.sha256()
            with open(filepath, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest()
        except (OSError, PermissionError):
            return None

    @classmethod
    def _take_snapshot(cls, root_dir: Path, ignore_patterns: List[str]) -> Tuple[Dict[str, str], Dict[str, bytes]]:
        """Takes snapshot of files, relative paths, hashes, and small content backups."""
        hashes: Dict[str, str] = {}
        backups: Dict[str, bytes] = {}

        if not root_dir.exists():
            return hashes, backups

        for root, dirs, files in os.walk(root_dir):
            rel_root = os.path.relpath(root, root_dir).replace("\\", "/")
            if rel_root == ".":
                rel_root = ""

            # Prune ignored directories
            dirs[:] = [
                d for d in dirs
                if not any(d == pat or d.startswith(pat) for pat in ignore_patterns)
            ]

            for fname in files:
                if any(fname == pat for pat in ignore_patterns):
                    continue
                full_p = Path(root) / fname
                rel_p = f"{rel_root}/{fname}".strip("/") if rel_root else fname
                chash = cls._compute_sha256(full_p)
                if chash:
                    hashes[rel_p] = chash
                    # If file is under 5MB, store backup in memory for fast rollback
                    try:
                        if full_p.stat().st_size <= 5 * 1024 * 1024:
                            with open(full_p, "rb") as f:
                                backups[rel_p] = f.read()
                    except (OSError, PermissionError):
                        pass

        return hashes, backups

    @classmethod
    def execute_isolated(
        cls,
        command: Union[str, List[str]],
        workspace_dir: Union[str, Path],
        sandbox: Optional[BaseExecutionSandbox] = None,
        timeout: Optional[int] = None,
        extra_env: Optional[Dict[str, str]] = None,
        policy: Optional[TestIsolationPolicy] = None,
        mode: Optional[Union[TestIsolationMode, str]] = None,
    ) -> IsolatedExecutionResult:
        """
        Executes a test command inside an isolated workspace context.
        Guarantees that the authoritative workspace is preserved without side-effect pollution or deletion.
        """
        root_dir = Path(workspace_dir).resolve()
        pol = policy or TestIsolationPolicy()
        if mode is not None:
            if isinstance(mode, str):
                try:
                    pol.mode = TestIsolationMode(mode)
                except ValueError:
                    pol.mode = TestIsolationMode.AUTO
            else:
                pol.mode = mode

        effective_mode = pol.mode
        if effective_mode == TestIsolationMode.AUTO:
            # Prefer SHADOW_WORKSPACE for local execution, or READ_ONLY_DOCKER if Docker is active
            if sandbox and hasattr(sandbox, "_docker_available") and sandbox._docker_available:
                effective_mode = TestIsolationMode.READ_ONLY_DOCKER
            else:
                effective_mode = TestIsolationMode.SHADOW_WORKSPACE

        if effective_mode == TestIsolationMode.READ_ONLY_DOCKER:
            return cls._execute_docker_read_only(
                command=command,
                root_dir=root_dir,
                sandbox=sandbox,
                timeout=timeout,
                extra_env=extra_env,
                policy=pol,
            )
        elif effective_mode == TestIsolationMode.SHADOW_WORKSPACE:
            return cls._execute_shadow_workspace(
                command=command,
                root_dir=root_dir,
                sandbox=sandbox,
                timeout=timeout,
                extra_env=extra_env,
                policy=pol,
            )
        else:
            return cls._execute_snapshot_rollback(
                command=command,
                root_dir=root_dir,
                sandbox=sandbox,
                timeout=timeout,
                extra_env=extra_env,
                policy=pol,
            )

    @classmethod
    def _execute_shadow_workspace(
        cls,
        command: Union[str, List[str]],
        root_dir: Path,
        sandbox: Optional[BaseExecutionSandbox],
        timeout: Optional[int],
        extra_env: Optional[Dict[str, str]],
        policy: TestIsolationPolicy,
    ) -> IsolatedExecutionResult:
        """Clones workspace into a temporary isolated shadow directory, runs tests there, and discards."""
        start_time = time.time()
        temp_dir = tempfile.mkdtemp(prefix="agent_test_shadow_")
        shadow_path = Path(temp_dir).resolve()

        mutations: List[FileMutationRecord] = []
        restored: List[str] = []
        cleaned: List[str] = []

        try:
            # 1. Clone workspace files into shadow directory (ignoring heavy virtualenvs / caches)
            cls._copy_workspace_tree(root_dir, shadow_path, policy.ignore_patterns)

            # 2. Take initial snapshot of shadow dir to detect what the test changed
            pre_hashes, _ = cls._take_snapshot(shadow_path, policy.ignore_patterns)

            # 3. Execute command in the shadow directory
            if sandbox:
                res: SandboxResult = sandbox.run_command(command, timeout=timeout, cwd=shadow_path, extra_env=extra_env)
            else:
                from .sandbox import LocalProcessSandbox
                sb = LocalProcessSandbox(shadow_path)
                res = sb.run_command(command, timeout=timeout, cwd=shadow_path, extra_env=extra_env)

            duration = round(time.time() - start_time, 4)

            # 4. Compare post-test snapshot in shadow dir to detect mutations
            post_hashes, _ = cls._take_snapshot(shadow_path, policy.ignore_patterns)

            for p, h in pre_hashes.items():
                if p not in post_hashes:
                    mutations.append(FileMutationRecord(path=p, mutation_type="DELETED", restored=True))
                    restored.append(p)
                elif post_hashes[p] != h:
                    mutations.append(FileMutationRecord(path=p, mutation_type="MODIFIED", restored=True))
                    restored.append(p)

            for p in post_hashes:
                if p not in pre_hashes:
                    mutations.append(FileMutationRecord(path=p, mutation_type="CREATED", cleaned_up=True))
                    cleaned.append(p)

            return IsolatedExecutionResult(
                success=res.success,
                exit_code=res.exit_code,
                stdout=res.stdout,
                stderr=res.stderr,
                duration_seconds=duration,
                command=command,
                mode_used=TestIsolationMode.SHADOW_WORKSPACE.value,
                mutations_detected=mutations,
                restored_files=restored,
                pollution_cleaned=cleaned,
            )
        finally:
            # Clean up entire shadow scratchpad - authoritative workspace was never touched
            shutil.rmtree(temp_dir, ignore_errors=True)

    @classmethod
    def _execute_snapshot_rollback(
        cls,
        command: Union[str, List[str]],
        root_dir: Path,
        sandbox: Optional[BaseExecutionSandbox],
        timeout: Optional[int],
        extra_env: Optional[Dict[str, str]],
        policy: TestIsolationPolicy,
    ) -> IsolatedExecutionResult:
        """Executes in-place with pre-flight snapshot, detecting drift, cleaning pollution, and restoring files."""
        start_time = time.time()
        pre_hashes, pre_backups = cls._take_snapshot(root_dir, policy.ignore_patterns)

        # Execute command in root_dir
        if sandbox:
            res: SandboxResult = sandbox.run_command(command, timeout=timeout, cwd=root_dir, extra_env=extra_env)
        else:
            from .sandbox import LocalProcessSandbox
            sb = LocalProcessSandbox(root_dir)
            res = sb.run_command(command, timeout=timeout, cwd=root_dir, extra_env=extra_env)

        duration = round(time.time() - start_time, 4)
        post_hashes, _ = cls._take_snapshot(root_dir, policy.ignore_patterns)

        mutations: List[FileMutationRecord] = []
        restored: List[str] = []
        cleaned: List[str] = []

        # 1. Detect and restore deleted or modified files
        for p, pre_h in pre_hashes.items():
            full_p = root_dir / p
            if p not in post_hashes:
                # File was deleted by test
                rec = FileMutationRecord(path=p, mutation_type="DELETED")
                if policy.restore_modified_files and p in pre_backups:
                    try:
                        full_p.parent.mkdir(parents=True, exist_ok=True)
                        with open(full_p, "wb") as f:
                            f.write(pre_backups[p])
                        rec.restored = True
                        restored.append(p)
                    except (OSError, PermissionError):
                        pass
                mutations.append(rec)
            elif post_hashes[p] != pre_h:
                # File was modified by test
                rec = FileMutationRecord(path=p, mutation_type="MODIFIED")
                if policy.restore_modified_files and p in pre_backups:
                    try:
                        with open(full_p, "wb") as f:
                            f.write(pre_backups[p])
                        rec.restored = True
                        restored.append(p)
                    except (OSError, PermissionError):
                        pass
                mutations.append(rec)

        # 2. Detect and clean up newly created pollution files
        for p in post_hashes:
            if p not in pre_hashes:
                rec = FileMutationRecord(path=p, mutation_type="CREATED")
                full_p = root_dir / p
                fname = full_p.name
                is_pollution = any(
                    fnmatch.fnmatch(fname, pat) or fnmatch.fnmatch(p, pat)
                    for pat in policy.pollution_patterns
                ) or any(
                    part.startswith(".") and "cache" in part
                    for part in p.split("/")
                ) or not any(
                    p == allowed or p.startswith(allowed + "/")
                    for allowed in policy.allowed_mutation_paths
                )

                if policy.cleanup_test_artifacts and is_pollution:
                    try:
                        if full_p.is_file():
                            full_p.unlink(missing_ok=True)
                        elif full_p.is_dir():
                            shutil.rmtree(full_p, ignore_errors=True)
                        rec.cleaned_up = True
                        cleaned.append(p)
                    except (OSError, PermissionError):
                        pass
                mutations.append(rec)

        return IsolatedExecutionResult(
            success=res.success,
            exit_code=res.exit_code,
            stdout=res.stdout,
            stderr=res.stderr,
            duration_seconds=duration,
            command=command,
            mode_used=TestIsolationMode.SNAPSHOT_ROLLBACK.value,
            mutations_detected=mutations,
            restored_files=restored,
            pollution_cleaned=cleaned,
        )

    @classmethod
    def _execute_docker_read_only(
        cls,
        command: Union[str, List[str]],
        root_dir: Path,
        sandbox: Optional[BaseExecutionSandbox],
        timeout: Optional[int],
        extra_env: Optional[Dict[str, str]],
        policy: TestIsolationPolicy,
    ) -> IsolatedExecutionResult:
        """Executes in Docker with read-only workspace mount and tmpfs write layer."""
        start_time = time.time()
        # If sandbox is DockerContainerSandbox, run with read_only flag
        if sandbox and hasattr(sandbox, "_docker_available") and sandbox._docker_available:
            res = sandbox.run_command(command, timeout=timeout, cwd=root_dir, extra_env=extra_env, read_only=True)
            duration = round(time.time() - start_time, 4)
            return IsolatedExecutionResult(
                success=res.success,
                exit_code=res.exit_code,
                stdout=res.stdout,
                stderr=res.stderr,
                duration_seconds=duration,
                command=command,
                mode_used=TestIsolationMode.READ_ONLY_DOCKER.value,
            )
        # Fallback to shadow workspace
        return cls._execute_shadow_workspace(command, root_dir, sandbox, timeout, extra_env, policy)

    @classmethod
    def _copy_workspace_tree(cls, src_dir: Path, dst_dir: Path, ignore_patterns: List[str]):
        """Recursively copies code files while ignoring heavy build caches and virtual environments."""
        dst_dir.mkdir(parents=True, exist_ok=True)
        for root, dirs, files in os.walk(src_dir):
            rel_root = os.path.relpath(root, src_dir).replace("\\", "/")
            if rel_root == ".":
                rel_root = ""

            # Filter out ignored directories
            dirs[:] = [
                d for d in dirs
                if not any(d == pat or d.startswith(pat) for pat in ignore_patterns)
            ]

            target_sub = dst_dir / rel_root if rel_root else dst_dir
            target_sub.mkdir(parents=True, exist_ok=True)

            for fname in files:
                if any(fname == pat for pat in ignore_patterns):
                    continue
                src_file = Path(root) / fname
                dst_file = target_sub / fname
                try:
                    shutil.copy2(src_file, dst_file)
                except (OSError, PermissionError):
                    pass
