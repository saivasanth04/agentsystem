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
        from ..context.budget_allocator import estimate_tokens
        est_tokens = estimate_tokens(json.dumps(messages, default=str))
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
        self.on_step = on_step_callback or kwargs.get("on_step") or (lambda stage, payload: None)
        if approval_gate is not None:
            self.approval_gate = approval_gate
        else:
            from .approval_gate import PolicyBasedApprovalGate
            self.approval_gate = PolicyBasedApprovalGate()
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

        # Delegate execution to authoritative AgentExecutionLoop
        from runtime.agent_loop import AgentExecutionLoop
        eff_ws = self.current_workspace or getattr(self.tool_registry, "workspace", None)
        execution_engine = AgentExecutionLoop(
            llm_client=self.llm,
            workspace_manager=eff_ws,
            max_iterations=max_turns or self.max_turns or 15,
        )
        exec_state = execution_engine.run(
            task_objective=user_prompt or "Execute task",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            available_tools=available_tools,
            agent_id=agent_name,
            max_iterations=max_turns or self.max_turns or 15,
            model=model,
            permissions=permissions,
            session_id=session_id,
            task_id=task_id,
            task_info=task_info,
            **kwargs,
        )
        return exec_state.to_dict()

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
        except Exception as e:
            return f"Permission Denied: Authorization policy evaluation error ({type(e).__name__}: {str(e)})"

        try:
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
        except Exception as e:
            return f"Permission Denied: Permission verification error ({type(e).__name__}: {str(e)})"

        return None


# Backward-compatible alias
ReActLoop = ReActAgentLoop

