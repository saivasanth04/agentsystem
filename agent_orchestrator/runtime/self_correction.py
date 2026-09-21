"""
Intra-Agent Self-Correction Engine.
Enforces the Generate -> Inspect -> Validate -> Fix -> Return loop within agent execution turns.
Prevents unverified, broken, or syntactically invalid files from escaping to downstream agents.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .artifact_validator import ArtifactValidator
from .static_verifier import StaticVerificationEngine


@dataclass
class SelfCorrectionReport:
    """Outcome of intra-agent self-correction cycle."""
    initial_errors: List[str] = field(default_factory=list)
    attempts_made: int = 0
    max_attempts: int = 2
    fixed_errors: List[str] = field(default_factory=list)
    remaining_errors: List[str] = field(default_factory=list)
    is_clean: bool = True
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "initial_errors": self.initial_errors,
            "attempts_made": self.attempts_made,
            "max_attempts": self.max_attempts,
            "fixed_errors": self.fixed_errors,
            "remaining_errors": self.remaining_errors,
            "is_clean": self.is_clean,
            "duration_seconds": round(self.duration_seconds, 4),
        }


class SelfCorrectionEngine:
    """
    Validates modified files immediately upon agent generation and orchestrates localized repair iterations.
    """

    @classmethod
    def inspect_and_validate(
        cls,
        workspace: Any,
        modified_files: List[str],
        allowed_paths: Optional[List[str]] = None,
        forbidden_paths: Optional[List[str]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Inspects all modified files for syntax errors, formatting defects, and contract compliance.
        Returns (is_clean, error_messages).
        """
        if not modified_files or not workspace:
            return True, []

        errors: List[str] = []
        root = Path(getattr(workspace, "root_dir", "."))

        for rel_fp in modified_files:
            clean_fp = rel_fp.replace("\\", "/").strip("/")
            full_path = root / clean_fp
            if not full_path.exists():
                errors.append(f"File '{clean_fp}' does not exist on disk after modification.")
                continue

            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                errors.append(f"Could not read file '{clean_fp}': {str(e)}")
                continue

            # Run ArtifactValidator
            val_res = ArtifactValidator.validate_file_deliverable(
                workspace=workspace,
                filepath=clean_fp,
                content=content,
                is_new_file=False,
                allowed_paths=allowed_paths,
                forbidden_paths=forbidden_paths,
                verify_disk=True,
            )
            if not val_res.is_valid:
                for err in val_res.errors:
                    errors.append(f"Validation failure in '{clean_fp}': {err}")

            # Run Python AST syntax check for Python files
            if clean_fp.endswith(".py"):
                import ast
                try:
                    ast.parse(content, filename=clean_fp)
                except SyntaxError as se:
                    errors.append(
                        f"Python SyntaxError in '{clean_fp}' at Line {se.lineno}, Col {se.offset}: {se.msg}"
                    )
                except Exception as ex:
                    errors.append(f"Parse error in '{clean_fp}': {str(ex)}")

            # Check for JSON syntax in JSON files
            elif clean_fp.endswith(".json"):
                try:
                    json.loads(content)
                except json.JSONDecodeError as jde:
                    errors.append(
                        f"JSON syntax error in '{clean_fp}' at Line {jde.lineno}, Col {jde.colno}: {jde.msg}"
                    )

        return len(errors) == 0, errors

    @classmethod
    def run_repair_loop(
        cls,
        agent: Any,
        state: Any,
        modified_files: List[str],
        max_attempts: int = 2,
        task_info: Optional[Dict[str, Any]] = None,
        permissions: Optional[Any] = None,
        workspace: Optional[Any] = None,
        **kwargs: Any,
    ) -> SelfCorrectionReport:
        """
        Executes localized self-correction iterations until all modified files pass validation or max_attempts is reached.
        """
        t_start = time.time()
        target_ws = workspace or getattr(agent, "workspace", None)
        allowed_writes = getattr(permissions, "allowed_write_paths", None)
        if isinstance(permissions, dict):
            allowed_writes = permissions.get("allowed_write_paths")
        forbidden_writes = getattr(permissions, "forbidden_write_paths", None)
        if isinstance(permissions, dict):
            forbidden_writes = permissions.get("forbidden_write_paths")

        is_clean, initial_errors = cls.inspect_and_validate(
            workspace=target_ws,
            modified_files=modified_files,
            allowed_paths=allowed_writes,
            forbidden_paths=forbidden_writes,
        )

        report = SelfCorrectionReport(
            initial_errors=list(initial_errors),
            attempts_made=0,
            max_attempts=max_attempts,
            fixed_errors=[],
            remaining_errors=list(initial_errors),
            is_clean=is_clean,
            duration_seconds=0.0,
        )

        if is_clean or max_attempts <= 0:
            report.duration_seconds = time.time() - t_start
            return report

        current_errors = list(initial_errors)
        attempt = 0

        while attempt < max_attempts and current_errors:
            attempt += 1
            report.attempts_made = attempt

            repair_prompt = f"""### Automated Self-Correction Required (Attempt {attempt}/{max_attempts})
The files you just created or modified contain syntax or validation errors that must be corrected before concluding:

{chr(10).join(f'• {err}' for err in current_errors)}

Instructions for Self-Repair:
1. Inspect the affected lines in the files using `read_file`.
2. Apply surgical delta fixes using `replace_file_content`, `insert_lines`, `delete_lines`, or `write_file`.
3. Verify syntax using `ast_syntax_check`.
4. Call `complete_task` when the syntax errors have been resolved."""

            agent_tools = [t.name for t in agent.tool_registry.get_tools_for_agent(agent.name)]
            repair_result = agent.react_loop.run(
                system_prompt=agent.build_system_prompt(),
                user_prompt=repair_prompt,
                model=agent.model,
                available_tools=agent_tools,
                agent_name=agent.name,
                permissions=permissions,
                max_turns=kwargs.get("max_turns", 5),
                workspace=target_ws,
                task_info=task_info,
            )

            # Re-inspect all modified files
            re_clean, remaining_errors = cls.inspect_and_validate(
                workspace=target_ws,
                modified_files=modified_files,
                allowed_paths=allowed_writes,
                forbidden_paths=forbidden_writes,
            )

            if re_clean:
                report.fixed_errors = list(initial_errors)
                report.remaining_errors = []
                report.is_clean = True
                break
            else:
                fixed = [e for e in current_errors if e not in remaining_errors]
                report.fixed_errors.extend(fixed)
                current_errors = remaining_errors
                report.remaining_errors = remaining_errors

        report.duration_seconds = time.time() - t_start
        return report
