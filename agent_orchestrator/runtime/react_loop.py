"""
agent_orchestrator/runtime/react_loop.py

Backward-compatibility delegation shim over authoritative AgentExecutionLoop (runtime/agent_loop.py).
Contains zero execution logic. Translates parameters and delegates directly to AgentExecutionLoop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union

if TYPE_CHECKING:
    from ..llm import LLMClient

from runtime.agent_loop import AgentExecutionLoop

logger = logging.getLogger("orchestrator.runtime.react_loop")


class ReActAgentLoop:
    """
    Backward-compatibility delegation shim over authoritative AgentExecutionLoop.
    Contains zero execution logic. Translates parameters and delegates directly to AgentExecutionLoop.
    """

    def __init__(
        self,
        llm: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
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
        self.approval_gate = approval_gate
        self.enforce_react = enforce_react
        self.require_verification = require_verification
        self.event_bus = event_bus or kwargs.get("event_bus") or getattr(tool_registry, "event_bus", None)
        self.telemetry_engine = telemetry_engine or kwargs.get("telemetry_engine") or getattr(tool_registry, "telemetry_engine", None)
        self.extra_kwargs = kwargs

    def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Convenience execution wrapper forwarding to run()."""
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
        system_prompt: str = "",
        user_prompt: str = "",
        model: Optional[str] = None,
        available_tools: Optional[List[str]] = None,
        agent_name: Optional[str] = None,
        max_turns: Optional[int] = None,
        workspace: Optional[Any] = None,
        permissions: Optional[Any] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        task_info: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Translates parameters and delegates directly to authoritative AgentExecutionLoop.
        Contains ZERO execution or reasoning logic.
        """
        eff_ws = workspace or getattr(self.tool_registry, "workspace", None)
        eff_dispatcher = getattr(self.tool_registry, "dispatcher", None) or self.tool_registry
        execution_engine = AgentExecutionLoop(
            llm_client=self.llm,
            workspace_manager=eff_ws,
            tool_dispatcher=eff_dispatcher,
            max_iterations=max_turns or self.max_turns or 15,
        )
        task_objective = user_prompt or system_prompt or "Execute task"
        exec_state = execution_engine.run(
            task_objective=task_objective,
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
        """Asynchronously executes the ReAct Agent Loop via delegation."""
        return await asyncio.to_thread(self.run, system_prompt, user_prompt, **kwargs)


# Backward-compatible aliases
ReActLoop = ReActAgentLoop
