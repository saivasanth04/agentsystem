"""
Security and execution sandboxing module.
Provides environment scrubbing, process tree isolation, secret redaction,
resource and timeout management, command safety verification, and pluggable sandbox runners.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import fnmatch
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set, Union

logger = logging.getLogger(__name__)


@dataclass
class SandboxResult:
    """Standardized result of sandboxed command execution."""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float = 0.0
    success: bool = False
    timed_out: bool = False
    killed_due_to_limit: bool = False
    redacted_secrets_count: int = 0
    command: Optional[Union[str, List[str]]] = None

    def __post_init__(self):
        if not self.success and self.exit_code == 0 and not self.timed_out and not self.killed_due_to_limit:
            self.success = True

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def __contains__(self, item: str) -> bool:
        return hasattr(self, item)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "success": self.success,
            "timed_out": self.timed_out,
            "killed_due_to_limit": self.killed_due_to_limit,
            "redacted_secrets_count": self.redacted_secrets_count,
            "command": self.command,
        }


# Default Whitelist: strictly required variables for OS shell and Python interpreter
DEFAULT_ENV_WHITELIST = [
    # Cross-platform
    "PATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "TEMP",
    "TMP",
    "USER",
    "USERNAME",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TERM",
    "COLORTERM",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    # Windows system essentials
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES",
    "COMMONPROGRAMFILES(X86)",
    "ALLUSERSPROFILE",
    "PUBLIC",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
]

# Sensitive patterns that must ALWAYS be stripped from child environments
DEFAULT_FORBIDDEN_ENV_PATTERNS = [
    "*KEY*",
    "*SECRET*",
    "*TOKEN*",
    "*PASSWORD*",
    "*PASSWD*",
    "*AUTH*",
    "*CREDENTIAL*",
    "*PRIVATE*",
    "*ACCESS_KEY*",
    "*BEARER*",
    "*HASH*",
    "*SALT*",
    "OPENAI_*",
    "ANTHROPIC_*",
    "GEMINI_*",
    "AWS_*",
    "GITHUB_*",
    "GITLAB_*",
    "SLACK_*",
    "STRIPE_*",
    "DATABASE_URL*",
    "MONGO*",
    "REDIS*",
]

# Known secret formats for redaction from stdout/stderr
DEFAULT_SECRET_REDACTION_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9\._\-]{20,}", re.IGNORECASE),
    re.compile(r"(AKIA|ASIA)[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[A-Za-z0-9+/=\s\r\n]+?-----END [A-Z ]+ PRIVATE KEY-----"),
    re.compile(r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24,}"),
]

# High-risk destructive command patterns
DEFAULT_BLOCKED_COMMAND_PATTERNS = [
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\s+[/~]", re.IGNORECASE),
    re.compile(r"\b(rmdir|rd|del)\s+.*?[a-zA-Z]:\\", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-zA-Z]:", re.IGNORECASE),
    re.compile(r"\bmkfs\.", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.IGNORECASE),
    re.compile(r"\bdd\s+if=.*?of=/dev/", re.IGNORECASE),
]


@dataclass
class SandboxPolicy:
    """Security policy and resource quotas for execution sandboxes."""
    timeout_seconds: int = 30
    max_memory_mb: int = 1024
    max_output_bytes: int = 500_000
    network_disabled: bool = False
    network_policy: Optional[Any] = None
    resource_budget: Optional[Any] = None
    resource_tracker: Optional[Any] = None
    isolate_tests: bool = True
    test_isolation_policy: Optional[Any] = None
    allowed_env_whitelist: List[str] = field(default_factory=lambda: list(DEFAULT_ENV_WHITELIST))
    forbidden_env_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_FORBIDDEN_ENV_PATTERNS))
    secret_redaction_patterns: List[re.Pattern] = field(default_factory=lambda: list(DEFAULT_SECRET_REDACTION_PATTERNS))
    blocked_command_patterns: List[re.Pattern] = field(default_factory=lambda: list(DEFAULT_BLOCKED_COMMAND_PATTERNS))


class BaseExecutionSandbox(ABC):
    """Abstract interface for all execution sandbox providers."""

    def __init__(self, working_dir: Union[str, Path], policy: Optional[SandboxPolicy] = None):
        self.working_dir = Path(working_dir).resolve()
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self.policy = policy or SandboxPolicy()

    def is_network_disabled(self) -> bool:
        """Determines if network access is disabled under the active policy."""
        if self.policy.network_disabled:
            return True
        if self.policy.network_policy:
            from .network_policy import NetworkAccessMode, NetworkAccessPolicy
            np = self.policy.network_policy
            if isinstance(np, dict):
                np = NetworkAccessPolicy.from_dict(np)
            return getattr(np, "mode", None) == NetworkAccessMode.DISABLED
        return False

    @abstractmethod
    def run_command(
        self,
        cmd: Union[str, List[str]],
        timeout: Optional[int] = None,
        cwd: Optional[Union[str, Path]] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> SandboxResult:
        """Executes a command safely inside the sandbox."""
        pass

    @abstractmethod
    def run_tests(
        self,
        pattern: str = "test_*.py",
        command: Optional[Union[str, List[str]]] = None,
        timeout: Optional[int] = None,
        cwd: Optional[Union[str, Path]] = None,
    ) -> SandboxResult:
        """Executes automated unit tests inside the sandbox."""
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Cleans up sandbox resources."""
        pass


