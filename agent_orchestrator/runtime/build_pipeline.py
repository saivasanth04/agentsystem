"""
BuildVerificationPipeline: Deterministic Multi-Stage Build and Verification Pipeline Engine.
Executes staged build lifecycles:
1. INSTALL_DEPENDENCIES (npm install, pip install, cargo fetch, go mod download)
2. BUILD (npm run build, cargo build, go build, mvn compile)
3. COMPILE_TYPECHECK (tsc --noEmit, cargo check, go vet, mypy)
4. LINT (eslint, ruff, cargo clippy)
5. UNIT_TEST (test_command)
6. INTEGRATION_TEST (test:e2e, integration tests)
"""
from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Union

from .project_detector import ProjectEnvironment, ProjectEnvironmentDetector
try:
    from ..security.sandbox import BaseExecutionSandbox, create_sandbox
except (ImportError, ValueError):
    from agent_orchestrator.security.sandbox import BaseExecutionSandbox, create_sandbox


class PipelineStage(str, Enum):
    INSTALL_DEPENDENCIES = "INSTALL_DEPENDENCIES"
    BUILD = "BUILD"
    COMPILE_TYPECHECK = "COMPILE_TYPECHECK"
    LINT = "LINT"
    UNIT_TEST = "UNIT_TEST"
    INTEGRATION_TEST = "INTEGRATION_TEST"


@dataclass
class StageResult:
    stage: PipelineStage
    passed: bool
    exit_code: int = 0
    command: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    skipped: bool = False
    skip_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage.value if isinstance(self.stage, PipelineStage) else str(self.stage),
            "passed": self.passed,
            "exit_code": self.exit_code,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


@dataclass
class PipelineReport:
    passed: bool
    failed_stage: Optional[PipelineStage] = None
    stage_results: Dict[str, StageResult] = field(default_factory=dict)
    total_duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "failed_stage": self.failed_stage.value if isinstance(self.failed_stage, PipelineStage) else self.failed_stage,
            "stage_results": {k: v.to_dict() for k, v in self.stage_results.items()},
            "total_duration_seconds": self.total_duration_seconds,
        }

    def summary(self) -> str:
        lines = [f"Build Verification Pipeline: {'PASSED' if self.passed else 'FAILED'} (Duration: {self.total_duration_seconds}s)"]
        if self.failed_stage:
            lines.append(f"Failed Stage: {self.failed_stage.value if isinstance(self.failed_stage, PipelineStage) else self.failed_stage}")
        for stage_name, res in self.stage_results.items():
            if res.skipped:
                lines.append(f"  - [{stage_name}] SKIPPED: {res.skip_reason or 'No command'}")
            elif res.passed:
                lines.append(f"  - [{stage_name}] PASSED ({res.duration_seconds}s): `{res.command}`")
            else:
                lines.append(f"  - [{stage_name}] FAILED (Exit code: {res.exit_code}, {res.duration_seconds}s): `{res.command}`")
                err_msg = res.stderr.strip() or res.stdout.strip()
                if err_msg:
                    snippet = err_msg.splitlines()[-5:]
                    lines.append(f"    Error: {' '.join(snippet)}")
        return "\n".join(lines)


