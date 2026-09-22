"""
TaskVerificationGate: Automated Acceptance Test and Deliverable Verification Engine.
Validates code generation outputs, runs acceptance criteria commands, checks file integrity,
and verifies deliverables before tasks transition to COMPLETED.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from .task_graph import ExecutableTask, TaskState
from ..tools.workspace import WorkspaceManager


@dataclass
class VerificationResult:
    passed: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    failure_reasons: List[str] = field(default_factory=list)
    verified_outputs: List[str] = field(default_factory=list)
    pipeline_report: Optional[Dict[str, Any]] = None
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    static_warnings: List[Dict[str, Any]] = field(default_factory=list)
    static_report: Optional[Dict[str, Any]] = None
    ground_truth_report: Optional[Dict[str, Any]] = None
    regression_report: Optional[Dict[str, Any]] = None

    @property
    def errors(self) -> List[str]:
        return self.failure_reasons

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "failure_reasons": self.failure_reasons,
            "verified_outputs": self.verified_outputs,
            "pipeline_report": self.pipeline_report,
            "diagnostics": self.diagnostics,
            "static_warnings": self.static_warnings,
            "static_report": self.static_report,
            "ground_truth_report": self.ground_truth_report,
            "regression_report": self.regression_report,
        }



class TaskVerificationGate:
    """
    Automated Gatekeeper that tests task deliverables against acceptance criteria.
    """

    def __init__(
        self,
        workspace: WorkspaceManager,
        tool_dispatcher: Optional[Any] = None,
        sandbox: Optional[Any] = None,
        event_bus: Optional[Any] = None,
        **kwargs: Any,
    ):
        self.workspace = workspace
        self.tool_dispatcher = tool_dispatcher
        self.sandbox = sandbox
        self.event_bus = event_bus or getattr(workspace, "event_bus", None)
        try:
            from .static_verifier import StaticVerificationEngine
            self.static_engine = StaticVerificationEngine(workspace=self.workspace, sandbox=self.sandbox)
        except Exception:
            self.static_engine = None

    def run_static_verification(self, files: Optional[List[str]] = None, workspace: Optional[WorkspaceManager] = None):
        """Runs multi-tier deterministic static analysis directly."""
        target_ws = workspace or self.workspace
        try:
            from .static_verifier import StaticVerificationEngine
            engine = StaticVerificationEngine(workspace=target_ws, sandbox=self.sandbox)
            return engine.verify(files=files)
        except Exception as e:
            return None

    def verify_task(self, task: ExecutableTask, workspace: Optional[WorkspaceManager] = None) -> VerificationResult:
        """
        Executes multi-axis automated verification on the task deliverables.
        1. Checks existence of declared outputs (files, modules).
        2. Validates Python syntax of created/modified files.
        3. Executes declared acceptance tests and assertions via shell/runner.
        """
        task_id = task.get("task_id", "unknown") if isinstance(task, dict) else getattr(task, "task_id", "unknown")
        task_obj = task.get("objective", "") if isinstance(task, dict) else getattr(task, "objective", "")

        from ..tracing import get_tracer, SpanType, SpanStatus
        tracer = get_tracer("orchestrator")
        verif_scope = tracer.start_as_current_span(
            f"Verification: {task_id}",
            span_type=SpanType.VERIFICATION,
            attributes={"task_id": task_id, "objective": task_obj},
        )
        verif_span = verif_scope.__enter__()

        target_ws = workspace or self.workspace
        failure_reasons: List[str] = []
        verified_outputs: List[str] = []
        combined_stdout = []
        combined_stderr = []
        final_exit_code = 0

        task_outputs = task.get("outputs", []) if isinstance(task, dict) else getattr(task, "outputs", [])
        task_acceptance_tests = task.get("acceptance_tests", []) if isinstance(task, dict) else getattr(task, "acceptance_tests", [])
        raw_caps = (task.get("required_capabilities") or task.get("capabilities", [])) if isinstance(task, dict) else (getattr(task, "required_capabilities", None) or getattr(task, "capabilities", []))
        task_capabilities = [str(c).lower() for c in raw_caps]

        # 0. Check Deliverable Schema Validation
        result_data = getattr(task, "result_data", None) or (task.get("result_data") if isinstance(task, dict) else None)
        if isinstance(result_data, dict):
            val_report = result_data.get("validation_report")
            if isinstance(val_report, dict) and not val_report.get("is_valid", True):
                errs = val_report.get("errors", [])
                c_name = val_report.get("contract_name", "Deliverable")
                failure_reasons.append(f"Deliverable Schema Validation Failed for [{c_name}]: {'; '.join(errs)}")

        # 0b. Check Inter-Agent Contract Compliance (Issue #58)
        agent_contract = getattr(task, "agent_contract", None) or (task.get("agent_contract") if isinstance(task, dict) else None)
        if agent_contract:
            try:
                from .agent_contract import AgentContractEnforcer
                contract_report = AgentContractEnforcer.validate_deliverable(
                    contract=agent_contract,
                    candidate_deliverable=result_data,
                    workspace=target_ws,
                )
                if not contract_report.is_valid:
                    for v in contract_report.violations:
                        if v not in failure_reasons:
                            failure_reasons.append(v)
            except Exception as e:
                failure_reasons.append(f"Contract Verification Failed (Exception): {str(e)}")

        # 1. Verify Expected Outputs
        for out in task_outputs:
            if not out:
                continue
            # Strip symbol notation if path:Symbol
            file_part = out.split(":")[0].strip()
            if not file_part:
                continue

            # Safe resolution against workspace root
            try:
                from ..tools.workspace import safe_resolve_path, PathTraversalError
            except (ImportError, ValueError):
                from agent_orchestrator.tools.workspace import safe_resolve_path, PathTraversalError

            try:
                file_path = safe_resolve_path(target_ws.root_dir, file_part)
            except PathTraversalError:
                failure_reasons.append(f"Security Violation: Output file '{file_part}' attempts path traversal.")
                continue

            if not file_path.exists() and target_ws != self.workspace:
                try:
                    file_path = safe_resolve_path(self.workspace.root_dir, file_part)
                except PathTraversalError:
                    pass

            if not file_path.exists():
                # Check if it was written anywhere in workspace with relative name
                matches = list(target_ws.root_dir.glob(f"**/{file_part}"))
                if not matches and target_ws != self.workspace:
                    matches = list(self.workspace.root_dir.glob(f"**/{file_part}"))
                if not matches:
                    failure_reasons.append(f"Expected output file '{file_part}' does not exist.")
                    continue
                file_path = matches[0]

            if file_path.is_file() and file_path.stat().st_size == 0:
                failure_reasons.append(f"Expected output file '{file_part}' exists but is empty (0 bytes).")
            else:
                verified_outputs.append(str(file_part))

        # 1b. Reconcile Task Deliverables & Validate Artifact Content/Syntax
        written_files = []
        if isinstance(result_data, dict):
            written_files = result_data.get("written_files", [])
        if not written_files and hasattr(target_ws, "get_uncommitted_changes"):
            try:
                uncommitted = target_ws.get_uncommitted_changes()
                written_files = uncommitted.get("created", []) + uncommitted.get("modified", [])
            except Exception:
                pass

        try:
            from .artifact_validator import ArtifactValidator

            forbidden_paths = None
            task_perms = getattr(task, "permissions", None) or (task.get("permissions") if isinstance(task, dict) else None)
            if task_perms:
                forbidden_paths = getattr(task_perms, "forbidden_write_paths", None)
                if isinstance(task_perms, dict):
                    forbidden_paths = task_perms.get("forbidden_write_paths")

            recon = ArtifactValidator.reconcile_task_outputs(
                workspace=target_ws,
                required_outputs=task_outputs,
                modified_files=written_files,
                forbidden_paths=forbidden_paths,
                fallback_workspace=self.workspace if target_ws != self.workspace else None,
            )
            if not recon.is_valid:
                for err in recon.errors:
                    if err not in failure_reasons:
                        failure_reasons.append(f"Artifact Validation Failed: {err}")

            # Validate content and syntax for each verified output
            for v_out in verified_outputs:
                content = target_ws.read_file(v_out)
                if content is None and target_ws != self.workspace:
                    content = self.workspace.read_file(v_out)
                if content is not None:
                    c_valid, c_errs = ArtifactValidator.validate_content(v_out, content)
                    for ce in c_errs:
                        failure_reasons.append(f"Artifact Content Error in '{v_out}': {ce}")
                    s_valid, s_errs, _ = ArtifactValidator.validate_syntax(v_out, content)
                    for se in s_errs:
                        failure_reasons.append(f"Artifact Syntax Error in '{v_out}': {se}")
        except Exception as e:
            failure_reasons.append(f"Artifact Verification Failed (Exception): {str(e)}")

        # 2. Multi-Tier Deterministic Static Verification
        static_report_dict = None
        static_diagnostics = []
        static_warnings = []
        if verified_outputs:
            try:
                from .static_verifier import StaticVerificationEngine
                engine = self.static_engine or StaticVerificationEngine(workspace=target_ws, sandbox=self.sandbox)
                if target_ws != self.workspace:
                    engine = StaticVerificationEngine(workspace=target_ws, sandbox=self.sandbox)
                report = engine.verify(files=verified_outputs)
                static_report_dict = report.to_dict()
                static_diagnostics = report.diagnostics
                static_warnings = report.warnings
                if not report.passed:
                    failure_reasons.extend(report.all_failures)
            except Exception as e:
                failure_reasons.append(f"Static Verification Failed (Exception): {str(e)}")

        # 3. Staged Build Verification Pipeline
        pipeline_report_dict = None
        try:
            from .build_pipeline import BuildVerificationPipeline, PipelineStage
            from .project_detector import ProjectEnvironmentDetector
            env = ProjectEnvironmentDetector.detect(target_ws)
            pipeline = BuildVerificationPipeline(workspace=target_ws, sandbox=self.sandbox, env=env, event_bus=self.event_bus)

            is_test_task = any(cap in ("testing", "unit-tests", "verification") for cap in task_capabilities)
            if is_test_task or task_acceptance_tests:
                stages = [
                    PipelineStage.BUILD,
                    PipelineStage.COMPILE_TYPECHECK,
                    PipelineStage.UNIT_TEST,
                ]
                if env.integration_test_command:
                    stages.append(PipelineStage.INTEGRATION_TEST)

                custom_cmds = {}
                if task_acceptance_tests:
                    custom_cmds[PipelineStage.UNIT_TEST] = task_acceptance_tests[0]

                pipe_report = pipeline.execute(stages=stages, fail_fast=True, custom_commands=custom_cmds)
                pipeline_report_dict = pipe_report.to_dict()

                for s_name, s_res in pipe_report.stage_results.items():
                    if s_res.command and not s_res.skipped:
                        if s_res.stdout:
                            combined_stdout.append(f"[{s_name}: {s_res.command}]\n{s_res.stdout}")
                        if s_res.stderr:
                            combined_stderr.append(f"[{s_name}: {s_res.command}]\n{s_res.stderr}")

                if not pipe_report.passed and pipe_report.failed_stage:
                    failed_res = pipe_report.stage_results.get(pipe_report.failed_stage.value)
                    final_exit_code = failed_res.exit_code if failed_res else 1
                    err_msg = (failed_res.stderr if failed_res else "") or (failed_res.stdout if failed_res else "")
                    if task_acceptance_tests and pipe_report.failed_stage in (PipelineStage.UNIT_TEST, PipelineStage.INTEGRATION_TEST):
                        failure_reasons.append(
                            f"Acceptance test command failed: `{failed_res.command if failed_res else 'N/A'}` (Exit code: {final_exit_code}). Error: {err_msg[:300]}"
                        )
                    else:
                        failure_reasons.append(
                            f"Build verification pipeline failed at stage '{pipe_report.failed_stage.value}' "
                            f"(Command: `{failed_res.command if failed_res else 'N/A'}`). Error: {err_msg[:300]}"
                        )

            elif any(cap in ("code-generation", "refactoring", "frontend", "backend") for cap in task_capabilities):
                if env.build_command:
                    pipe_report = pipeline.execute(stages=[PipelineStage.BUILD, PipelineStage.COMPILE_TYPECHECK], fail_fast=True)
                    pipeline_report_dict = pipe_report.to_dict()
                    for s_name, s_res in pipe_report.stage_results.items():
                        if s_res.command and not s_res.skipped:
                            if s_res.stdout:
                                combined_stdout.append(f"[{s_name}: {s_res.command}]\n{s_res.stdout}")
                            if s_res.stderr:
                                combined_stderr.append(f"[{s_name}: {s_res.command}]\n{s_res.stderr}")

                    if not pipe_report.passed and pipe_report.failed_stage:
                        failed_res = pipe_report.stage_results.get(pipe_report.failed_stage.value)
                        final_exit_code = failed_res.exit_code if failed_res else 1
                        err_msg = (failed_res.stderr if failed_res else "") or (failed_res.stdout if failed_res else "")
                        failure_reasons.append(
                            f"Build verification pipeline failed at stage '{pipe_report.failed_stage.value}' "
                            f"(Command: `{failed_res.command if failed_res else 'N/A'}`). Error: {err_msg[:300]}"
                        )
        except Exception as e:
            failure_reasons.append(f"Build Verification Failed (Exception): {str(e)}")

        # 4. Fallback Acceptance Tests (if pipeline was not invoked)
        if not pipeline_report_dict and task_acceptance_tests:
            for test_cmd in task_acceptance_tests:
                cmd = test_cmd.strip()
                if not cmd:
                    continue
                exec_res = self._execute_command(cmd)
                out_str = exec_res.get("stdout", "") or exec_res.get("output", "")
                err_str = exec_res.get("stderr", "")
                code = exec_res.get("exit_code", 0)

                if out_str:
                    combined_stdout.append(f"[{cmd}]\n{out_str}")
                if err_str:
                    combined_stderr.append(f"[{cmd}]\n{err_str}")

                if not exec_res.get("success", True) or code != 0:
                    final_exit_code = code or 1
                    failure_reasons.append(f"Acceptance test command failed: '{cmd}' (Exit code: {code}). Error: {err_str or out_str}")

        # 4.5 Dependency-Aware Regression Detection across Blast Radius
        regression_report_dict = None
        if verified_outputs:
            try:
                from .regression_detector import DependencyRegressionDetector
                reg_report = DependencyRegressionDetector.verify_regressions(
                    modified_files=verified_outputs,
                    workspace=target_ws,
                    static_engine=self.static_engine,
                    test_info={
                        "execution_success": (final_exit_code == 0 and len(failure_reasons) == 0),
                        "exit_code": final_exit_code,
                        "stdout": "\n".join(combined_stdout),
                        "stderr": "\n".join(combined_stderr),
                    },
                )
                regression_report_dict = reg_report.to_dict()
                if not reg_report.passed:
                    for bd in reg_report.broken_dependents:
                        failure_reasons.append(f"Cross-Module Regression in dependent: {bd}")
                    for ft in reg_report.failing_tests:
                        failure_reasons.append(f"Regression Test Failure: {ft}")
            except Exception as e:
                failure_reasons.append(f"Regression Verification Failed (Exception): {str(e)}")

        # 5. Deterministic Ground-Truth Verification Matrix
        ground_truth_report_dict = None
        try:
            from .verification_matrix import GroundTruthVerificationMatrix
            gt_matrix = GroundTruthVerificationMatrix(workspace=target_ws, sandbox=self.sandbox)
            eval_state = {
                "test_output": {
                    "execution_success": (final_exit_code == 0 and len(failure_reasons) == 0),
                    "exit_code": final_exit_code,
                    "stdout": "\n".join(combined_stdout),
                    "stderr": "\n".join(combined_stderr),
                    "test_command": " ".join(task_acceptance_tests) if task_acceptance_tests else None,
                    "build_pipeline_report": pipeline_report_dict,
                },
                "specification_output": getattr(task, "metadata", {}).get("specification_output", {}),
            }
            gt_report = gt_matrix.evaluate(
                state=eval_state,
                modified_files=verified_outputs,
                static_report=static_report_dict,
                build_pipeline_report=pipeline_report_dict,
            )
            ground_truth_report_dict = gt_report.to_dict()
            if not gt_report.passed:
                for bf in gt_report.blocking_failures:
                    failure_reasons.append(f"Ground-truth gate failure: {bf}")
        except Exception as e:
            failure_reasons.append(f"Ground-Truth Verification Failed (Exception): {str(e)}")

        # 6. Check for Vacuous Task Success on Mutating Coding and Testing Tasks
        is_test_task = any(cap in ("testing", "unit-tests", "verification", "integration-tests") for cap in task_capabilities)
        is_coding_task = any(cap in ("code-generation", "refactoring", "frontend", "backend", "coding", "implementation", "bugfix") for cap in task_capabilities)

        task_artifacts = getattr(task, "artifacts", None) or (task.get("artifacts") if isinstance(task, dict) else []) or []
        task_typed_artifacts = getattr(task, "typed_artifacts", None) or (task.get("typed_artifacts") if isinstance(task, dict) else []) or []

        if is_coding_task and not verified_outputs and not written_files and not task_artifacts and not task_typed_artifacts:
            failure_reasons.append(
                f"Vacuous task success: Mutating coding task '{task_id}' produced no concrete deliverables (no verified outputs, modified files, or artifacts)."
            )

        has_pipeline_test_execution = False
        if pipeline_report_dict and "stage_results" in pipeline_report_dict:
            for s_name, s_data in pipeline_report_dict["stage_results"].items():
                if s_name in ("UNIT_TEST", "INTEGRATION_TEST") and not s_data.get("skipped", False):
                    has_pipeline_test_execution = True

        if is_test_task and not has_pipeline_test_execution and not task_acceptance_tests:
            test_evidence = False
            if isinstance(result_data, dict) and (result_data.get("test_results") or result_data.get("passed") is not None or result_data.get("exit_code") is not None or result_data.get("tests_executed")):
                test_evidence = True
            if not test_evidence:
                failure_reasons.append(
                    f"Vacuous task success: Testing task '{task_id}' executed with no concrete acceptance evidence (no test pipeline or acceptance tests executed)."
                )

        passed = len(failure_reasons) == 0
        if passed:
            verif_span.set_status(SpanStatus.OK)
        else:
            verif_span.set_status(SpanStatus.ERROR, "; ".join(failure_reasons))
        verif_span.set_attribute("passed", passed)
        verif_span.set_attribute("verified_outputs_count", len(verified_outputs))
        verif_scope.__exit__(None, None, None)

        return VerificationResult(
            passed=passed,
            exit_code=final_exit_code,
            stdout="\n".join(combined_stdout),
            stderr="\n".join(combined_stderr),
            failure_reasons=failure_reasons,
            verified_outputs=verified_outputs,
            pipeline_report=pipeline_report_dict,
            diagnostics=static_diagnostics,
            static_warnings=static_warnings,
            static_report=static_report_dict,
            ground_truth_report=ground_truth_report_dict,
            regression_report=regression_report_dict,
        )


    def _execute_command(self, command: str) -> Dict[str, Any]:
        """Runs a verification command safely within the workspace."""
        if self.tool_dispatcher and hasattr(self.tool_dispatcher, "call_tool"):
            try:
                # Try run_command or terminal_execute
                if hasattr(self.tool_dispatcher, "get_schemas"):
                    schemas = self.tool_dispatcher.get_schemas()
                    names = [s.get("function", {}).get("name") for s in schemas]
                    if "terminal_execute" in names:
                        return self.tool_dispatcher.call_tool("terminal_execute", {"command": command})
                    elif "run_command" in names:
                        return self.tool_dispatcher.call_tool("run_command", {"command": command})
            except Exception as e:
                pass

        if self.sandbox:
            res = self.sandbox.run_command(command, timeout=60, cwd=self.workspace.root_dir)
            return {
                "success": res.success,
                "exit_code": res.exit_code,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "output": res.stdout if res.success else res.stderr,
            }

        # Fallback to execution sandbox in workspace
        try:
            from ..security.sandbox import create_sandbox
        except (ImportError, ValueError):
            from agent_orchestrator.security.sandbox import create_sandbox

        sb = create_sandbox(self.workspace.root_dir)
        res = sb.run_command(command, timeout=60, cwd=self.workspace.root_dir)
        return {
            "success": res.success,
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "output": res.stdout if res.success else res.stderr,
        }