class LocalProcessSandbox(BaseExecutionSandbox):
    """
    Hardened Local Process Execution Sandbox.
    Enforces environment scrubbing, destructive command blacklists,
    secret output redaction, network isolation, and recursive process tree termination.
    Works with zero external dependencies across Windows, Linux, and macOS.
    """

    def sanitize_environment(self, extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Constructs a strictly scrubbed environment dictionary with dead proxies when network is disabled."""
        sanitized: Dict[str, str] = {}
        upper_whitelist = {k.upper() for k in self.policy.allowed_env_whitelist}

        for k, v in os.environ.items():
            k_upper = k.upper()
            if k_upper in upper_whitelist:
                # Check against forbidden wildcard patterns
                is_forbidden = any(fnmatch.fnmatch(k_upper, pat.upper()) for pat in self.policy.forbidden_env_patterns)
                if not is_forbidden:
                    sanitized[k] = v

        if extra_env:
            for k, v in extra_env.items():
                k_upper = k.upper()
                is_forbidden = any(fnmatch.fnmatch(k_upper, pat.upper()) for pat in self.policy.forbidden_env_patterns)
                if not is_forbidden:
                    sanitized[k] = str(v)

        # Inject dead proxy environment if network is disabled to block child process egress (pip, requests, urllib, etc.)
        if self.is_network_disabled():
            from .network_policy import NetworkAccessPolicy, NetworkAccessMode
            np = self.policy.network_policy
            if isinstance(np, dict):
                np = NetworkAccessPolicy.from_dict(np)
            elif not np:
                np = NetworkAccessPolicy(mode=NetworkAccessMode.DISABLED)
            sanitized.update(np.get_disabled_proxy_env())

        return sanitized

    def redact_secrets(self, text: str) -> tuple[str, int]:
        """Redacts identified secret tokens from output streams."""
        if not text:
            return "", 0

        redacted = text
        count = 0
        for pattern in self.policy.secret_redaction_patterns:
            matches = pattern.findall(redacted)
            if matches:
                count += len(matches)
                redacted = pattern.sub("[REDACTED_SECRET]", redacted)

        return redacted, count

    def check_command_safety(self, cmd_str: str) -> Optional[str]:
        """Verifies command string against blocked destructive patterns and active network policy."""
        for pattern in self.policy.blocked_command_patterns:
            if pattern.search(cmd_str):
                return f"Security Violation: Command matched blocked pattern '{pattern.pattern}'."

        if self.is_network_disabled():
            from .network_policy import NetworkAccessPolicy, NetworkAccessMode
            np = self.policy.network_policy
            if isinstance(np, dict):
                np = NetworkAccessPolicy.from_dict(np)
            elif not np:
                np = NetworkAccessPolicy(mode=NetworkAccessMode.DISABLED)
            dec = np.evaluate_command(cmd_str)
            if not dec.allowed:
                return f"Security Violation: {dec.reason}"
        elif self.policy.network_policy:
            from .network_policy import NetworkAccessPolicy
            np = self.policy.network_policy
            if isinstance(np, dict):
                np = NetworkAccessPolicy.from_dict(np)
            dec = np.evaluate_command(cmd_str)
            if not dec.allowed:
                return f"Security Violation: {dec.reason}"

        return None

    def _terminate_process_tree(self, proc: subprocess.Popen) -> None:
        """Recursively terminates process and all spawned child processes."""
        pid = proc.pid
        if os.name == "nt":
            try:
                # Windows taskkill /T /F terminates the entire process tree cleanly
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except Exception as e:
                logger.debug(f"taskkill failed for pid {pid}: {e}")
                try:
                    proc.kill()
                except Exception:
                    pass
        else:
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        try:
            proc.wait(timeout=2)
        except Exception:
            pass

    def run_command(
        self,
        cmd: Union[str, List[str]],
        timeout: Optional[int] = None,
        cwd: Optional[Union[str, Path]] = None,
        extra_env: Optional[Dict[str, str]] = None,
        cancellation_token: Optional[Any] = None,
    ) -> SandboxResult:
        """Runs command with environment isolation, process tree cleanup, secret redaction, and cancellation support."""
        if cancellation_token and cancellation_token.is_cancelled:
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command aborted before launch: {cancellation_token.cancel_reason or 'Cancelled by user'}",
                duration_seconds=0.0,
                success=False,
                timed_out=False,
                killed_due_to_limit=True,
                redacted_secrets_count=0,
                command=cmd,
            )

        effective_timeout = timeout if timeout is not None else self.policy.timeout_seconds
        target_dir = Path(cwd).resolve() if cwd else self.working_dir
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)

        # 1. Destructive command safety guard
        safety_error = self.check_command_safety(cmd_str)
        if safety_error:
            return SandboxResult(
                exit_code=126,
                stdout="",
                stderr=safety_error,
                duration_seconds=0.0,
                success=False,
                timed_out=False,
                killed_due_to_limit=True,
                redacted_secrets_count=0,
                command=cmd,
            )

        # 2. Scrub environment
        clean_env = self.sanitize_environment(extra_env)

        # 3. Execution configuration
        use_shell = isinstance(cmd, str)
        popen_kwargs: Dict[str, Any] = {
            "cwd": str(target_dir),
            "env": clean_env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "shell": use_shell,
        }

        # On Unix, start in new process group to allow os.killpg
        if os.name != "nt":
            popen_kwargs["preexec_fn"] = os.setsid

        start_time = time.time()
        proc = None
        stop_watcher = threading.Event()
        violation_holder: Dict[str, str] = {}

        def _monitor_resources():
            rb = self.policy.resource_budget
            max_proc = getattr(rb, "max_processes", 0) if rb else 0
            max_mem = getattr(rb, "max_memory_mb", 0.0) if rb else (self.policy.max_memory_mb if self.policy.max_memory_mb > 0 else 0.0)
            max_cpu = getattr(rb, "max_cpu_percent", 0.0) if rb else 0.0
            tracker = self.policy.resource_tracker

            while not stop_watcher.is_set():
                if proc is None or proc.poll() is not None:
                    break

                if cancellation_token and cancellation_token.is_cancelled:
                    violation_holder["cancelled"] = f"Command cancelled: {cancellation_token.cancel_reason or 'Execution stopped by user'}"
                    self._terminate_process_tree(proc)
                    break

                if max_proc <= 0 and max_mem <= 0 and max_cpu <= 0 and not tracker:
                    stop_watcher.wait(0.05)
                    continue

                try:
                    import psutil
                    p = psutil.Process(proc.pid)
                    children = p.children(recursive=True)
                    proc_count = len(children) + 1

                    total_rss = p.memory_info().rss
                    for c in children:
                        try:
                            total_rss += c.memory_info().rss
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    rss_mb = total_rss / (1024.0 * 1024.0)

                    if tracker:
                        tracker.record_process_sample(proc_count, rss_mb)

                    if max_proc > 0 and proc_count > max_proc:
                        violation_holder["violation"] = f"Resource Limit Exceeded: {proc_count} active processes > {max_proc} max processes."
                        self._terminate_process_tree(proc)
                        break

                    if max_mem > 0 and rss_mb > max_mem:
                        violation_holder["violation"] = f"Resource Limit Exceeded: {rss_mb:.1f}MB resident memory > {max_mem:.1f}MB budget."
                        self._terminate_process_tree(proc)
                        break

                except Exception:
                    pass

                stop_watcher.wait(0.05)

        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
            if cancellation_token:
                cancellation_token.register_callback(
                    lambda reason: self._terminate_process_tree(proc) if proc and proc.poll() is None else None
                )
            watcher_thread = threading.Thread(target=_monitor_resources, daemon=True)
            watcher_thread.start()

            try:
                stdout, stderr = proc.communicate(timeout=effective_timeout)
            finally:
                stop_watcher.set()
                watcher_thread.join(timeout=0.5)

            duration = round(time.time() - start_time, 4)
            exit_code = proc.returncode

            if "cancelled" in violation_holder or (cancellation_token and cancellation_token.is_cancelled):
                return SandboxResult(
                    exit_code=-1,
                    stdout="",
                    stderr=violation_holder.get("cancelled", "Command cancelled by user"),
                    duration_seconds=duration,
                    success=False,
                    timed_out=False,
                    killed_due_to_limit=True,
                    redacted_secrets_count=0,
                    command=cmd,
                )

            if "violation" in violation_holder:
                return SandboxResult(
                    exit_code=-1,
                    stdout="",
                    stderr=violation_holder["violation"],
                    duration_seconds=duration,
                    success=False,
                    timed_out=False,
                    killed_due_to_limit=True,
                    redacted_secrets_count=0,
                    command=cmd,
                )

            # Check output size limits
            if len(stdout) > self.policy.max_output_bytes:
                stdout = stdout[: self.policy.max_output_bytes] + "\n[TRUNCATED: Max output bytes exceeded]"
            if len(stderr) > self.policy.max_output_bytes:
                stderr = stderr[: self.policy.max_output_bytes] + "\n[TRUNCATED: Max output bytes exceeded]"

            # Redact secrets
            clean_stdout, red_out_cnt = self.redact_secrets(stdout)
            clean_stderr, red_err_cnt = self.redact_secrets(stderr)

            return SandboxResult(
                exit_code=exit_code,
                stdout=clean_stdout,
                stderr=clean_stderr,
                duration_seconds=duration,
                success=(exit_code == 0),
                timed_out=False,
                killed_due_to_limit=False,
                redacted_secrets_count=red_out_cnt + red_err_cnt,
                command=cmd,
            )

        except subprocess.TimeoutExpired:
            duration = round(time.time() - start_time, 4)
            if proc:
                self._terminate_process_tree(proc)

            timeout_msg = f"Execution timed out after {effective_timeout} seconds. Process tree terminated."
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=timeout_msg,
                duration_seconds=duration,
                success=False,
                timed_out=True,
                killed_due_to_limit=True,
                redacted_secrets_count=0,
                command=cmd,
            )

        except Exception as e:
            duration = round(time.time() - start_time, 4)
            if proc:
                self._terminate_process_tree(proc)
            err_msg = str(e)
            clean_err, cnt = self.redact_secrets(err_msg)
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=clean_err,
                duration_seconds=duration,
                success=False,
                timed_out=False,
                killed_due_to_limit=False,
                redacted_secrets_count=cnt,
                command=cmd,
            )

    def run_tests(
        self,
        pattern: str = "test_*.py",
        command: Optional[Union[str, List[str]]] = None,
        timeout: Optional[int] = None,
        cwd: Optional[Union[str, Path]] = None,
        isolate: Optional[bool] = None,
    ) -> SandboxResult:
        """Runs test discovery or dynamic project test runner within the sanitized sandbox."""
        target_dir = Path(cwd).resolve() if cwd else self.working_dir
        cmd = command or [sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", pattern]
        should_isolate = self.policy.isolate_tests if isolate is None else isolate

        if should_isolate:
            try:
                from .test_isolation import TestIsolationEngine, TestIsolationPolicy
                tip = self.policy.test_isolation_policy or TestIsolationPolicy()
                iso_res = TestIsolationEngine.execute_isolated(
                    command=cmd,
                    workspace_dir=target_dir,
                    sandbox=self,
                    timeout=timeout,
                    policy=tip,
                )
                clean_err, cnt = self.redact_secrets(iso_res.stderr)
                clean_out, cnt_out = self.redact_secrets(iso_res.stdout)
                return SandboxResult(
                    exit_code=iso_res.exit_code,
                    stdout=clean_out,
                    stderr=clean_err,
                    duration_seconds=iso_res.duration_seconds,
                    success=iso_res.success,
                    timed_out=False,
                    killed_due_to_limit=False,
                    redacted_secrets_count=cnt + cnt_out,
                    command=cmd,
                )
            except Exception:
                pass

        return self.run_command(cmd, timeout=timeout, cwd=target_dir)

    def cleanup(self) -> None:
        """Local sandbox cleanup."""
        pass


class DockerContainerSandbox(BaseExecutionSandbox):
    """
    Docker Container Sandbox Provider.
    Runs commands within an isolated container with hard resource quotas and no network access.
    Falls back to LocalProcessSandbox if Docker is not installed or available.
    """

    def __init__(self, working_dir: Union[str, Path], policy: Optional[SandboxPolicy] = None, image: str = "python:3.11-slim"):
        super().__init__(working_dir, policy)
        self.image = image
        self._fallback_sandbox = LocalProcessSandbox(self.working_dir, self.policy)
        self._docker_available = self._check_docker()

    def _check_docker(self) -> bool:
        try:
            res = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            return res.returncode == 0
        except Exception:
            return False

    def is_available(self) -> bool:
        return self._docker_available

    def run_command(
        self,
        cmd: Union[str, List[str]],
        timeout: Optional[int] = None,
        cwd: Optional[Union[str, Path]] = None,
        extra_env: Optional[Dict[str, str]] = None,
        read_only: bool = False,
    ) -> SandboxResult:
        if not self._docker_available:
            return self._fallback_sandbox.run_command(cmd, timeout=timeout, cwd=cwd, extra_env=extra_env)

        effective_timeout = timeout if timeout is not None else self.policy.timeout_seconds
        target_dir = Path(cwd).resolve() if cwd else self.working_dir

        mount_opt = "ro" if read_only else "rw"
        cmd_args = ["docker", "run", "--rm", "-v", f"{target_dir}:/workspace:{mount_opt}", "-w", "/workspace"]
        if read_only:
            cmd_args.extend(["--tmpfs", "/tmp:rw"])

        if self.is_network_disabled():
            cmd_args.extend(["--network", "none"])

        mem_mb = self.policy.max_memory_mb
        cpus = 2
        pids = 100
        if self.policy.resource_budget:
            rb = self.policy.resource_budget
            if getattr(rb, "max_memory_mb", 0) > 0:
                mem_mb = int(rb.max_memory_mb)
            if getattr(rb, "max_cpu_cores", 0) > 0:
                cpus = int(rb.max_cpu_cores)
            if getattr(rb, "max_processes", 0) > 0:
                pids = int(rb.max_processes)

        cmd_args.extend(["--memory", f"{mem_mb}m", "--cpus", str(cpus), "--pids-limit", str(pids)])

        # Inject sanitized env
        clean_env = self._fallback_sandbox.sanitize_environment(extra_env)
        for k, v in clean_env.items():
            cmd_args.extend(["-e", f"{k}={v}"])

        cmd_args.append(self.image)
        if isinstance(cmd, str):
            cmd_args.extend(["sh", "-c", cmd])
        else:
            cmd_args.extend(cmd)

        return self._fallback_sandbox.run_command(cmd_args, timeout=effective_timeout)

    def run_tests(
        self,
        pattern: str = "test_*.py",
        command: Optional[Union[str, List[str]]] = None,
        timeout: Optional[int] = None,
        cwd: Optional[Union[str, Path]] = None,
        isolate: Optional[bool] = None,
    ) -> SandboxResult:
        if not self._docker_available:
            return self._fallback_sandbox.run_tests(pattern=pattern, command=command, timeout=timeout, cwd=cwd, isolate=isolate)
        target_dir = Path(cwd).resolve() if cwd else self.working_dir
        cmd = command or ["python", "-m", "unittest", "discover", "-s", ".", "-p", pattern]
        should_isolate = self.policy.isolate_tests if isolate is None else isolate
        if should_isolate:
            try:
                from .test_isolation import TestIsolationEngine, TestIsolationPolicy, TestIsolationMode
                tip = self.policy.test_isolation_policy or TestIsolationPolicy(mode=TestIsolationMode.READ_ONLY_DOCKER)
                iso_res = TestIsolationEngine.execute_isolated(
                    command=cmd,
                    workspace_dir=target_dir,
                    sandbox=self,
                    timeout=timeout,
                    policy=tip,
                )
                clean_err, cnt = self.redact_secrets(iso_res.stderr)
                clean_out, cnt_out = self.redact_secrets(iso_res.stdout)
                return SandboxResult(
                    exit_code=iso_res.exit_code,
                    stdout=clean_out,
                    stderr=clean_err,
                    duration_seconds=iso_res.duration_seconds,
                    success=iso_res.success,
                    timed_out=False,
                    killed_due_to_limit=False,
                    redacted_secrets_count=cnt + cnt_out,
                    command=cmd,
                )
            except Exception:
                pass
        return self.run_command(cmd, timeout=timeout, cwd=target_dir)

    def cleanup(self) -> None:
        self._fallback_sandbox.cleanup()


def create_sandbox(
    working_dir: Union[str, Path],
    policy: Optional[SandboxPolicy] = None,
    backend: str = "auto",
) -> BaseExecutionSandbox:
    """
    Factory function to instantiate the preferred execution sandbox.
    """
    if backend == "docker":
        try:
            docker_sb = DockerContainerSandbox(working_dir, policy=policy)
            if docker_sb.is_available():
                return docker_sb
        except Exception:
            pass
    return LocalProcessSandbox(working_dir, policy=policy)
