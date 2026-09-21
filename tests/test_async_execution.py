"""
Tests for Issue #91: Asynchronous Execution, Async Tools, Background Jobs, and Event Streaming.
"""
import asyncio
import pytest
import time
from unittest.mock import MagicMock
from agent_orchestrator.runtime.background_jobs import (
    BackgroundJobManager,
    BackgroundJobStatus,
)
from agent_orchestrator.runtime.event_bus import EventBus, ExecutionEvent
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.tools.workspace import WorkspaceManager


@pytest.mark.anyio
async def test_background_job_manager_async_coroutine():
    manager = BackgroundJobManager()

    async def _async_task(x: int, y: int) -> int:
        await asyncio.sleep(0.05)
        return x + y

    job = manager.start_job("async_add", _async_task, 10, 20)
    assert job.job_id.startswith("job-")
    assert job.status in (BackgroundJobStatus.RUNNING, BackgroundJobStatus.PENDING)

    res = await manager.await_job(job.job_id, timeout=2.0)
    assert res == 30
    assert job.status == BackgroundJobStatus.COMPLETED
    assert job.duration_seconds > 0.0


@pytest.mark.anyio
async def test_background_job_manager_sync_callable():
    manager = BackgroundJobManager()

    def _sync_task(msg: str) -> str:
        time.sleep(0.05)
        return f"Echo: {msg}"

    job = manager.start_job("sync_echo", _sync_task, "Hello World")
    assert job.job_id.startswith("job-")

    res = await manager.await_job(job.job_id, timeout=2.0)
    assert res == "Echo: Hello World"
    assert job.status == BackgroundJobStatus.COMPLETED


@pytest.mark.anyio
async def test_background_job_manager_cancellation():
    manager = BackgroundJobManager()

    async def _long_running():
        await asyncio.sleep(10.0)
        return "Done"

    job = manager.start_job("long_job", _long_running)
    await asyncio.sleep(0.05)

    cancelled = manager.cancel_job(job.job_id)
    assert cancelled is True
    assert job.status == BackgroundJobStatus.CANCELLED


@pytest.mark.anyio
async def test_event_bus_stream_async():
    bus = EventBus(session_id="sess-stream-test")
    collected_events = []

    async def _consumer():
        async for evt in bus.stream_async(session_id="sess-stream-test", timeout=1.0):
            collected_events.append(evt)

    consumer_task = asyncio.create_task(_consumer())
    await asyncio.sleep(0.05)

    # Publish lifecycle events
    bus.publish("TASK_STARTED", {"task_id": "T1"}, session_id="sess-stream-test")
    bus.publish("TOOL_CALLED", {"tool": "read_file"}, session_id="sess-stream-test")
    bus.publish("WORKFLOW_COMPLETED", {"verdict": "PASS"}, session_id="sess-stream-test")

    await consumer_task
    assert len(collected_events) == 3
    event_types = [e.event_type_value for e in collected_events]
    assert "TASK_STARTED" in event_types
    assert "TOOL_CALLED" in event_types
    assert "WORKFLOW_COMPLETED" in event_types


@pytest.mark.anyio
async def test_unified_tool_dispatcher_execute_tool_async(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    builtin_registry = BuiltinToolRegistry(workspace=ws)
    dispatcher = UnifiedToolDispatcher(builtin_registry=builtin_registry)

    # Execute async file write
    res = await dispatcher.execute_tool_async("write_file", {"filepath": "async_test.txt", "content": "Async Hello"})
    assert res.get("success") is True

    # Execute async file read
    read_res = await dispatcher.execute_tool_async("read_file", {"filepath": "async_test.txt"})
    assert read_res.get("success") is True
    assert "Async Hello" in str(read_res.get("data") or read_res.get("output") or read_res.get("content"))


@pytest.mark.anyio
async def test_react_loop_run_async():
    import json
    mock_llm = MagicMock()
    mock_llm.chat_with_tools.return_value = {
        "content": json.dumps({"summary": "Async ReAct loop executed cleanly."}),
        "final_output": {"summary": "Async ReAct loop executed cleanly."},
        "tool_calls": [],
    }

    mock_registry = MagicMock()
    mock_registry.get_tool_schemas.return_value = []

    loop = ReActAgentLoop(
        llm=mock_llm,
        tool_registry=mock_registry,
        max_turns=3,
    )

    result = await loop.run_async(
        system_prompt="You are an async agent.",
        user_prompt="Execute task asynchronously",
    )

    assert result is not None
    assert result.get("final_output") is not None
