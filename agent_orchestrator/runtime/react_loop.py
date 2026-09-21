"""
Multi-turn ReAct (Reasoning + Action) execution loop with active tool calling and observation feedback.
Supports both BuiltinToolRegistry and UnifiedToolDispatcher (MCP + Local tools).
"""
from __future__ import annotations
import fnmatch
import json
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple, Union
if TYPE_CHECKING:
    from ..llm import LLMClient
from .task_graph import TokenUsage, ObservationRecord
from .validator import DeliverableValidator, ValidationReport
from .react_step import ReActStep, ReActTrajectory
from .json_repair import loads_repaired


import hashlib
import re


class ContextCompactor:
    """
    Compacts and prunes conversation messages when context size approaches model limits.
    Preserves system prompt, task instructions, and recent turns while intelligently
    summarizing older tool observations with structured semantics and deduplication.
    """

    @classmethod
    def _extract_semantic_summary(cls, tool_name: str, content: str) -> str:
        """Extracts structured semantic facts from tool output."""
        raw_content = content
        if "<tool_observation" in raw_content and "</tool_observation>" in raw_content:
            inner = re.sub(r"^<tool_observation[^>]*>\s*", "", raw_content, flags=re.DOTALL)
            inner = re.sub(r"\s*</tool_observation>$", "", inner, flags=re.DOTALL)
            inner = re.sub(r"<!--\s*\[SECURITY ADVISORY:[^\]]*\]\s*-->\s*", "", inner)
            raw_content = inner.strip()

        try:
            parsed = json.loads(raw_content)
        except Exception:
            parsed = None

        extracted: List[str] = []

        if isinstance(parsed, dict):
            if parsed.get("error") or parsed.get("is_error"):
                err = str(parsed.get("error") or parsed.get("message") or "")
                extracted.append(f"• Status: ERROR - {err[:150]}")
            elif parsed.get("success") is False:
                extracted.append("• Status: FAILED")
            elif parsed.get("success") is True:
                extracted.append("• Status: SUCCESS")

            if "filepath" in parsed:
                lines = parsed.get("total_lines") or parsed.get("lines")
                extracted.append(f"• File: {parsed['filepath']} ({lines or 'unknown'} lines)")

            if "output" in parsed and isinstance(parsed["output"], str):
                out = parsed["output"]
                if "def " in out or "class " in out:
                    syms = re.findall(r"^\s*(?:def|class)\s+([A-Za-z0-9_]+)", out, re.MULTILINE)[:5]
                    if syms:
                        extracted.append(f"• Top symbols: {', '.join(syms)}")
                elif "Traceback" in out:
                    tb_lines = [l.strip() for l in out.splitlines() if l.strip().startswith("File ") or "Error:" in l]
                    if tb_lines:
                        extracted.append(f"• Traceback summary: {'; '.join(tb_lines[-2:])}")
            elif "files" in parsed and isinstance(parsed["files"], list):
                extracted.append(f"• Files found: {', '.join(str(f)[:30] for f in parsed['files'][:5])}")
        else:
            if "Traceback" in content:
                tb_lines = [l.strip() for l in content.splitlines() if l.strip().startswith("File ") or "Error:" in l]
                if tb_lines:
                    extracted.append(f"• Traceback summary: {'; '.join(tb_lines[-2:])}")
            elif "def " in content or "class " in content:
                syms = re.findall(r"^\s*(?:def|class)\s+([A-Za-z0-9_]+)", content, re.MULTILINE)[:5]
                if syms:
                    extracted.append(f"• Top symbols: {', '.join(syms)}")

        if extracted:
            return "\n" + "\n".join(extracted)
        return ""

    @classmethod
    def compact(cls, messages: List[Dict[str, Any]], max_tokens: int = 64000) -> Tuple[List[Dict[str, Any]], bool]:
        est_tokens = len(json.dumps(messages, default=str)) // 4
        if est_tokens <= max_tokens or len(messages) <= 6:
            return messages, False

        compacted = []
        # Keep first 2 messages (system + user prompt)
        compacted.extend(messages[:2])

        # Middle messages (candidates for compaction)
        middle = messages[2:-4]
        # Recent 4 messages kept intact
        recent = messages[-4:]

        seen_tool_outputs: Dict[str, int] = {}

        for idx, msg in enumerate(middle):
            if msg.get("role") == "tool":
                t_name = msg.get("name", "tool")
                content = str(msg.get("content", ""))
                c_hash = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()

                if c_hash in seen_tool_outputs:
                    ref_idx = seen_tool_outputs[c_hash]
                    summary = f"[Observation compacted: {t_name} output identical to earlier observation in turn {ref_idx}. Full result omitted to preserve context window.]"
                    compacted.append({**msg, "content": summary})
                    continue

                seen_tool_outputs[c_hash] = idx + 2

                if len(content) > 300:
                    semantic_notes = cls._extract_semantic_summary(t_name, content)
                    summary = f"[Observation compacted: {t_name} produced {len(content)} chars of output. Full result omitted to preserve context window.]{semantic_notes}"
                    compacted.append({**msg, "content": summary})
                else:
                    compacted.append(msg)
            else:
                compacted.append(msg)

        compacted.extend(recent)
        return compacted, True


