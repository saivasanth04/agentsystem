"""
Epistemic Replanning Engine for Autonomous Coding Agents.
Transitions orchestrator from a static single-remediation retry macro to dynamic epistemic replanning.
Answers the 9 core replanning questions:
1. What failed?
2. Why did it fail?
3. Which assumptions are invalid?
4. What evidence is missing?
5. Which tasks are now invalid?
6. What new tasks are required?
7. Which tasks should be removed?
8. Can tasks execute in parallel?
9. Should another agent be selected?
"""
from dataclasses import dataclass, field
from datetime import datetime
import json
from typing import Any, Dict, List, Optional, Set, Tuple

from .diagnostics import DiagnosticReport
from .task_graph import (
    TaskDAG,
    ExecutableTask,
    TaskState,
    TaskPermissions,
    RetryPolicy,
)
from ..memory.working_memory import WorkingMemory


@dataclass
class ReplanResult:
    """The structured outcome of an epistemic replanning phase."""
    iteration: int
    root_cause: str
    failure_type: str
    invalid_assumptions: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    invalid_task_ids: List[str] = field(default_factory=list)
    pruned_task_ids: List[str] = field(default_factory=list)
    injected_tasks: List[ExecutableTask] = field(default_factory=list)
    parallel_groups: List[List[str]] = field(default_factory=list)
    replan_record: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration,
            "root_cause": self.root_cause,
            "failure_type": self.failure_type,
            "invalid_assumptions": list(self.invalid_assumptions),
            "missing_evidence": list(self.missing_evidence),
            "invalid_task_ids": list(self.invalid_task_ids),
            "pruned_task_ids": list(self.pruned_task_ids),
            "injected_task_ids": [t.task_id for t in self.injected_tasks],
            "parallel_groups": self.parallel_groups,
            "replan_record": self.replan_record,
        }


