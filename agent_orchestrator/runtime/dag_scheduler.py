"""
ConcurrentDAGScheduler: Multi-Branch Parallel Wave Execution & Barrier Synchronization for TaskDAG.
Dispatches independent READY tasks concurrently, injects dependency-scoped artifacts,
and coordinates fan-in join nodes across multi-agent workflows.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid
from .task_graph import (
    TaskDAG,
    ExecutableTask,
    TaskState,
    TaskAttemptRecord,
    CheckpointRecord,
    TokenUsage,
    ObservationRecord,
)
from .verification import TaskVerificationGate, VerificationResult
from ..tools.workspace import SandboxedWorkspace, MergeConflictError



class ConcurrentDAGScheduler:
    """
    Coordinates multi-branch concurrent execution waves over a TaskDAG.
    Partitions independent tasks into conflict-free parallel execution batches,
    dispatches isolated sandboxed agent workers, and performs barrier synchronization.
    """

    def __init__(
        self,
        max_workers: int = 4,
        on_event: Optional[Callable[[str, str], None]] = None,
        on_event_callback: Optional[Callable[[str, str], None]] = None,
        event_bus: Optional[Any] = None,
        telemetry_engine: Optional[Any] = None,
        **kwargs: Any,
    ):
        self.max_workers = max(1, max_workers)
        self.on_event = on_event or on_event_callback or (lambda ev, msg: None)
        self.event_bus = event_bus or kwargs.get("event_bus")
        self.telemetry_engine = telemetry_engine or kwargs.get("telemetry_engine")
        self._lock = threading.Lock()

    def execute_ready_wave(
        self,
        task_dag: TaskDAG,
        orchestrator: Any,
        state_data: Dict[str, Any],
        max_workers: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Executes all currently READY tasks in a parallel wave.
        Returns aggregated step results and updated subtask records.
        """
        if not self.event_bus and hasattr(orchestrator, "event_bus"):
            self.event_bus = getattr(orchestrator, "event_bus", None)
        if not self.telemetry_engine and hasattr(orchestrator, "telemetry_engine"):
            self.telemetry_engine = getattr(orchestrator, "telemetry_engine", None)

        ready_tasks = task_dag.get_ready_tasks()
        if not ready_tasks:
            return {
                "subtasks": task_dag.to_list(),
                "completed_subtasks": [t.to_dict() for t in task_dag.list_tasks() if t.state == TaskState.COMPLETED],
                "step_results": state_data.get("step_results", {}),
            }

        # Conflict-free wave partitioning:
        # Avoid scheduling tasks concurrently if they claim the same write outputs
        dispatched_tasks: List[ExecutableTask] = []
        claimed_write_targets: Set[str] = set()

        for t in ready_tasks:
            write_targets = set()
            for o in (t.outputs or []):
                if o and o != "*":
                    write_targets.add(o.replace("\\", "/").strip("/").lower())
            for w in (getattr(t.permissions, "allowed_write_paths", []) or []):
                if w and w != "*":
                    write_targets.add(w.replace("\\", "/").strip("/").lower())

            if not write_targets or not (write_targets & claimed_write_targets):
                dispatched_tasks.append(t)
                claimed_write_targets.update(write_targets)
            else:
                t.state = TaskState.READY

        effective_workers = min(len(dispatched_tasks), max_workers or self.max_workers)
        self.on_event(
            "CONCURRENT WAVE START",
            f"Dispatching wave of {len(dispatched_tasks)} parallel ready task(s) with {effective_workers} worker(s): {[t.task_id + ': ' + t.objective for t in dispatched_tasks]}"
        )

        wave_results: Dict[str, Any] = {}
        user_request = state_data.get("user_request", "")
        existing_step_results = dict(state_data.get("step_results") or {})

        # Initialize or retrieve working memory on orchestrator (Issue #36)
        from ..memory.working_memory import WorkingMemory
        if not hasattr(orchestrator, "working_memory") or orchestrator.working_memory is None:
            orchestrator.working_memory = WorkingMemory(active_goal=str(user_request))
        wm = orchestrator.working_memory
        if hasattr(orchestrator, "tool_registry") and orchestrator.tool_registry:
            orchestrator.tool_registry.working_memory = wm

        def _execute_single_task(task: ExecutableTask) -> Dict[str, Any]:
            task_id = task.task_id
            step_name = task.objective
            req_caps = task.required_capabilities
            req_tools = task.required_tools
            pref_skills = task.preferred_skills

            from ..logging import set_log_context, clear_log_context
            from ..tracing import get_tracer, SpanType, SpanStatus
            sess_id = getattr(orchestrator, "active_session_id", None)
            tracer = getattr(orchestrator, "tracer", None) or get_tracer("orchestrator")
            trace_id = getattr(orchestrator, "active_trace_id", None) or sess_id

            task_span_scope = tracer.start_as_current_span(
                f"Task: {step_name}",
                span_type=SpanType.TASK,
                attributes={"task_id": task_id, "objective": step_name, "session_id": sess_id},
                trace_id=trace_id,
            )
            task_span = task_span_scope.__enter__()
            set_log_context(session_id=sess_id, task_id=task_id, trace_id=task_span.trace_id, span_id=task_span.span_id)

            task_dag.mark_task_running(task_id)
            if getattr(self, "event_bus", None):
                try:
                    self.event_bus.publish(
                        "TASK_STARTED",
                        task_id=task_id,
                        agent_name=getattr(task, "owner_agent", None),
                        payload={"objective": step_name, "inputs": task.inputs, "outputs": task.outputs},
                    )
                except Exception:
                    pass

            # 1. Dynamically Discover Optimal Agent for this Task
            if getattr(task, "owner_agent", None):
                try:
                    agent = orchestrator.select_agent(task.owner_agent)
                    self.on_event(
                        "DYNAMIC ACTION DISPATCH",
                        f"Task [{task_id}]: '{step_name}' -> Explicit Owner Agent [{agent.name}]"
                    )
                except Exception:
                    agent = None
            else:
                agent = None

            if agent is None:
                discovered = orchestrator.agent_registry.discover(
                    query=f"{step_name} {' '.join(task.inputs)} {' '.join(task.outputs)} {user_request}",
                    capabilities=req_caps,
                    tools=req_tools,
                    top_k=1,
                )
                if discovered:
                    manifest, score = discovered[0]
                    agent = orchestrator.select_agent(manifest)
                    self.on_event(
                        "DYNAMIC ACTION DISPATCH",
                        f"Task [{task_id}]: '{step_name}' -> Matched Agent [{agent.name}] (Score: {score:.2f}, Capabilities: {getattr(agent, 'capabilities', req_caps)})"
                    )
                else:
                    agent = orchestrator.select_agent("CODER")
                    self.on_event("DYNAMIC ACTION DISPATCH", f"Task [{task_id}]: '{step_name}' -> Fallback Agent [{agent.name}]")

            task.owner_agent = agent.name
            set_log_context(agent_name=agent.name)
            if getattr(self, "event_bus", None):
                try:
                    self.event_bus.publish(
                        "AGENT_SELECTED",
                        task_id=task_id,
                        agent_name=agent.name,
                        payload={"score": score if discovered else 1.0, "capabilities": getattr(agent, "capabilities", req_caps)},
                    )
                except Exception:
                    pass

            # 2. Ingest JIT Skills
            skill_query = f"{step_name} {' '.join(req_caps)} {' '.join(pref_skills)} {user_request}"
            discovered_skills = orchestrator.skill_registry.discover(query=skill_query, top_k=3)
            active_skills = [m.name for m, _ in discovered_skills]
            if active_skills:
                self.on_event("DYNAMIC SKILLS", f"Injected JIT skills for [{agent.name}] on Task [{task_id}]: {active_skills}")

            # Dependency-Scoped Artifact Routing & MessageBus Inboxes
            parent_artifacts = task_dag.get_parent_artifacts(task_id)
            inbox_messages = []
            if hasattr(orchestrator, "message_bus") and orchestrator.message_bus:
                inbox_messages = [m.to_dict() for m in orchestrator.message_bus.get_inbox(task_id)]
                inbox_messages.extend([m.to_dict() for m in orchestrator.message_bus.get_inbox(agent.name)])

            # Ephemeral Workspace Sandboxing per Concurrent Worker
            clean_id = task_id.replace(":", "_").replace("/", "_").replace("\\", "_")
            sandbox = SandboxedWorkspace(main_workspace=orchestrator.workspace, sandbox_id=clean_id)

            ostate = orchestrator._graph_to_state(state_data)
            if hasattr(orchestrator, "tool_registry") and hasattr(orchestrator.tool_registry, "set_orchestrator_state"):
                orchestrator.tool_registry.set_orchestrator_state(ostate)
            if hasattr(agent, "tool_registry") and hasattr(agent.tool_registry, "set_orchestrator_state"):
                agent.tool_registry.set_orchestrator_state(ostate)

            # Strict dependency isolation: do not leak unrelated parallel tasks
            if task.dependencies is not None:
                scoped_prev_results = parent_artifacts
            else:
                scoped_prev_results = parent_artifacts if parent_artifacts else existing_step_results

            task_context = {
                "task": task.to_dict(),
                "subtask": task.to_dict(),
                "task_id": task_id,
                "objective": step_name,
                "inputs": task.inputs,
                "outputs": task.outputs,
                "acceptance_tests": task.acceptance_tests,
                "dependencies": task.dependencies,
                "parent_artifacts": parent_artifacts,
                "inbox_messages": inbox_messages,
                "permissions": task.permissions.to_dict() if hasattr(task.permissions, "to_dict") else task.permissions,
                "previous_step_results": scoped_prev_results,
                "working_memory_summary": wm.get_summary() if wm else "",
            }


            # Active memory priming across all 5 tiers (Issue #66)
            if hasattr(orchestrator, "memory_engine") and orchestrator.memory_engine:
                try:
                    memory_primed = orchestrator.memory_engine.prime_context_for_task(task_context)
                    task_context.update({
                        "working_memory_summary": memory_primed.get("working_memory") or task_context["working_memory_summary"],
                        "task_memory_summary": memory_primed.get("task_memory", ""),
                        "episodic_experience_summary": memory_primed.get("episodic_experience", ""),
                        "project_memory_summary": memory_primed.get("project_memory", ""),
                        "semantic_knowledge_summary": memory_primed.get("semantic_knowledge", ""),
                    })
                except Exception:
                    pass


            attempt_start_time = time.time()
            started_at_str = datetime.now().isoformat()
            attempt_num = len(task.attempts) + 1

            # 3. Dynamic Model Routing based on task complexity and retry attempt (Issue #34)
            from ..routing.model_router import model_router, ComplexityClassifier
            task_complexity = ComplexityClassifier.classify(task, attempt=attempt_num)
            routed_model = model_router.route(role=agent.name, complexity=task_complexity, attempt=attempt_num)
            self.on_event(
                "MODEL ROUTING",
                f"Task [{task_id}] (Complexity: {task_complexity.value}, Attempt: {attempt_num}) -> Routed to Model [{routed_model}] for [{agent.name}]"
            )

            pre_ckpt = task.create_checkpoint(sandbox, "PRE_EXECUTION")
            session_id = getattr(orchestrator, "active_session_id", "default-session")
            if hasattr(orchestrator, "checkpoint_manager") and orchestrator.checkpoint_manager:
                orchestrator.checkpoint_manager.create_snapshot(
                    f"ckpt-{task_id}-pre_execution-{attempt_num}",
                    sandbox,
                    metadata={"task_id": task_id, "attempt": attempt_num, "stage": "PRE_EXECUTION", "session_id": session_id},
                )
            res: Dict[str, Any] = {}
            exec_error: Optional[str] = None

            try:
                from ..cost.budget_tracker import budget_tracker, BudgetExceededError
                try:
                    budget_tracker.assert_budget(task_id=task_id, agent_name=agent.name)
                except BudgetExceededError as bee:
                    exec_error = str(bee)
                    res = {"error": str(bee), "success": False}
                    self.on_event("BUDGET EXCEEDED", f"Task [{task_id}] halted by budget limit: {bee}")

                if not exec_error:
                    try:
                        from .agent_contract import InterAgentContract
                        arch_data = state_data.get("architecture_output")
                        derived_contract = InterAgentContract.from_architecture_and_task(arch_data, task)
                        if derived_contract:
                            setattr(task, "agent_contract", derived_contract)

                        with tracer.start_as_current_span(
                            f"Agent: {agent.name}",
                            span_type=SpanType.AGENT,
                            attributes={"agent_name": agent.name, "task_id": task_id, "model": routed_model},
                        ) as agent_span:
                            res = agent.execute(
                                ostate,
                                active_skills=active_skills,
                                task_info=task_context,
                                permissions=task.permissions,
                                max_turns=task.max_turns,
                                timeout_seconds=task.timeout_seconds,
                                session_id=session_id,
                                task_id=task_id,
                                state_store=getattr(orchestrator, "state_store", None),
                                checkpoint_manager=getattr(orchestrator, "checkpoint_manager", None),
                                workspace=sandbox,
                                approval_gate=getattr(orchestrator, "approval_gate", None),
                                model=routed_model,
                                event_bus=self.event_bus,
                                telemetry_engine=self.telemetry_engine,
                                agent_contract=derived_contract,
                                cancellation_token=getattr(orchestrator, "cancellation_token", None) if hasattr(getattr(orchestrator, "cancellation_token", None), "is_cancelled") and not hasattr(getattr(orchestrator, "cancellation_token", None), "_mock_methods") else None,
                            )
                            if exec_error or (isinstance(res, dict) and res.get("success") is False):
                                agent_span.set_status(SpanStatus.ERROR, exec_error or str(res.get("error")))
                    except Exception as e:
                        exec_error = str(e)
                        res = {"error": str(e), "success": False}

                # 4. Post-execution Checkpoint & Verification Gate
                post_ckpt = task.create_checkpoint(sandbox, "POST_EXECUTION")
                if hasattr(orchestrator, "checkpoint_manager") and orchestrator.checkpoint_manager:
                    orchestrator.checkpoint_manager.create_snapshot(
                        f"ckpt-{task_id}-post_execution-{attempt_num}",
                        sandbox,
                        metadata={"task_id": task_id, "attempt": attempt_num, "stage": "POST_EXECUTION", "session_id": session_id},
                    )
                attempt_duration = round(time.time() - attempt_start_time, 4)
                completed_at_str = datetime.now().isoformat()

                task_dag.mark_task_verifying(task_id)
                v_res = orchestrator.verification_gate.verify_task(task)

                # Self-healing for missing dependencies (Issue #64)
                if not v_res.passed:
                    combined_err = " ".join(v_res.failure_reasons) + " " + str(exec_error or "")
                    try:
                        from .dependency_healer import DependencyHealingEngine
                        missing_dep = DependencyHealingEngine.identify_missing_dependency(stderr=combined_err, stdout=v_res.stdout)
                        if missing_dep:
                            self.on_event("DEPENDENCY HEALING", f"Missing dependency detected for Task [{task_id}]: {missing_dep.module_name} (package: {missing_dep.package_name}). Attempting auto-installation...")
                            pkg_mgr = "pip"
                            prof = getattr(orchestrator, "project_profile", None)
                            if prof and isinstance(prof, dict):
                                pkg_mgr = prof.get("package_manager", "pip")
                            heal_res = DependencyHealingEngine.heal_and_retry(
                                failed_command="",
                                stderr=combined_err,
                                sandbox=sandbox,
                                stdout=v_res.stdout,
                                package_manager=pkg_mgr,
                                auto_approve=True,
                            )
                            if heal_res.installed:
                                self.on_event("DEPENDENCY HEALING", f"Successfully installed '{missing_dep.package_name}'. Re-verifying task [{task_id}]...")
                                v_res = orchestrator.verification_gate.verify_task(task)
                    except Exception as e:
                        self.on_event("DEPENDENCY HEALING ERROR", f"Notice: Dependency healing error: {e}")

                # Extract telemetry metrics
                attempt_tools: List[str] = []
                attempt_skills = list(active_skills)
                attempt_observations: List[ObservationRecord] = []
                attempt_errors: List[str] = []
                attempt_token_usage = TokenUsage()

                if exec_error:
                    attempt_errors.append(exec_error)

                if isinstance(res, dict):
                    if "tools_used" in res and isinstance(res["tools_used"], list):
                        attempt_tools = res["tools_used"]
                    elif "tool_history" in res and isinstance(res["tool_history"], list):
                        attempt_tools = list({e.get("tool") for e in res["tool_history"] if e.get("tool")})

                    if "token_usage" in res:
                        tu = res["token_usage"]
                        if isinstance(tu, TokenUsage):
                            attempt_token_usage = tu
                        elif isinstance(tu, dict):
                            attempt_token_usage = TokenUsage.from_dict(tu)
                    else:
                        from ..context.token_estimator import estimate_tokens
                        p_tok = estimate_tokens(json.dumps(task_context, default=str))
                        c_tok = estimate_tokens(json.dumps(res, default=str))
                        attempt_token_usage = TokenUsage(prompt_tokens=p_tok, completion_tokens=c_tok, total_tokens=p_tok + c_tok)

                    if getattr(attempt_token_usage, "cost_usd", 0.0) == 0.0:
                        if "cost_usd" in res:
                            attempt_token_usage.cost_usd = float(res.get("cost_usd", 0.0))
                        else:
                            from ..cost.cost_engine import cost_engine
                            attempt_token_usage.cost_usd = cost_engine.calculate_llm_cost(
                                model=routed_model,
                                prompt_tokens=attempt_token_usage.prompt_tokens,
                                completion_tokens=attempt_token_usage.completion_tokens,
                            )

                    if "observations" in res and isinstance(res["observations"], list):
                        for o in res["observations"]:
                            if isinstance(o, ObservationRecord):
                                attempt_observations.append(o)
                            elif isinstance(o, dict):
                                attempt_observations.append(ObservationRecord.from_dict(o))
                    elif "tool_history" in res and isinstance(res["tool_history"], list):
                        for h in res["tool_history"]:
                            attempt_observations.append(ObservationRecord(
                                turn=h.get("turn", 1),
                                tool_name=h.get("tool", "unknown"),
                                input_args=h.get("args") or {},
                                output_result=h.get("result"),
                                is_error=bool(isinstance(h.get("result"), dict) and h.get("result", {}).get("success") is False),
                            ))

                    if "errors" in res and isinstance(res["errors"], list):
                        for err in res["errors"]:
                            if str(err) not in attempt_errors:
                                attempt_errors.append(str(err))

                if not v_res.passed:
                    for f_reason in v_res.failure_reasons:
                        if f_reason not in attempt_errors:
                            attempt_errors.append(f_reason)

                attempt_id = f"att-{task_id}-{attempt_num}-{uuid.uuid4().hex[:8]}"
                for obs in attempt_observations:
                    obs.attempt_id = attempt_id
                    obs.execution_id = task.execution_id

                attempt_rec = TaskAttemptRecord(
                    attempt_number=attempt_num,
                    agent_name=agent.name,
                    started_at=started_at_str,
                    completed_at=completed_at_str,
                    status="COMPLETED" if v_res.passed else "FAILED",
                    attempt_id=attempt_id,
                    execution_id=task.execution_id,
                    tools_used=attempt_tools,
                    skills_used=attempt_skills,
                    observations=attempt_observations,
                    errors=attempt_errors,
                    verification_result={"passed": v_res.passed, "verified_outputs": v_res.verified_outputs, "failure_reasons": v_res.failure_reasons},
                    token_usage=attempt_token_usage,
                    duration_seconds=attempt_duration,
                    model_used=routed_model,
                    llm_latency_seconds=float(res.get("llm_latency_seconds", 0.0)) if isinstance(res, dict) else 0.0,
                )
                task.record_attempt(attempt_rec)

                if self.telemetry_engine:
                    try:
                        self.telemetry_engine.record_agent_execution(
                            agent_name=agent.name,
                            duration_seconds=attempt_duration,
                            status="COMPLETED" if v_res.passed else "FAILED",
                            prompt_tokens=attempt_token_usage.prompt_tokens,
                            completion_tokens=attempt_token_usage.completion_tokens,
                            cost_usd=getattr(attempt_token_usage, "cost_usd", 0.0),
                        )
                        self.telemetry_engine.record_task_outcome(
                            task_id=task_id,
                            passed=v_res.passed,
                            cost_usd=getattr(attempt_token_usage, "cost_usd", 0.0),
                            is_retry=(task.retry_policy.current_retry > 0),
                        )
                    except Exception:
                        pass

                merged = []
                if v_res.passed:
                    try:
                        merged = sandbox.merge_into_main()
                    except MergeConflictError as mce:
                        v_res.passed = False
                        merge_err = f"Workspace merge conflict on '{mce.filepath}': {mce.message}"
                        v_res.failure_reasons.append(merge_err)
                        attempt_rec.status = "FAILED"
                        attempt_rec.errors.append(merge_err)
                        attempt_rec.verification_result = {
                            "passed": False,
                            "verified_outputs": v_res.verified_outputs,
                            "failure_reasons": v_res.failure_reasons,
                            "merge_conflict": {
                                "filepath": mce.filepath,
                                "base_hash": mce.base_hash,
                                "current_hash": mce.current_hash,
                                "sandbox_id": mce.sandbox_id,
                            },
                        }
                    except Exception as me:
                        v_res.passed = False
                        merge_err = f"Workspace merge error: {str(me)}"
                        v_res.failure_reasons.append(merge_err)
                        attempt_rec.status = "FAILED"
                        attempt_rec.errors.append(merge_err)
                        attempt_rec.verification_result = {
                            "passed": False,
                            "verified_outputs": v_res.verified_outputs,
                            "failure_reasons": v_res.failure_reasons,
                        }

                raw_canc_tok = getattr(orchestrator, "cancellation_token", None)
                has_real_cancelled_token = (
                    hasattr(raw_canc_tok, "is_cancelled")
                    and not hasattr(raw_canc_tok, "_mock_methods")
                    and getattr(raw_canc_tok, "is_cancelled", False) is True
                )
                is_cancelled = (
                    (isinstance(res, dict) and res.get("status") == "CANCELLED")
                    or has_real_cancelled_token
                )

                if is_cancelled:
                    task.state = TaskState.CANCELLED
                    attempt_rec.status = "CANCELLED"
                    self.on_event("TASK CANCELLED", f"Task [{task_id}] execution was CANCELLED.")
                    if getattr(self, "event_bus", None):
                        try:
                            self.event_bus.publish(
                                "TASK_CANCELLED",
                                task_id=task_id,
                                agent_name=agent.name,
                                payload={"reason": res.get("cancel_reason", "Cancelled by user") if isinstance(res, dict) else "Cancelled"},
                            )
                        except Exception:
                            pass
                elif v_res.passed:
                    task_dag.mark_task_completed(
                        task_id,
                        artifacts=[{
                            "verified_outputs": v_res.verified_outputs,
                            "merged_files": merged,
                            "stdout": v_res.stdout,
                        }],
                        result_data=res,
                    )
                    self.on_event("ACCEPTANCE GATE", f"Task [{task_id}] PASSED verification. Outputs: {v_res.verified_outputs}, Merged {len(merged)} file(s).")
                    if getattr(self, "event_bus", None):
                        try:
                            self.event_bus.publish(
                                "TASK_COMPLETED",
                                task_id=task_id,
                                agent_name=agent.name,
                                payload={"verified_outputs": v_res.verified_outputs, "merged_files": merged, "result": res},
                            )
                        except Exception:
                            pass
                    
                    # Evidence-based dynamic DAG pruning
                    ev_info = res.get("evidence") if isinstance(res, dict) else None
                    if not ev_info and isinstance(res, dict) and isinstance(res.get("final_output"), dict):
                        ev_info = res.get("final_output", {}).get("evidence")
                    
                    r_tasks = []
                    if isinstance(res, dict):
                        r_tasks = res.get("redundant_tasks") or []
                    if not r_tasks and isinstance(ev_info, dict):
                        r_tasks = ev_info.get("redundant_tasks") or []
                    if not r_tasks and isinstance(res, dict) and isinstance(res.get("final_output"), dict):
                        r_tasks = res.get("final_output", {}).get("redundant_tasks") or []

                    if (isinstance(ev_info, dict) and ev_info.get("evidence_type") == "GOAL_SATISFIED_EARLY") or r_tasks:
                        if r_tasks:
                            pruned = task_dag.prune_tasks(
                                r_tasks,
                                reason=f"Goal satisfied early by task [{task_id}]: {ev_info.get('proof_citation', '') if isinstance(ev_info, dict) else ''}"
                            )
                            if pruned:
                                self.on_event("DAG PRUNED", f"Task [{task_id}] satisfied goal early. Pruned redundant downstream tasks: {pruned}")
                                if wm:
                                    wm.record_fact(f"Task [{task_id}] satisfied goal early; pruned redundant tasks: {pruned}")

                    if wm:
                        wm.record_fact(f"Task [{task_id}] '{step_name}' completed successfully.")
                        for out_item in task.outputs:
                            wm.record_modified_symbol(out_item)
                    if hasattr(orchestrator, "memory_engine") and orchestrator.memory_engine:
                        try:
                            orchestrator.memory_engine.distill_task_outcome(
                                task_id=task_id,
                                task_info=task_context,
                                result_data=res if isinstance(res, dict) else {"summary": str(res)},
                                success=True,
                            )
                        except Exception:
                            pass
                    if hasattr(orchestrator, "message_bus") and orchestrator.message_bus:
                        from .messaging import StructuredMessage, MessageType
                        orchestrator.message_bus.publish(
                            topic="task_results",
                            message=StructuredMessage(
                                sender=f"{agent.name}:{task_id}",
                                recipient="*",
                                message_type=MessageType.TASK_RESULT,
                                content=f"Task [{task_id}] '{step_name}' completed successfully.",
                                payload={"task_id": task_id, "verified_outputs": v_res.verified_outputs, "merged_files": merged, "result": res},
                                correlation_id=task_id,
                            )
                        )
                    if hasattr(orchestrator, "swarm_coordinator") and orchestrator.swarm_coordinator and hasattr(orchestrator.swarm_coordinator, "blackboard"):
                        try:
                            orchestrator.swarm_coordinator.blackboard.post(
                                task_id=task_id,
                                key="deliverables",
                                value={
                                    "verified_outputs": v_res.verified_outputs,
                                    "merged_files": merged,
                                    "agent": agent.name,
                                    "summary": res.get("summary", "") if isinstance(res, dict) else str(res),
                                }
                            )
                        except Exception:
                            pass
                else:
                    if wm and v_res.failure_reasons:
                        wm.record_pitfall(f"Task [{task_id}] '{step_name}' failed verification: {'; '.join(v_res.failure_reasons[:2])}")
                    if hasattr(orchestrator, "memory_engine") and orchestrator.memory_engine:
                        try:
                            orchestrator.memory_engine.distill_task_outcome(
                                task_id=task_id,
                                task_info=task_context,
                                result_data=res if isinstance(res, dict) else {"summary": str(res)},
                                success=False,
                                error_message="; ".join(v_res.failure_reasons),
                            )
                        except Exception:
                            pass
                    if task.retry_policy.current_retry < task.retry_policy.max_retries:
                        task.retry_policy.current_retry += 1
                        if self.telemetry_engine:
                            try:
                                self.telemetry_engine.record_retry(
                                    task_id=task_id,
                                    retry_number=task.retry_policy.current_retry,
                                    agent_name=agent.name,
                                )
                            except Exception:
                                pass
                        task.state = TaskState.READY
                        self.on_event("ACCEPTANCE GATE", f"Task [{task_id}] verification failed (Retry {task.retry_policy.current_retry}/{task.retry_policy.max_retries}): {'; '.join(v_res.failure_reasons)}")
                    else:
                        task_dag.mark_task_failed(task_id, error_message="; ".join(v_res.failure_reasons))
                        self.on_event("ACCEPTANCE GATE", f"Task [{task_id}] FAILED verification: {'; '.join(v_res.failure_reasons)}")
                        if getattr(self, "event_bus", None):
                            try:
                                self.event_bus.publish(
                                    "TASK_FAILED",
                                    task_id=task_id,
                                    agent_name=agent.name,
                                    payload={"error": "; ".join(v_res.failure_reasons), "failure_reasons": v_res.failure_reasons},
                                )
                            except Exception:
                                pass
                    if hasattr(orchestrator, "message_bus") and orchestrator.message_bus:
                        from .messaging import StructuredMessage, MessageType
                        orchestrator.message_bus.publish(
                            topic="task_failures",
                            message=StructuredMessage(
                                sender=f"{agent.name}:{task_id}",
                                recipient="*",
                                message_type=MessageType.OBSERVATION,
                                content=f"Task [{task_id}] verification failed: {'; '.join(v_res.failure_reasons)}",
                                payload={"task_id": task_id, "failure_reasons": v_res.failure_reasons, "stderr": getattr(v_res, "stderr", "")},
                                correlation_id=task_id,
                            )
                        )
            finally:
                if 'v_res' in locals() and v_res and v_res.passed:
                    task_span.set_status(SpanStatus.OK)
                elif 'v_res' in locals() and v_res and v_res.failure_reasons:
                    task_span.set_status(SpanStatus.ERROR, "; ".join(v_res.failure_reasons))
                elif exec_error:
                    task_span.set_status(SpanStatus.ERROR, exec_error)
                task_span_scope.__exit__(None, None, None)
                sandbox.cleanup()
                clear_log_context()

            return {"task_id": task_id, "step_name": step_name, "result": res}


        # Dispatch Wave
        if effective_workers > 1:
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                futures = {executor.submit(_execute_single_task, task): task for task in dispatched_tasks}
                for future in as_completed(futures):
                    try:
                        out = future.result()
                        wave_results[out["task_id"]] = out["result"]
                        wave_results[out["step_name"]] = out["result"]
                    except Exception as e:
                        t = futures[future]
                        task_dag.mark_task_failed(t.task_id, error_message=str(e))
                        self.on_event("TASK ERROR", f"Execution error in Task [{t.task_id}]: {str(e)}")
                        if getattr(self, "event_bus", None):
                            try:
                                self.event_bus.publish(
                                    "TASK_FAILED",
                                    task_id=t.task_id,
                                    agent_name=getattr(t, "owner_agent", "UNKNOWN"),
                                    payload={"error": str(e)},
                                )
                            except Exception:
                                pass
        else:
            for task in dispatched_tasks:
                try:
                    out = _execute_single_task(task)
                    wave_results[out["task_id"]] = out["result"]
                    wave_results[out["step_name"]] = out["result"]
                except Exception as e:
                    task_dag.mark_task_failed(task.task_id, error_message=str(e))
                    self.on_event("TASK ERROR", f"Execution error in Task [{task.task_id}]: {str(e)}")
                    if getattr(self, "event_bus", None):
                        try:
                            self.event_bus.publish(
                                "TASK_FAILED",
                                task_id=task.task_id,
                                agent_name=getattr(task, "owner_agent", "UNKNOWN"),
                                payload={"error": str(e)},
                            )
                        except Exception:
                            pass

        # Combine updated step results
        combined_step_results = {**existing_step_results, **wave_results}
        completed_tasks = [t.to_dict() for t in task_dag.list_tasks() if t.state == TaskState.COMPLETED]
        updated_subtasks = task_dag.to_list()

        self.on_event(
            "CONCURRENT WAVE COMPLETE",
            f"Completed wave. Total completed tasks: {len(completed_tasks)}/{len(updated_subtasks)}"
        )

        return {
            "subtasks": updated_subtasks,
            "task_decomposition": updated_subtasks,
            "current_subtask_index": len(completed_tasks),
            "completed_subtasks": completed_tasks,
            "step_results": combined_step_results,
        }
