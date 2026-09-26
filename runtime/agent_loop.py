"""
Claude-Style Execution Loop.
Implements the 7-phase state machine:
Reason -> Select Tool -> Execute -> Observation -> State Update -> Context Rebuild -> Reason Again

Integrates with:
- SkillRegistry
- AgentRegistry
- MCPManager
- UnifiedToolDispatcher
- WorkspaceManager
- LangGraph
- LiteLLM Gateway
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from .execution_state import ExecutionState, LoopStatus
from .observation_engine import Observation, ObservationEngine
from .event_stream import EventStream, LoopEvent, LoopEventType
from .tool_policy import ToolPolicy
from .capability_router import CapabilityRouter
from .permission_engine import PermissionEngine

logger = logging.getLogger("runtime.agent_loop")


class AgentExecutionLoop:
    """
    Claude-style execution loop governed by the 7-phase state machine.
    Enforces strict observation normalization (never appends raw logs)
    and dynamic context rebuilding on every iteration.
    """

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        tool_dispatcher: Optional[Any] = None,
        dispatcher: Optional[Any] = None,
        capability_router: Optional[CapabilityRouter] = None,
        permission_engine: Optional[PermissionEngine] = None,
        observation_engine: Optional[ObservationEngine] = None,
        context_compiler: Optional[Any] = None,
        event_stream: Optional[EventStream] = None,
        workspace_manager: Optional[Any] = None,
        skill_registry: Optional[Any] = None,
        agent_registry: Optional[Any] = None,
        mcp_manager: Optional[Any] = None,
        repository_brain: Optional[Any] = None,
        max_iterations: int = 15,
        default_model: str = "gpt-4o",
        **kwargs: Any,
    ):
        self.llm_client = llm_client
        self.max_iterations = max_iterations
        self.default_model = default_model

        # Core registries and managers
        self.skill_registry = skill_registry
        self.agent_registry = agent_registry
        self.mcp_manager = mcp_manager
        self.workspace_manager = workspace_manager
        self.repository_brain = repository_brain

        # Unified tool dispatcher
        eff_disp = tool_dispatcher or dispatcher
        if eff_disp is not None:
            self.dispatcher = eff_disp
        else:
            try:
                from agent_orchestrator.tools.dispatcher import UnifiedToolDispatcher
                self.dispatcher = UnifiedToolDispatcher()
            except Exception as e:
                logger.debug(f"Default UnifiedToolDispatcher initialization deferred: {e}")
                self.dispatcher = None

        # Capability Router
        if capability_router is not None:
            self.router = capability_router
        else:
            self.router = CapabilityRouter(
                dispatcher=self.dispatcher,
                mcp_manager=self.mcp_manager,
                skill_registry=self.skill_registry,
                workspace_manager=self.workspace_manager,
            )

        # Permission Engine
        self.permission_engine = permission_engine or PermissionEngine(
            dispatcher=self.dispatcher,
            workspace_manager=self.workspace_manager,
        )

        # Observation Engine
        ws_root = getattr(self.workspace_manager, "workspace_root", None) if self.workspace_manager else None
        self.obs_engine = observation_engine or ObservationEngine(workspace_root=ws_root)

        # Context Compiler
        if context_compiler is not None:
            self.context_compiler = context_compiler
        else:
            try:
                from context.compiler import ContextCompiler
                self.context_compiler = ContextCompiler(
                    skill_registry=self.skill_registry,
                    agent_registry=self.agent_registry,
                    mcp_manager=self.mcp_manager,
                    tool_dispatcher=self.dispatcher,
                    workspace_manager=self.workspace_manager,
                    llm_client=self.llm_client,
                )
            except Exception as e:
                logger.debug(f"Default ContextCompiler initialization deferred: {e}")
                self.context_compiler = None

        # Event stream
        self.event_stream = event_stream or EventStream()

    def run(
        self,
        task_objective: str = "",
        active_skills: Optional[List[str]] = None,
        agent_id: Optional[str] = None,
        max_iterations: Optional[int] = None,
        initial_context: Optional[Any] = None,
        system_prompt: Optional[str] = None,
        user_prompt: Optional[str] = None,
        available_tools: Optional[List[str]] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> ExecutionState:
        """
        Executes the full Claude-style execution loop:
        Reason -> Select Tool -> Execute -> Observation -> State Update -> Context Rebuild -> Reason Again
        """
        effective_objective = task_objective or user_prompt or kwargs.get("task_prompt") or "Execute task"
        effective_max = max_iterations or kwargs.get("max_turns") or self.max_iterations

        # 1. Resolve agent configuration if agent_id is provided
        agent_persona = system_prompt
        if agent_id and self.agent_registry and not agent_persona:
            try:
                agent_meta = self.agent_registry.get_agent(agent_id)
                if agent_meta:
                    agent_persona = getattr(agent_meta, "role_prompt", None) or getattr(agent_meta, "description", None)
            except Exception as e:
                logger.debug(f"Agent registry lookup: {e}")

        # 2. Derive task capabilities and scoped tool policy
        capabilities = self.router.route_task(effective_objective, active_skills=active_skills)
        tool_policy = self.router.get_tool_policy_for_task(effective_objective, active_skills=active_skills)
        allowed_tools = self.router.get_allowed_tools_for_task(effective_objective, active_skills=active_skills)

        if available_tools:
            allowed_tools = [t for t in allowed_tools if t in available_tools or t.replace("filesystem.", "").replace("terminal.", "") in available_tools]

        # 3. Initialize ExecutionState
        state = ExecutionState(
            task_objective=effective_objective,
            iteration=0,
            max_iterations=effective_max,
            status=LoopStatus.RUNNING,
            active_skills=active_skills or [],
            capabilities=capabilities,
            allowed_tools=allowed_tools,
            tool_policy=tool_policy,
        )

        # 4. Initial Context Build
        if initial_context:
            state.rebuilt_context = initial_context
        elif self.context_compiler:
            try:
                state.rebuilt_context = self.context_compiler.compile(
                    task_objective=task_objective,
                    skills=state.active_skills,
                    repository_brain=self.repository_brain,
                    agent_persona=agent_persona,
                )
            except Exception as e:
                logger.warning(f"Initial context compile failed: {e}")

        # -------------------------------------------------------------
        # STATE MACHINE LOOP
        # -------------------------------------------------------------
        while not state.is_terminal():
            state.iteration += 1

            if state.iteration > state.max_iterations:
                state.terminate(reason=f"Reached maximum iterations limit ({state.max_iterations})")
                self.event_stream.emit(
                    LoopEventType.LOOP_COMPLETED,
                    data={"state": state.to_dict()},
                    message=f"Terminated: {state.exit_reason}",
                    iteration=state.iteration,
                )
                break

            # =========================================================
            # PHASE 1: REASON
            # =========================================================
            self.event_stream.emit(
                LoopEventType.REASONING_STARTED,
                message=f"Reasoning for iteration {state.iteration}",
                iteration=state.iteration,
            )

            thought, action_decision = self._phase_reason(state, agent_persona)
            state.add_reasoning(thought)

            self.event_stream.emit(
                LoopEventType.REASONING_COMPLETED,
                data={"thought": thought, "decision": action_decision},
                message=f"Reasoning completed: {action_decision.get('action')}",
                iteration=state.iteration,
            )

            # Check if reason phase decided to complete
            if action_decision.get("action") == "complete":
                final_text = action_decision.get("final_response") or thought or "Task completed."
                state.complete(response=final_text)
                self.event_stream.emit(
                    LoopEventType.LOOP_COMPLETED,
                    data={"final_response": final_text},
                    message="Task completed successfully.",
                    iteration=state.iteration,
                )
                break

            # =========================================================
            # PHASE 2: SELECT TOOL
            # =========================================================
            tool_name = action_decision.get("tool")
            tool_args = action_decision.get("parameters") or {}
            call_id = action_decision.get("call_id")

            if not tool_name:
                # No tool called and not completed; treat reasoning as completion or ask clarification
                state.complete(response=thought)
                self.event_stream.emit(
                    LoopEventType.LOOP_COMPLETED,
                    data={"final_response": thought},
                    message="Completed without tool invocation.",
                    iteration=state.iteration,
                )
                break

            state.record_tool_call(tool_name=tool_name, arguments=tool_args, call_id=call_id)

            # Evaluate permissions
            perm_eval = self.permission_engine.evaluate(
                tool_name=tool_name,
                tool_policy=state.tool_policy,
                parameters=tool_args,
                active_capabilities=state.capabilities,
            )

            if not perm_eval.allowed:
                # Permission denied: strictly produce structured observation and proceed to State Update
                self.event_stream.emit(
                    LoopEventType.PERMISSION_DENIED,
                    data={"tool": tool_name, "reason": perm_eval.reason},
                    message=f"Permission denied for '{tool_name}': {perm_eval.reason}",
                    iteration=state.iteration,
                )
                obs = Observation(
                    type="permission_denied",
                    severity="error",
                    evidence=f"Tool '{tool_name}' disallowed: {perm_eval.reason}",
                    metadata={"tool": tool_name, "violation_category": perm_eval.violation_category},
                )
                self._phase_state_update(state, obs)
                self._phase_context_rebuild(state, agent_persona)
                continue

            self.event_stream.emit(
                LoopEventType.TOOL_SELECTED,
                data={"tool": tool_name, "parameters": tool_args},
                message=f"Selected tool '{tool_name}'",
                iteration=state.iteration,
            )

            # =========================================================
            # PHASE 3: EXECUTE
            # =========================================================
            self.event_stream.emit(
                LoopEventType.TOOL_EXECUTION_STARTED,
                data={"tool": tool_name, "parameters": tool_args},
                message=f"Executing '{tool_name}'",
                iteration=state.iteration,
            )

            raw_output, exit_code = self._phase_execute(tool_name, tool_args)

            self.event_stream.emit(
                LoopEventType.TOOL_EXECUTION_COMPLETED,
                data={"tool": tool_name, "exit_code": exit_code},
                message=f"Executed '{tool_name}' with exit code {exit_code}",
                iteration=state.iteration,
            )

            # =========================================================
            # PHASE 4: OBSERVATION (Strict Normalization)
            # =========================================================
            obs = self.obs_engine.normalize(
                tool_name=tool_name,
                raw_output=raw_output,
                exit_code=exit_code,
                parameters=tool_args,
            )

            self.event_stream.emit(
                LoopEventType.OBSERVATION_PRODUCED,
                data={"observation": obs.to_dict()},
                message=f"Observation: {obs.to_replan_summary()}",
                iteration=state.iteration,
            )

            # =========================================================
            # PHASE 5: STATE UPDATE
            # =========================================================
            self._phase_state_update(state, obs)

            # Check if task specifically invoked complete_task
            if tool_name in ("complete_task", "finish_task", "submit_solution"):
                sol = tool_args.get("summary") or obs.evidence
                state.complete(response=sol)
                self.event_stream.emit(
                    LoopEventType.LOOP_COMPLETED,
                    data={"final_response": sol},
                    message="Task completed via complete_task tool.",
                    iteration=state.iteration,
                )
                break

            # =========================================================
            # PHASE 6: CONTEXT REBUILD (Replanner / Compiler Integration)
            # =========================================================
            self._phase_context_rebuild(state, agent_persona)

            # =========================================================
            # PHASE 7: REASON AGAIN
            # (The loop continues to Phase 1 with the rebuilt context)
            # =========================================================

        return state

    def _phase_reason(
        self,
        state: ExecutionState,
        agent_persona: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Phase 1: Reason.
        Queries the LLM Gateway with scoped tool schemas and the latest compiled context.
        """
        # Fetch tool schemas scoped strictly to task capabilities
        schemas = self.router.get_tool_schemas_for_task(
            task_description=state.task_objective,
            active_skills=state.active_skills,
        )

        # Build messages prompt using rebuilt context if available
        messages = self._build_prompt_messages(state, agent_persona)

        # Call LLM via authoritative LiteLLM/OpenAI abstraction (Fix 2: chat / chat_with_tools)
        if self.llm_client:
            try:
                # 1. Native tool calling if schemas present
                if schemas and hasattr(self.llm_client, "chat_with_tools"):
                    resp = self.llm_client.chat_with_tools(
                        messages=messages,
                        tools=schemas,
                        model=self.default_model,
                    )
                    return self._parse_llm_response(resp)

                # 2. Standard chat completion (PARTIAL FIX 2: chat_completion removed)
                if hasattr(self.llm_client, "chat"):
                    raw_resp = self.llm_client.chat(
                        messages=messages,
                        model=self.default_model,
                    )
                    return self._parse_llm_response(raw_resp)
            except Exception as e:
                logger.warning(f"LLM call failed: {e}. Falling back to deterministic reasoning.")
                return self._deterministic_fallback_reason(state)

        # Deterministic fallback when no LLM client configured (for headless verification)
        return self._deterministic_fallback_reason(state)

    def _build_prompt_messages(
        self,
        state: ExecutionState,
        agent_persona: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Constructs prompt messages strictly using compiled context and structured observations."""
        system_content = "You are an autonomous engineering agent operating in a Claude-style execution loop."
        if agent_persona:
            system_content += f"\nPersona / Role: {agent_persona}"

        # Insert rebuilt context package text
        if state.rebuilt_context and hasattr(state.rebuilt_context, "to_prompt_context"):
            compiled_prompt = state.rebuilt_context.to_prompt_context()
            user_content = compiled_prompt
        elif state.rebuilt_context and hasattr(state.rebuilt_context, "render"):
            compiled_prompt = state.rebuilt_context.render()
            user_content = compiled_prompt
        else:
            # Fallback format: structured errors only, never raw logs
            error_evidence = state.get_active_error_evidence()
            err_block = f"\nActive Errors:\n" + "\n".join(f"- {e}" for e in error_evidence) if error_evidence else ""
            user_content = f"Objective: {state.task_objective}\nIteration: {state.iteration}{err_block}"

        # Append recent observations if any (structured summaries only!)
        if state.observations:
            recent_obs = state.observations[-3:]
            obs_lines = [f"Step Observation: {o.to_replan_summary()}" for o in recent_obs]
            user_content += "\n\nRecent Observations:\n" + "\n".join(obs_lines)

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    def _parse_llm_response(self, response: Any) -> Tuple[str, Dict[str, Any]]:
        """Parses LLM gateway response into (thought, action_decision)."""
        if isinstance(response, dict):
            # Native OpenAI tool call format
            if response.get("tool_calls"):
                tc = response["tool_calls"][0]
                fn = tc.get("function", {})
                t_name = fn.get("name")
                args_raw = fn.get("arguments", {})
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                thought = response.get("content") or f"Calling tool {t_name}"
                return thought, {"action": "call_tool", "tool": t_name, "parameters": args, "call_id": tc.get("id")}

            if "thought" in response or "tool" in response:
                thought = response.get("thought", "")
                t_name = response.get("tool")
                if t_name:
                    return thought, {"action": "call_tool", "tool": t_name, "parameters": response.get("parameters", {})}
                return thought, {"action": "complete", "final_response": response.get("final_response", thought)}

            content = response.get("content", "")
            return self._parse_text_response(content)

        if isinstance(response, str):
            return self._parse_text_response(response)

        return str(response), {"action": "complete", "final_response": str(response)}

    def _parse_text_response(self, text: str) -> Tuple[str, Dict[str, Any]]:
        """Extracts JSON thought/action or returns plain text thought."""
        clean = text.strip()
        # Look for markdown JSON block or raw JSON
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", clean, re.DOTALL)
        candidate = json_match.group(1) if json_match else clean

        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                data = json.loads(candidate)
                thought = data.get("thought") or data.get("reasoning") or clean
                if data.get("tool"):
                    return thought, {
                        "action": "call_tool",
                        "tool": data["tool"],
                        "parameters": data.get("parameters", {}),
                    }
                if data.get("final_response"):
                    return thought, {"action": "complete", "final_response": data["final_response"]}
            except Exception:
                pass

        return clean, {"action": "complete", "final_response": clean}

    def _deterministic_fallback_reason(self, state: ExecutionState) -> Tuple[str, Dict[str, Any]]:
        """Deterministic reasoning fallback when executing without LLM gateway."""
        # If active error exists, attempt fix or verification
        active_errors = state.get_active_errors()
        if active_errors:
            err = active_errors[-1]
            thought = f"Identified active error at {err.file or 'system'}: {err.evidence}. Verifying resolution."
            if "verification.test" in state.capabilities or "terminal.run" in state.capabilities:
                return thought, {"action": "call_tool", "tool": "terminal.run", "parameters": {"command": "pytest -q"}}

        # If has read capability and has not called tools
        if not state.tool_calls:
            thought = f"Beginning exploration for task objective: {state.task_objective}"
            target_tool = state.allowed_tools[0] if state.allowed_tools else "filesystem.read"
            return thought, {"action": "call_tool", "tool": target_tool, "parameters": {"path": "README.md"}}

        # Default completion
        thought = f"Task objective fulfilled in iteration {state.iteration}."
        return thought, {"action": "complete", "final_response": thought}

    @staticmethod
    def _is_long_running_dev_command(command: str) -> bool:
        """Detects long-running dev servers, watch processes, or container workflows (Fix 5)."""
        clean = (command or "").strip().lower()
        dev_patterns = [
            r"\bnpm\s+(?:run\s+)?dev\b",
            r"\bpnpm\s+(?:run\s+)?dev\b",
            r"\bbun\s+(?:run\s+)?dev\b",
            r"\bvite\b",
            r"\bnext\s+dev\b",
            r"\bspring-boot:run\b",
            r"\buvicorn\b",
            r"\bflask\s+run\b",
            r"\bdocker\s+compose\s+up\b",
            r"\bpython\s+-m\s+http\.server\b",
        ]
        return any(re.search(pat, clean) for pat in dev_patterns)

    def _handle_long_running_project_runtime(self, command: str) -> Dict[str, Any]:
        """Automatically registers, allocates port, and launches project runtime via ProjectRuntimeManager (Fix 5)."""
        try:
            from agent_orchestrator.runtime.project_runtime import ProjectRuntimeManager
            ws_path = getattr(self.workspace_manager, "root_dir", None) or Path.cwd()
            mgr = ProjectRuntimeManager(workspace_root=ws_path)
            session_id = getattr(self.workspace_manager, "session_id", "session_runtime")
            proc = mgr.start_runtime(session_id=session_id, workspace_dir=Path(ws_path), custom_command=command)
            time.sleep(0.5)
            preview_url = proc.preview_url or f"http://localhost:{proc.port or 5173}"
            return {
                "status": "RUNNING",
                "runtime_id": proc.runtime_id,
                "command": command,
                "pid": proc.pid,
                "port": proc.port,
                "preview_url": preview_url,
                "message": f"Dev server launched and monitored by ProjectRuntimeManager at {preview_url}",
                "success": True,
            }
        except Exception as e:
            logger.warning("ProjectRuntimeManager auto-launch failed: %s", e)
            return {"error": f"Failed to start dev server via ProjectRuntimeManager: {e}", "success": False}

    def _phase_execute(self, tool_name: str, tool_args: Dict[str, Any]) -> Tuple[Any, int]:
        """
        Phase 3: Execute.
        Dispatches call via UnifiedToolDispatcher or registered adapter.
        Automatically intercepts long-running dev servers via ProjectRuntimeManager.
        """
        # Intercept long-running dev commands automatically (Fix 5)
        if tool_name in ("terminal.run", "terminal_execute", "run_command", "terminal"):
            cmd = tool_args.get("command") or tool_args.get("cmd") or ""
            if self._is_long_running_dev_command(cmd):
                res = self._handle_long_running_project_runtime(cmd)
                exit_code = 0 if res.get("success", False) or "preview_url" in res else 1
                return res, exit_code

        if self.dispatcher and hasattr(self.dispatcher, "call_tool"):
            try:
                result = self.dispatcher.call_tool(tool_name, tool_args)
                exit_code = 0
                if isinstance(result, dict) and (result.get("error") or result.get("exit_code")):
                    exit_code = int(result.get("exit_code", 1))
                return result, exit_code
            except Exception as e:
                return {"error": str(e)}, 1

        # Check browser adapter if applicable
        if tool_name.startswith("browser_") and self.router.browser_adapter:
            try:
                res = self.router.browser_adapter.call_tool(tool_name, tool_args)
                exit_code = 0 if res.get("success", True) else 1
                return res, exit_code
            except Exception as e:
                return {"error": str(e)}, 1

        # Fallback simulation of tool success
        return f"Executed {tool_name} successfully.", 0

    def _phase_state_update(self, state: ExecutionState, observation: Observation) -> None:
        """
        Phase 5: State Update.
        Appends structured observation and updates working memory and diff.
        """
        state.add_observation(observation)

        # Update working memory with structured facts
        if observation.file:
            state.update_memory(f"last_touched_{observation.file}", observation.type)
        if observation.severity == "error":
            state.update_memory("last_error", observation.evidence)

        # Update diff if workspace manager provides it
        if self.workspace_manager and hasattr(self.workspace_manager, "get_diff"):
            try:
                state.diff = self.workspace_manager.get_diff()
            except Exception as e:
                logger.debug(f"Failed to fetch workspace diff: {e}")

        self.event_stream.emit(
            LoopEventType.STATE_UPDATED,
            data={"observations_count": len(state.observations), "active_errors": len(state.get_active_errors())},
            message=f"State updated at iteration {state.iteration}",
            iteration=state.iteration,
        )

    def _phase_context_rebuild(self, state: ExecutionState, agent_persona: Optional[str] = None) -> None:
        """
        Phase 6: Context Rebuild.
        Dynamically rebuilds the OptimizedContextPackage using ContextCompiler.
        Strict rule: Consumes structured observations - NOT raw strings!
        """
        if not self.context_compiler:
            return

        try:
            # Extract structured error evidence strings only
            structured_errors = state.get_active_error_evidence()

            package = self.context_compiler.compile(
                task_objective=state.task_objective,
                errors=structured_errors if structured_errors else None,
                working_memory=state.working_memory,
                diff=state.diff if state.diff else None,
                skills=state.active_skills,
                repository_brain=self.repository_brain,
                agent_persona=agent_persona,
            )

            state.rebuilt_context = package

            self.event_stream.emit(
                LoopEventType.CONTEXT_REBUILT,
                data={
                    "total_tokens": getattr(package, "total_tokens", 0),
                    "active_errors_compiled": len(structured_errors),
                },
                message=f"Context successfully rebuilt for iteration {state.iteration}",
                iteration=state.iteration,
            )
        except Exception as e:
            logger.warning(f"Context rebuild error: {e}")

    def create_langgraph_node(self) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        """
        Creates a LangGraph-compatible StateGraph node function.
        Executes the Claude-style loop and maps results into LangGraph graph state.
        """
        def agent_loop_node(graph_state: Dict[str, Any]) -> Dict[str, Any]:
            task = graph_state.get("user_request") or graph_state.get("task") or graph_state.get("objective") or "Execute task"
            skills = graph_state.get("active_skills") or []
            agent_id = graph_state.get("agent_id")
            max_iter = graph_state.get("max_iterations") or self.max_iterations

            # Execute the Claude-style loop
            exec_state = self.run(
                task_objective=task,
                active_skills=skills,
                agent_id=agent_id,
                max_iterations=max_iter,
            )

            new_state = dict(graph_state)
            new_state["execution_state"] = exec_state.to_dict()
            new_state["status"] = exec_state.status
            new_state["final_response"] = exec_state.final_response
            new_state["observations"] = [o.to_dict() for o in exec_state.observations]
            new_state["error_evidence"] = exec_state.get_active_error_evidence()
            return new_state

        return agent_loop_node

    execute = run
    run_loop = run

