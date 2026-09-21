"""
Unit and integration tests for Issue #87:
Incremental Agent Reasoning & Multi-Turn Deliberation.
"""
import pytest
from unittest.mock import MagicMock

from agent_orchestrator.contracts import (
    ReasoningMode,
    ReasoningConfigContract,
    ArchitectureContract,
    ExecutionPlanContract,
    SpecificationContract,
)
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.state import OrchestratorState
from agent_orchestrator.agents.architecture import ArchitectureAgent
from agent_orchestrator.agents.planner import PlannerAgent
from agent_orchestrator.agents.specification import SpecificationAgent


def test_reasoning_config_contract():
    cfg = ReasoningConfigContract(
        mode=ReasoningMode.DELIBERATIVE,
        min_exploration_turns=2,
        enable_self_reflection=True,
        reflection_prompt="Check edge cases",
    )
    d = cfg.model_dump() if hasattr(cfg, "model_dump") else cfg.dict()
    assert d["mode"] == "DELIBERATIVE"
    assert d["min_exploration_turns"] == 2
    assert d["enable_self_reflection"] is True
    assert d["reflection_prompt"] == "Check edge cases"


def test_min_exploration_turns_enforcement(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    tools = BuiltinToolRegistry(workspace=ws)

    # Mock LLM that tries to output direct deliverable immediately
    mock_llm = MagicMock()
    responses = [
        # Turn 1: Immediate premature output without tool calls
        {"system_title": "Premature System", "component_structure": [], "file_layout": []},
        # Turn 2: Agent calls tool to explore
        {"tool_call": {"name": "list_directory", "arguments": {"path": "."}}},
        # Turn 3: Final deliverable after exploration
        {"system_title": "Explored System", "component_structure": [{"module_name": "app.py"}], "file_layout": []},
    ]
    mock_llm.chat_json.side_effect = responses

    step_events = []
    def on_step(evt, data):
        step_events.append((evt, data))

    react_loop = ReActAgentLoop(
        llm=mock_llm,
        tool_registry=tools,
        max_turns=5,
        on_step_callback=on_step,
    )

    reasoning_cfg = ReasoningConfigContract(
        mode=ReasoningMode.DELIBERATIVE,
        min_exploration_turns=1,
        enable_self_reflection=False,
    )

    result = react_loop.run(
        system_prompt="You are an architect.",
        user_prompt="Design system",
        reasoning_config=reasoning_cfg,
    )

    # Verify that EXPLORATION_REQUIRED was triggered on Turn 1
    event_names = [e[0] for e in step_events]
    assert "EXPLORATION_REQUIRED" in event_names
    assert result["turns_taken"] >= 2
    assert "list_directory" in result["tools_used"]


def test_self_reflection_turn_interception(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    tools = BuiltinToolRegistry(workspace=ws)

    mock_llm = MagicMock()
    responses = [
        # Turn 1: First candidate deliverable
        {"system_title": "Initial Draft", "component_structure": [{"module_name": "draft.py"}]},
        # Turn 2: Refined deliverable after receiving reflection critique prompt
        {"system_title": "Refined & Verified System", "component_structure": [{"module_name": "refined.py"}], "invariants_verified": True},
    ]
    mock_llm.chat_json.side_effect = responses

    step_events = []
    def on_step(evt, data):
        step_events.append((evt, data))

    react_loop = ReActAgentLoop(
        llm=mock_llm,
        tool_registry=tools,
        max_turns=5,
        on_step_callback=on_step,
    )

    reasoning_cfg = ReasoningConfigContract(
        mode=ReasoningMode.REFLECTIVE,
        min_exploration_turns=0,
        enable_self_reflection=True,
    )

    result = react_loop.run(
        system_prompt="You are an architect.",
        user_prompt="Design system",
        reasoning_config=reasoning_cfg,
    )

    event_names = [e[0] for e in step_events]
    assert "SELF_REFLECTION" in event_names
    assert result["turns_taken"] == 2
    assert result["final_output"].get("system_title") == "Refined & Verified System"


def test_architecture_agent_deliberative_reasoning(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    tools = BuiltinToolRegistry(workspace=ws)

    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = [
        # Turn 1: Agent tries premature output
        {"system_title": "Premature Arch", "component_structure": []},
        # Turn 2: Agent inspects directory
        {"tool_call": {"name": "list_directory", "arguments": {"path": "."}}},
        # Turn 3: Agent provides candidate
        {"system_title": "Candidate Arch", "component_structure": [{"module_name": "src/app.py", "purpose": "App entry"}]},
        # Turn 4: Agent reflects and finalizes
        {"system_title": "Final Arch", "component_structure": [{"module_name": "src/app.py", "purpose": "App entry"}], "data_flow_description": "Clean flow"},
    ]

    arch_agent = ArchitectureAgent(workspace=ws, tool_registry=tools, llm=mock_llm)
    state = OrchestratorState(user_request="Design microservice")

    reasoning_cfg = ReasoningConfigContract(
        mode=ReasoningMode.DELIBERATIVE,
        min_exploration_turns=1,
        enable_self_reflection=True,
    )

    res = arch_agent.execute(state=state, reasoning_config=reasoning_cfg)
    assert res["system_title"] == "Final Arch"
    assert state.architecture_output["system_title"] == "Final Arch"


def test_one_shot_backward_compatibility(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    tools = BuiltinToolRegistry(workspace=ws)

    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = {
        "feature_name": "Auth",
        "functional_requirements": [{"id": "FR-1", "description": "Login"}],
    }

    spec_agent = SpecificationAgent(workspace=ws, tool_registry=tools, llm=mock_llm)
    state = OrchestratorState(user_request="Define auth specs")

    # Default one-shot without explicit reasoning config should complete in 1 turn
    res = spec_agent.execute(state=state)
    assert res["feature_name"] == "Auth"
    assert len(res["functional_requirements"]) == 1
