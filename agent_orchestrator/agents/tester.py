import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from .base import BaseAgent
from ..state import OrchestratorState


class TesterAgent(BaseAgent):
    __test__ = False

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
            name="TESTER",
            role_description="Responsible for automated test generation, test execution in terminal/sandbox, and debugging stack traces.",
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

    def execute(self, state: Union[OrchestratorState, Dict[str, Any]], active_skills: Optional[List[str]] = None, **kwargs) -> Dict[str, Any]:
        if isinstance(state, dict) or state is None:
            dict_state = state or {}
            req = dict_state.get("user_request") or dict_state.get("goal") or "Run automated tests"
            state_obj = OrchestratorState(user_request=req)
            for k, v in dict_state.items():
                if hasattr(state_obj, k):
                    setattr(state_obj, k, v)
            state = state_obj
        task_info = kwargs.get("task_info") or {}

        # Multi-Level Retrieval Hierarchy: Level 1 (focal code) -> Level 2 (interfaces) -> Level 3 (fixtures/tests) -> Level 4 (conventions) -> Level 5 (repo map)
        from ..context.retrieval_hierarchy import RetrievalHierarchyEngine, HierarchyBudgetConfig
        hierarchy_engine = RetrievalHierarchyEngine(
            workspace=self.workspace,
            code_graph=getattr(self.tool_registry, "code_graph", None),
            semantic_index=getattr(self.tool_registry, "semantic_index", None),
            arch_analyzer=getattr(self.tool_registry, "arch_analyzer", None),
            cbm=getattr(self.tool_registry, "cbm", None),
            fallback_engine=getattr(self.tool_registry, "fallback_engine", None),
        )
        hierarchy_bundle = hierarchy_engine.retrieve_hierarchy(
            task_info=task_info,
            budget_config=HierarchyBudgetConfig(total_budget=8000),
            query="test fixtures assertion mock helper",
        )
        hierarchical_context_md = hierarchy_bundle.to_markdown()

        from ..runtime.project_detector import ProjectEnvironmentDetector, ProjectEnvironment
        from ..runtime.test_detector import ExistingTestDetector, RepoTestContext
        env: ProjectEnvironment = ProjectEnvironmentDetector.detect(self.workspace)
        test_context: RepoTestContext = ExistingTestDetector.scan(self.workspace)

        # Reconcile test runner & command: prefer authoritative CI command or detected framework
        test_runner = test_context.test_framework if test_context.test_framework != "unknown" else env.test_runner
        test_command = test_context.authoritative_ci_command or (
            "pytest" if test_runner == "pytest" and "pytest" not in env.test_command else env.test_command
        )

        lang_title = env.language.capitalize()
        framework_desc = f" ({env.framework})" if env.framework else ""
        existing_test_prompt_section = test_context.to_prompt_context()

        from ..context.budget_allocator import ContextBudget, ContextSection, ContextAssembler
        from ..context.compressor import JSONCompressor
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

        env_details = f"""- Primary Language: {lang_title}{framework_desc}
- Build Tool / Package Manager: {env.build_tool} ({env.package_manager})
- Build Command: `{env.build_command or 'None'}`
- Test Runner: {test_runner}
- Target Test File Pattern: `{env.test_file_pattern}` (e.g. `{env.test_file_example}`)
- Test Execution Command: `{test_command}`
- Integration Test Command: `{env.integration_test_command or 'None'}`"""

        tester_instructions = f"""1. SPECIFICATION-FIRST (BLACK-BOX) TEST DESIGN:
   - Formulate test cases based strictly on the Specification, Acceptance Criteria, and User Request.
   - Do NOT assume the Coder's implementation is correct. Test what the system SHOULD do according to contracts, not merely what the code happens to do.
   - Include positive functional tests, edge cases (empty inputs, zero, boundary values), and error handling.
2. REPOSITORY TEST CONVENTIONS & FIXTURES:
   - Reuse existing fixtures and setup helpers documented above. Do NOT recreate duplicate mock classes or fixtures.
   - Follow repository conventions (assertion styles, test structures, mock libraries) discovered in sibling tests.
   - Place new tests in appropriate existing test directories (e.g. {', '.join(test_context.test_directories) if test_context.test_directories else env.test_dirs}).
3. Use `write_file` to create comprehensive unit and integration test files matching {lang_title} conventions (e.g. `{env.test_file_pattern}`).
4. Use `terminal_execute` or `run_build_pipeline` to run the test suite: `{test_command}`.
5. If tests fail, diagnose whether the failure reveals an actual bug in the implementation or an invalid test assertion.
6. Avoid trivial tautological tests (e.g. `assert True`, `assert 1 == 1`). Every test must contain substantive assertions verifying contract behavior.
7. Context Isolation & On-Demand Retrieval: You receive an isolated, task-scoped context view. Use `query_specification` to inspect detailed schemas or edge cases, `query_architecture` to inspect component modules, and `get_task_artifact` to inspect predecessor deliverables on demand.
8. When all tests are verified, call `complete_task` with your test results summary."""

        task_mem_summary = task_info.get("task_memory_summary", "")
        episodic_summary = task_info.get("episodic_experience_summary", "")
        project_mem_summary = task_info.get("project_memory_summary", "")

        scoped_spec_content = json.dumps(isolated_ctx.scoped_specification if isolated_ctx.scoped_specification is not None else (state.specification_output or {}), indent=2) if (isolated_ctx.scoped_specification or state.specification_output) else ""

        sections = [
            ContextSection(name="user_request", title="User Request", content=str(state.user_request), priority=1, max_tokens=1500, is_essential=True, trust_level=TrustLevel.USER_INSTRUCTION),
            ContextSection(name="current_subtask", title="Current Subtask & Acceptance Criteria", content=JSONCompressor.compress(task_info, max_tokens=3000), priority=1, max_tokens=3000, is_essential=True, trust_level=TrustLevel.CONTROL_SYSTEM),
            ContextSection(name="task_memory", title="Task Memory & Dependency Takeaways", content=task_mem_summary, priority=2, max_tokens=getattr(budget, "task_memory", 1000), is_essential=False),
            ContextSection(name="project_memory", title="Project Conventions & Environment Rules", content=project_mem_summary, priority=3, max_tokens=getattr(budget, "project_memory", 1500), is_essential=False),
            ContextSection(name="episodic_experience", title="Past Execution Experiences & Trajectories", content=episodic_summary, priority=3, max_tokens=getattr(budget, "episodic_memory", 1000), is_essential=False),
            ContextSection(name="spec", title="Specification & Acceptance Criteria (Scoped)", content=scoped_spec_content, priority=2, max_tokens=2500, is_essential=False),
            ContextSection(name="test_context", title="Existing Test Suite Context & Fixtures", content=existing_test_prompt_section, priority=2, max_tokens=4000, is_essential=False, trust_level=TrustLevel.UNTRUSTED_REPOSITORY),
            ContextSection(name="env", title="Project Environment Detected", content=env_details, priority=3, max_tokens=1500, is_essential=False),
            ContextSection(name="hierarchical_context", title="Hierarchical Codebase Context (Target Contracts & Interfaces)", content=hierarchical_context_md, priority=2, max_tokens=6000, is_essential=True, trust_level=TrustLevel.UNTRUSTED_REPOSITORY),
            ContextSection(name="instructions", title="Instructions", content=tester_instructions, priority=1, max_tokens=1500, is_essential=True, trust_level=TrustLevel.CONTROL_SYSTEM),
        ]


        prompt = assembler.assemble(sections)
        system_prompt = self.build_system_prompt(active_skills=active_skills)
        from runtime.tool_policy import ToolPolicyEngine
        executable = ToolPolicyEngine.get_executable_tools(allowed_tools={"read_file", "write_file", "terminal_execute", "ast_syntax_check", "complete_task"})
        tester_tools = [getattr(t, "name", str(t)) for t in executable]

        effective_model = kwargs.get("model") or self.model
        loop_result = self.execution_loop.run(
            system_prompt=system_prompt,
            user_prompt=prompt,
            model=effective_model,
            available_tools=tester_tools,
            agent_name=self.name,
            **{k: v for k, v in kwargs.items() if k not in ("model", "active_skills")},
        )

        final_out = loop_result.get("final_output", {})
        history = loop_result.get("history_events", [])

        # Track written test files
        written_tests_set = set()
        last_terminal_result = None
        for event in history:
            tool = event.get("tool")
            args = event.get("args", {})
            res = event.get("result", {})
            if tool in ("write_file", "replace_file_content", "edit_file"):
                fp = args.get("filepath") or args.get("file_path")
                if fp and ("test" in fp.lower() or "spec" in fp.lower()):
                    written_tests_set.add(fp)
            elif tool in ("terminal_execute", "run_command", "run_build_pipeline"):
                last_terminal_result = res

        # Fallback test file write if model returned them in final_output
        if isinstance(final_out, dict) and "test_files" in final_out and isinstance(final_out["test_files"], list):
            for tfile in final_out["test_files"]:
                fp = tfile.get("filepath")
                content = tfile.get("content")
                if fp and content:
                    try:
                        self.workspace.write_file(
                            fp,
                            content,
                            agent_role=self.name,
                            task_scope=task_info,
                        )
                        written_tests_set.add(fp)
                    except Exception:
                        pass

        # Staged build verification prior to test safety net
        pipeline_report_dict = None
        if env.build_command:
            try:
                from ..runtime.build_pipeline import BuildVerificationPipeline, PipelineStage
                sandbox = getattr(self.tool_registry, "sandbox", None)
                pipeline = BuildVerificationPipeline(workspace=self.workspace, sandbox=sandbox, env=env)
                pipe_report = pipeline.execute(stages=[PipelineStage.BUILD, PipelineStage.COMPILE_TYPECHECK], fail_fast=True)
                pipeline_report_dict = pipe_report.to_dict()
                if not pipe_report.passed and not last_terminal_result:
                    last_terminal_result = {
                        "success": False,
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": f"Build verification failed: {pipe_report.summary()}",
                    }
            except Exception:
                pass
        elif isinstance(last_terminal_result, dict):
            if "pipeline_report" in last_terminal_result:
                pipeline_report_dict = last_terminal_result["pipeline_report"]
            elif "stage_results" in last_terminal_result and "passed" in last_terminal_result:
                pipeline_report_dict = last_terminal_result

        # If terminal wasn't executed during the loop, run detected test command as safety net
        if not last_terminal_result:
            last_terminal_result = self.tool_registry.call_tool(
                "run_command", {"command": test_command}
            )

        exec_res = last_terminal_result.get("output", last_terminal_result) if isinstance(last_terminal_result, dict) else {}
        if not isinstance(exec_res, dict):
            exec_res = last_terminal_result if isinstance(last_terminal_result, dict) else {}

        exit_code = exec_res.get("exit_code", 0)
        success = exec_res.get("success", exit_code == 0)

        # Check for trivial or tautological assertions
        tautological_warnings = []
        for tf in written_tests_set:
            tautological_warnings.extend(self._detect_trivial_assertions(tf))

        # Intra-Agent Self-Correction for Test Deliverables (Issue #88)
        self_correction_report = None
        if written_tests_set:
            try:
                from ..runtime.self_correction import SelfCorrectionEngine
                max_self_correction = kwargs.get("max_self_correction_attempts", 2)
                sc_rep = SelfCorrectionEngine.run_repair_loop(
                    agent=self,
                    state=state,
                    modified_files=list(written_tests_set),
                    max_attempts=max_self_correction,
                    task_info=task_info,
                    workspace=self.workspace,
                )
                self_correction_report = sc_rep.to_dict()
            except Exception:
                self_correction_report = None

        test_summary = {
            "test_strategy": final_out.get("summary") or final_out.get("test_strategy", f"Automated {env.language.capitalize()} test suite"),
            "language": env.language,
            "test_runner": test_runner,
            "test_command": test_command,
            "test_files": list(written_tests_set),
            "self_correction": self_correction_report,
            "execution_success": success,
            "exit_code": exit_code,
            "stdout": exec_res.get("stdout", ""),
            "stderr": exec_res.get("stderr", ""),
            "tautological_warnings": tautological_warnings,
            "existing_test_context": test_context.to_dict(),
            "build_pipeline_report": pipeline_report_dict,
            "turns_taken": loop_result.get("turns_taken", 1),
            "tool_history": history,
        }

        state.test_output = test_summary
        state.add_message(
            self.name,
            "TESTING",
            f"Executed test suite ({'PASSED' if success else 'FAILED'}). Exit code: {exit_code}",
            structured_data=test_summary,
        )
        return test_summary

    def _detect_trivial_assertions(self, filepath: str) -> List[str]:
        """Scans test file for tautological assertions (e.g. assert True, assert 1 == 1)."""
        warnings = []
        if not filepath.endswith(".py"):
            return warnings
        try:
            full_path = self.workspace.root_dir / filepath if hasattr(self.workspace, "root_dir") else Path(filepath)
            if not full_path.is_file():
                return warnings
            content = full_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content, filename=filepath)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assert):
                    if isinstance(node.test, ast.Constant) and bool(node.test.value) is True:
                        warnings.append(f"Trivial tautological assertion 'assert {node.test.value}' in {filepath}:{node.lineno}")
                    elif isinstance(node.test, ast.Compare):
                        if (
                            isinstance(node.test.left, ast.Constant)
                            and len(node.test.comparators) == 1
                            and isinstance(node.test.comparators[0], ast.Constant)
                            and node.test.left.value == node.test.comparators[0].value
                        ):
                            warnings.append(f"Trivial tautological comparison in {filepath}:{node.lineno}")
                elif isinstance(node, ast.Call):
                    fn_name = ""
                    if isinstance(node.func, ast.Attribute):
                        fn_name = node.func.attr
                    elif isinstance(node.func, ast.Name):
                        fn_name = node.func.id
                    if fn_name in ("assertTrue", "assert_true") and node.args:
                        arg0 = node.args[0]
                        if isinstance(arg0, ast.Constant) and bool(arg0.value) is True:
                            warnings.append(f"Trivial tautological assertion 'self.{fn_name}({arg0.value})' in {filepath}:{node.lineno}")
                    elif fn_name in ("assertEqual", "assert_equal", "assertEquals") and len(node.args) >= 2:
                        a, b = node.args[0], node.args[1]
                        if isinstance(a, ast.Constant) and isinstance(b, ast.Constant) and a.value == b.value:
                            warnings.append(f"Trivial tautological equality 'self.{fn_name}({a.value!r}, {b.value!r})' in {filepath}:{node.lineno}")
        except Exception:
            pass
        return warnings
