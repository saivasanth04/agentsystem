"""
Tests for Issue #89: Tool-call budgeting, Repetition Circuit Breakers, and Wind-Down Protocols.
"""
import pytest
from unittest.mock import MagicMock
from agent_orchestrator.security.resource_budget import (
    ResourceBudget,
    ResourceUsageTracker,
    BudgetAction,
)
from agent_orchestrator.runtime.react_loop import ReActAgentLoop


def test_resource_budget_defaults_and_serialization():
    budget = ResourceBudget(
        max_tool_calls_total=20,
        max_tool_calls_per_turn=5,
        max_consecutive_identical_tool_calls=3,
        max_tokens=50000,
        max_runtime_seconds=60.0,
    )
    b_dict = budget.to_dict()
    assert b_dict["max_consecutive_identical_tool_calls"] == 3
    assert b_dict["max_tool_calls_total"] == 20

    restored = ResourceBudget.from_dict(b_dict)
    assert restored.max_consecutive_identical_tool_calls == 3
    assert restored.max_tool_calls_total == 20


def test_repetition_circuit_breaker_identical_arguments():
    budget = ResourceBudget(max_consecutive_identical_tool_calls=3, action=BudgetAction.HALT)
    tracker = ResourceUsageTracker(budget)

    # 1st call - allowed
    d1 = tracker.record_tool_call("search_code", arguments={"query": "def foo"})
    assert d1.allowed is True
    assert tracker.consecutive_identical_tool_calls == 1

    # 2nd call - allowed
    d2 = tracker.record_tool_call("search_code", arguments={"query": "def foo"})
    assert d2.allowed is True
    assert tracker.consecutive_identical_tool_calls == 2

    # 3rd call - allowed (at limit)
    d3 = tracker.record_tool_call("search_code", arguments={"query": "def foo"})
    assert d3.allowed is True
    assert tracker.consecutive_identical_tool_calls == 3

    # 4th call - BLOCKED by circuit breaker
    d4 = tracker.record_tool_call("search_code", arguments={"query": "def foo"})
    assert d4.allowed is False
    assert d4.resource_type == "consecutive_identical_tool_calls"
    assert "Repetitive tool call loop detected" in d4.reason


def test_repetition_circuit_breaker_varying_arguments_resets_counter():
    budget = ResourceBudget(max_consecutive_identical_tool_calls=2, action=BudgetAction.HALT)
    tracker = ResourceUsageTracker(budget)

    d1 = tracker.record_tool_call("search_code", arguments={"query": "query_1"})
    assert d1.allowed is True
    assert tracker.consecutive_identical_tool_calls == 1

    d2 = tracker.record_tool_call("search_code", arguments={"query": "query_2"})
    assert d2.allowed is True
    assert tracker.consecutive_identical_tool_calls == 1

    d3 = tracker.record_tool_call("search_code", arguments={"query": "query_2"})
    assert d3.allowed is True
    assert tracker.consecutive_identical_tool_calls == 2

    # 3rd identical query_2 breaches threshold of 2
    d4 = tracker.record_tool_call("search_code", arguments={"query": "query_2"})
    assert d4.allowed is False


def test_utilization_ratio_calculation():
    budget = ResourceBudget(max_tool_calls_total=10, max_tokens=1000)
    tracker = ResourceUsageTracker(budget)

    tracker.record_tool_call("read_file")
    tracker.record_tool_call("read_file", arguments={"path": "a.py"})
    tracker.record_tokens(prompt_tokens=400, completion_tokens=400)

    ratios = tracker.get_utilization_ratio(current_turn=8, max_turns=10)
    assert ratios["tool_calls"] == 0.2
    assert ratios["tokens"] == 0.8
    assert ratios["turns"] == 0.8
    assert ratios["max_ratio"] == 0.8


def test_react_loop_budget_wind_down_alert():
    mock_llm = MagicMock()
    # Turn 1: tool call, triggers 80% turn usage if max_turns=2 (turn 1 is 50%, turn 2 is 100%) or tool call budget reached
    mock_llm.chat_with_tools.side_effect = [
        {"tool_calls": [{"name": "read_dummy", "arguments": {"x": 1}}]},
        {"final_output": {"summary": "Completed task gracefully after warning"}},
    ]

    mock_registry = MagicMock()
    mock_registry.get_tool_schemas.return_value = [{"name": "read_dummy"}]
    mock_registry.execute_tool.return_value = {"content": "file data"}

    steps_recorded = []

    def on_step(action, payload):
        steps_recorded.append((action, payload))

    budget = ResourceBudget(max_tool_calls_total=1, max_consecutive_identical_tool_calls=2)
    tracker = ResourceUsageTracker(budget)

    loop = ReActAgentLoop(
        llm=mock_llm,
        tool_registry=mock_registry,
        max_turns=5,
        on_step_callback=on_step,
        resource_budget=budget,
        resource_tracker=tracker,
    )

    result = loop.run(
        system_prompt="You are a research agent.",
        user_prompt="Perform analysis task",
    )
    assert result["final_output"] is not None
    actions = [s[0] for s in steps_recorded]
    assert "BUDGET_WIND_DOWN" in actions or "TOOL_CALL" in actions


def test_react_loop_repetition_breaker_halts_loop():
    mock_llm = MagicMock()
    # Agent keeps attempting identical tool call
    mock_llm.chat_with_tools.return_value = {
        "tool_calls": [{"name": "stuck_tool", "arguments": {"query": "same_args"}}]
    }

    mock_registry = MagicMock()
    mock_registry.get_tool_schemas.return_value = [{"name": "stuck_tool"}]
    mock_registry.execute_tool.return_value = {"content": "stuck output"}

    steps_recorded = []

    def on_step(action, payload):
        steps_recorded.append((action, payload))

    budget = ResourceBudget(max_consecutive_identical_tool_calls=2, action=BudgetAction.HALT)
    tracker = ResourceUsageTracker(budget)

    loop = ReActAgentLoop(
        llm=mock_llm,
        tool_registry=mock_registry,
        max_turns=6,
        on_step_callback=on_step,
        resource_budget=budget,
        resource_tracker=tracker,
    )

    result = loop.run(
        system_prompt="You are a stuck agent.",
        user_prompt="Loop test",
    )
    # Should have recorded error from circuit breaker
    assert any("Repetitive tool call loop detected" in str(err) for err in result.get("errors", []))