class ReActAgentLoop:
    """
    Executes an agent in an interactive ReAct loop:
    Reasoning -> Native Tool Calling (Action) -> Observation (Tool Result) -> Next Action -> Final Output
    """

    def __init__(
        self,
        llm: LLMClient,
        tool_registry: Any,
        max_turns: int = 15,
        on_step_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        approval_gate: Optional[Any] = None,
        enforce_react: bool = False,
        require_verification: bool = False,
        event_bus: Optional[Any] = None,
        telemetry_engine: Optional[Any] = None,
        **kwargs: Any,
    ):
        self.llm = llm
        self.tool_registry = tool_registry
        self.max_turns = max_turns
        self.on_step = on_step_callback or (lambda action, payload: None)
        self.approval_gate = approval_gate
        self.enforce_react = enforce_react
        self.require_verification = require_verification
        self.event_bus = event_bus or kwargs.get("event_bus") or getattr(tool_registry, "event_bus", None)
        self.telemetry_engine = telemetry_engine or kwargs.get("telemetry_engine") or getattr(tool_registry, "telemetry_engine", None)
        self.resource_budget = kwargs.get("resource_budget")
        self.resource_tracker = kwargs.get("resource_tracker")

    def _extract_thought(
        self,
        llm_response: Optional[Dict[str, Any]],
        content: Any,
        parsed_json: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Extract explicit thought/reasoning from LLM response structure or natural language content."""
        if isinstance(parsed_json, dict):
            for k in ("thought", "reasoning", "plan", "reflection"):
                if parsed_json.get(k):
                    return str(parsed_json[k])
        if isinstance(llm_response, dict):
            for k in ("thought", "reasoning", "plan"):
                if llm_response.get(k):
                    return str(llm_response[k])
        if isinstance(content, str) and content:
            content_str = content.strip()
            # Check for "Thought: ..." prefix
            for line in content_str.splitlines():
                clean = line.strip()
                if clean.lower().startswith("thought:"):
                    return clean[8:].strip()
            # If content is non-empty and not raw JSON, treat it as thought/reasoning
            if not (content_str.startswith("{") and content_str.endswith("}")):
                return content_str
        return ""

    def execute(self, *args, **kwargs) -> Dict[str, Any]:
        """Convenience execution wrapper for run()."""
        if "user_prompt" not in kwargs:
            if "task_prompt" in kwargs:
                kwargs["user_prompt"] = kwargs.pop("task_prompt")
            elif len(args) > 0 and isinstance(args[0], str):
                kwargs["user_prompt"] = args[0]
                args = args[1:]
        if "system_prompt" not in kwargs:
            kwargs["system_prompt"] = "You are an autonomous AI coding agent."
        return self.run(*args, **kwargs)

    def run(
        self,
        system_prompt: str,
        user_prompt: str,
        model: Optional[str] = None,
        available_tools: Optional[List[str]] = None,
        agent_name: Optional[str] = None,
        temperature: float = 0.2,
        permissions: Optional[Any] = None,
        max_turns: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        state_store: Optional[Any] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        checkpoint_manager: Optional[Any] = None,
        workspace: Optional[Any] = None,
        initial_messages: Optional[List[Dict[str, Any]]] = None,
        initial_observations: Optional[List[ObservationRecord]] = None,
        start_turn: int = 0,
        target_contract: Optional[Any] = None,
        approval_gate: Optional[Any] = None,
        task_info: Optional[Any] = None,
        enforce_react: Optional[bool] = None,
        require_tools: Optional[bool] = None,
        require_verification: Optional[bool] = None,
        event_bus: Optional[Any] = None,
        telemetry_engine: Optional[Any] = None,
        agent_contract: Optional[Any] = None,
        pause_checker: Optional[Callable[[], Optional[str]]] = None,
        resource_budget: Optional[Any] = None,
        resource_tracker: Optional[Any] = None,
        cancellation_token: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        active_cancellation_token = cancellation_token or kwargs.get("cancellation_token")
        if event_bus:
            self.event_bus = event_bus
        if telemetry_engine:
            self.telemetry_engine = telemetry_engine
        self.current_agent_contract = agent_contract or kwargs.get("agent_contract")
        self.current_workspace = workspace or getattr(self.tool_registry, "workspace", None)

        # Resolve ResourceBudget and ResourceUsageTracker
        active_budget = resource_budget or kwargs.get("resource_budget") or getattr(self, "resource_budget", None) or getattr(permissions, "resource_budget", None)
        if not active_budget and isinstance(permissions, dict):
            active_budget = permissions.get("resource_budget")
        if not active_budget and isinstance(task_info, dict):
            active_budget = task_info.get("resource_budget")

        from ..security.resource_budget import ResourceBudget, ResourceUsageTracker, BudgetAction
        if active_budget and isinstance(active_budget, dict):
            active_budget = ResourceBudget.from_dict(active_budget)
        elif not active_budget or not isinstance(active_budget, ResourceBudget):
            active_budget = ResourceBudget.unconstrained()

        active_tracker = resource_tracker or getattr(self, "resource_tracker", None) or ResourceUsageTracker(budget=active_budget)
        self.resource_tracker = active_tracker
        self.resource_budget = active_budget

        if self.current_workspace and hasattr(self.current_workspace, "set_resource_tracker"):
            self.current_workspace.set_resource_tracker(active_tracker)
            self.current_workspace.set_resource_budget(active_budget)
        """
        Run multi-turn ReAct loop with native tool calling until the agent completes the task or max turns reached.
        Enforces per-task tool/path permissions and execution turn budgets.
        Supports fine-grained step-level checkpointing and resumption.
        """
        if initial_messages:
            messages: List[Dict[str, Any]] = list(initial_messages)
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        effective_max_turns = max_turns or self.max_turns
        start_time = time.time()
        timeout_budget = timeout_seconds or (int(active_budget.max_runtime_seconds) if active_budget.max_runtime_seconds > 0 else 180)

        effective_enforce_react = (
            self.enforce_react
            if enforce_react is None
            else enforce_react
        )
        if require_tools:
            effective_enforce_react = True
        if isinstance(task_info, dict):
            if task_info.get("enforce_react") or task_info.get("require_tools"):
                effective_enforce_react = True
            elif task_info.get("outputs") and agent_name == "CODER" and enforce_react:
                effective_enforce_react = True

        effective_require_verification = (
            getattr(self, "require_verification", False)
            if require_verification is None
            else require_verification
        )
        if require_tools:
            effective_require_verification = True
        if isinstance(task_info, dict) and "require_verification" in task_info:
            effective_require_verification = bool(task_info.get("require_verification"))

        # Resolve ReasoningConfigContract (Issue #87)
        from ..contracts import ReasoningConfigContract, ReasoningMode
        active_reasoning_cfg = kwargs.get("reasoning_config") or getattr(self, "reasoning_config", None)
        if not active_reasoning_cfg and isinstance(task_info, dict):
            active_reasoning_cfg = task_info.get("reasoning_config")
        if isinstance(active_reasoning_cfg, dict):
            active_reasoning_cfg = ReasoningConfigContract(**active_reasoning_cfg)
        elif not isinstance(active_reasoning_cfg, ReasoningConfigContract):
            active_reasoning_cfg = ReasoningConfigContract(
                min_exploration_turns=kwargs.get("min_exploration_turns", 0),
                enable_self_reflection=kwargs.get("enable_self_reflection", False),
                reflection_prompt=kwargs.get("reflection_prompt"),
            )
        _reflection_completed = False

        # Gather tool schemas
        if hasattr(self.tool_registry, "get_schemas"):
            try:
                schemas = self.tool_registry.get_schemas(agent_name=agent_name)
            except TypeError:
                schemas = self.tool_registry.get_schemas()
        else:
            schemas = []

        if available_tools:
            schemas = [s for s in schemas if s.get("function", {}).get("name") in available_tools]

        history_events: List[Dict[str, Any]] = []
        tools_used: Set[str] = set()
        observations: List[ObservationRecord] = list(initial_observations or [])
        trajectory = ReActTrajectory()
        errors: List[str] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        turn = start_turn
        final_response: Dict[str, Any] = {}
        task_evidence: Optional[Dict[str, Any]] = None
        redundant_tasks: List[str] = []
        total_cost_usd = 0.0
        total_llm_latency = 0.0
        total_tool_latency = 0.0

        from ..cost.cost_engine import cost_engine
        from ..cost.budget_tracker import budget_tracker, BudgetExceededError

        mutating_tools = {
            "write_file", "replace_file_content", "edit_file", "delete_file",
            "insert_lines", "delete_lines", "apply_diff_blocks", "diff_blocks",
            "filesystem_write", "filesystem_delete"
        }
        _read_file_hashes: Dict[str, str] = {}
        _read_file_versions: Dict[str, str] = {}
        _budget_warning_injected = False

        while turn < effective_max_turns:
            # Check cancellation token
            if active_cancellation_token and active_cancellation_token.is_cancelled:
                cancel_reason = active_cancellation_token.cancel_reason or "Execution stopped by user"
                from .execution_frame import AgentExecutionFrame
                frame = AgentExecutionFrame(
                    agent_id=agent_name or "AGENT",
                    role=agent_name or "AGENT",
                    session_id=session_id or "default",
                    task_id=task_id,
                    turn=turn,
                    max_turns=effective_max_turns,
                    messages=list(messages),
                    observations=[o.to_dict() if hasattr(o, "to_dict") else o for o in observations],
                    pause_reason=f"CANCELLED: {cancel_reason}",
                    paused_at=time.time(),
                )
                self.on_step("TASK_CANCELLED", {"turn": turn, "reason": cancel_reason, "task_id": task_id})
                return {
                    "status": "CANCELLED",
                    "execution_frame": frame,
                    "final_output": {"status": "CANCELLED", "cancel_reason": cancel_reason},
                    "turns_taken": turn,
                    "history_events": history_events,
                    "tool_history": history_events,
                    "messages": messages,
                    "token_usage": TokenUsage(
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_prompt_tokens + total_completion_tokens,
                        cost_usd=round(total_cost_usd, 6),
                    ),
                    "cost_usd": round(total_cost_usd, 6),
                    "tools_used": sorted(list(tools_used)),
                    "observations": observations,
                    "trajectory": trajectory,
                    "errors": errors + [f"Task cancelled: {cancel_reason}"],
                    "cancel_reason": cancel_reason,
                    "resource_usage": active_tracker.to_dict() if active_tracker else None,
                }

            # Check pause request from lifecycle manager or agent
            pause_reason = None
            if pause_checker:
                try:
                    pause_reason = pause_checker()
                except Exception:
                    pass
            elif kwargs.get("check_pause"):
                try:
                    pause_reason = kwargs["check_pause"]()
                except Exception:
                    pass

            if pause_reason:
                from .execution_frame import AgentExecutionFrame
                frame = AgentExecutionFrame(
                    agent_id=agent_name or "AGENT",
                    role=agent_name or "AGENT",
                    session_id=session_id or "default",
                    task_id=task_id,
                    turn=turn,
                    max_turns=effective_max_turns,
                    messages=list(messages),
                    observations=[o.to_dict() if hasattr(o, "to_dict") else o for o in observations],
                    pause_reason=pause_reason,
                    paused_at=time.time(),
                )
                self.on_step("AGENT_PAUSED", {"turn": turn, "reason": pause_reason, "frame_id": frame.frame_id})
                return {
                    "status": "PAUSED",
                    "execution_frame": frame,
                    "final_output": {"status": "PAUSED", "pause_reason": pause_reason},
                    "turns_taken": turn,
                    "history_events": history_events,
                    "tool_history": history_events,
                    "messages": messages,
                    "token_usage": TokenUsage(
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_prompt_tokens + total_completion_tokens,
                        cost_usd=round(total_cost_usd, 6),
                    ),
                    "cost_usd": round(total_cost_usd, 6),
                    "tools_used": sorted(list(tools_used)),
                    "observations": observations,
                    "trajectory": trajectory,
                    "errors": errors,
                    "pause_reason": pause_reason,
                    "resource_usage": active_tracker.to_dict() if active_tracker else None,
                }

            if time.time() - start_time > timeout_budget:
                timeout_err = f"Execution timed out after {timeout_budget}s."
                errors.append(timeout_err)
                self.on_step("TIMEOUT", {"turn": turn, "elapsed": time.time() - start_time, "timeout_seconds": timeout_budget})
                break

            # Check resource budget limits before executing next turn (Issue #82)
            if active_tracker and active_budget:
                rt_dec = active_tracker.check_runtime()
                if not rt_dec.allowed:
                    if active_budget.action == BudgetAction.HALT:
                        errors.append(rt_dec.reason)
                        self.on_step("RESOURCE_BUDGET_EXCEEDED", {"turn": turn, "error": rt_dec.reason, "details": rt_dec.to_dict()})
                        break
                    else:
                        self.on_step("RESOURCE_BUDGET_WARNING", {"turn": turn, "reason": rt_dec.reason})

                gen_dec = active_tracker.check_budget()
                if not gen_dec.allowed:
                    if active_budget.action == BudgetAction.HALT:
                        errors.append(gen_dec.reason)
                        self.on_step("RESOURCE_BUDGET_EXCEEDED", {"turn": turn, "error": gen_dec.reason, "details": gen_dec.to_dict()})
                        break
                    else:
                        self.on_step("RESOURCE_BUDGET_WARNING", {"turn": turn, "reason": gen_dec.reason})

            # Check budget limits before executing next turn (Issue #35)
            try:
                budget_tracker.assert_budget(task_id=task_id, agent_name=agent_name)
            except BudgetExceededError as bee:
                budget_err = str(bee)
                errors.append(budget_err)
                self.on_step("BUDGET_EXCEEDED", {"turn": turn, "error": budget_err})
                break

            # Budget Wind-Down Alert when utilization >= 80%
            if active_tracker and not _budget_warning_injected:
                ratios = active_tracker.get_utilization_ratio(current_turn=turn, max_turns=effective_max_turns)
                max_rat = ratios.get("max_ratio", 0.0)
                if max_rat >= 0.80:
                    _budget_warning_injected = True
                    pct = int(round(max_rat * 100))
                    limit_str = f"{active_budget.max_tool_calls_total}" if (active_budget and active_budget.max_tool_calls_total > 0) else "unlimited"
                    wind_down_msg = (
                        f"[Budget Wind-Down Alert]: Task execution has reached {pct}% of allotted budget "
                        f"(turn: {turn}/{effective_max_turns}, tool calls: {active_tracker.total_tool_calls}/{limit_str}). "
                        f"Please synthesize your findings, verify changes, and return your final deliverable before limits are exhausted."
                    )
                    messages.append({"role": "user", "content": wind_down_msg})
                    self.on_step("BUDGET_WIND_DOWN", {"turn": turn, "utilization_percent": pct, "ratios": ratios})

            # Context compaction if messages exceed max_context_tokens
            from ..config import config as orch_cfg
            max_ctx = getattr(orch_cfg, "max_context_tokens", 64000)
            messages, was_compacted = ContextCompactor.compact(messages, max_tokens=max_ctx)
            if was_compacted:
                self.on_step("CONTEXT_COMPACTED", {"turn": turn, "max_context_tokens": max_ctx})

            turn += 1

            # Estimate prompt tokens
            turn_prompt_str = json.dumps(messages, default=str)
            total_prompt_tokens += max(1, len(turn_prompt_str) // 4)

            llm_response = None
            t_llm_start = time.time()
            from ..tracing import get_tracer, SpanType, SpanStatus
            tracer = get_tracer("orchestrator")
            with tracer.start_as_current_span(
                f"LLM Call: {model or 'default'}",
                span_type=SpanType.LLM_CALL,
                attributes={
                    "model": model,
                    "turn": turn,
                    "temperature": temperature,
                    "agent_name": agent_name,
                    "task_id": task_id,
                },
            ) as llm_span:
                if hasattr(self.llm, "chat_with_tools"):
                    try:
                        res = self.llm.chat_with_tools(
                            messages=messages,
                            tools=schemas if schemas else None,
                            tool_choice="auto" if schemas else None,
                            model=model,
                            temperature=temperature,
                        )
                        if isinstance(res, dict) and ("tool_calls" in res or "content" in res):
                            llm_response = res
                            if "usage" in res and isinstance(res["usage"], dict):
                                total_prompt_tokens += res["usage"].get("prompt_tokens", 0)
                                total_completion_tokens += res["usage"].get("completion_tokens", 0)
                    except Exception as e:
                        errors.append(f"LLM tool calling error: {str(e)}")
                        llm_response = None
                        llm_span.set_status(SpanStatus.ERROR, str(e))

                if llm_response is None:
                    try:
                        response_json = self.llm.chat_json(messages, model=model, temperature=temperature)
                    except Exception as e:
                        errors.append(f"LLM chat_json error: {str(e)}")
                        response_json = {}
                        llm_span.set_status(SpanStatus.ERROR, str(e))
                    if not isinstance(response_json, dict):
                        response_json = {"raw_output": response_json}
                    content = json.dumps(response_json, default=str) if response_json else ""
                    tool_calls = []
                    final_out = response_json.get("final_output") or response_json.get("deliverables")

                    if "tool_call" in response_json and isinstance(response_json["tool_call"], dict):
                        tc_fallback = response_json["tool_call"]
                        tool_calls = [{
                            "id": f"call_{int(time.time()*1000)}",
                            "type": "function",
                            "name": tc_fallback.get("name"),
                            "arguments": tc_fallback.get("arguments", {}),
                        }]
                    elif "tool_calls" in response_json and isinstance(response_json["tool_calls"], list):
                        tool_calls = response_json["tool_calls"]
                    elif not tool_calls and (
                        final_out
                        or "summary" in response_json
                        or "verdict" in response_json
                        or "files" in response_json
                        or "phases" in response_json
                        or "functional_requirements" in response_json
                        or "component_structure" in response_json
                    ):
                        final_out = final_out or response_json

                    assistant_msg = {"role": "assistant", "content": content}
                    if tool_calls:
                        assistant_msg["tool_calls"] = [
                            {
                                "id": tc.get("id", f"call_{int(time.time()*1000)}"),
                                "type": "function",
                                "function": {
                                    "name": tc.get("function", {}).get("name") if isinstance(tc.get("function"), dict) else tc.get("name", ""),
                                    "arguments": (
                                        json.dumps(tc.get("function", {}).get("arguments", {}))
                                        if isinstance(tc.get("function"), dict) and isinstance(tc["function"].get("arguments"), dict)
                                        else (
                                            json.dumps(tc.get("arguments", {}))
                                            if isinstance(tc.get("arguments"), dict)
                                            else str(tc.get("arguments", "{}"))
                                        )
                                    ),
                                }
                            }
                            for tc in tool_calls
                        ]

                    llm_response = {
                        "content": content,
                        "tool_calls": tool_calls,
                        "final_output": final_out,
                        "message": assistant_msg,
                    }

                turn_llm_lat = max(0.0, time.time() - t_llm_start)
                total_llm_latency += turn_llm_lat
                llm_span.set_attribute("latency_seconds", turn_llm_lat)
                llm_span.set_attribute("tool_calls_count", len(llm_response.get("tool_calls", [])))
                if llm_span.status == SpanStatus.UNSET.value:
                    llm_span.set_status(SpanStatus.OK)

            content = llm_response.get("content", "")
            tool_calls = llm_response.get("tool_calls", [])
            assistant_msg = llm_response.get("message", {"role": "assistant", "content": content})
            final_out_direct = llm_response.get("final_output")

            # Accumulate completion tokens and compute turn cost
            turn_c_tok = max(1, len(str(content)) // 4)
            total_completion_tokens += turn_c_tok

            turn_p_tok = max(1, len(turn_prompt_str) // 4)
            turn_llm_cost = cost_engine.calculate_llm_cost(
                model=str(model or "auto"),
                prompt_tokens=turn_p_tok,
                completion_tokens=turn_c_tok,
            )
            total_cost_usd += turn_llm_cost
            budget_tracker.record_spend(
                task_id=task_id,
                agent_name=agent_name,
                prompt_tokens=turn_p_tok,
                completion_tokens=turn_c_tok,
                cost_usd=turn_llm_cost,
            )
            if active_tracker and active_budget:
                tok_dec = active_tracker.record_tokens(turn_p_tok, turn_c_tok)
                if not tok_dec.allowed:
                    if active_budget.action == BudgetAction.HALT:
                        errors.append(tok_dec.reason)
                        self.on_step("RESOURCE_BUDGET_EXCEEDED", {"turn": turn, "error": tok_dec.reason, "details": tok_dec.to_dict()})
                        break
                    else:
                        self.on_step("RESOURCE_BUDGET_WARNING", {"turn": turn, "reason": tok_dec.reason})
            if getattr(self, "telemetry_engine", None):
                try:
                    self.telemetry_engine.record_llm_call(
                        model=str(model or "default"),
                        latency_seconds=turn_llm_lat,
                        prompt_tokens=turn_p_tok,
                        completion_tokens=turn_c_tok,
                        cost_usd=turn_llm_cost,
                        agent_name=agent_name,
                    )
                except Exception:
                    pass

            # Append assistant turn to conversation
            messages.append(assistant_msg)

            if final_out_direct and not tool_calls:
                candidate = final_out_direct
                # Deliberative Reasoning exploration gate (Issue #87)
                if active_reasoning_cfg.min_exploration_turns > 0 and turn <= active_reasoning_cfg.min_exploration_turns and not tools_used and turn < effective_max_turns:
                    feedback = (
                        f"Deliberative Reasoning Active: Before finalizing your deliverable, "
                        f"you must inspect the codebase/environment or query relevant tools (e.g. read_file, find_symbol, list_directory, query_codebase_graph) "
                        f"to ground your proposal in actual workspace evidence (Turn {turn}/{active_reasoning_cfg.min_exploration_turns})."
                    )
                    self.on_step("EXPLORATION_REQUIRED", {"turn": turn, "reason": feedback})
                    messages.append({"role": "user", "content": feedback})
                    obs_rec = ObservationRecord(
                        turn=turn,
                        tool_name="deliberative_reasoning",
                        input_args={},
                        output_result={"error": feedback},
                        is_error=True,
                    )
                    observations.append(obs_rec)
                    trajectory.add_step(ReActStep(
                        turn=turn,
                        thought=self._extract_thought(llm_response, content),
                        action_tool="deliberative_reasoning",
                        action_input={},
                        observation={"error": feedback},
                        status="ERROR",
                        is_error=True,
                    ))
                    continue

                # ReAct enforcement: reject premature deliverable exit before tool execution
                if effective_enforce_react and not tools_used and turn < effective_max_turns:
                    feedback = (
                        "You provided a response without creating or modifying the required workspace files via tools. "
                        "Please execute the appropriate tool calls (e.g. write_file, replace_file_content) to implement and verify these changes in the workspace."
                    )
                    self.on_step("TOOL_EXECUTION_REQUIRED", {"turn": turn, "reason": feedback})
                    messages.append({"role": "user", "content": feedback})
                    obs_rec = ObservationRecord(
                        turn=turn,
                        tool_name="react_enforcer",
                        input_args={},
                        output_result={"error": feedback},
                        is_error=True,
                    )
                    observations.append(obs_rec)
                    trajectory.add_step(ReActStep(
                        turn=turn,
                        thought=self._extract_thought(llm_response, content),
                        action_tool="react_enforcer",
                        action_input={},
                        observation={"error": feedback},
                        status="ERROR",
                        is_error=True,
                    ))
                    continue

                # Self-Reflection turn before final delivery (Issue #87)
                if active_reasoning_cfg.enable_self_reflection and not _reflection_completed and turn < effective_max_turns:
                    _reflection_completed = True
                    ref_prompt = active_reasoning_cfg.reflection_prompt or (
                        "Self-Critique & Reflection Turn:\n"
                        "Please critically evaluate your proposed deliverable against the following dimensions before final approval:\n"
                        "1. Invariant & Contract Safety: Does this design/plan violate any existing interfaces, conventions, or constraints?\n"
                        "2. Edge Cases & Failure Modes: What potential edge cases, null/error states, or race conditions might occur?\n"
                        "3. Feasibility & Completeness: Are there any missing dependencies or unaddressed requirements?\n"
                        "Reflect on these questions and provide your verified, refined final deliverable (or make any necessary tool adjustments)."
                    )
                    self.on_step("SELF_REFLECTION", {"turn": turn, "prompt": ref_prompt})
                    messages.append({"role": "user", "content": ref_prompt})
                    obs_rec = ObservationRecord(
                        turn=turn,
                        tool_name="self_reflection",
                        input_args={},
                        output_result={"reflection_prompt": ref_prompt},
                    )
                    observations.append(obs_rec)
                    trajectory.add_step(ReActStep(
                        turn=turn,
                        thought=self._extract_thought(llm_response, content),
                        action_tool="self_reflection",
                        action_input={},
                        observation={"reflection_prompt": ref_prompt},
                        status="SUCCESS",
                    ))
                    continue

                val_report = self._validate_candidate_deliverable(candidate, target_contract, agent_name)
                if val_report and not val_report.is_valid and turn < effective_max_turns:
                    self.on_step("VALIDATION_ERROR", {"turn": turn, "errors": val_report.errors})
                    err_feedback = DeliverableValidator.format_error_feedback(val_report)
                    messages.append({"role": "user", "content": err_feedback})
                    observations.append(ObservationRecord(
                        turn=turn,
                        tool_name="deliverable_validator",
                        input_args={},
                        output_result={"error": val_report.errors_summary()},
                        is_error=True,
                    ))
                    trajectory.add_step(ReActStep(
                        turn=turn,
                        thought=self._extract_thought(llm_response, content),
                        action_tool="deliverable_validator",
                        action_input={},
                        observation={"error": val_report.errors_summary()},
                        status="ERROR",
                        is_error=True,
                    ))
                    continue
                final_response = candidate
                self.on_step("FINISH", {"turn": turn, "final_response": final_response})
                if state_store and session_id and task_id:
                    state_store.save_task_step(
                        session_id=session_id,
                        task_id=task_id,
                        turn=turn,
                        stage="TASK_FINISHED",
                        tool_result=final_response,
                        messages=messages,
                        observations=observations,
                    )
                break

            # 2. Check for native tool calls
            if tool_calls:
                task_completed = False
                # Check tool calls per turn limit (Issue #82)
                if active_budget and active_budget.max_tool_calls_per_turn > 0:
                    if len(tool_calls) > active_budget.max_tool_calls_per_turn:
                        limit = active_budget.max_tool_calls_per_turn
                        orig_len = len(tool_calls)
                        tool_calls = tool_calls[:limit]
                        self.on_step("TOOL_CALLS_THROTTLED", {
                            "turn": turn,
                            "original_count": orig_len,
                            "throttled_count": limit,
                            "reason": f"Tool calls per turn capped at {limit} by resource budget."
                        })

                for tc in tool_calls:
                    t_id = tc.get("id", f"call_{int(time.time()*1000)}")
                    if "function" in tc and isinstance(tc["function"], dict):
                        t_name = tc["function"].get("name", "")
                        raw_args = tc["function"].get("arguments", {})
                    else:
                        t_name = tc.get("name", "")
                        raw_args = tc.get("arguments", {})
                    if isinstance(raw_args, str):
                        try:
                            t_args = loads_repaired(raw_args)
                            if not isinstance(t_args, dict):
                                t_args = {"data": t_args}
                        except Exception:
                            t_args = {"raw": raw_args}
                    else:
                        t_args = raw_args or {}

                    tools_used.add(t_name)

                    self.on_step("TOOL_CALL", {"turn": turn, "tool": t_name, "arguments": t_args, "content": content})
                    if getattr(self, "event_bus", None):
                        try:
                            self.event_bus.publish(
                                "TOOL_CALLED",
                                task_id=task_id,
                                agent_name=agent_name,
                                payload={"tool_name": t_name, "arguments": t_args, "turn": turn},
                            )
                        except Exception:
                            pass

                    # Checkpoint planned tool call
                    if state_store and session_id and task_id:
                        state_store.save_task_step(
                            session_id=session_id,
                            task_id=task_id,
                            turn=turn,
                            stage="ACTION_PLANNED",
                            tool_name=t_name,
                            tool_args=t_args,
                            messages=messages,
                            observations=observations,
                        )

                    # Check cancellation token and tool permissions
                    perm_error = None
                    if active_cancellation_token and active_cancellation_token.is_cancelled:
                        perm_error = f"Tool execution aborted: {active_cancellation_token.cancel_reason or 'Operation cancelled'}"

                    # Check tool/path permissions
                    if not perm_error:
                        perm_error = self._check_permission(t_name, t_args, permissions, workspace=workspace, agent_name=agent_name)

                    # Check resource budget for tool calls and runtime (Issue #82)
                    if not perm_error and active_tracker and active_budget:
                        rt_check = active_tracker.check_runtime()
                        if not rt_check.allowed:
                            if active_budget.action == BudgetAction.HALT:
                                perm_error = rt_check.reason
                            else:
                                self.on_step("RESOURCE_BUDGET_WARNING", {"turn": turn, "reason": rt_check.reason})

                        if not perm_error:
                            tc_dec = active_tracker.record_tool_call(t_name, arguments=t_args)
                            if not tc_dec.allowed:
                                if active_budget.action == BudgetAction.HALT:
                                    perm_error = tc_dec.reason
                                else:
                                    self.on_step("RESOURCE_BUDGET_WARNING", {"turn": turn, "reason": tc_dec.reason})

                    # Check approval gate for potentially destructive actions
                    effective_gate = approval_gate or getattr(self, "approval_gate", None)
                    if not perm_error and effective_gate:
                        try:
                            from .approval_gate import DestructiveActionClassifier, ApprovalRequest
                            classified = DestructiveActionClassifier.classify_action(t_name, t_args)
                            if classified:
                                act_type, risk_lvl, target_item, act_desc = classified
                                app_req = ApprovalRequest(
                                    action_type=act_type,
                                    tool_name=t_name,
                                    target=target_item,
                                    command_or_details=act_desc,
                                    risk_level=risk_lvl,
                                    reason=f"Agent [{agent_name or 'UNKNOWN'}] requested execution of {t_name}",
                                    agent_name=agent_name,
                                    task_id=task_id,
                                )
                                decision = effective_gate.request_approval(app_req)
                                if not decision.approved:
                                    perm_error = f"Approval Denied: Destructive action '{act_type.value}' was rejected by approval gate. Reason: {decision.reason}"
                        except Exception as gate_err:
                            perm_error = f"Approval Gate Error: {str(gate_err)}"
                    t_start = time.time()
                    target_rel = None
                    before_content = None
                    ws_target = workspace or getattr(self.tool_registry, "workspace", None)
                    if t_name in mutating_tools and target_rel is None:
                        target_rel = t_args.get("filepath") or t_args.get("path") or t_args.get("rel_path")
                        if ws_target and target_rel and hasattr(ws_target, "read_file"):
                            try:
                                res = ws_target.read_file(target_rel)
                                if isinstance(res, dict) and res.get("success"):
                                    before_content = res.get("content")
                                elif isinstance(res, str):
                                    before_content = res
                            except Exception:
                                before_content = None

                    if perm_error:
                        tool_result = {"success": False, "error": perm_error}
                        errors.append(perm_error)
                        status_str = "ERROR"
                    else:
                        # Execute tool
                        from ..tracing import get_tracer, SpanType, SpanStatus
                        tracer = get_tracer("orchestrator")
                        with tracer.start_as_current_span(
                            f"Tool: {t_name}",
                            span_type=SpanType.TOOL_CALL,
                            attributes={
                                "tool_name": t_name,
                                "turn": turn,
                                "agent_name": agent_name,
                                "task_id": task_id,
                            },
                        ) as tool_span:
                            try:
                                if t_name in mutating_tools:
                                    if "operation_id" not in t_args:
                                        from .idempotency import compute_operation_id
                                        t_args["operation_id"] = compute_operation_id(task_id or "default", t_name, t_args)
                                    has_exp_ver = bool(t_args.get("expected_version"))
                                    has_exp_hash = bool(t_args.get("expected_hash"))
                                    if not has_exp_ver and not has_exp_hash:
                                        target_p = t_args.get("filepath") or t_args.get("path")
                                        if target_p:
                                            norm_p = target_p.replace("\\", "/").strip("/")
                                            h = _read_file_hashes.get(norm_p) or _read_file_hashes.get(target_p)
                                            if h:
                                                t_args["expected_hash"] = h
                                                t_args["expected_version"] = h
                                from ..reproducibility.provenance import provenance_context
                                prompt_h = hashlib.sha256((system_prompt or "").encode("utf-8")).hexdigest() if system_prompt else None
                                is_mcp = getattr(self.tool_registry, "is_mcp_tool", lambda x: False)(t_name)
                                with provenance_context.scope(
                                    agent_name=agent_name or "AGENT",
                                    model_name=model,
                                    prompt_template_id=f"{agent_name.lower()}_system_prompt" if agent_name else "system_prompt",
                                    prompt_hash=prompt_h,
                                    tool_name=t_name,
                                    tool_source="mcp" if is_mcp else "builtin",
                                ):
                                    tool_result = self.tool_registry.call_tool(t_name, t_args)
                                status_str = "SUCCESS" if not (isinstance(tool_result, dict) and tool_result.get("success") is False) else "ERROR"
                                if status_str == "ERROR" and isinstance(tool_result, dict) and "error" in tool_result:
                                    errors.append(str(tool_result["error"]))
                                    tool_span.set_status(SpanStatus.ERROR, str(tool_result["error"]))
                                else:
                                    tool_span.set_status(SpanStatus.OK)
                            except Exception as e:
                                tool_result = {"success": False, "error": str(e)}
                                errors.append(str(e))
                                status_str = "ERROR"
                                tool_span.set_status(SpanStatus.ERROR, str(e))

                    if t_name in ("read_file", "read_file_tool", "filesystem_read") and isinstance(tool_result, dict):
                        f_hash = tool_result.get("file_hash")
                        f_ver = tool_result.get("version")
                        r_fp = t_args.get("filepath") or t_args.get("path")
                        if f_hash and r_fp:
                            norm_rfp = r_fp.replace("\\", "/").strip("/")
                            _read_file_hashes[norm_rfp] = f_hash
                            _read_file_hashes[r_fp] = f_hash
                            if f_ver:
                                _read_file_versions[norm_rfp] = f_ver
                                _read_file_versions[r_fp] = f_ver

                    from ..security.secrets import secret_manager
                    tool_result = secret_manager.redact_structure(tool_result)
                    if errors:
                        errors = [secret_manager.redact_text(err) for err in errors]

                    t_duration = round(time.time() - t_start, 4)
                    self.on_step("OBSERVATION", {"turn": turn, "tool": t_name, "result": tool_result})
                    if getattr(self, "event_bus", None):
                        try:
                            self.event_bus.publish(
                                "TOOL_COMPLETED",
                                task_id=task_id,
                                agent_name=agent_name,
                                payload={
                                    "tool_name": t_name,
                                    "status": status_str,
                                    "duration_seconds": t_duration,
                                    "result": tool_result,
                                    "turn": turn,
                                },
                            )
                        except Exception:
                            pass

                    # Calculate tool compute cost and record spend (Issue #35)
                    t_cost = cost_engine.calculate_tool_cost(tool_name=t_name, duration_seconds=t_duration)
                    total_cost_usd += t_cost
                    total_tool_latency += t_duration
                    budget_tracker.record_spend(
                        task_id=task_id,
                        agent_name=agent_name,
                        cost_usd=t_cost,
                        tool_name=t_name,
                    )
                    if getattr(self, "telemetry_engine", None):
                        try:
                            self.telemetry_engine.record_tool_call(
                                tool_name=t_name,
                                duration=t_duration,
                                is_error=(status_str == "ERROR"),
                                cost_usd=t_cost,
                            )
                        except Exception:
                            pass

                    # If mutating file tool, record file change and create step snapshot
                    ckpt_id = None
                    if t_name in mutating_tools and status_str == "SUCCESS":
                        after_content = None
                        if ws_target and target_rel and hasattr(ws_target, "read_file"):
                            try:
                                res = ws_target.read_file(target_rel)
                                if isinstance(res, dict) and res.get("success"):
                                    after_content = res.get("content")
                                elif isinstance(res, str):
                                    after_content = res
                            except Exception:
                                after_content = None

                        if target_rel:
                            norm_t = target_rel.replace("\\", "/").strip("/")
                            from ..tools.change_tracker import compute_file_change, compute_sha256
                            if after_content is not None:
                                new_h = compute_sha256(after_content)
                                _read_file_hashes[norm_t] = new_h
                                _read_file_hashes[target_rel] = new_h
                                _read_file_versions[norm_t] = new_h[:12]
                                _read_file_versions[target_rel] = new_h[:12]
                            else:
                                _read_file_hashes.pop(norm_t, None)
                                _read_file_hashes.pop(target_rel, None)
                                _read_file_versions.pop(norm_t, None)
                                _read_file_versions.pop(target_rel, None)

                            change_rec = compute_file_change(target_rel, before_content, after_content)
                            if change_rec and state_store and session_id and task_id and hasattr(state_store, "save_file_change"):
                                state_store.save_file_change(session_id=session_id, task_id=task_id, change=change_rec)

                        # Persist operation record in state store if available
                        op_id = t_args.get("operation_id")
                        if op_id and state_store and hasattr(state_store, "save_operation"):
                            try:
                                from .idempotency import global_operation_ledger
                                rec = global_operation_ledger.get(op_id)
                                if rec:
                                    state_store.save_operation(rec, session_id=session_id, task_id=task_id)
                            except Exception:
                                pass

                        if checkpoint_manager and ws_target and task_id:
                            ckpt_id = f"ckpt-{task_id}-t{turn}-{t_name}"
                            checkpoint_manager.create_snapshot(ckpt_id, ws_target, metadata={"task_id": task_id, "turn": turn, "tool": t_name})

                    current_thought = self._extract_thought(llm_response, content)
                    obs_rec = ObservationRecord(
                        turn=turn,
                        tool_name=t_name,
                        input_args=t_args,
                        output_result=tool_result,
                        status=status_str,
                        is_error=bool(status_str == "ERROR"),
                        duration_seconds=t_duration,
                    )
                    observations.append(obs_rec)
                    trajectory.add_step(ReActStep(
                        turn=turn,
                        thought=current_thought,
                        action_tool=t_name,
                        action_input=t_args,
                        observation=tool_result,
                        duration_seconds=t_duration,
                        status=status_str,
                        is_error=bool(status_str == "ERROR"),
                    ))

                    # Format tool observation with TrustBoundaryEnforcer & ToolProvenance
                    from ..security.trust_boundaries import TrustBoundaryEnforcer, ToolProvenance
                    tool_prov = None
                    if isinstance(tool_result, dict) and "_provenance" in tool_result:
                        tool_prov = tool_result.get("_provenance")
                    elif isinstance(tool_result, dict) and tool_result.get("is_mcp"):
                        tool_prov = ToolProvenance.EXTERNAL_MCP
                    else:
                        reg = getattr(self.tool_registry, "registry", self.tool_registry)
                        entry = reg.get(t_name) if hasattr(reg, "get") else None
                        if entry and getattr(entry, "provenance", None):
                            tool_prov = entry.provenance
                        elif t_name.startswith("mcp_") or t_name.startswith("external_"):
                            tool_prov = ToolProvenance.EXTERNAL_MCP
                        elif t_name in (
                            "query_specification", "query_spec",
                            "query_architecture", "query_arch",
                            "get_task_artifact", "task_artifact",
                            "complete_task", "rollback_to_checkpoint",
                            "execute_rollback", "log_state_transition",
                            "state_transition_logger", "send_agent_message",
                            "query_agent", "publish_finding", "read_inbox",
                        ):
                            tool_prov = ToolProvenance.INTERNAL_CONTROL
                        elif t_name in ("terminal_execute", "run_command", "ast_syntax_check", "static_code_check", "execute_skill_script"):
                            tool_prov = ToolProvenance.EXECUTION_ENVIRONMENT
                        else:
                            tool_prov = ToolProvenance.WORKSPACE_DATA

                    obs_content = TrustBoundaryEnforcer.wrap_tool_observation(t_name, tool_result, provenance=tool_prov)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": t_id,
                        "name": t_name,
                        "content": obs_content,
                    })

                    history_events.append({"turn": turn, "tool": t_name, "args": t_args, "result": tool_result})

                    # Checkpoint tool execution & observation
                    if state_store and session_id and task_id:
                        state_store.save_observation(session_id=session_id, task_id=task_id, observation=obs_rec)
                        state_store.save_task_step(
                            session_id=session_id,
                            task_id=task_id,
                            turn=turn,
                            stage="TOOL_EXECUTED",
                            tool_name=t_name,
                            tool_args=t_args,
                            tool_result=tool_result,
                            messages=messages,
                            observations=observations,
                            checkpoint_id=ckpt_id,
                        )

                    if t_name == "complete_task":
                        candidate = tool_result.get("output", tool_result) if isinstance(tool_result, dict) else tool_result
                        if isinstance(candidate, dict) and isinstance(t_args, dict):
                            merged = dict(t_args)
                            merged.update(candidate)
                            candidate = merged

                        # Evidence stopping verification:
                        # If effective_enforce_react is active and mutating tools were used in this session:
                        used_mutating = bool(tools_used & mutating_tools)
                        used_verification = bool(tools_used & {"ast_syntax_check", "terminal_execute", "run_command", "syntax_check"})
                        
                        # Untrusted tool handling: proof_citation from tool_result is only accepted if provenance is INTERNAL_CONTROL
                        proof_from_result = None
                        if isinstance(tool_result, dict) and tool_result.get("proof_citation"):
                            if tool_prov in (ToolProvenance.INTERNAL_CONTROL, "INTERNAL_CONTROL"):
                                proof_from_result = tool_result.get("proof_citation")

                        has_proof = bool(
                            (isinstance(t_args, dict) and t_args.get("proof_citation"))
                            or proof_from_result
                        )
                        conf_score = float(t_args.get("confidence_score", 1.0)) if isinstance(t_args, dict) else 1.0

                        if effective_require_verification and used_mutating and not (used_verification or (has_proof and conf_score >= 0.70)) and turn < effective_max_turns:
                            err_feedback = (
                                "Task completion requires verification proof. You modified workspace files but did not run ast_syntax_check, "
                                "terminal tests, or provide verification proof in complete_task. Please verify your work before concluding."
                            )
                            self.on_step("VERIFICATION_PROOF_REQUIRED", {"turn": turn, "reason": err_feedback})
                            messages.append({"role": "user", "content": err_feedback})
                            observations.append(ObservationRecord(
                                turn=turn,
                                tool_name="complete_task",
                                input_args=t_args,
                                output_result={"error": err_feedback},
                                is_error=True,
                            ))
                            trajectory.add_step(ReActStep(
                                turn=turn,
                                thought=current_thought,
                                action_tool="complete_task",
                                action_input=t_args,
                                observation={"error": err_feedback},
                                status="ERROR",
                                is_error=True,
                            ))
                            continue

                        val_report = self._validate_candidate_deliverable(candidate, target_contract, agent_name)
                        if val_report and not val_report.is_valid and turn < effective_max_turns:
                            self.on_step("VALIDATION_ERROR", {"turn": turn, "errors": val_report.errors})
                            err_feedback = DeliverableValidator.format_error_feedback(val_report)
                            messages.append({"role": "user", "content": err_feedback})
                            observations.append(ObservationRecord(
                                turn=turn,
                                tool_name="complete_task",
                                input_args=t_args,
                                output_result={"error": val_report.errors_summary()},
                                is_error=True,
                            ))
                            trajectory.add_step(ReActStep(
                                turn=turn,
                                thought=current_thought,
                                action_tool="complete_task",
                                action_input=t_args,
                                observation={"error": val_report.errors_summary()},
                                status="ERROR",
                                is_error=True,
                            ))
                            continue

                        # Self-Reflection turn before final delivery in complete_task (Issue #87)
                        if active_reasoning_cfg.enable_self_reflection and not _reflection_completed and turn < effective_max_turns:
                            _reflection_completed = True
                            ref_prompt = active_reasoning_cfg.reflection_prompt or (
                                "Self-Critique & Reflection Turn:\n"
                                "Please critically evaluate your proposed deliverable against the following dimensions before final approval:\n"
                                "1. Invariant & Contract Safety: Does this design/plan violate any existing interfaces, conventions, or constraints?\n"
                                "2. Edge Cases & Failure Modes: What potential edge cases, null/error states, or race conditions might occur?\n"
                                "3. Feasibility & Completeness: Are there any missing dependencies or unaddressed requirements?\n"
                                "Reflect on these questions and provide your verified, refined final deliverable (or make any necessary tool adjustments)."
                            )
                            self.on_step("SELF_REFLECTION", {"turn": turn, "prompt": ref_prompt})
                            messages.append({"role": "user", "content": ref_prompt})
                            observations.append(ObservationRecord(
                                turn=turn,
                                tool_name="self_reflection",
                                input_args=t_args,
                                output_result={"reflection_prompt": ref_prompt},
                            ))
                            trajectory.add_step(ReActStep(
                                turn=turn,
                                thought=current_thought,
                                action_tool="self_reflection",
                                action_input=t_args,
                                observation={"reflection_prompt": ref_prompt},
                                status="SUCCESS",
                            ))
                            continue

                        if isinstance(tool_result, dict):
                            if tool_result.get("evidence"):
                                task_evidence = tool_result.get("evidence")
                            if tool_result.get("redundant_tasks"):
                                redundant_tasks = tool_result.get("redundant_tasks")
                            elif isinstance(t_args, dict) and t_args.get("redundant_tasks"):
                                redundant_tasks = t_args.get("redundant_tasks")

                        task_completed = True
                        final_response = candidate

                if task_completed:
                    self.on_step("FINISH", {"turn": turn, "final_response": final_response})
                    if state_store and session_id and task_id:
                        state_store.save_task_step(
                            session_id=session_id,
                            task_id=task_id,
                            turn=turn,
                            stage="TASK_FINISHED",
                            tool_result=final_response,
                            messages=messages,
                            observations=observations,
                        )
                    break

            # 3. Fallback text parsing if no native tool calls returned
            elif content:
                # Check if content has JSON with tool_call or final_output
                parsed_json = None
                if hasattr(self.llm, "_extract_json"):
                    try:
                        parsed_json = self.llm._extract_json(content)
                    except Exception:
                        parsed_json = None

                if not isinstance(parsed_json, dict) and isinstance(content, str):
                    stripped = content.strip()
                    if stripped.startswith("```"):
                        lines = stripped.splitlines()
                        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
                            stripped = "\n".join(lines[1:-1]).strip()
                            if stripped.lower().startswith("json"):
                                stripped = stripped[4:].strip()
                    try:
                        loaded = json.loads(stripped)
                        if isinstance(loaded, dict):
                            parsed_json = loaded
                    except Exception:
                        pass

                if isinstance(parsed_json, dict) and "tool_call" in parsed_json:
                    tc_fallback = parsed_json["tool_call"]
                    t_name = tc_fallback.get("name")
                    t_args = tc_fallback.get("arguments", {})
                    thought = parsed_json.get("thought", "")
                    if t_name:
                        tools_used.add(t_name)

                    self.on_step("TOOL_CALL", {"turn": turn, "tool": t_name, "arguments": t_args, "thought": thought})
                    if getattr(self, "event_bus", None):
                        try:
                            self.event_bus.publish(
                                "TOOL_CALLED",
                                task_id=task_id,
                                agent_name=agent_name,
                                payload={"tool_name": t_name, "arguments": t_args, "turn": turn, "thought": thought},
                            )
                        except Exception:
                            pass

                    if state_store and session_id and task_id:
                        state_store.save_task_step(
                            session_id=session_id,
                            task_id=task_id,
                            turn=turn,
                            stage="ACTION_PLANNED",
                            thought=thought,
                            tool_name=t_name,
                            tool_args=t_args,
                            messages=messages,
                            observations=observations,
                        )

                    perm_error = self._check_permission(t_name, t_args, permissions, workspace=workspace, agent_name=agent_name)
                    # Check resource budget for tool calls and runtime (Issue #82)
                    if not perm_error and active_tracker and active_budget:
                        rt_check = active_tracker.check_runtime()
                        if not rt_check.allowed:
                            if active_budget.action == BudgetAction.HALT:
                                perm_error = rt_check.reason
                            else:
                                self.on_step("RESOURCE_BUDGET_WARNING", {"turn": turn, "reason": rt_check.reason})

                        if not perm_error:
                            tc_dec = active_tracker.record_tool_call(t_name)
                            if not tc_dec.allowed:
                                if active_budget.action == BudgetAction.HALT:
                                    perm_error = tc_dec.reason
                                else:
                                    self.on_step("RESOURCE_BUDGET_WARNING", {"turn": turn, "reason": tc_dec.reason})

                    t_start = time.time()
                    if perm_error:
                        tool_result = {"success": False, "error": perm_error}
                        errors.append(perm_error)
                        status_str = "ERROR"
                    else:
                        try:
                            if t_name in mutating_tools:
                                if "operation_id" not in t_args:
                                    from .idempotency import compute_operation_id
                                    t_args["operation_id"] = compute_operation_id(task_id or "default", t_name, t_args)
                                has_exp_ver = bool(t_args.get("expected_version"))
                                has_exp_hash = bool(t_args.get("expected_hash"))
                                if not has_exp_ver and not has_exp_hash:
                                    target_p = t_args.get("filepath") or t_args.get("path")
                                    if target_p:
                                        norm_p = target_p.replace("\\", "/").strip("/")
                                        h = _read_file_hashes.get(norm_p) or _read_file_hashes.get(target_p)
                                        if h:
                                            t_args["expected_hash"] = h
                                            t_args["expected_version"] = h
                            tool_result = self.tool_registry.call_tool(t_name, t_args)
                            status_str = "SUCCESS" if not (isinstance(tool_result, dict) and tool_result.get("success") is False) else "ERROR"
                            if status_str == "ERROR" and isinstance(tool_result, dict) and "error" in tool_result:
                                errors.append(str(tool_result["error"]))
                        except Exception as e:
                            tool_result = {"success": False, "error": str(e)}
                            errors.append(str(e))
                            status_str = "ERROR"

                    if t_name in ("read_file", "read_file_tool", "filesystem_read") and isinstance(tool_result, dict):
                        f_hash = tool_result.get("file_hash")
                        f_ver = tool_result.get("version")
                        r_fp = t_args.get("filepath") or t_args.get("path")
                        if f_hash and r_fp:
                            norm_rfp = r_fp.replace("\\", "/").strip("/")
                            _read_file_hashes[norm_rfp] = f_hash
                            _read_file_hashes[r_fp] = f_hash
                            if f_ver:
                                _read_file_versions[norm_rfp] = f_ver
                                _read_file_versions[r_fp] = f_ver

                    t_duration = round(time.time() - t_start, 4)
                    self.on_step("OBSERVATION", {"turn": turn, "tool": t_name, "result": tool_result})
                    if getattr(self, "event_bus", None):
                        try:
                            self.event_bus.publish(
                                "TOOL_COMPLETED",
                                task_id=task_id,
                                agent_name=agent_name,
                                payload={
                                    "tool_name": t_name,
                                    "status": status_str,
                                    "duration_seconds": t_duration,
                                    "result": tool_result,
                                    "turn": turn,
                                },
                            )
                        except Exception:
                            pass

                    total_tool_latency += t_duration
                    if getattr(self, "telemetry_engine", None):
                        try:
                            self.telemetry_engine.record_tool_call(
                                tool_name=t_name,
                                duration=t_duration,
                                is_error=(status_str == "ERROR"),
                                cost_usd=0.0,
                            )
                        except Exception:
                            pass

                    ckpt_id = None
                    if t_name in mutating_tools and status_str == "SUCCESS":
                        ws = workspace or getattr(self.tool_registry, "workspace", None)
                        target_p = t_args.get("filepath") or t_args.get("path")
                        if ws and target_p and hasattr(ws, "read_file"):
                            try:
                                res_c = ws.read_file(target_p)
                                c_val = res_c.get("content") if isinstance(res_c, dict) else res_c
                                norm_t = target_p.replace("\\", "/").strip("/")
                                if c_val is not None:
                                    from ..tools.change_tracker import compute_sha256
                                    new_h = compute_sha256(c_val)
                                    _read_file_hashes[norm_t] = new_h
                                    _read_file_hashes[target_p] = new_h
                                    _read_file_versions[norm_t] = new_h[:12]
                                    _read_file_versions[target_p] = new_h[:12]
                                else:
                                    _read_file_hashes.pop(norm_t, None)
                                    _read_file_hashes.pop(target_p, None)
                                    _read_file_versions.pop(norm_t, None)
                                    _read_file_versions.pop(target_p, None)
                            except Exception:
                                pass

                    if t_name in mutating_tools and checkpoint_manager and workspace and task_id:
                        ckpt_id = f"ckpt-{task_id}-t{turn}-{t_name}"
                        checkpoint_manager.create_snapshot(ckpt_id, workspace, metadata={"task_id": task_id, "turn": turn, "tool": t_name})

                    op_id = t_args.get("operation_id")
                    if op_id and state_store and hasattr(state_store, "save_operation"):
                        try:
                            from .idempotency import global_operation_ledger
                            rec = global_operation_ledger.get(op_id)
                            if rec:
                                state_store.save_operation(rec, session_id=session_id, task_id=task_id)
                        except Exception:
                            pass

                    obs_rec = ObservationRecord(
                        turn=turn,
                        tool_name=t_name or "unknown",
                        input_args=t_args,
                        output_result=tool_result,
                        status=status_str,
                        is_error=bool(status_str == "ERROR"),
                        duration_seconds=t_duration,
                    )
                    observations.append(obs_rec)
                    trajectory.add_step(ReActStep(
                        turn=turn,
                        thought=thought,
                        action_tool=t_name or "unknown",
                        action_input=t_args,
                        observation=tool_result,
                        duration_seconds=t_duration,
                        status=status_str,
                        is_error=bool(status_str == "ERROR"),
                    ))

                    messages.append({
                        "role": "user",
                        "content": f"[Tool Observation from '{t_name}']:\n{json.dumps(tool_result, default=str, indent=2)}\n\nWhat is your next action?",
                    })
                    history_events.append({"turn": turn, "tool": t_name, "args": t_args, "result": tool_result})

                    if state_store and session_id and task_id:
                        state_store.save_observation(session_id=session_id, task_id=task_id, observation=obs_rec)
                        state_store.save_task_step(
                            session_id=session_id,
                            task_id=task_id,
                            turn=turn,
                            stage="TOOL_EXECUTED",
                            thought=thought,
                            tool_name=t_name,
                            tool_args=t_args,
                            tool_result=tool_result,
                            messages=messages,
                            observations=observations,
                            checkpoint_id=ckpt_id,
                        )

                    if t_name == "complete_task":
                        candidate = tool_result.get("output", tool_result) if isinstance(tool_result, dict) else tool_result
                        if isinstance(candidate, dict) and isinstance(t_args, dict):
                            merged = dict(t_args)
                            merged.update(candidate)
                            candidate = merged

                        # Evidence stopping verification:
                        used_mutating = bool(tools_used & mutating_tools)
                        used_verification = bool(tools_used & {"ast_syntax_check", "terminal_execute", "run_command", "syntax_check"})
                        has_proof = bool(
                            (isinstance(t_args, dict) and t_args.get("proof_citation"))
                            or (isinstance(tool_result, dict) and tool_result.get("proof_citation"))
                        )
                        conf_score = float(t_args.get("confidence_score", 1.0)) if isinstance(t_args, dict) else 1.0

                        if effective_require_verification and used_mutating and not (used_verification or (has_proof and conf_score >= 0.70)) and turn < effective_max_turns:
                            err_feedback = (
                                "Task completion requires verification proof. You modified workspace files but did not run ast_syntax_check, "
                                "terminal tests, or provide verification proof in complete_task. Please verify your work before concluding."
                            )
                            self.on_step("VERIFICATION_PROOF_REQUIRED", {"turn": turn, "reason": err_feedback})
                            messages.append({"role": "user", "content": err_feedback})
                            observations.append(ObservationRecord(
                                turn=turn,
                                tool_name="complete_task",
                                input_args=t_args,
                                output_result={"error": err_feedback},
                                is_error=True,
                            ))
                            trajectory.add_step(ReActStep(
                                turn=turn,
                                thought=thought,
                                action_tool="complete_task",
                                action_input=t_args,
                                observation={"error": err_feedback},
                                status="ERROR",
                                is_error=True,
                            ))
                            continue

                        val_report = self._validate_candidate_deliverable(candidate, target_contract, agent_name)
                        if val_report and not val_report.is_valid and turn < effective_max_turns:
                            self.on_step("VALIDATION_ERROR", {"turn": turn, "errors": val_report.errors})
                            err_feedback = DeliverableValidator.format_error_feedback(val_report)
                            messages.append({"role": "user", "content": err_feedback})
                            observations.append(ObservationRecord(
                                turn=turn,
                                tool_name="complete_task",
                                input_args=t_args,
                                output_result={"error": val_report.errors_summary()},
                                is_error=True,
                            ))
                            trajectory.add_step(ReActStep(
                                turn=turn,
                                thought=thought,
                                action_tool="complete_task",
                                action_input=t_args,
                                observation={"error": val_report.errors_summary()},
                                status="ERROR",
                                is_error=True,
                            ))
                            continue

                        if isinstance(tool_result, dict):
                            if tool_result.get("evidence"):
                                task_evidence = tool_result.get("evidence")
                            if tool_result.get("redundant_tasks"):
                                redundant_tasks = tool_result.get("redundant_tasks")
                            elif isinstance(t_args, dict) and t_args.get("redundant_tasks"):
                                redundant_tasks = t_args.get("redundant_tasks")

                        final_response = candidate
                        self.on_step("FINISH", {"turn": turn, "final_response": final_response})
                        if state_store and session_id and task_id:
                            state_store.save_task_step(
                                session_id=session_id,
                                task_id=task_id,
                                turn=turn,
                                stage="TASK_FINISHED",
                                tool_result=final_response,
                                messages=messages,
                                observations=observations,
                            )
                        break
                else:
                    # Model produced a terminal message or final output
                    candidate = parsed_json if isinstance(parsed_json, dict) and "parse_error" not in parsed_json else {"content": content}
                    thought = self._extract_thought(llm_response, content, parsed_json if isinstance(parsed_json, dict) else None)

                    # ReAct enforcement: reject premature deliverable exit before tool execution
                    if effective_enforce_react and not tools_used and turn < effective_max_turns:
                        feedback = (
                            "You provided a response without creating or modifying the required workspace files via tools. "
                            "Please execute the appropriate tool calls (e.g. write_file, replace_file_content) to implement and verify these changes in the workspace."
                        )
                        self.on_step("TOOL_EXECUTION_REQUIRED", {"turn": turn, "reason": feedback})
                        messages.append({"role": "user", "content": feedback})
                        obs_rec = ObservationRecord(
                            turn=turn,
                            tool_name="react_enforcer",
                            input_args={},
                            output_result={"error": feedback},
                            is_error=True,
                        )
                        observations.append(obs_rec)
                        trajectory.add_step(ReActStep(
                            turn=turn,
                            thought=thought,
                            action_tool="react_enforcer",
                            action_input={},
                            observation={"error": feedback},
                            status="ERROR",
                            is_error=True,
                        ))
                        continue

                    val_report = self._validate_candidate_deliverable(candidate, target_contract, agent_name)
                    if val_report and not val_report.is_valid and turn < effective_max_turns:
                        self.on_step("VALIDATION_ERROR", {"turn": turn, "errors": val_report.errors})
                        err_feedback = DeliverableValidator.format_error_feedback(val_report)
                        messages.append({"role": "user", "content": err_feedback})
                        observations.append(ObservationRecord(
                            turn=turn,
                            tool_name="deliverable_validator",
                            input_args={},
                            output_result={"error": val_report.errors_summary()},
                            is_error=True,
                        ))
                        trajectory.add_step(ReActStep(
                            turn=turn,
                            thought=thought,
                            action_tool="deliverable_validator",
                            action_input={},
                            observation={"error": val_report.errors_summary()},
                            status="ERROR",
                            is_error=True,
                        ))
                        continue
                    final_response = candidate
                    self.on_step("FINISH", {"turn": turn, "final_response": final_response})
                    if state_store and session_id and task_id:
                        state_store.save_task_step(
                            session_id=session_id,
                            task_id=task_id,
                            turn=turn,
                            stage="TASK_FINISHED",
                            tool_result=final_response,
                            messages=messages,
                            observations=observations,
                        )
                    break
            else:
                break

        token_usage = TokenUsage(
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
            cost_usd=round(total_cost_usd, 6),
        )

        final_val = self._validate_candidate_deliverable(final_response, target_contract, agent_name)

        from .execution_frame import AgentExecutionFrame
        frame = AgentExecutionFrame(
            agent_id=agent_name or "AGENT",
            role=agent_name or "AGENT",
            session_id=session_id or "default",
            task_id=task_id,
            turn=turn,
            max_turns=effective_max_turns,
            messages=list(messages),
            observations=[o.to_dict() if hasattr(o, "to_dict") else o for o in observations],
            paused_at=time.time(),
        )

        return {
            "final_output": final_response,
            "execution_frame": frame,
            "turns_taken": turn,
            "history_events": history_events,
            "tool_history": history_events,
            "messages": messages,
            "token_usage": token_usage,
            "cost_usd": round(total_cost_usd, 6),
            "tools_used": sorted(list(tools_used)),
            "observations": observations,
            "trajectory": trajectory,
            "errors": errors,
            "validation_report": final_val.to_dict() if final_val else None,
            "evidence": task_evidence,
            "redundant_tasks": redundant_tasks,
            "llm_latency_seconds": round(total_llm_latency, 4),
            "tool_latency_seconds": round(total_tool_latency, 4),
            "resource_usage": active_tracker.to_dict() if active_tracker else None,
        }

    run_loop = run

    async def run_async(
        self,
        system_prompt: str,
        user_prompt: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Asynchronously executes the ReAct Agent Loop without blocking the event loop.
        """
        import asyncio
        return await asyncio.to_thread(self.run, system_prompt, user_prompt, **kwargs)

    def _validate_candidate_deliverable(
        self,
        candidate: Any,
        target_contract: Optional[Any],
        agent_name: Optional[str],
    ) -> Optional[ValidationReport]:
        """Validates deliverable against explicit target_contract, agent's registered contract, and InterAgentContract."""
        effective_contract = target_contract or (
            DeliverableValidator.get_contract_for_agent(agent_name) if agent_name else None
        )
        val_report: Optional[ValidationReport] = None
        if effective_contract:
            val_report = DeliverableValidator.validate_contract(effective_contract, candidate)
            if not val_report.is_valid:
                return val_report

        # Inter-Agent Contract Enforcement (Issue #58)
        eff_agent_contract = getattr(self, "current_agent_contract", None)
        eff_ws = getattr(self, "current_workspace", None) or getattr(self.tool_registry, "workspace", None)
        if eff_agent_contract:
            try:
                from .agent_contract import AgentContractEnforcer
                contract_report = AgentContractEnforcer.validate_deliverable(
                    contract=eff_agent_contract,
                    candidate_deliverable=candidate,
                    workspace=eff_ws,
                )
                if not contract_report.is_valid:
                    if getattr(self, "event_bus", None):
                        try:
                            self.event_bus.publish(
                                "CONTRACT_VIOLATION",
                                payload=contract_report.to_dict(),
                            )
                        except Exception:
                            pass
                    return ValidationReport(
                        is_valid=False,
                        contract_name=contract_report.contract_name,
                        errors=contract_report.violations,
                        raw_data=candidate if isinstance(candidate, dict) else None,
                    )
            except Exception:
                pass

        return val_report

    def _check_permission(
        self,
        tool_name: str,
        args: Dict[str, Any],
        permissions: Optional[Any],
        workspace: Optional[Any] = None,
        agent_name: Optional[str] = None,
    ) -> Optional[str]:
        """Validates tool invocation against role policy and task permissions."""
        try:
            from .permission_policy import ToolPermissionPolicyEngine
            eval_res = ToolPermissionPolicyEngine.evaluate_tool_invocation(
                agent_role=agent_name,
                tool_name=tool_name,
                args=args,
                task_permissions=permissions,
                workspace=workspace,
            )
            if not eval_res.allowed:
                feedback = eval_res.reason
                if eval_res.suggested_action:
                    feedback += f" Suggested action: {eval_res.suggested_action}"
                return feedback
        except Exception:
            pass

        if not permissions:
            return None

        # Check task-level FileAccessPolicy specifications
        task_allowed_paths = getattr(permissions, "allowed_paths", None) or (permissions.get("allowed_paths") if isinstance(permissions, dict) else None)
        task_blocked_paths = getattr(permissions, "blocked_paths", None) or (permissions.get("blocked_paths") if isinstance(permissions, dict) else None)
        task_read_only_paths = getattr(permissions, "read_only_paths", None) or (permissions.get("read_only_paths") if isinstance(permissions, dict) else None)

        if task_allowed_paths or task_blocked_paths or task_read_only_paths:
            target_p = args.get("filepath") or args.get("path") or args.get("filename") or ""
            if target_p:
                from ..security.file_access_policy import FileAccessPolicy, FileAccessMode
                fap = FileAccessPolicy(
                    allowed_paths=task_allowed_paths or ["*"],
                    blocked_paths=task_blocked_paths or FileAccessPolicy().blocked_paths,
                    read_only_paths=task_read_only_paths or [],
                )
                mode = FileAccessMode.WRITE if tool_name in (
                    "write_file", "replace_file_content", "edit_file", "delete_file",
                    "insert_lines", "delete_lines", "apply_diff_blocks", "diff_blocks",
                    "filesystem_write", "filesystem_delete"
                ) else FileAccessMode.READ
                dec = fap.evaluate(target_p, mode)
                if not dec.allowed:
                    return f"Permission Denied: {dec.reason}"

        allowed_writes = getattr(permissions, "allowed_write_paths", None)
        if isinstance(permissions, dict):
            allowed_writes = permissions.get("allowed_write_paths")

        if allowed_writes and "*" not in allowed_writes:
            if tool_name in (
                "write_file", "replace_file_content", "edit_file", "delete_file",
                "insert_lines", "delete_lines", "apply_diff_blocks", "diff_blocks",
                "filesystem_write", "filesystem_delete"
            ):
                target_path = args.get("filepath") or args.get("path") or args.get("filename") or ""
                if target_path:
                    try:
                        from ..tools.workspace import safe_resolve_path, PathTraversalError
                    except (ImportError, ValueError):
                        from agent_orchestrator.tools.workspace import safe_resolve_path, PathTraversalError

                    # Attempt safe resolution against workspace root
                    ws_obj = workspace or getattr(self, "workspace", None)
                    root_dir = getattr(ws_obj, "root_dir", None) or Path.cwd()

                    try:
                        resolved_target = safe_resolve_path(root_dir, target_path)
                        rel_norm = str(resolved_target.relative_to(Path(root_dir).resolve())).replace("\\", "/")
                    except PathTraversalError as pte:
                        return f"Permission Denied: Path traversal detected: {str(pte)}"
                    except Exception:
                        rel_norm = str(target_path).replace("\\", "/").strip("/")

                    matched = False
                    for p in allowed_writes:
                        norm_p = str(p).replace("\\", "/").strip("/")
                        if norm_p == "*":
                            matched = True
                            break
                        clean_pattern = norm_p.rstrip("*").rstrip("/")
                        if (
                            rel_norm == clean_pattern
                            or rel_norm.startswith(clean_pattern + "/")
                            or fnmatch.fnmatch(rel_norm, norm_p)
                        ):
                            matched = True
                            break
                    if not matched:
                        return f"Permission Denied: Write operation on '{target_path}' is outside allowed paths {allowed_writes}."

        allowed_cmds = getattr(permissions, "allowed_commands", None)
        if isinstance(permissions, dict):
            allowed_cmds = permissions.get("allowed_commands")

        if allowed_cmds and "*" not in allowed_cmds:
            if tool_name in ("terminal_execute", "run_command", "bash"):
                cmd = args.get("command") or ""
                if cmd:
                    matched_cmd = any(cmd.strip().startswith(c) for c in allowed_cmds)
                    if not matched_cmd:
                        return f"Permission Denied: Command '{cmd}' is outside allowed command prefixes {allowed_cmds}."

        # Check task-level network permissions (defense-in-depth)
        task_net_allowed = getattr(permissions, "network_allowed", None)
        if task_net_allowed is None and isinstance(permissions, dict):
            task_net_allowed = permissions.get("network_allowed")

        task_allowed_doms = getattr(permissions, "allowed_domains", None) or (permissions.get("allowed_domains") if isinstance(permissions, dict) else None)
        task_blocked_doms = getattr(permissions, "blocked_domains", None) or (permissions.get("blocked_domains") if isinstance(permissions, dict) else None)

        if task_net_allowed is False:
            if tool_name in (
                "http_request", "http_fetch", "fetch_url", "read_url", "read_url_content",
                "web_search", "search_web", "download_file", "download_package"
            ):
                return f"Permission Denied: Network access is disabled for this task (network_allowed=False). Tool '{tool_name}' cannot be invoked."
            if tool_name in ("terminal_execute", "run_command", "bash"):
                cmd = args.get("command") or ""
                if cmd:
                    from ..security.network_policy import NetworkAccessPolicy, NetworkAccessMode
                    net_pol = NetworkAccessPolicy(mode=NetworkAccessMode.DISABLED)
                    dec = net_pol.evaluate_command(cmd)
                    if not dec.allowed:
                        return f"Permission Denied: Command '{cmd}' blocked by network policy: {dec.reason}"
        elif task_net_allowed is True and (task_allowed_doms or task_blocked_doms):
            from ..security.network_policy import NetworkAccessPolicy, NetworkAccessMode
            net_pol = NetworkAccessPolicy(
                mode=NetworkAccessMode.ALLOWLIST_ONLY if task_allowed_doms else NetworkAccessMode.UNRESTRICTED,
                allowed_domains=task_allowed_doms or NetworkAccessPolicy().allowed_domains,
                blocked_domains=list(NetworkAccessPolicy().blocked_domains) + list(task_blocked_doms or []),
            )
            if tool_name in (
                "http_request", "http_fetch", "fetch_url", "read_url", "read_url_content",
                "web_search", "search_web", "download_file", "download_package"
            ):
                target_u = args.get("url") or args.get("uri") or args.get("endpoint") or args.get("host") or ""
                if target_u:
                    target_str = str(target_u).strip()
                    dec = net_pol.evaluate_url(target_str) if ("://" in target_str or "/" in target_str) else net_pol.evaluate_host(target_str)
                    if not dec.allowed:
                        return f"Permission Denied: Network operation on '{target_u}' blocked by network policy: {dec.reason}"
            elif tool_name in ("terminal_execute", "run_command", "bash"):
                cmd = args.get("command") or ""
                if cmd:
                    dec = net_pol.evaluate_command(cmd)
                    if not dec.allowed:
                        return f"Permission Denied: Command '{cmd}' blocked by network policy: {dec.reason}"

        return None


# Backward-compatible alias
ReActLoop = ReActAgentLoop

