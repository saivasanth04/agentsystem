"""
Coder Agent: Writes and edits code using WriteFileTool, ReadFileTool, ASTSyntaxCheckerTool, RegexGrepTool, and mcp-server-filesystem.
"""
import json
from typing import Any, Dict, List, Optional, Union
from .base import BaseAgent
from ..state import OrchestratorState


class CoderAgent(BaseAgent):
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
        enforce_react: bool = False,
        **kwargs: Any,
    ):
        super().__init__(
            name="CODER",
            role_description="Responsible for writing clean, SOLID-compliant, modular code files, validating syntax via AST, and searching with regex grep.",
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
        self.enforce_react = enforce_react

    def execute(self, state: Union[OrchestratorState, Dict[str, Any]], active_skills: Optional[List[str]] = None, **kwargs) -> Dict[str, Any]:
        if isinstance(state, dict) or state is None:
            dict_state = state or {}
            req = dict_state.get("user_request") or dict_state.get("goal") or "Implement code"
            state_obj = OrchestratorState(user_request=req)
            for k, v in dict_state.items():
                if hasattr(state_obj, k):
                    setattr(state_obj, k, v)
            state = state_obj
        task_info = kwargs.get("task_info") or {}
        replan_context = ""
        if state.replan_history:
            latest = state.replan_history[-1]
            replan_context = f"\n\n[RE-PLANNING FIX CONTEXT - Iteration {latest.iteration}]\nIssues Found:\n{latest.feedback_summary}\nAction Plan:\n{json.dumps(latest.remediation_plan, indent=2)}"

        from ..context.budget_allocator import ContextBudget, ContextSection, ContextAssembler
        from ..context.compressor import JSONCompressor
        from ..context.relevance_ranker import RelevanceRanker, ContextTier
        from ..context.isolation import ContextIsolationEngine
        from ..security.trust_boundaries import TrustLevel
        from ..config import config as orch_cfg

        if hasattr(self.tool_registry, "set_orchestrator_state"):
            self.tool_registry.set_orchestrator_state(state)

        isolated_ctx = ContextIsolationEngine.isolate_for_task(state, task_info=task_info)

        workspace = kwargs.get("workspace") or self.workspace

        budget = ContextBudget(
            total_budget=getattr(orch_cfg, "context_budget_total", 32000),
            focal_files=getattr(orch_cfg, "context_budget_focal_files", 8000),
            repo_outline=getattr(orch_cfg, "context_budget_repo_map", 2000),
        )
        assembler = ContextAssembler(budget)

        # 1. Rank context items via RelevanceRanker with CBM & Semantic Index
        cbm_inst = getattr(self.tool_registry, "cbm", None)
        sem_index = getattr(self.tool_registry, "semantic_index", None)
        ranker = RelevanceRanker(
            code_graph=getattr(self.tool_registry, "code_graph", None),
            workspace=workspace,
            semantic_index=sem_index,
            cbm=cbm_inst,
        )
        ranked_items = ranker.rank_context_for_task(
            task_info=task_info,
            max_focal_tokens=budget.focal_files,
            max_interface_tokens=budget.interface_signatures,
        )

        focal_content = "\n\n".join(item.formatted_content for item in ranked_items if item.tier == ContextTier.FOCAL)
        interface_content = "\n\n".join(item.formatted_content for item in ranked_items if item.tier == ContextTier.INTERFACE)

        # Architectural slice from CBM
        arch_slice_content = ""
        if cbm_inst and hasattr(cbm_inst, "get_architecture_slice"):
            slices = []
            focal_inputs = (task_info.get("inputs", []) or []) + (task_info.get("outputs", []) or [])
            for inp in focal_inputs:
                clean_fp = inp.split(":")[0].strip().replace("\\", "/")
                if clean_fp:
                    asl = cbm_inst.get_architecture_slice(clean_fp)
                    if asl.get("found"):
                        slices.append(f"• File: {clean_fp} | Layer: {asl['layer']} | Frameworks: {', '.join(asl['frameworks'] or ['None'])}")
            if slices:
                arch_slice_content = "Architectural Conventions for Focal Components:\n" + "\n".join(slices)

        # Context Sufficiency & Evidence Evaluation
        evidence_warning_content = ""
        sufficiency = getattr(ranker, "last_sufficiency_evaluation", None)
        if sufficiency and (not sufficiency.is_sufficient or sufficiency.missing_entities):
            warning_lines = [
                f"⚠️ Context Sufficiency Assessment (Confidence: {sufficiency.confidence_score:.0%}):",
                sufficiency.evidence_summary,
            ]
            if sufficiency.missing_entities:
                warning_lines.append(f"• Unresolved task entities: {', '.join(sufficiency.missing_entities)}")
            if sufficiency.recommended_actions:
                warning_lines.append("• Recommended actions: " + "; ".join(sufficiency.recommended_actions))
            warning_lines.append(
                "CRITICAL: Do NOT guess or hallucinate method signatures, imports, or parameters for unresolved entities. "
                "Use `request_more_evidence` or `find_symbol` to retrieve exact contracts before writing code."
            )
            evidence_warning_content = "\n".join(warning_lines)

        # 2. Extract multi-tier memory summaries
        wm_summary = task_info.get("working_memory_summary", "")
        task_mem_summary = task_info.get("task_memory_summary", "")
        episodic_summary = task_info.get("episodic_experience_summary", "")
        project_mem_summary = task_info.get("project_memory_summary", "")
        semantic_summary = task_info.get("semantic_knowledge_summary", "")

        # Structured Edit Plan Engine (Issue #86)
        from ..runtime.edit_plan import EditPlanManager
        from ..contracts import StructuredEditPlanContract
        edit_plan_obj = task_info.get("structured_edit_plan") or task_info.get("edit_plan")
        if edit_plan_obj is None:
            edit_plan_obj = EditPlanManager.generate_plan(
                task_info=task_info,
                workspace=workspace,
                architecture=isolated_ctx.scoped_architecture if isolated_ctx.scoped_architecture is not None else (state.architecture_output or {}),
                specification=isolated_ctx.scoped_specification if isolated_ctx.scoped_specification is not None else (state.specification_output or {}),
            )
        elif isinstance(edit_plan_obj, dict):
            edit_plan_obj = StructuredEditPlanContract(**edit_plan_obj)

        if hasattr(self.tool_registry, "set_current_edit_plan"):
            self.tool_registry.set_current_edit_plan(edit_plan_obj)

        edit_plan_content = EditPlanManager.to_prompt_context(edit_plan_obj) if edit_plan_obj else ""

        # 3. Create structured sections
        sections = [
            ContextSection(
                name="user_request",
                title="User Request",
                content=str(state.user_request),
                priority=1,
                max_tokens=1500,
                is_essential=True,
                trust_level=TrustLevel.USER_INSTRUCTION,
            ),
            ContextSection(
                name="current_task",
                title="Current Task & Scope",
                content=JSONCompressor.compress(task_info, max_tokens=3000),
                priority=1,
                max_tokens=3000,
                is_essential=True,
                trust_level=TrustLevel.CONTROL_SYSTEM,
            ),
            ContextSection(
                name="structured_edit_plan",
                title="Structured Mutation Plan",
                content=edit_plan_content,
                priority=1,
                max_tokens=2500,
                is_essential=True,
                trust_level=TrustLevel.CONTROL_SYSTEM,
            ),
            ContextSection(
                name="evidence_warning",
                title="Evidence Sufficiency & Context Assessment",
                content=evidence_warning_content,
                priority=1,
                max_tokens=1000,
                is_essential=False,
            ),
            ContextSection(
                name="working_memory",
                title="Working Memory & Prior Discoveries",
                content=wm_summary,
                priority=2,
                max_tokens=budget.working_memory,
                is_essential=False,
            ),
            ContextSection(
                name="task_memory",
                title="Task Memory & Dependency Takeaways",
                content=task_mem_summary,
                priority=2,
                max_tokens=getattr(budget, "task_memory", 1000),
                is_essential=False,
            ),
            ContextSection(
                name="project_memory",
                title="Project Conventions & Environment Rules",
                content=project_mem_summary,
                priority=3,
                max_tokens=getattr(budget, "project_memory", 1500),
                is_essential=False,
            ),
            ContextSection(
                name="episodic_experience",
                title="Past Execution Experiences & Trajectories",
                content=episodic_summary,
                priority=3,
                max_tokens=getattr(budget, "episodic_memory", 1000),
                is_essential=False,
            ),
            ContextSection(
                name="semantic_knowledge",
                title="Semantic Knowledge & API Contracts",
                content=semantic_summary,
                priority=3,
                max_tokens=getattr(budget, "semantic_memory", 800),
                is_essential=False,
            ),
            ContextSection(
                name="focal_files",
                title="Focal Target Files",
                content=focal_content,
                priority=2,
                max_tokens=budget.focal_files,
                is_essential=False,
                trust_level=TrustLevel.UNTRUSTED_REPOSITORY,
            ),
            ContextSection(
                name="interface_signatures",
                title="Interface Contracts of Related Modules",
                content=interface_content,
                priority=3,
                max_tokens=budget.interface_signatures,
                is_essential=False,
                trust_level=TrustLevel.UNTRUSTED_REPOSITORY,
            ),
            ContextSection(
                name="specification",
                title="Specification (Scoped)",
                content=json.dumps(isolated_ctx.scoped_specification if isolated_ctx.scoped_specification is not None else (state.specification_output or {}), indent=2, default=str) if (isolated_ctx.scoped_specification or state.specification_output) else "",
                priority=4,
                max_tokens=2000,
                is_essential=False,
            ),
            ContextSection(
                name="architecture",
                title="Architecture Blueprint (Scoped)",
                content=json.dumps(isolated_ctx.scoped_architecture if isolated_ctx.scoped_architecture is not None else (state.architecture_output or {}), indent=2, default=str) if (isolated_ctx.scoped_architecture or state.architecture_output) else "",
                priority=4,
                max_tokens=2000,
                is_essential=False,
            ),
            ContextSection(
                name="replan_context",
                title="Re-planning Fix Context",
                content=(isolated_ctx.replan_context or replan_context).strip(),
                priority=2,
                max_tokens=2000,
                is_essential=False,
            ),
            ContextSection(
                name="architecture_slice",
                title="Architectural Context & Layer Conventions",
                content=arch_slice_content,
                priority=3,
                max_tokens=1500,
                is_essential=False,
            ),
            ContextSection(
                name="instructions",
                title="Instructions",
                content="""1. Follow the Structured Mutation Plan step-by-step. For each step, apply the planned mutation (CREATE_FILE, REPLACE_BLOCK, MODIFY_SYMBOL, DELETE_FILE, MOVE_FILE) in dependency order.
2. Use targeted Codebase Memory & Graph tools (`query_codebase_graph`, `get_symbol_neighbors`, `semantic_code_search`, `get_call_graph`, `get_impact_radius`) rather than scanning the entire workspace.
3. Inspect specific required files or interfaces using `read_file`, `find_symbol`, or `get_symbol_neighbors`. Note the version header `[File: ... | Version: ...]` from `read_file`.
4. For NEW files, create them using `write_file`.
5. For EXISTING files, modify them surgically using delta tools: `replace_file_content` (with optional start_line/end_line), `insert_lines`, `delete_lines`, `rename_file`, `delete_file`, or `apply_diff_blocks`. Always pass `expected_version` from your prior `read_file` observation to prevent conflicting overwrites. Do NOT regenerate entire existing files or use lazy truncation comments.
6. If you lack interface contracts, class definitions, or module context needed for this task, call `request_more_evidence` or `find_symbol` BEFORE writing code. Never hallucinate APIs.
7. Context Isolation & On-Demand Retrieval: You receive an isolated, need-to-know context view. You can retrieve additional details on demand using `query_specification` (e.g. for specific functional requirements or edge cases), `query_architecture` (for specific module layouts and topologies), `get_task_artifact` (for predecessor deliverables), or inspect/update your plan via `get_edit_plan` and `update_edit_plan_step`.
8. Validate Python files with `ast_syntax_check` and run tests via `terminal_execute`.
9. When finished, call `complete_task` with a clear summary of all implemented files and diffs.""",
                priority=1,
                max_tokens=1500,
                is_essential=True,
                trust_level=TrustLevel.CONTROL_SYSTEM,
            ),
        ]

        # ContextCompiler is the single final context authority (PARTIAL FIX 1)
        from context.compiler import ContextCompiler
        from context.budget import TokenBudget
        compiler = getattr(self.execution_loop, "context_compiler", None)
        if compiler is None:
            compiler = ContextCompiler(
                workspace_manager=workspace,
                llm_client=self.llm,
            )
        focal_inputs = (task_info.get("inputs", []) or []) + (task_info.get("outputs", []) or [])
        clean_targets = [inp.split(":")[0].strip() for inp in focal_inputs if inp]

        compiled_package = compiler.compile(
            task_objective=f"Objective: {task_info.get('objective', state.user_request)}",
            working_memory=wm_summary,
            errors=replan_context if replan_context else None,
            skills=active_skills,
            target_files=clean_targets,
            token_budget=TokenBudget(total_budget=8000),
        )
        prompt = compiled_package.to_prompt_context()
        system_prompt = self.build_system_prompt(active_skills=active_skills)
        from runtime.tool_policy import ToolPolicyEngine
        executable = ToolPolicyEngine.get_executable_tools(allowed_tools={"write_file", "replace_file_content", "edit_file", "read_file", "list_directory", "ast_syntax_check", "regex_grep", "complete_task"})
        coder_tools = [getattr(t, "name", str(t)) for t in executable]

        state_store = kwargs.get("state_store")
        session_id = kwargs.get("session_id")
        task_id = kwargs.get("task_id")
        checkpoint_manager = kwargs.get("checkpoint_manager")
        workspace = kwargs.get("workspace") or self.workspace
        initial_messages = kwargs.get("initial_messages")
        initial_observations = kwargs.get("initial_observations")
        start_turn = kwargs.get("start_turn", 0)
        permissions = kwargs.get("permissions")
        max_turns = kwargs.get("max_turns")
        timeout_seconds = kwargs.get("timeout_seconds")

        effective_enforce_react = kwargs.get("enforce_react", getattr(self, "enforce_react", False))
        require_tools = kwargs.get("require_tools", False)
        require_verification = kwargs.get("require_verification", getattr(self, "require_verification", None))

        effective_model = kwargs.get("model") or self.model
        loop_result = self.execution_loop.run(
            system_prompt=system_prompt,
            user_prompt=prompt,
            model=effective_model,
            available_tools=coder_tools,
            agent_name=self.name,
            permissions=permissions,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            state_store=state_store,
            session_id=session_id,
            task_id=task_id,
            checkpoint_manager=checkpoint_manager,
            workspace=workspace,
            initial_messages=initial_messages,
            initial_observations=initial_observations,
            start_turn=start_turn,
            task_info=task_info,
            enforce_react=effective_enforce_react,
            require_tools=require_tools,
            require_verification=require_verification,
            agent_contract=kwargs.get("agent_contract"),
        )

        final_out = loop_result.get("final_output", {})
        history = loop_result.get("history_events", [])

        # Track written files from active tool calls
        written_files_set = set()
        delta_mod_tools = {
            "write_file", "replace_file_content", "edit_file",
            "insert_lines", "delete_lines", "apply_diff_blocks", "diff_blocks"
        }
        for event in history:
            tool = event.get("tool")
            args = event.get("args", {})
            if tool in delta_mod_tools:
                fp = args.get("filepath") or args.get("file_path") or args.get("path")
                if fp:
                    written_files_set.add(fp)

        # Multi-tier artifact validation integration
        try:
            from ..runtime.artifact_validator import ArtifactValidator
        except (ImportError, ValueError):
            from agent_orchestrator.runtime.artifact_validator import ArtifactValidator

        artifact_validation_reports = []
        target_ws = workspace or self.workspace

        allowed_writes = getattr(permissions, "allowed_write_paths", None)
        if isinstance(permissions, dict):
            allowed_writes = permissions.get("allowed_write_paths")

        forbidden_writes = getattr(permissions, "forbidden_write_paths", None)
        if isinstance(permissions, dict):
            forbidden_writes = permissions.get("forbidden_write_paths")

        def _safe_persist_file(filepath: str, file_content: str):
            if not filepath or not file_content:
                return
            # If already modified via surgical tools in this turn, don't clobber
            if filepath in written_files_set and target_ws.file_exists(filepath):
                return

            # Multi-tier artifact validation
            val_res = ArtifactValidator.validate_file_deliverable(
                workspace=target_ws,
                filepath=filepath,
                content=file_content,
                is_new_file=not target_ws.file_exists(filepath),
                allowed_paths=allowed_writes,
                forbidden_paths=forbidden_writes,
                verify_disk=False,
            )
            artifact_validation_reports.append(val_res.to_dict())

            if not val_res.is_valid:
                return

            try:
                target_ws.write_file(
                    filepath,
                    file_content,
                    agent_role=self.name,
                    task_scope=task_info,
                    task_permissions=permissions,
                )
                on_disk, d_errs = ArtifactValidator.verify_on_disk(target_ws, filepath, expected_content=file_content)
                if on_disk:
                    written_files_set.add(filepath)
                else:
                    val_res.is_valid = False
                    val_res.errors.extend(d_errs)
            except Exception as e:
                val_res.is_valid = False
                val_res.errors.append(f"Write failed: {str(e)}")

        # Comprehensive file persistence from tool history and final deliverables
        if isinstance(final_out, dict):
            candidates = []
            if "files" in final_out:
                candidates.append(final_out["files"])
            if "code_files" in final_out:
                candidates.append(final_out["code_files"])
            if "components" in final_out:
                candidates.append(final_out["components"])
            if isinstance(final_out.get("deliverables"), dict) and "files" in final_out["deliverables"]:
                candidates.append(final_out["deliverables"]["files"])

            for c in candidates:
                if isinstance(c, list):
                    for f in c:
                        if isinstance(f, dict):
                            fp = f.get("filepath") or f.get("file_path") or f.get("filename") or f.get("name")
                            content = f.get("content") or f.get("code")
                            if fp and content:
                                _safe_persist_file(fp, content)
                elif isinstance(c, dict):
                    for fp, content in c.items():
                        if isinstance(content, str) and (fp.endswith(".js") or fp.endswith(".jsx") or fp.endswith(".css") or fp.endswith(".html") or fp.endswith(".py")):
                            _safe_persist_file(fp, content)

            # Check direct filename keys in final_out (e.g. "Calculator.jsx": "...")
            for k, v in final_out.items():
                if isinstance(v, str) and any(k.endswith(ext) for ext in [".js", ".jsx", ".css", ".html", ".py", ".json", ".md"]):
                    _safe_persist_file(k, v)

        task_outputs = task_info.get("outputs", []) if isinstance(task_info, dict) else getattr(task_info, "outputs", [])
        reconciliation = ArtifactValidator.reconcile_task_outputs(
            workspace=target_ws,
            required_outputs=task_outputs,
            modified_files=list(written_files_set),
            forbidden_paths=forbidden_writes,
        )

        deliverables = final_out.get("deliverables", {}) if isinstance(final_out, dict) else {}
        summary = (
            final_out.get("summary")
            if isinstance(final_out, dict) and "summary" in final_out
            else (final_out.get("content") if isinstance(final_out, dict) else str(final_out))
        )

        # Retrieve change manifest from workspace
        target_ws = workspace or self.workspace
        change_manifest_dict = None
        if hasattr(target_ws, "get_change_manifest"):
            m = target_ws.get_change_manifest()
            if m:
                change_manifest_dict = m.to_dict() if hasattr(m, "to_dict") else m

        # If state_store provided, persist change manifest
        if state_store and session_id and task_id and change_manifest_dict and hasattr(state_store, "save_change_manifest"):
            try:
                state_store.save_change_manifest(session_id, task_id, change_manifest_dict)
            except Exception:
                pass

        # Intra-Agent Self-Correction Engine (Issue #88)
        self_correction_report = None
        if written_files_set:
            try:
                from ..runtime.self_correction import SelfCorrectionEngine
                max_self_correction = kwargs.get("max_self_correction_attempts", 2)
                sc_rep = SelfCorrectionEngine.run_repair_loop(
                    agent=self,
                    state=state,
                    modified_files=list(written_files_set),
                    max_attempts=max_self_correction,
                    task_info=task_info,
                    permissions=permissions,
                    workspace=target_ws,
                )
                self_correction_report = sc_rep.to_dict()
            except Exception:
                self_correction_report = None

        code_summary = {
            "summary": summary or "Coding implementation completed.",
            "written_files": list(written_files_set),
            "deliverables": deliverables,
            "self_correction": self_correction_report,
            "turns_taken": loop_result.get("turns_taken", 1),
            "tool_history": history,
            "observations": loop_result.get("observations", []),
            "trajectory": loop_result.get("trajectory"),
            "token_usage": loop_result.get("token_usage"),
            "tools_used": loop_result.get("tools_used", []),
            "errors": loop_result.get("errors", []),
            "messages": loop_result.get("messages", []),
            "change_manifest": change_manifest_dict,
            "artifact_validation": artifact_validation_reports,
            "artifact_reconciliation": reconciliation.to_dict(),
        }

        state.code_output = code_summary
        change_summary_text = f" Changes: {change_manifest_dict.get('summary')}" if change_manifest_dict and change_manifest_dict.get('summary') else ""
        state.add_message(
            self.name,
            "CODING",
            f"Implemented {len(written_files_set)} files across {loop_result.get('turns_taken', 1)} turns: {list(written_files_set)}.{change_summary_text}",
            structured_data=code_summary,
        )
        return code_summary
