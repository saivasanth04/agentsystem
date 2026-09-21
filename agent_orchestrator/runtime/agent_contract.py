"""
Inter-Agent Contract Specification and Enforcement Engine.
Enforces architectural and planning agreements between agents:
- Prevents unauthorized file substitutions (e.g. users.py -> account.py).
- Enforces presence of required files and AST symbols (classes, functions).
- Provides actionable self-correction feedback for the ReAct execution loop.
"""

from __future__ import annotations
import ast
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Union


@dataclass
class InterAgentContract:
    """Explicit deliverable contract between an upstream and downstream agent."""
    source_agent: str = "ARCHITECTURE"
    target_agent: str = "CODER"
    task_id: Optional[str] = None
    required_files: List[str] = field(default_factory=list)
    required_symbols: Dict[str, List[str]] = field(default_factory=dict)
    forbidden_files: List[str] = field(default_factory=list)
    strict_file_matching: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "task_id": self.task_id,
            "required_files": self.required_files,
            "required_symbols": self.required_symbols,
            "forbidden_files": self.forbidden_files,
            "strict_file_matching": self.strict_file_matching,
        }

    @classmethod
    def from_architecture_and_task(
        cls,
        arch_data: Any,
        task: Any,
    ) -> Optional[InterAgentContract]:
        """
        Automatically derive an InterAgentContract from an ArchitectureContract
        and an ExecutableTask.
        """
        task_id = getattr(task, "task_id", None) or (task.get("task_id") if isinstance(task, dict) else None)
        task_outputs = getattr(task, "outputs", []) or (task.get("outputs") if isinstance(task, dict) else [])
        task_obj = getattr(task, "objective", "") or (task.get("objective") if isinstance(task, dict) else "")
        task_desc = getattr(task, "description", "") or (task.get("description") if isinstance(task, dict) else "")

        required_files: List[str] = []
        required_symbols: Dict[str, List[str]] = {}

        # 1. Parse from task.outputs (e.g. "src/users.py:UserManager")
        for out in task_outputs:
            if not out:
                continue
            out_str = str(out).strip()
            if ":" in out_str:
                f_part, sym_part = out_str.split(":", 1)
                f_part = f_part.strip()
                sym_part = sym_part.strip()
                if f_part and f_part not in required_files:
                    required_files.append(f_part)
                if f_part and sym_part:
                    required_symbols.setdefault(f_part, []).append(sym_part)
            else:
                if out_str not in required_files:
                    required_files.append(out_str)

        # 2. Reconcile with ArchitectureContract
        if arch_data:
            arch_dict = arch_data if isinstance(arch_data, dict) else (
                arch_data.model_dump() if hasattr(arch_data, "model_dump") else getattr(arch_data, "__dict__", {})
            )
            components = arch_dict.get("component_structure", [])
            for comp in components:
                if not isinstance(comp, dict):
                    continue
                mod_name = comp.get("module_name", "")
                purpose = comp.get("purpose", "")
                classes_or_fns = comp.get("classes_or_functions", [])

                # Check if this component matches the task objective, description, or outputs
                task_text = f"{task_id} {task_obj} {task_desc} {' '.join(required_files)}".lower()
                clean_mod = mod_name.replace("src/", "").replace(".py", "").lower()

                if (
                    mod_name.lower() in task_text
                    or clean_mod in task_text
                    or any(clean_mod in f.lower() for f in required_files)
                ):
                    if mod_name not in required_files:
                        required_files.append(mod_name)
                    for item in classes_or_fns:
                        sym_name = item.get("name") if isinstance(item, dict) else str(item)
                        if sym_name:
                            required_symbols.setdefault(mod_name, []).append(sym_name)

        if not required_files and not required_symbols:
            return None

        return cls(
            source_agent="ARCHITECTURE",
            target_agent="CODER",
            task_id=task_id,
            required_files=required_files,
            required_symbols=required_symbols,
            strict_file_matching=True,
        )


@dataclass
class ContractEnforcementReport:
    """Audit report of contract compliance."""
    is_valid: bool
    contract_name: str
    violations: List[str] = field(default_factory=list)
    satisfied_files: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    missing_symbols: List[str] = field(default_factory=list)
    unauthorized_files: List[str] = field(default_factory=list)
    feedback_message: str = ""

    def summary(self) -> str:
        status = "PASSED" if self.is_valid else "VIOLATED"
        return (
            f"[ContractEnforcement] {status} - "
            f"Satisfied Files: {len(self.satisfied_files)}, Missing: {len(self.missing_files)}, "
            f"Violations: {len(self.violations)}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "contract_name": self.contract_name,
            "violations": self.violations,
            "satisfied_files": self.satisfied_files,
            "missing_files": self.missing_files,
            "missing_symbols": self.missing_symbols,
            "unauthorized_files": self.unauthorized_files,
            "feedback_message": self.feedback_message,
            "summary": self.summary(),
        }