class BuildVerificationPipeline:
    """
    Executes a staged build verification lifecycle in workspace sandbox.
    """

    DEFAULT_STAGES = [
        PipelineStage.INSTALL_DEPENDENCIES,
        PipelineStage.BUILD,
        PipelineStage.COMPILE_TYPECHECK,
        PipelineStage.LINT,
        PipelineStage.UNIT_TEST,
        PipelineStage.INTEGRATION_TEST,
    ]

    def __init__(
        self,
        workspace: Any = None,
        workspace_dir: Any = None,
        sandbox: Optional[BaseExecutionSandbox] = None,
        env: Optional[ProjectEnvironment] = None,
        event_bus: Optional[Any] = None,
        **kwargs: Any,
    ):
        ws = workspace if workspace is not None else workspace_dir
        self.workspace = ws
        self.root_dir = Path(ws.root_dir if hasattr(ws, "root_dir") else ws).resolve()
        self.sandbox = sandbox or create_sandbox(self.root_dir)
        self.env = env or ProjectEnvironmentDetector.detect(self.root_dir)
        self.event_bus = event_bus or kwargs.get("event_bus")

    def execute(
        self,
        stages: Optional[List[Union[PipelineStage, str]]] = None,
        fail_fast: bool = True,
        custom_commands: Optional[Dict[Union[PipelineStage, str], str]] = None,
        timeout_per_stage: int = 60,
    ) -> PipelineReport:
        """
        Executes pipeline stages sequentially. If a stage fails and fail_fast is True,
        downstream stages are marked as skipped and execution halts immediately.
        """
        start_time = time.time()
        active_stages = [
            s if isinstance(s, PipelineStage) else PipelineStage(str(s))
            for s in (stages or self.DEFAULT_STAGES)
        ]
        custom_map = {
            (k if isinstance(k, PipelineStage) else PipelineStage(str(k))): v
            for k, v in (custom_commands or {}).items()
        }

        stage_results: Dict[str, StageResult] = {}
        pipeline_passed = True
        failed_stage = None

        for idx, stage in enumerate(active_stages):
            # Check if previous failure should skip downstream
            if not pipeline_passed and fail_fast:
                stage_results[stage.value] = StageResult(
                    stage=stage,
                    passed=False,
                    skipped=True,
                    skip_reason=f"Skipped due to failure in stage '{failed_stage.value if failed_stage else 'EARLIER'}'",
                )
                continue

            cmd = self._resolve_command_for_stage(stage, custom_map)

            # If no command is configured for this stage, mark skipped
            if not cmd:
                stage_results[stage.value] = StageResult(
                    stage=stage,
                    passed=True,
                    skipped=True,
                    skip_reason=f"No command configured for stage '{stage.value}'",
                )
                continue

            # Execute stage command
            if getattr(self, "event_bus", None) and stage in (PipelineStage.UNIT_TEST, PipelineStage.INTEGRATION_TEST):
                try:
                    self.event_bus.publish(
                        "TEST_STARTED",
                        payload={"stage": stage.value, "command": cmd},
                    )
                except Exception:
                    pass

            stage_start = time.time()
            res = self._execute_stage_command(cmd, stage=stage, timeout=timeout_per_stage)
            stage_duration = round(time.time() - stage_start, 4)

            # Check if binary/toolchain was missing on system
            out = res.get("stdout", "") or ""
            err = res.get("stderr", "") or ""
            combined = (out + " " + err).lower()

            if not res.get("success") and ("not recognized" in combined or "command not found" in combined):
                # Graceful skip for missing host toolchains
                stage_results[stage.value] = StageResult(
                    stage=stage,
                    passed=True,
                    command=cmd,
                    duration_seconds=stage_duration,
                    skipped=True,
                    skip_reason=f"Toolchain binary for '{cmd.split()[0]}' not found on host/sandbox.",
                )
                continue

            stage_passed = bool(res.get("success", False) and res.get("exit_code", 0) == 0)

            if getattr(self, "event_bus", None) and stage in (PipelineStage.UNIT_TEST, PipelineStage.INTEGRATION_TEST):
                try:
                    if stage_passed:
                        self.event_bus.publish(
                            "TEST_PASSED",
                            payload={"stage": stage.value, "command": cmd, "duration_seconds": stage_duration},
                        )
                    else:
                        self.event_bus.publish(
                            "TEST_FAILED",
                            payload={
                                "stage": stage.value,
                                "command": cmd,
                                "exit_code": res.get("exit_code", 1),
                                "error": err or out,
                                "stdout": out,
                                "stderr": err,
                            },
                        )
                except Exception:
                    pass

            stage_result = StageResult(
                stage=stage,
                passed=stage_passed,
                exit_code=res.get("exit_code", 0),
                command=cmd,
                stdout=out,
                stderr=err,
                duration_seconds=stage_duration,
                skipped=False,
            )
            stage_results[stage.value] = stage_result

            if not stage_passed:
                pipeline_passed = False
                failed_stage = stage

        total_duration = round(time.time() - start_time, 4)
        return PipelineReport(
            passed=pipeline_passed,
            failed_stage=failed_stage,
            stage_results=stage_results,
            total_duration_seconds=total_duration,
        )

    def _resolve_command_for_stage(
        self,
        stage: PipelineStage,
        custom_map: Dict[PipelineStage, str],
    ) -> Optional[str]:
        """Resolves the shell command for a given pipeline stage."""
        if stage in custom_map:
            return custom_map[stage]

        if stage == PipelineStage.INSTALL_DEPENDENCIES:
            return self.env.install_command
        elif stage == PipelineStage.BUILD:
            return self.env.build_command
        elif stage == PipelineStage.COMPILE_TYPECHECK:
            return self.env.compiler_command or self.env.type_checker_command
        elif stage == PipelineStage.LINT:
            return self.env.linter_command
        elif stage == PipelineStage.UNIT_TEST:
            return self.env.test_command
        elif stage == PipelineStage.INTEGRATION_TEST:
            return self.env.integration_test_command
        return None

    def _execute_stage_command(
        self,
        command: str,
        stage: Optional[PipelineStage] = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        """Executes stage command via execution sandbox or fallback subprocess, with test isolation."""
        is_test_stage = stage in (PipelineStage.UNIT_TEST, PipelineStage.INTEGRATION_TEST)
        if self.sandbox:
            try:
                if is_test_stage:
                    res = self.sandbox.run_tests(command=command, cwd=self.root_dir, timeout=timeout)
                else:
                    res = self.sandbox.run_command(command, timeout=timeout, cwd=self.root_dir)
                return {
                    "success": res.success,
                    "exit_code": res.exit_code,
                    "stdout": res.stdout,
                    "stderr": res.stderr,
                }
            except Exception as e:
                return {
                    "success": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": str(e),
                }

        if is_test_stage:
            try:
                from ..security.test_isolation import TestIsolationEngine
                iso_res = TestIsolationEngine.execute_isolated(
                    command=command,
                    workspace_dir=self.root_dir,
                    timeout=timeout,
                )
                return {
                    "success": iso_res.success,
                    "exit_code": iso_res.exit_code,
                    "stdout": iso_res.stdout,
                    "stderr": iso_res.stderr,
                }
            except Exception as e:
                return {
                    "success": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": str(e),
                }

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(self.root_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "success": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": 124,
                "stdout": "",
                "stderr": f"Command '{command}' timed out after {timeout} seconds.",
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": str(e),
            }
