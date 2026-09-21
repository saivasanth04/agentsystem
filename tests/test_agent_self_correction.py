"""
Unit and integration tests for Issue #88:
Intra-Agent Self-Correction Engine (Generate -> Inspect -> Validate -> Fix -> Return).
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from agent_orchestrator.runtime.self_correction import (
    SelfCorrectionEngine,
    SelfCorrectionReport,
)
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.state import OrchestratorState
from agent_orchestrator.agents.coder import CoderAgent
from agent_orchestrator.agents.tester import TesterAgent


def test_inspect_and_validate_clean_file(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    clean_file = tmp_path / "src" / "clean.py"
    clean_file.parent.mkdir(parents=True, exist_ok=True)
    clean_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    is_clean, errors = SelfCorrectionEngine.inspect_and_validate(ws, ["src/clean.py"])
    assert is_clean is True
    assert len(errors) == 0


def test_inspect_and_validate_syntax_error(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    broken_file = tmp_path / "src" / "broken.py"
    broken_file.parent.mkdir(parents=True, exist_ok=True)
    broken_file.write_text("def broken_func(\n    return 42\n", encoding="utf-8")

    is_clean, errors = SelfCorrectionEngine.inspect_and_validate(ws, ["src/broken.py"])
    assert is_clean is False
    assert len(errors) > 0
    assert any("SyntaxError" in err for err in errors)


def test_self_correction_repair_loop_success(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    broken_file = tmp_path / "src" / "app.py"
    broken_file.parent.mkdir(parents=True, exist_ok=True)
    broken_file.write_text("def bad_code(\n", encoding="utf-8")

    tools = BuiltinToolRegistry(workspace=ws)

    # Mock agent whose repair turn fixes the file
    mock_agent = MagicMock()
    mock_agent.name = "CODER"
    mock_agent.model = "default"
    mock_agent.tool_registry = tools
    mock_agent.workspace = ws
    mock_agent.build_system_prompt.return_value = "You are a coder."

    def mock_repair_run(*args, **kwargs):
        # Fix the file on disk during repair turn
        broken_file.write_text("def bad_code():\n    return 'fixed'\n", encoding="utf-8")
        return {"final_output": {"summary": "Fixed syntax error"}, "turns_taken": 1}

    mock_agent.react_loop.run = mock_repair_run

    state = OrchestratorState(user_request="Fix syntax error")
    report = SelfCorrectionEngine.run_repair_loop(
        agent=mock_agent,
        state=state,
        modified_files=["src/app.py"],
        max_attempts=2,
        workspace=ws,
    )

    assert report.is_clean is True
    assert report.attempts_made == 1
    assert len(report.fixed_errors) > 0
    assert len(report.remaining_errors) == 0


def test_self_correction_bounded_attempts(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    broken_file = tmp_path / "src" / "stubborn.py"
    broken_file.parent.mkdir(parents=True, exist_ok=True)
    broken_file.write_text("def stubborn(\n", encoding="utf-8")

    tools = BuiltinToolRegistry(workspace=ws)
    mock_agent = MagicMock()
    mock_agent.name = "CODER"
    mock_agent.model = "default"
    mock_agent.tool_registry = tools
    mock_agent.workspace = ws
    mock_agent.build_system_prompt.return_value = "You are a coder."

    # Mock repair run that fails to fix the file
    mock_agent.react_loop.run.return_value = {"final_output": {"summary": "Tried"}, "turns_taken": 1}

    state = OrchestratorState(user_request="Attempt fix")
    report = SelfCorrectionEngine.run_repair_loop(
        agent=mock_agent,
        state=state,
        modified_files=["src/stubborn.py"],
        max_attempts=2,
        workspace=ws,
    )

    assert report.is_clean is False
    assert report.attempts_made == 2
    assert len(report.remaining_errors) > 0


def test_coder_agent_with_self_correction(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    tools = BuiltinToolRegistry(workspace=ws)

    # Pre-write a file
    f = tmp_path / "src" / "service.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("def run():\n    return True\n", encoding="utf-8")

    coder = CoderAgent(workspace=ws, tool_registry=tools)
    mock_llm_run = MagicMock(return_value={
        "final_output": {"files": [{"filepath": "src/service.py", "content": "def run():\n    return True\n"}]},
        "turns_taken": 1,
        "history_events": [],
    })
    coder.react_loop.run = mock_llm_run

    state = OrchestratorState(user_request="Build service")
    task_info = {"task_id": "T-01", "outputs": ["src/service.py:run"]}

    out = coder.execute(state=state, task_info=task_info)
    assert "self_correction" in out
    if out["self_correction"]:
        assert out["self_correction"]["is_clean"] is True