class EpistemicReplanner:
    """
    Epistemic Replanner that dynamically restructures the TaskDAG upon failure.
    Evaluates failure causality, invalidates falsified assumptions, prunes obsolete tasks,
    and synthesizes multi-task remediation branches with dependency resolution and concurrency analysis.
    """

    def __init__(self, on_event: Optional[Any] = None):
        self.on_event = on_event or (lambda stage, msg: None)

    def replan(
        self,
        task_dag: TaskDAG,
        diagnostic: DiagnosticReport,
        working_memory: Optional[WorkingMemory] = None,
        iteration: int = 1,
        rollback_executed: bool = False,
        rollback_details: Optional[Dict[str, Any]] = None,
        trigger_reason: Optional[str] = None,
        feedback_summary: Optional[str] = None,
    ) -> ReplanResult:
        """
        Executes epistemic plan revision over the TaskDAG.
        """
        # 1. What failed?
        failed_tasks = task_dag.get_failed_tasks()
        failed_ids = [t.task_id for t in failed_tasks]

        # 2. Why did it fail?
        root_cause = diagnostic.root_cause_summary
        failure_type = diagnostic.failure_type

        # 3. Which assumptions are invalid?
        invalid_assumptions = list(diagnostic.invalid_assumptions)
        if working_memory:
            for assumption in invalid_assumptions:
                working_memory.record_pitfall(f"Invalidated assumption: {assumption}")

        # 4. What evidence is missing?
        missing_evidence = list(diagnostic.missing_evidence)
        if working_memory:
            for ev in missing_evidence:
                working_memory.update_scratchpad(f"Missing evidence to acquire: {ev}")

        # 5. Which tasks are now invalid & 7. Which tasks should be removed?
        to_prune: List[str] = []
        if diagnostic.invalid_task_ids:
            to_prune.extend(diagnostic.invalid_task_ids)
        if diagnostic.pruned_task_ids:
            to_prune.extend(diagnostic.pruned_task_ids)

        # De-duplicate while preserving order
        unique_prune: List[str] = []
        for tid in to_prune:
            if tid not in unique_prune and tid not in failed_ids:
                unique_prune.append(tid)

        pruned_ids = []
        if unique_prune:
            pruned_ids = task_dag.prune_tasks(
                unique_prune,
                reason=f"Pruned during Re-plan iteration {iteration} due to root cause: {root_cause}",
                cascade=True,
            )
            if pruned_ids:
                self.on_event("DAG PRUNED", f"Re-plan pruned {len(pruned_ids)} invalid/redundant task(s): {pruned_ids}")

        # Check if architectural drift, spec violation, or invalid assumptions invalidate downstream contracts
        is_architectural_failure = (
            failure_type in ("ARCHITECTURE_DRIFT", "SPEC_VIOLATION")
            or bool(invalid_assumptions and any("architecture" in a.lower() or "contract" in a.lower() or "schema" in a.lower() for a in invalid_assumptions))
        )
        if is_architectural_failure and failed_ids:
            # Transitively invalidate all downstream dependents of failed tasks to avoid repeating bad work
            arch_pruned = task_dag.invalidate_dependent_tasks(
                failed_ids,
                reason=f"Stale downstream task invalidated by architectural replan for failure in {failed_ids}: {root_cause[:100]}",
            )
            for ap_id in arch_pruned:
                if ap_id not in pruned_ids:
                    pruned_ids.append(ap_id)
            if arch_pruned:
                self.on_event("TRANSITIVE TASK INVALIDATION", f"Architectural failure invalidated {len(arch_pruned)} stale downstream task(s): {arch_pruned}")

        # 6. What new tasks are required? & 9. Should another agent be selected?
        injected_tasks: List[ExecutableTask] = []
        raw_rem_tasks = diagnostic.remediation_tasks
        dag_exec_id = next((t.execution_id for t in task_dag.list_tasks() if t.execution_id), None)

        rem_prefix = "Clean Baseline Remediation" if rollback_executed else "Remediate Defect"

        if raw_rem_tasks and isinstance(raw_rem_tasks, list):
            # Dynamic multi-task remediation branch synthesized by reasoning model
            created_tasks: List[ExecutableTask] = []
            for idx, r_data in enumerate(raw_rem_tasks, start=1):
                if not isinstance(r_data, dict):
                    continue
                tid = r_data.get("task_id") or f"T-REM-{iteration}-{idx}"
                obj = r_data.get("objective") or f"{rem_prefix}: Step {idx}"
                if "dependencies" in r_data and r_data["dependencies"] is not None:
                    deps = list(r_data["dependencies"])
                elif created_tasks:
                    deps = [created_tasks[-1].task_id]
                else:
                    deps = []

                caps = list(r_data.get("required_capabilities") or ["code-generation", "defect-repair"])
                tools = list(r_data.get("required_tools") or ["filesystem", "terminal"])
                skills = list(r_data.get("preferred_skills") or ["debugging-and-error-recovery"])
                inputs = list(r_data.get("inputs") or [])
                outputs = list(r_data.get("outputs") or [])
                acc_tests = list(r_data.get("acceptance_tests") or [])

                task_obj = ExecutableTask(
                    task_id=tid,
                    objective=obj,
                    execution_id=dag_exec_id,
                    dependencies=deps,
                    required_capabilities=caps,
                    required_tools=tools,
                    preferred_skills=skills,
                    inputs=inputs,
                    outputs=outputs,
                    acceptance_tests=acc_tests,
                    permissions=TaskPermissions(allowed_write_paths=["*"]),
                    state=TaskState.PENDING,
                )
                created_tasks.append(task_obj)

            if created_tasks:
                # Ensure terminal verification task is present
                has_verification = any("test" in t.objective.lower() or "verify" in t.objective.lower() for t in created_tasks)
                if not has_verification:
                    verify_task_id = f"T-VERIFY-{iteration}"
                    verify_task = ExecutableTask(
                        task_id=verify_task_id,
                        objective="Verify Remediation & Tests",
                        execution_id=dag_exec_id,
                        dependencies=[t.task_id for t in created_tasks],
                        required_capabilities=["testing", "unit-tests"],
                        required_tools=["filesystem", "terminal"],
                        preferred_skills=["test-driven-development"],
                        inputs=[],
                        outputs=[],
                        acceptance_tests=[],
                        permissions=TaskPermissions(allowed_write_paths=["*"]),
                        state=TaskState.PENDING,
                    )
                    created_tasks.append(verify_task)

                terminal_remediation_task = created_tasks[-1]
                first_task = created_tasks[0]
                failed_primary = failed_ids[0] if failed_ids else None
                task_dag.inject_remediation_task(
                    first_task,
                    failed_task_id=failed_primary,
                    invalidate_downstream=is_architectural_failure,
                    terminal_task_id=terminal_remediation_task.task_id,
                )
                injected_tasks.append(first_task)

                for subsequent in created_tasks[1:]:
                    task_dag.add_task(subsequent)
                    injected_tasks.append(subsequent)

                # If there were multiple failed tasks, retarget their downstream dependencies to terminal remediation task as well
                if len(failed_ids) > 1 and not is_architectural_failure:
                    for extra_failed_id in failed_ids[1:]:
                        for t in task_dag.list_tasks():
                            if extra_failed_id in t.dependencies:
                                t.dependencies = [
                                    terminal_remediation_task.task_id if d == extra_failed_id else d
                                    for d in t.dependencies
                                ]
                                if t.state == TaskState.BLOCKED:
                                    t.state = TaskState.PENDING

        if not injected_tasks:
            # Sane default baseline remediation pair (100% backward compatibility)
            rem_task_id = f"T-REM-{iteration}"
            target_agent = diagnostic.target_agent.upper() if diagnostic.target_agent else "CODER"
            if target_agent == "TESTER":
                caps = ["testing", "unit-tests"]
                skills = ["test-driven-development"]
            elif target_agent in ("SPECIFICATION", "ARCHITECTURE"):
                caps = ["architecture", "spec-design"]
                skills = ["api-and-interface-design"]
            else:
                caps = ["code-generation", "debugging", "defect-repair", "refactoring"]
                skills = ["debugging-and-error-recovery"]

            remediation_task = ExecutableTask(
                task_id=rem_task_id,
                owner_agent=target_agent,
                objective=f"{rem_prefix}: {root_cause[:60]}",
                execution_id=dag_exec_id,
                dependencies=[],
                required_capabilities=caps,
                required_tools=["filesystem", "terminal"],
                preferred_skills=skills,
                inputs=[],
                outputs=[],
                acceptance_tests=[],
                permissions=TaskPermissions(allowed_write_paths=["*"]),
                state=TaskState.PENDING,
            )

            verify_task_id = f"T-VERIFY-{iteration}"
            verify_task = ExecutableTask(
                task_id=verify_task_id,
                owner_agent="TESTER",
                objective="Verify Remediation & Tests",
                execution_id=dag_exec_id,
                dependencies=[rem_task_id],
                required_capabilities=["testing", "unit-tests"],
                required_tools=["filesystem", "terminal"],
                preferred_skills=["test-driven-development"],
                inputs=[],
                outputs=[],
                acceptance_tests=[],
                permissions=TaskPermissions(allowed_write_paths=["*"]),
                state=TaskState.PENDING,
            )

            failed_primary = failed_ids[0] if failed_ids else None
            task_dag.inject_remediation_task(
                remediation_task,
                failed_task_id=failed_primary,
                invalidate_downstream=is_architectural_failure,
                terminal_task_id=verify_task.task_id,
            )
            task_dag.add_task(verify_task)
            injected_tasks.extend([remediation_task, verify_task])

            if len(failed_ids) > 1 and not is_architectural_failure:
                for extra_failed_id in failed_ids[1:]:
                    for t in task_dag.list_tasks():
                        if extra_failed_id in t.dependencies:
                            t.dependencies = [
                                verify_task.task_id if d == extra_failed_id else d
                                for d in t.dependencies
                            ]
                            if t.state == TaskState.BLOCKED:
                                t.state = TaskState.PENDING

        # 8. Can tasks execute in parallel?
        # Partition injected tasks by dependency waves
        parallel_groups: List[List[str]] = []
        dep_map: Dict[str, Set[str]] = {t.task_id: set(t.dependencies) for t in injected_tasks}
        remaining_ids = set(dep_map.keys())

        while remaining_ids:
            wave = [tid for tid in remaining_ids if not (dep_map[tid] & remaining_ids)]
            if not wave:
                # Circular dependency or mutual block, break cycle
                wave = list(remaining_ids)
            parallel_groups.append(sorted(wave))
            remaining_ids -= set(wave)

        # Audit record
        eff_trigger = trigger_reason or f"Failure at iteration {iteration - 1}"
        eff_feedback = feedback_summary or root_cause
        replan_record = {
            "iteration": iteration,
            "trigger_reason": eff_trigger,
            "root_cause": root_cause,
            "failure_type": failure_type,
            "feedback_summary": eff_feedback,
            "remediation_plan": diagnostic.suggested_remediation,
            "invalid_assumptions": invalid_assumptions,
            "missing_evidence": missing_evidence,
            "invalid_task_ids": diagnostic.invalid_task_ids,
            "pruned_task_ids": pruned_ids,
            "injected_task_ids": [t.task_id for t in injected_tasks],
            "parallel_groups": parallel_groups,
            "target_agent_for_fix": diagnostic.target_agent,
            "rollback_executed": rollback_executed,
            "rollback_details": rollback_details,
            "timestamp": datetime.now().isoformat(),
        }

        return ReplanResult(
            iteration=iteration,
            root_cause=root_cause,
            failure_type=failure_type,
            invalid_assumptions=invalid_assumptions,
            missing_evidence=missing_evidence,
            invalid_task_ids=diagnostic.invalid_task_ids,
            pruned_task_ids=pruned_ids,
            injected_tasks=injected_tasks,
            parallel_groups=parallel_groups,
            replan_record=replan_record,
        )


__all__ = ["EpistemicReplanner", "ReplanResult"]
