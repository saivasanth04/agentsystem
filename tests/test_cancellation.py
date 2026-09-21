"""
Tests for Issue #90: Cooperative and Preemptive Cancellation Infrastructure.
Verifies CancellationToken, Sandboxed Process Aborting, ReAct Loop Interruption, and Orchestrator-level STOP.
"""
import pytest
import time
from unittest.mock import MagicMock
from agent_orchestrator.runtime.cancellation import (
    CancellationSource,
    CancellationToken,
    TaskCancelledError,
)
from agent_orchestrator.security.sandbox import LocalProcessSandbox, SandboxPolicy
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.runtime.task_graph import TaskState


def test_cancellation_source_and_token_basics():
    source = CancellationSource()
    token = source.token

    assert token.is_cancelled is False
    assert token.cancel_reason is None

    callbacks_called = []
    token.register_callback(lambda r: callbacks_called.append(r))

    # Trigger cancellation
    res = source.cancel(reason="User typed STOP")
    assert res is True
    assert token.is_cancelled is True
    assert token.cancel_reason == "User typed STOP"
    assert len(callbacks_called) == 1
    assert callbacks_called[0] == "User typed STOP"

    # Second cancel call is idempotent
    assert source.cancel() is False

    # Throw if cancelled
    with pytest.raises(TaskCancelledError) as exc_info:
        token.throw_if_cancelled()
    assert "User typed STOP" in str(exc_info.value)


def test_cascaded_child_token_cancellation():
    parent_source = CancellationSource()
    child_token = parent_source.create_child_token()

    assert child_token.is_cancelled is False

    # Cancel parent -> child should automatically cancel
    parent_source.cancel("Parent cancelled")
    assert child_token.is_cancelled is True
    assert "Parent cancelled" in str(child_token.cancel_reason)


def test_sandboxed_command_preemptive_cancellation(tmp_path):
    sandbox = LocalProcessSandbox(working_dir=tmp_path)
    source = CancellationSource()

    # Cancel before running
    source.cancel("Aborted immediately")
    res = sandbox.run_command(
        cmd=["python", "-c", "import time; time.sleep(10)"],
        cancellation_token=source.token,
    )
    assert res.success is False
    assert res.exit_code == -1
    assert "Command aborted before launch" in res.stderr


def test_sandboxed_command_mid_execution_cancellation(tmp_path):
    sandbox = LocalProcessSandbox(working_dir=tmp_path)
    source = CancellationSource()

    def _cancel_after_delay():
        time.sleep(0.2)
        source.cancel("User pressed STOP during execution")

    import threading
    t = threading.Thread(target=_cancel_after_delay)
    t.start()

    start = time.time()
    res = sandbox.run_command(
        cmd=["python", "-c", "import time; time.sleep(10)"],
        timeout=15,
        cancellation_token=source.token,
    )
    duration = time.time() - start
    t.join()

    # Should have terminated well before the 10s sleep
    assert duration < 5.0
    assert res.success is False
    assert res.exit_code == -1
    assert "cancelled" in res.stderr.lower() or res.killed_due_to_limit is True


def test_react_loop_cancellation_interruption():
    mock_llm = MagicMock()
    mock_registry = MagicMock()
    mock_registry.get_tool_schemas.return_value = [{"name": "long_task"}]

    source = CancellationSource()
    steps_recorded = []

    def on_step(action, payload):
        steps_recorded.append((action, payload))
        if action == "TOOL_CALL":
            # Cancel mid-loop
            source.cancel("User aborted task after tool call")

    mock_llm.chat_with_tools.return_value = {
        "tool_calls": [{"name": "long_task", "arguments": {"x": 1}}]
    }

    loop = ReActAgentLoop(
        llm=mock_llm,
        tool_registry=mock_registry,
        max_turns=5,
        on_step_callback=on_step,
    )

    result = loop.run(
        system_prompt="You are an assistant.",
        user_prompt="Run long task",
        cancellation_token=source.token,
    )

    assert result["status"] == "CANCELLED"
    assert "User aborted task" in result["cancel_reason"]
    actions = [s[0] for s in steps_recorded]
    assert "TASK_CANCELLED" in actions


def test_task_state_enum_has_cancelled():
    assert hasattr(TaskState, "CANCELLED")
    assert TaskState.CANCELLED.value == "CANCELLED"
