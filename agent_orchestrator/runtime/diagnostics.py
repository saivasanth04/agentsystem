"""
Root-cause failure diagnosis engine. Analyzes test errors, stack traces, linter outputs,
and generates structured patch plans for the orchestrator.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple
if TYPE_CHECKING:
    from ..llm import LLMClient
from .fault_localization import EmpiricalFaultLocalizer, AdversarialAttributionArbiter, FaultLocus


@dataclass
class DiagnosticReport:
    root_cause_summary: str
    failure_type: str  # SYNTAX_ERROR | IMPORT_ERROR | ASSERTION_FAILURE | RUNTIME_CRASH | SPEC_MISMATCH | INFRASTRUCTURE_ERROR | CONTRACT_MISMATCH
    affected_files: List[str]
    suggested_remediation: List[str]
    target_agent: str
    should_rollback: bool = False
    rollback_target: Optional[str] = None
    regression_detected: bool = False
    reasons_for_rollback: List[str] = field(default_factory=list)
    invalid_assumptions: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    invalid_task_ids: List[str] = field(default_factory=list)
    pruned_task_ids: List[str] = field(default_factory=list)
    remediation_tasks: List[Dict[str, Any]] = field(default_factory=list)
    empirical_locus: Optional[Dict[str, Any]] = None
    attribution_override: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_cause_summary": self.root_cause_summary,
            "failure_type": self.failure_type,
            "affected_files": list(self.affected_files),
            "suggested_remediation": list(self.suggested_remediation),
            "target_agent": self.target_agent,
            "should_rollback": self.should_rollback,
            "rollback_target": self.rollback_target,
            "regression_detected": self.regression_detected,
            "reasons_for_rollback": list(self.reasons_for_rollback),
            "invalid_assumptions": list(self.invalid_assumptions),
            "missing_evidence": list(self.missing_evidence),
            "invalid_task_ids": list(self.invalid_task_ids),
            "pruned_task_ids": list(self.pruned_task_ids),
            "remediation_tasks": list(self.remediation_tasks),
            "empirical_locus": self.empirical_locus,
            "attribution_override": self.attribution_override,
        }


class FailureDiagnostician:
    """
    Diagnoses test or execution failures, evaluates regressions, and determines
    whether changes made things worse and should be transactionally rolled back.
    Distinguishes workspace/domain defects from infrastructure/contract mismatches.
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def diagnose_failure(
        self,
        task_title: str,
        execution_stderr: str,
        execution_stdout: str,
        codebase_context: str = "",
        model: Optional[str] = None,
        baseline_test_info: Optional[Dict[str, Any]] = None,
        current_test_info: Optional[Dict[str, Any]] = None,
        review_info: Optional[Dict[str, Any]] = None,
        change_manifest: Optional[Dict[str, Any]] = None,
        checkpoint_id: Optional[str] = None,
        default_rollback_target: Optional[str] = None,
        failed_tasks: Optional[List[Any]] = None,
        verification_reports: Optional[List[Dict[str, Any]]] = None,
        workspace_dir: Optional[str] = None,
    ) -> DiagnosticReport:
        effective_checkpoint_id = checkpoint_id or default_rollback_target
        review_text = json.dumps(review_info or {}, indent=2)
        baseline_text = json.dumps(baseline_test_info or {}, indent=2)
        changes_text = json.dumps(change_manifest or {}, indent=2)
        combined_err = f"{execution_stderr}\n{review_text}".lower()

        # Empirical Fault Localization (Deterministic Analysis)
        empirical_locus = EmpiricalFaultLocalizer.localize_fault(
            execution_stderr=execution_stderr,
            execution_stdout=execution_stdout,
            failed_tasks=failed_tasks,
            verification_reports=verification_reports,
            workspace_dir=workspace_dir,
            review_info=review_info,
        )

        # Deterministic Gate for MISSING_DEPENDENCY (Issue #64)
        if empirical_locus.locus_type == "MISSING_DEPENDENCY":
            dep = getattr(empirical_locus.parsed_failure, "missing_dependency", None)
            pkg_name = getattr(dep, "package_name", "unknown") if dep else "unknown"
            return DiagnosticReport(
                root_cause_summary=f"Missing dependency: package '{pkg_name}' is not installed in the environment.",
                failure_type="MISSING_DEPENDENCY",
                affected_files=[],
                suggested_remediation=[
                    f"Install missing package '{pkg_name}' via active package manager.",
                    "Re-run verification after dependency installation.",
                    "DO NOT modify source code or remove valid imports.",
                ],
                target_agent="DEPENDENCY_MANAGER",
                should_rollback=False,
                rollback_target=None,
                regression_detected=False,
                reasons_for_rollback=[],
            )

        # 1. Deterministic Heuristic Gate for CONTRACT_MISMATCH & INFRASTRUCTURE_ERROR
        # Prevents LLM misclassification and prevents routing internal framework crashes to CODER
        if "unexpected keyword argument" in combined_err or "got an unexpected keyword argument" in combined_err:
            return DiagnosticReport(
                root_cause_summary=f"Constructor/caller contract mismatch: {execution_stderr[:200] or 'Unexpected keyword argument in agent initialization'}",
                failure_type="CONTRACT_MISMATCH",
                affected_files=[],
                suggested_remediation=[
                    "Inspect caller and constructor contracts.",
                    "Reconcile parameter signatures at source using signature introspection.",
                    "STOP workspace code remediation to prevent infinite loop.",
                ],
                target_agent="SYSTEM",
                should_rollback=False,
                rollback_target=None,
                regression_detected=False,
                reasons_for_rollback=[],
            )

        if any(term in combined_err for term in ["agentregistry", "taskdag", "concurrentdagscheduler", "sqlite3.operationalerror"]):
            if "typeerror" in combined_err or "attributeerror" in combined_err or "keyerror" in combined_err:
                return DiagnosticReport(
                    root_cause_summary=f"Infrastructure framework error: {execution_stderr[:200] or 'Internal runtime component failure'}",
                    failure_type="INFRASTRUCTURE_ERROR",
                    affected_files=[],
                    suggested_remediation=[
                        "Inspect orchestrator runtime logs and component interfaces.",
                        "Reconcile framework component configurations.",
                        "STOP workspace code remediation.",
                    ],
                    target_agent="SYSTEM",
                    should_rollback=False,
                    rollback_target=None,
                    regression_detected=False,
                    reasons_for_rollback=[],
                )

        prompt = f"""
Analyze the following execution failure and assess if the recent changes caused a severe regression or made things worse:
Task: '{task_title}'

STDOUT:
{execution_stdout}

STDERR / Stack Trace:
{execution_stderr}

Baseline Test State (Prior to changes):
{baseline_text}

Reviewer Evaluation:
{review_text}

Changes Made:
{changes_text}

Codebase Symbol Map & Context:
{codebase_context}

Empirical Ground-Truth Fault Localization:
- Primary Defect File: {empirical_locus.primary_file or 'N/A'}
- Defect Locus: {empirical_locus.locus_type}
- Ground Truth Attribution: {empirical_locus.ground_truth_attribution} (Confidence: {empirical_locus.confidence:.2f})
- Empirical Rationale: {empirical_locus.rationale}

Please perform a root-cause diagnosis and evaluate whether the system should ROLLBACK the workspace to a clean baseline checkpoint before re-attempting.
Rollback SHOULD be recommended if:
1. The changes introduced catastrophic breakage, multiple syntax/import errors across modules, or broke previously passing tests.
2. The Reviewer explicitly noted that changes made things worse or broke architecture.
3. Patching forward on the corrupted code is riskier than reverting to baseline.

Provide your answer in JSON format conforming strictly to:
{{
  "root_cause_summary": "Precise explanation of what broke and why",
  "failure_type": "SYNTAX_ERROR | IMPORT_ERROR | ASSERTION_FAILURE | RUNTIME_CRASH | SPEC_MISMATCH | INFRASTRUCTURE_ERROR | CONTRACT_MISMATCH",
  "affected_files": ["list of filenames that need changes"],
  "suggested_remediation": ["Step 1...", "Step 2..."],
  "target_agent": "CODER | SPECIFICATION | ARCHITECTURE | TESTER | SYSTEM",
  "regression_detected": true,
  "should_rollback": true,
  "reasons_for_rollback": ["Why reverting to baseline is required"],
  "invalid_assumptions": ["Underlying assumption proven wrong (e.g. assumed synchronous API, assumed library X was installed)"],
  "missing_evidence": ["Evidence/specifications/contracts needed before retrying"],
  "invalid_task_ids": ["Downstream task IDs in the DAG that are now invalid or need re-planning"],
  "pruned_task_ids": ["Downstream task IDs in the DAG that are obsolete or redundant"],
  "remediation_tasks": [
    {{
      "task_id": "T-FIX-1",
      "objective": "Targeted fix goal",
      "dependencies": [],
      "required_capabilities": ["code-generation"],
      "required_tools": ["filesystem", "terminal"],
      "inputs": ["file.py"],
      "outputs": ["file.py"],
      "acceptance_tests": ["pytest tests/test_file.py"]
    }}
  ]
}}
Respond ONLY with the JSON object.
"""
        messages = [
            {"role": "system", "content": "You are the Root Cause Failure Diagnostician and Transaction Safety Auditor in an autonomous agent runtime."},
            {"role": "user", "content": prompt},
        ]
        result = {}
        if self.llm:
            try:
                result = self.llm.chat_json(messages, model=model, temperature=0.1)
            except Exception:
                result = {}

        if not result or not isinstance(result, dict) or result.get("_parse_error") or result.get("parse_error"):
            if "unexpected keyword argument" in combined_err:
                f_type = "CONTRACT_MISMATCH"
                t_agent = "SYSTEM"
            elif "syntaxerror" in execution_stderr.lower():
                f_type = "SYNTAX_ERROR"
                t_agent = "CODER"
            else:
                f_type = "RUNTIME_CRASH"
                t_agent = "CODER"

            result = {
                "root_cause_summary": f"Execution failure during '{task_title}': {execution_stderr[:100] or 'Review failed'}",
                "failure_type": f_type,
                "affected_files": [],
                "suggested_remediation": ["Review error logs and fix offending syntax or tests."],
                "target_agent": t_agent,
                "regression_detected": False,
                "should_rollback": False,
                "reasons_for_rollback": [],
                "invalid_assumptions": [],
                "missing_evidence": [],
                "invalid_task_ids": [],
                "pruned_task_ids": [],
                "remediation_tasks": [],
            }

        # Heuristic regression & rollback checks to safeguard against LLM false negatives
        regression = bool(result.get("regression_detected", False))
        should_rb = bool(result.get("should_rollback", False))
        reasons = list(result.get("reasons_for_rollback") or [])

        # Check explicit reviewer feedback for regression signals
        if review_info:
            r_str = str(review_info).lower()
            if any(term in r_str for term in ["made things worse", "worse", "regression", "rollback", "revert"]):
                should_rb = True
                regression = True
                if "Reviewer indicated changes made things worse or requested rollback." not in reasons:
                    reasons.append("Reviewer indicated changes made things worse or requested rollback.")

        # Check if baseline was passing and current is failing
        if baseline_test_info and current_test_info:
            base_passed = baseline_test_info.get("passed", 0)
            base_failed = baseline_test_info.get("failed", 0)
            curr_passed = current_test_info.get("passed", 0)
            curr_failed = current_test_info.get("failed", 0)
            if (base_passed > 0 and base_failed == 0 and curr_failed > 0) or (
                baseline_test_info.get("execution_success") is True and current_test_info.get("execution_success") is False
            ):
                regression = True
                should_rb = True
                reason_msg = f"Automated tests previously passed ({base_passed} passed) but have failed ({curr_failed} failed) after changes; test regression detected."
                if reason_msg not in reasons:
                    reasons.append(reason_msg)

        # Adversarial Arbitration: Ground LLM attribution against empirical evidence
        llm_target = result.get("target_agent", "CODER")
        final_target_agent, was_overridden, arb_rationale = AdversarialAttributionArbiter.arbitrate(
            llm_suggested_agent=llm_target,
            empirical_locus=empirical_locus,
        )
        override_info = None
        if was_overridden:
            override_info = {
                "original_llm_target": llm_target,
                "overridden_to": final_target_agent,
                "rationale": arb_rationale,
                "timestamp": datetime.now().isoformat(),
            }

        return DiagnosticReport(
            root_cause_summary=result.get("root_cause_summary", "Unspecified execution failure"),
            failure_type=result.get("failure_type", "RUNTIME_CRASH"),
            affected_files=result.get("affected_files", []),
            suggested_remediation=result.get("suggested_remediation", ["Fix issues in workspace code"]),
            target_agent=final_target_agent.upper(),
            should_rollback=should_rb,
            rollback_target=effective_checkpoint_id if should_rb else None,
            regression_detected=regression,
            reasons_for_rollback=reasons,
            invalid_assumptions=list(result.get("invalid_assumptions") or []),
            missing_evidence=list(result.get("missing_evidence") or []),
            invalid_task_ids=list(result.get("invalid_task_ids") or []),
            pruned_task_ids=list(result.get("pruned_task_ids") or []),
            remediation_tasks=list(result.get("remediation_tasks") or []),
            empirical_locus=empirical_locus.to_dict(),
            attribution_override=override_info,
        )
