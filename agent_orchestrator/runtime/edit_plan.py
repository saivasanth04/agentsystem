"""
Structured Edit Plan Engine.
Transforms macro-level task objectives into precise, dependency-ordered, verifiable mutation blueprints.
Provides topological sorting, policy pre-validation, live step progress tracking, and prompt formatting.
"""
from collections import defaultdict, deque
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid

from ..contracts import (
    FileEditAction,
    FileEditActionType,
    FileEditStepStatus,
    StructuredEditPlanContract,
)


class EditPlanManager:
    """
    Manages generation, validation, topological ordering, and execution tracking of Structured Edit Plans.
    """

    @staticmethod
    def create_empty_plan(task_id: Optional[str] = None, summary: str = "") -> StructuredEditPlanContract:
        return StructuredEditPlanContract(
            plan_id=f"plan-{uuid.uuid4().hex[:8]}",
            task_id=task_id,
            summary=summary,
            edit_sequence=[],
            invariants=[],
            verification_commands=[],
            estimated_risk="LOW",
            created_at=datetime.now().isoformat(),
        )

    @classmethod
    def generate_plan(
        cls,
        task_info: Dict[str, Any],
        workspace: Optional[Any] = None,
        code_graph: Optional[Any] = None,
        architecture: Optional[Dict[str, Any]] = None,
        specification: Optional[Dict[str, Any]] = None,
    ) -> StructuredEditPlanContract:
        """
        Synthesizes a structured edit plan from task metadata, architecture blueprints, and workspace state.
        """
        task_id = task_info.get("task_id") or "T-01"
        objective = task_info.get("objective") or task_info.get("description") or "Execute task"
        inputs = task_info.get("inputs") or []
        outputs = task_info.get("outputs") or []
        acceptance_tests = task_info.get("acceptance_tests") or []

        edit_actions: List[FileEditAction] = []
        step_counter = 1

        # Track files to touch
        touched_files: List[Tuple[str, str, List[str]]] = []  # (filepath, intent, symbols)

        # 1. Inspect outputs specified by task
        for out in outputs:
            clean_out = out.strip().replace("\\", "/")
            if not clean_out:
                continue
            symbol_name = ""
            fp = clean_out
            if ":" in clean_out:
                fp, symbol_name = clean_out.split(":", 1)
            symbols = [symbol_name] if symbol_name else []
            touched_files.append((fp, "OUTPUT", symbols))

        # 2. Inspect inputs
        for inp in inputs:
            clean_inp = inp.strip().replace("\\", "/")
            if not clean_inp:
                continue
            fp = clean_inp.split(":")[0].strip()
            if not any(f[0] == fp for f in touched_files):
                touched_files.append((fp, "INPUT", []))

        # 3. Fallback: if no files in inputs/outputs, inspect objective keywords or architecture blueprint
        if not touched_files and architecture:
            component_struct = architecture.get("component_structure", []) or []
            for comp in component_struct:
                mod_name = comp.get("module_name", "")
                if mod_name:
                    touched_files.append((mod_name, "ARCH_COMPONENT", []))

        # If still empty, use a sensible default based on task_id
        if not touched_files:
            default_fp = f"src/task_{task_id.lower().replace('-', '_')}.py"
            touched_files.append((default_fp, "DEFAULT", []))

        # 4. Generate discrete FileEditAction per target file
        prev_step_id: Optional[str] = None

        for fp, intent, symbols in touched_files:
            file_exists = False
            if workspace:
                try:
                    target_path = Path(getattr(workspace, "root_dir", ".")) / fp
                    file_exists = target_path.exists()
                except Exception:
                    file_exists = False

            step_id = f"step-{step_counter}"
            step_counter += 1

            if not file_exists:
                action_type = FileEditActionType.CREATE_FILE
                desc = f"Create initial implementation file '{fp}'"
                if symbols:
                    desc += f" declaring symbols: {', '.join(symbols)}"
            else:
                action_type = FileEditActionType.MODIFY_SYMBOL if symbols else FileEditActionType.REPLACE_BLOCK
                desc = f"Update '{fp}'"
                if symbols:
                    desc += f" implementing {', '.join(symbols)}"

            deps = [prev_step_id] if prev_step_id else []

            edit_actions.append(
                FileEditAction(
                    step_id=step_id,
                    filepath=fp,
                    action_type=action_type,
                    target_symbols=symbols,
                    description=desc,
                    rationale=f"Deliverable for task objective: '{objective}'",
                    dependencies=deps,
                    status=FileEditStepStatus.PENDING,
                    verification_check=f"ast_syntax_check(filepath='{fp}')" if fp.endswith(".py") else None,
                )
            )
            prev_step_id = step_id

        # 5. Append test creation/update step if acceptance_tests are specified
        for test_cmd in acceptance_tests:
            # Extract test file if present
            test_file = None
            for part in test_cmd.split():
                if "test" in part.lower() and (part.endswith(".py") or part.endswith(".js") or part.endswith(".ts")):
                    test_file = part.replace("\\", "/")
                    break
            if test_file and not any(a.filepath == test_file for a in edit_actions):
                step_id = f"step-{step_counter}"
                step_counter += 1
                edit_actions.append(
                    FileEditAction(
                        step_id=step_id,
                        filepath=test_file,
                        action_type=FileEditActionType.CREATE_FILE,
                        target_symbols=["test_cases"],
                        description=f"Implement or update test suite '{test_file}'",
                        rationale="Ensure acceptance test coverage and verification",
                        dependencies=[prev_step_id] if prev_step_id else [],
                        status=FileEditStepStatus.PENDING,
                        verification_check=test_cmd,
                    )
                )
                prev_step_id = step_id

        sorted_actions = cls.topological_sort(edit_actions)

        # Invariants derived from specification or general best practices
        invariants = [
            "Maintain 100% syntax validity across all edited files",
            "Do not introduce breaking changes to existing module signatures",
            "Use optimistic concurrency control version tokens on existing files",
        ]
        if specification and isinstance(specification, dict):
            for edge in specification.get("edge_cases", []) or []:
                if isinstance(edge, dict) and edge.get("scenario"):
                    invariants.append(f"Handle edge case: {edge.get('scenario')} -> {edge.get('expected_behavior', '')}")

        return StructuredEditPlanContract(
            plan_id=f"plan-{uuid.uuid4().hex[:8]}",
            task_id=task_id,
            summary=f"Mutation Plan for {task_id}: {objective}",
            edit_sequence=sorted_actions,
            invariants=invariants,
            verification_commands=acceptance_tests if acceptance_tests else ["pytest tests/"],
            estimated_risk="LOW" if len(sorted_actions) <= 2 else "MEDIUM",
            created_at=datetime.now().isoformat(),
        )

    @staticmethod
    def topological_sort(actions: List[FileEditAction]) -> List[FileEditAction]:
        """
        Sorts edit actions in dependency order (topological sort).
        If circular dependencies occur, preserves incoming order and logs warning.
        """
        if not actions:
            return []

        action_map = {a.step_id: a for a in actions}
        in_degree: Dict[str, int] = defaultdict(int)
        graph: Dict[str, List[str]] = defaultdict(list)

        for a in actions:
            if a.step_id not in in_degree:
                in_degree[a.step_id] = 0
            for dep in a.dependencies:
                if dep in action_map:
                    graph[dep].append(a.step_id)
                    in_degree[a.step_id] += 1

        queue = deque([sid for sid in action_map if in_degree[sid] == 0])
        sorted_steps: List[str] = []

        while queue:
            node = queue.popleft()
            sorted_steps.append(node)
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_steps) == len(actions):
            return [action_map[sid] for sid in sorted_steps]

        # Circular fallback: return original order
        return actions

    @staticmethod
    def update_step_status(
        plan: StructuredEditPlanContract,
        step_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> StructuredEditPlanContract:
        """
        Updates the status of a specific step in the plan.
        """
        for step in plan.edit_sequence:
            if step.step_id == step_id:
                step.status = status
                if error:
                    step.error = error
                break
        return plan

    @staticmethod
    def validate_against_policies(
        plan: StructuredEditPlanContract,
        mutation_policy: Optional[Any] = None,
        file_access_policy: Optional[Any] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Pre-validates proposed edit plan against active mutation and file access policies.
        Returns (is_valid, violation_reasons).
        """
        violations = []

        for step in plan.edit_sequence:
            rel_norm = step.filepath.replace("\\", "/").strip("/")

            # Check FileAccessPolicy
            if file_access_policy and hasattr(file_access_policy, "evaluate"):
                from ..security.file_access_policy import FileAccessMode
                mode = FileAccessMode.DELETE if step.action_type == FileEditActionType.DELETE_FILE else FileAccessMode.WRITE
                dec = file_access_policy.evaluate(rel_norm, mode)
                if not dec.allowed:
                    violations.append(f"Step '{step.step_id}' ({rel_norm}): Denied by FileAccessPolicy ({dec.reason})")

            # Check MutationPolicy
            if mutation_policy:
                if hasattr(mutation_policy, "forbidden_write_paths") and mutation_policy.forbidden_write_paths:
                    import fnmatch
                    for pat in mutation_policy.forbidden_write_paths:
                        if fnmatch.fnmatch(rel_norm, pat):
                            violations.append(f"Step '{step.step_id}' ({rel_norm}): Matches forbidden write pattern '{pat}'")

        return (len(violations) == 0, violations)

    @staticmethod
    def to_prompt_context(plan: StructuredEditPlanContract) -> str:
        """
        Formats the structured edit plan into an actionable Markdown context section for LLM prompts.
        """
        lines = [
            f"### Structured Mutation Plan (Plan ID: `{plan.plan_id}` | Risk: `{plan.estimated_risk}`)",
            f"**Goal Summary:** {plan.summary}\n",
            "| Step | Target File | Action | Target Symbols | Dependencies | Status |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        status_emojis = {
            FileEditStepStatus.PENDING: "⏳ PENDING",
            FileEditStepStatus.IN_PROGRESS: "🔄 IN_PROGRESS",
            FileEditStepStatus.COMPLETED: "✅ COMPLETED",
            FileEditStepStatus.SKIPPED: "⏭️ SKIPPED",
            FileEditStepStatus.FAILED: "❌ FAILED",
        }

        for step in plan.edit_sequence:
            syms = ", ".join(step.target_symbols) if step.target_symbols else "-"
            deps = ", ".join(step.dependencies) if step.dependencies else "None"
            st = status_emojis.get(step.status, step.status)
            lines.append(f"| `{step.step_id}` | `{step.filepath}` | `{step.action_type}` | {syms} | {deps} | {st} |")

        if plan.invariants:
            lines.append("\n**System Invariants to Maintain:**")
            for inv in plan.invariants:
                lines.append(f"- {inv}")

        if plan.verification_commands:
            lines.append("\n**Post-Mutation Verification Steps:**")
            for cmd in plan.verification_commands:
                lines.append(f"- Run: `{cmd}`")

        return "\n".join(lines)