class AgentContractEnforcer:
    """
    Validates candidate deliverables against InterAgentContract specifications.
    """

    @classmethod
    def _normalize_path(cls, p: str) -> str:
        return p.replace("\\", "/").strip().lstrip("./")

    @classmethod
    def validate_deliverable(
        cls,
        contract: InterAgentContract,
        candidate_deliverable: Any,
        workspace: Optional[Any] = None,
    ) -> ContractEnforcementReport:
        """
        Validate that the candidate deliverable conforms to the required files,
        symbols, and constraints in the contract.
        """
        if not contract:
            return ContractEnforcementReport(is_valid=True, contract_name="None")

        # Extract written files from candidate
        written_files_raw: List[str] = []
        if isinstance(candidate_deliverable, dict):
            written_files_raw = candidate_deliverable.get("written_files", [])
        elif hasattr(candidate_deliverable, "written_files"):
            written_files_raw = getattr(candidate_deliverable, "written_files", [])

        # Also inspect workspace uncommitted changes if available
        if not written_files_raw and workspace and hasattr(workspace, "get_uncommitted_changes"):
            try:
                uncommitted = workspace.get_uncommitted_changes()
                written_files_raw = uncommitted.get("created", []) + uncommitted.get("modified", [])
            except Exception:
                pass

        normalized_written = [cls._normalize_path(f) for f in written_files_raw]

        # Gather workspace files if available
        workspace_files: List[str] = []
        if workspace and hasattr(workspace, "list_files"):
            try:
                workspace_files = [cls._normalize_path(f) for f in workspace.list_files()]
            except Exception:
                workspace_files = []

        violations: List[str] = []
        satisfied_files: List[str] = []
        missing_files: List[str] = []
        missing_symbols: List[str] = []
        unauthorized_files: List[str] = []

        # 1. Check required files
        for req_f in contract.required_files:
            norm_req = cls._normalize_path(req_f)
            # Check if file was written or exists in workspace
            in_written = any(
                nw == norm_req or nw.endswith(norm_req) or norm_req.endswith(nw)
                for nw in normalized_written
            )
            in_workspace = any(
                wf == norm_req or wf.endswith(norm_req) or norm_req.endswith(wf)
                for wf in workspace_files
            )

            if in_written or in_workspace:
                satisfied_files.append(req_f)
            else:
                missing_files.append(req_f)
                violations.append(
                    f"Contract Violation: Required file '{req_f}' was not created or modified."
                )

        # 2. Check unauthorized file substitutions
        if contract.strict_file_matching and missing_files and normalized_written:
            for w_file in normalized_written:
                # Is this written file part of the contract?
                is_authorized = any(
                    cls._normalize_path(req) in w_file or w_file in cls._normalize_path(req)
                    for req in contract.required_files
                )
                if not is_authorized and not w_file.startswith("tests/") and not "test_" in w_file:
                    unauthorized_files.append(w_file)
                    violations.append(
                        f"Contract Violation: Substituted unauthorized file '{w_file}' "
                        f"for required file(s) {contract.required_files}."
                    )

        # 3. Check forbidden files
        for forb_f in contract.forbidden_files:
            norm_forb = cls._normalize_path(forb_f)
            for w_file in normalized_written:
                if norm_forb in w_file:
                    violations.append(f"Contract Violation: Modified forbidden file '{forb_f}'.")

        # 4. Check required symbols via AST inspection
        if contract.required_symbols and workspace and hasattr(workspace, "read_file"):
            for file_key, symbols in contract.required_symbols.items():
                norm_key = cls._normalize_path(file_key)
                target_file = None
                for wf in workspace_files:
                    if wf == norm_key or wf.endswith(norm_key):
                        target_file = wf
                        break

                if not target_file:
                    continue

                if target_file.endswith(".py") or file_key.endswith(".py"):
                    try:
                        read_res = workspace.read_file(target_file)
                        content = read_res.get("content", "") if isinstance(read_res, dict) else str(read_res or "")
                        if content:
                            tree = ast.parse(content)
                            defined_symbols = set()
                            for node in ast.walk(tree):
                                if isinstance(node, ast.ClassDef):
                                    defined_symbols.add(node.name)
                                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                    defined_symbols.add(node.name)

                            for sym in symbols:
                                if sym not in defined_symbols:
                                    missing_symbols.append(f"{file_key}:{sym}")
                                    violations.append(
                                        f"Contract Violation: Required symbol '{sym}' was not defined in '{file_key}'."
                                    )
                    except Exception:
                        pass

        is_valid = len(violations) == 0
        feedback_msg = ""
        if not is_valid:
            feedback_msg = cls.format_feedback(violations, contract)

        return ContractEnforcementReport(
            is_valid=is_valid,
            contract_name=f"{contract.source_agent}->{contract.target_agent}",
            violations=violations,
            satisfied_files=satisfied_files,
            missing_files=missing_files,
            missing_symbols=missing_symbols,
            unauthorized_files=unauthorized_files,
            feedback_message=feedback_msg,
        )

    @classmethod
    def format_feedback(cls, violations: List[str], contract: InterAgentContract) -> str:
        """Format an actionable prompt message for LLM in-loop self-correction."""
        violation_list = "\n".join(f"- {v}" for v in violations)
        return (
            f"Inter-Agent Contract Violation for [{contract.source_agent} -> {contract.target_agent}]:\n"
            f"Your deliverable fails the agreed architectural contract with the following violation(s):\n"
            f"{violation_list}\n\n"
            f"Required Files: {contract.required_files}\n"
            f"Required Symbols: {contract.required_symbols}\n"
            f"Please modify your implementation to satisfy the required contract files and symbols."
        )
