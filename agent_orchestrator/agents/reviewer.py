"""
Reviewer Agent: Performs code review, security audits (semgrep/git diff), and root-cause failure analysis.
"""
import json
from typing import Any, Dict, List, Optional, Union
from .base import BaseAgent
from ..state import OrchestratorState, ReviewVerdict
from ..runtime.diagnostics import FailureDiagnostician


class ReviewerAgent(BaseAgent):
    def __init__(
        self,
        model: str = None,
        llm=None,
        workspace=None,
        tool_registry=None,
        skill_registry=None,
        mcp_client=None,
        message_bus=None,
        approval_gate=None,
        **kwargs: Any,
    ):
        super().__init__(
            name="REVIEWER",
            role_description="Responsible for code-review, static security audit, verifying test results against acceptance criteria, and root-cause failure analysis.",
            model=model,
            llm=llm,
            workspace=workspace,
            tool_registry=tool_registry,
            skill_registry=skill_registry,
            mcp_client=mcp_client,
            message_bus=message_bus,
            approval_gate=approval_gate,
            **kwargs,
        )
        self.diagnostician = FailureDiagnostician(self.llm)

    def review(self, state: Any, **kwargs) -> Dict[str, Any]:
        """Convenience evaluation method supporting OrchestratorState or dictionary state."""
        return self.execute(state, **kwargs)

    def execute(self, state: Union[OrchestratorState, Dict[str, Any]], active_skills: Optional[List[str]] = None, **kwargs) -> Dict[str, Any]:
        if isinstance(state, dict) or state is None:
            dict_state = state or {}
            req = dict_state.get("user_request") or dict_state.get("goal") or "Review code changes"
            state_obj = OrchestratorState(user_request=req)
            for k, v in dict_state.items():
                if hasattr(state_obj, k):
                    setattr(state_obj, k, v)
            if "coder_output" in dict_state and not state_obj.code_output:
                state_obj.code_output = dict_state["coder_output"]
            state = state_obj
        task_info = kwargs.get("task_info") or {}
        test_info = state.test_output or {}
        code_info = state.code_output or {}

        # Format change manifest if present in code_info or workspace
        change_manifest = code_info.get("change_manifest")
        if not change_manifest and hasattr(self.workspace, "get_change_manifest"):
            m = self.workspace.get_change_manifest()
            if m:
                change_manifest = m.to_dict() if hasattr(m, "to_dict") else m

        # Multi-Level Retrieval Hierarchy: Level 1 (changed files) -> Level 2 (impacted dependencies) -> Level 4 (architecture rules) -> Level 5 (repo map)
        from ..context.retrieval_hierarchy import RetrievalHierarchyEngine, HierarchyBudgetConfig, RetrievalLevel
        hierarchy_engine = RetrievalHierarchyEngine(
            workspace=self.workspace,
            code_graph=getattr(self.tool_registry, "code_graph", None),
            semantic_index=getattr(self.tool_registry, "semantic_index", None),
            arch_analyzer=getattr(self.tool_registry, "arch_analyzer", None),
            cbm=getattr(self.tool_registry, "cbm", None),
            fallback_engine=getattr(self.tool_registry, "fallback_engine", None),
        )
        target_mod_files = list(code_info.get("written_files") or [])
        if change_manifest and isinstance(change_manifest, dict):
            target_mod_files.extend(change_manifest.get("modified_files", []))
            target_mod_files.extend(change_manifest.get("created_files", []))
        hierarchy_bundle = hierarchy_engine.retrieve_hierarchy(
            task_info=task_info,
            target_files=target_mod_files,
            budget_config=HierarchyBudgetConfig(total_budget=6000),
            include_levels=[
                RetrievalLevel.LEVEL_1_EXACT,
                RetrievalLevel.LEVEL_2_DEPENDENCY_GRAPH,
                RetrievalLevel.LEVEL_4_ARCHITECTURE_KNOWLEDGE,
                RetrievalLevel.LEVEL_5_BROADER_REPOSITORY,
            ],
        )
        review_context_hierarchy = hierarchy_bundle.to_markdown()

        change_details_parts = []
        if change_manifest and isinstance(change_manifest, dict):
            change_details_parts.append(f"Change Summary: {change_manifest.get('summary', 'N/A')}")
            change_details_parts.append(f"Lines Added: {change_manifest.get('total_lines_added', 0)}, Lines Removed: {change_manifest.get('total_lines_removed', 0)}")
            change_details_parts.append(f"Created Files: {change_manifest.get('created_files', [])}")
            change_details_parts.append(f"Modified Files: {change_manifest.get('modified_files', [])}")
            change_details_parts.append(f"Deleted Files: {change_manifest.get('deleted_files', [])}")

            f_changes = change_manifest.get("file_changes", {})
            if isinstance(f_changes, dict) and f_changes:
                change_details_parts.append("\nDetailed Per-File Diffs & Symbol Changes:")
                for fp, fc in f_changes.items():
                    c_type = fc.get("change_type", "MODIFIED")
                    b_hash = fc.get("before_hash")
                    a_hash = fc.get("after_hash")
                    b_str = b_hash[:8] if b_hash else "None"
                    a_str = a_hash[:8] if a_hash else "None"
                    change_details_parts.append(f"\n--- {c_type}: {fp} (Before SHA: {b_str} -> After SHA: {a_str}) ---")

                    syms = fc.get("changed_symbols", [])
                    if syms:
                        sym_summaries = [f"{s.get('change_type')}: {s.get('kind')} {s.get('name')}" for s in syms]
                        change_details_parts.append(f"Changed Symbols: {', '.join(sym_summaries)}")

                    diff_text = fc.get("diff", "")
                    if diff_text:
                        diff_lines = diff_text.splitlines()
                        if len(diff_lines) > 100:
                            diff_truncated = "\n".join(diff_lines[:100]) + f"\n... [Diff truncated, total {len(diff_lines)} lines] ..."
                        else:
                            diff_truncated = diff_text
                        change_details_parts.append(f"```diff\n{diff_truncated}\n```")
            change_details_str = "\n".join(change_details_parts)
        else:
            change_details_str = f"Written Files: {code_info.get('written_files', [])}\nSummary: {code_info.get('summary', '')}"

        # Run static verification checks
        static_check_result = {}
        if self.tool_registry:
            try:
                raw_sc = self.tool_registry.call_tool("static_code_check", {})
                if isinstance(raw_sc, dict) and "output" in raw_sc and isinstance(raw_sc["output"], dict):
                    static_check_result = raw_sc["output"]
                elif isinstance(raw_sc, dict):
                    static_check_result = raw_sc
            except Exception:
                pass
        if not static_check_result and hasattr(self.workspace, "root_dir"):
            try:
                from ..runtime.static_verifier import StaticVerificationEngine
                engine = StaticVerificationEngine(workspace_dir=self.workspace.root_dir)
                static_check_result = engine.verify().to_dict()
            except Exception:
                pass

        static_passed = True
        static_summary_parts = []
        if isinstance(static_check_result, dict) and static_check_result:
            static_passed = bool(static_check_result.get("passed", True))
            status_label = "PASSED" if static_passed else "FAILED"
            static_summary_parts.append(f"Static Analysis Status: {status_label}")
            all_errs = []
            for tier_name in ["syntax_errors", "compiler_errors", "type_errors", "linter_issues", "linter_errors", "import_errors", "errors"]:
                tier_errs = static_check_result.get(tier_name, [])
                if isinstance(tier_errs, list):
                    all_errs.extend(tier_errs)
            if all_errs:
                static_summary_parts.append(f"Errors Found ({len(all_errs)}):")
                for e in all_errs[:10]:
                    msg = e.get("message") if isinstance(e, dict) else str(e)
                    fp = e.get("file_path", "") if isinstance(e, dict) else ""
                    tier = e.get("source_tool") or e.get("tier", "") if isinstance(e, dict) else ""
                    prefix = f"[{tier}] " if tier else ""
                    static_summary_parts.append(f"  - {prefix}{fp}: {msg}")
            else:
                static_summary_parts.append("No static syntax, compiler, linter, or import errors detected.")

            static_warns = static_check_result.get("warnings", [])
            if static_warns:
                static_summary_parts.append(f"Warnings ({len(static_warns)}):")
                for w in static_warns[:5]:
                    msg = w.get("message") if isinstance(w, dict) else str(w)
                    fp = w.get("file_path", "") if isinstance(w, dict) else ""
                    tool = w.get("source_tool", "") if isinstance(w, dict) else ""
                    static_summary_parts.append(f"  - [{tool}] {fp}: {msg}")
        else:
            static_summary_parts.append("Static Analysis: Skipped or not applicable.")
        static_summary_str = "\n".join(static_summary_parts)

        # Extract tautological warnings from test output
        tautological_warnings = test_info.get("tautological_warnings", [])
        if tautological_warnings:
            tautology_summary = f"CRITICAL WARNING: Tautological/Trivial Assertions Detected ({len(tautological_warnings)}):\n" + "\n".join(f"  - {w}" for w in tautological_warnings)
        else:
            tautology_summary = "No tautological or trivial assertions detected in test suite."

        # Extract build verification pipeline report
        pipe_report = test_info.get("build_pipeline_report")
        if pipe_report and isinstance(pipe_report, dict) and not pipe_report.get("passed", True):
            pipe_summary_str = f"FAILED at stage '{pipe_report.get('failed_stage')}'. Details:\n{json.dumps(pipe_report.get('stage_results', {}), indent=2)}"
        elif pipe_report and isinstance(pipe_report, dict):
            pipe_summary_str = f"PASSED across all stages (Duration: {pipe_report.get('total_duration_seconds', 0)}s)."
        else:
            pipe_summary_str = "Build pipeline: not executed or single-stage test runner used."

        # Evaluate Deterministic Ground-Truth Verification Matrix
        from ..runtime.verification_matrix import GroundTruthVerificationMatrix
        gt_matrix = GroundTruthVerificationMatrix(
            workspace=self.workspace,
            sandbox=getattr(self.tool_registry, "sandbox", None),
        )
        target_mod_files = None
        if change_manifest and isinstance(change_manifest, dict):
            target_mod_files = list(change_manifest.get("created_files", [])) + list(change_manifest.get("modified_files", []))
        elif code_info.get("written_files"):
            target_mod_files = code_info.get("written_files")

        gt_report = gt_matrix.evaluate(
            state=state,
            modified_files=target_mod_files,
            static_report=static_check_result,
            build_pipeline_report=pipe_report,
        )

        try:
            from ..runtime.evidence import EvidenceSynthesizer
        except (ImportError, ValueError):
            from agent_orchestrator.runtime.evidence import EvidenceSynthesizer

        evidence = EvidenceSynthesizer.synthesize(
            state=state,
            ground_truth_report=gt_report,
            test_info=test_info,
            static_report=static_check_result,
            build_pipeline_report=pipe_report,
            workspace=self.workspace,
        )

        from ..context.budget_allocator import ContextBudget, ContextSection, ContextAssembler
        from ..context.isolation import ContextIsolationEngine
        from ..security.trust_boundaries import TrustLevel
        from ..config import config as orch_cfg

        if hasattr(self.tool_registry, "set_orchestrator_state"):
            self.tool_registry.set_orchestrator_state(state)

        isolated_ctx = ContextIsolationEngine.isolate_for_task(state, task_info=task_info)

        budget = ContextBudget(
            total_budget=getattr(orch_cfg, "context_budget_total", 32000),
        )
        assembler = ContextAssembler(budget)

        review_instructions = """Review Instructions & Evidence-Based Rubric:
1. Ground your review strictly in the Deterministic Verification Evidence matrix above.
2. If any evidence gate failed (build failed, tests failed, lint errors > 0, acceptance criteria unverified), verdict MUST be FAIL.
3. Cross-examine the acceptance criteria against actual implementations and tests.
4. Context Isolation: You receive an isolated context view. Query additional specification details via `query_specification` or component architecture via `query_architecture` if needed.
5. When finished, call `complete_task` or provide a structured review JSON:
{
  "verdict": "PASS | FAIL",
  "summary": "High level evaluation summary grounded in evidence",
  "strengths": ["string"],
  "issues": [
    {
      "severity": "CRITICAL | MAJOR | MINOR",
      "component": "string",
      "description": "string"
    }
  ],
  "target_agent_for_fix": "CODER | TESTER | SPECIFICATION | ARCHITECTURE",
  "remediation_plan": [
    "Step 1...", "Step 2..."
  ]
}"""

        test_results_str = f"Success: {test_info.get('execution_success')}\nExit Code: {test_info.get('exit_code')}\nStdout:\n{test_info.get('stdout')}\nStderr:\n{test_info.get('stderr')}"

        scoped_spec_str = json.dumps(isolated_ctx.scoped_specification if isolated_ctx.scoped_specification is not None else (state.specification_output or {}), indent=2, default=str) if (isolated_ctx.scoped_specification or state.specification_output) else ""
        scoped_arch_str = json.dumps(isolated_ctx.scoped_architecture if isolated_ctx.scoped_architecture is not None else (state.architecture_output or {}), indent=2, default=str) if (isolated_ctx.scoped_architecture or state.architecture_output) else ""

        sections = [
            ContextSection(name="user_request", title="User Request", content=str(state.user_request), priority=1, max_tokens=1500, is_essential=True, trust_level=TrustLevel.USER_INSTRUCTION),
            ContextSection(name="evidence", title="Verification Evidence & Deterministic Matrix", content=f"{evidence.to_markdown()}\n\n{gt_report.summary()}", priority=1, max_tokens=4000, is_essential=True, trust_level=TrustLevel.CONTROL_SYSTEM),
            ContextSection(name="diffs", title="Code Changes & Diff Analysis:", content=change_details_str, priority=2, max_tokens=6000, is_essential=False, trust_level=TrustLevel.UNTRUSTED_REPOSITORY),
            ContextSection(name="test_results", title="Test Execution Results & Anti-Tautology Checks", content=f"{test_results_str}\n\n{tautology_summary}", priority=2, max_tokens=4000, is_essential=False, trust_level=TrustLevel.TOOL_OBSERVATION),
            ContextSection(name="spec", title="Specification & Acceptance Criteria (Scoped)", content=scoped_spec_str, priority=3, max_tokens=2000, is_essential=False),
            ContextSection(name="static_checks", title="Static Verification & Health Checks", content=f"{pipe_summary_str}\n\n{static_summary_str}", priority=3, max_tokens=2000, is_essential=False, trust_level=TrustLevel.TOOL_OBSERVATION),
            ContextSection(name="architecture", title="Architecture Blueprint (Scoped)", content=scoped_arch_str, priority=4, max_tokens=1500, is_essential=False),
            ContextSection(name="hierarchical_context", title="Hierarchical Codebase Context (Dependencies & Boundaries)", content=review_context_hierarchy, priority=4, max_tokens=3000, is_essential=False, trust_level=TrustLevel.UNTRUSTED_REPOSITORY),
            ContextSection(name="instructions", title="Review Instructions", content=review_instructions, priority=1, max_tokens=1500, is_essential=True, trust_level=TrustLevel.CONTROL_SYSTEM),
        ]


        prompt = assembler.assemble(sections)
        system_prompt = self.build_system_prompt(active_skills=active_skills)
        from runtime.tool_policy import ToolPolicyEngine
        executable = ToolPolicyEngine.get_executable_tools(allowed_tools={"read_file", "list_directory", "git_diff", "git_status", "find_symbol", "complete_task"})
        reviewer_tools = [getattr(t, "name", str(t)) for t in executable]

        effective_model = kwargs.get("model") or self.model
        loop_result = self.execution_loop.run(
            system_prompt=system_prompt,
            user_prompt=prompt,
            model=effective_model,
            available_tools=reviewer_tools,
            agent_name=self.name,
            temperature=0.1,
            **{k: v for k, v in kwargs.items() if k not in ("model", "active_skills", "temperature")},
        )

        final_out = loop_result.get("final_output", {})
        result = final_out.get("deliverables", {}) if (isinstance(final_out, dict) and "deliverables" in final_out and final_out["deliverables"]) else final_out

        explicit_test_failure = (
            (test_info.get("execution_success") is False)
            or (test_info.get("exit_code", 0) != 0)
            or bool(test_info.get("tests_failed", 0) > 0)
        )
        has_tautologies = bool(tautological_warnings)
        static_failed = not static_passed
        build_pipeline_failed = bool(pipe_report and isinstance(pipe_report, dict) and not pipe_report.get("passed", True))

        if not isinstance(result, dict) or "verdict" not in result:
            if isinstance(final_out, dict) and "verdict" in final_out:
                result = final_out
            else:
                # Objective adversarial scoring when structured output is missing
                score = 100
                issues = []
                strengths = []
                target_agent = "CODER"
                remediation = []

                if build_pipeline_failed:
                    score -= 50
                    target_agent = "CODER"
                    failed_stg = pipe_report.get("failed_stage", "BUILD") if pipe_report else "BUILD"
                    issues.append({
                        "severity": "CRITICAL",
                        "component": "BuildPipeline",
                        "description": f"Build verification pipeline failed at stage '{failed_stg}'."
                    })
                    remediation.append(f"Resolve {failed_stg} errors and ensure project builds successfully.")

                if static_failed:
                    score -= 40
                    target_agent = "CODER"
                    issues.append({
                        "severity": "CRITICAL",
                        "component": "StaticVerification",
                        "description": "Static code checks (syntax/typecheck/lint/import) reported failures."
                    })
                    remediation.append("Fix syntax, compiler, or import errors before re-testing.")

                if explicit_test_failure:
                    score -= 40
                    target_agent = "CODER"
                    issues.append({
                        "severity": "CRITICAL",
                        "component": "TestSuite",
                        "description": test_info.get("stderr") or "Automated test execution failed."
                    })
                    remediation.append("Fix failing test assertions and execution errors.")
                elif test_info.get("execution_success"):
                    strengths.append("Automated test suite executed cleanly with zero exit code.")

                if has_tautologies:
                    score -= 35
                    target_agent = "TESTER"
                    for tw in tautological_warnings:
                        issues.append({
                            "severity": "MAJOR",
                            "component": "TestIntegrity",
                            "description": f"Tautological assertion: {tw}"
                        })
                    remediation.append("Rewrite test cases to verify genuine domain logic instead of trivial assertions.")

                score = max(0, min(100, score))
                verdict_str = "PASS" if (score >= 80 and not explicit_test_failure and not static_failed and not has_tautologies and not build_pipeline_failed) else "FAIL"


                result = {
                    "verdict": verdict_str,
                    "score_out_of_100": score,
                    "summary": f"Adversarial review audit completed. Verdict: {verdict_str}.",
                    "strengths": strengths,
                    "issues": issues,
                    "target_agent_for_fix": target_agent,
                    "remediation_plan": remediation,
                }

        # Adversarial Ground-Truth Veto: Never allow a rubber-stamp PASS if deterministic ground truth failed
        raw_verdict = str(result.get("verdict", "FAIL")).upper().strip()
        verdict_is_pass = "PASS" in raw_verdict
        ground_truth_failed = not gt_report.passed

        if (verdict_is_pass or result.get("verdict") == "PASS") and (ground_truth_failed or explicit_test_failure or has_tautologies or static_failed):
            verdict = ReviewVerdict.FAIL
            result["verdict"] = "FAIL"
            result["score_out_of_100"] = min(result.get("score_out_of_100", 90), 45)
            if "issues" not in result or not isinstance(result["issues"], list):
                result["issues"] = []

            for bf in gt_report.blocking_failures:
                result["issues"].append({
                    "severity": "CRITICAL",
                    "component": "GroundTruthMatrix",
                    "description": f"Deterministic Ground-Truth Gate Failure: {bf}",
                })

            if explicit_test_failure:
                result["issues"].append({
                    "severity": "CRITICAL",
                    "component": "TestSuite",
                    "description": "Adversarial override: Tests failed during automated execution."
                })
                result["target_agent_for_fix"] = "CODER"
            elif has_tautologies:
                result["issues"].append({
                    "severity": "MAJOR",
                    "component": "TestIntegrity",
                    "description": f"Adversarial override: {len(tautological_warnings)} tautological assertions found in test suite."
                })
                result["target_agent_for_fix"] = "TESTER"
            elif static_failed:
                result["issues"].append({
                    "severity": "CRITICAL",
                    "component": "StaticVerification",
                    "description": "Adversarial override: Static code checks failed."
                })
                result["target_agent_for_fix"] = "CODER"
            elif not gt_report.gates.get("SPEC_TRACEABILITY", None) or not gt_report.gates["SPEC_TRACEABILITY"].passed:
                result["target_agent_for_fix"] = "TESTER"
            else:
                result["target_agent_for_fix"] = "CODER"
        elif verdict_is_pass and not ground_truth_failed:
            verdict = ReviewVerdict.PASS
        else:
            verdict = ReviewVerdict.FAIL

        result["verdict"] = verdict.value
        result["evidence"] = evidence.to_dict()
        result["ground_truth_report"] = gt_report.to_dict()
        state.review_output = result
        state.verdict = verdict

        state.add_message(
            self.name,
            "REVIEW",
            f"Review Verdict: {verdict.value} (Score: {result.get('score_out_of_100', 'N/A')}/100) | {evidence.summary()}",
            structured_data=result,
        )
        return result
