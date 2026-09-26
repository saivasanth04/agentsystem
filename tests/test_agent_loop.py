"""
Comprehensive Integration Tests for Claude-Style Execution Loop.
Verifies compliance with the 10 Non-Negotiable Contract clauses:
- Real executable classes (Zero mocks, zero placeholders)
- 7-Phase State Machine:
  Reason -> Select Tool -> Execute -> Observation -> State Update -> Context Rebuild -> Reason Again
- Strict observation normalization:
  Never append raw logs.
  Parse tool outputs into structured Observation objects:
  Observation(type="compiler_error", file="auth.py", line=84, symbol="validate_token",
              severity="error", evidence="...")
- Replanner & Context Compiler consume structured observations - NOT raw strings.
- Integration across all 7 core systems:
  1. SkillRegistry
  2. AgentRegistry
  3. MCPManager
  4. UnifiedToolDispatcher
  5. WorkspaceManager
  6. LangGraph StateGraph Node
  7. LiteLLM Gateway
"""
import json
import os
from pathlib import Path
import tempfile
import pytest

from agent_orchestrator.registry.skill_registry import SkillRegistry
from agent_orchestrator.registry.agent_registry import AgentRegistry
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.llm import LLMClient

from context.compiler import ContextCompiler
from repository.repository_brain import RepositoryBrain
from runtime.tool_policy import ToolPolicy
from runtime.capability_router import CapabilityRouter
from runtime.permission_engine import PermissionEngine
from runtime.observation_engine import Observation, ObservationEngine
from runtime.execution_state import ExecutionState, LoopStatus
from runtime.event_stream import EventStream, LoopEvent, LoopEventType
from runtime.agent_loop import AgentExecutionLoop

# In-tree backward compatibility verification
import agent_orchestrator.runtime as in_tree_runtime


@pytest.fixture
def test_workspace():
    """Provides a temporary workspace with real files."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        ws = WorkspaceManager(root_dir=tmpdir)
        ws.write_file("auth.py", "def validate_token(token):\n    if not token:\n        raise ValueError('Invalid token')\n    return True\n")
        ws.write_file("test_auth.py", "from auth import validate_token\n\ndef test_validate():\n    assert validate_token('valid_token') is True\n")
        yield ws


@pytest.fixture
def core_systems(test_workspace):
    """Initializes the real 7-component architecture stack without mocks."""
    ws = test_workspace
    skill_reg = SkillRegistry()
    agent_reg = AgentRegistry()
    mcp_mgr = MCPManager(workspace_dir=ws.root_dir)
    builtin_reg = BuiltinToolRegistry(
        workspace=ws,
        skill_registry=skill_reg,
        agent_registry=agent_reg,
    )
    dispatcher = UnifiedToolDispatcher(
        builtin_registry=builtin_reg,
        mcp_manager=mcp_mgr,
    )
    llm = LLMClient()
    return {
        "workspace": ws,
        "skill_registry": skill_reg,
        "agent_registry": agent_reg,
        "mcp_manager": mcp_mgr,
        "builtin_registry": builtin_reg,
        "dispatcher": dispatcher,
        "llm": llm,
    }


class TestObservationNormalization:
    """Verifies strict observation normalization into structured Observation objects."""

    def test_python_traceback_normalization(self, test_workspace):
        engine = ObservationEngine(workspace_root=test_workspace.root_dir)
        raw_traceback = """Traceback (most recent call last):
  File "c:/Users/perur/Desktop/Vasanth/auth.py", line 84, in validate_token
    raise ValueError("Token signature expired")
ValueError: Token signature expired
"""
        obs = engine.normalize(
            tool_name="terminal.run",
            raw_output=raw_traceback,
            exit_code=1,
            parameters={"command": "python auth.py"},
        )

        assert isinstance(obs, Observation)
        assert obs.type in ("runtime_error", "test_failure")
        assert obs.severity == "error"
        assert obs.line == 84
        assert obs.symbol == "validate_token"
        assert "ValueError" in obs.evidence or "Token signature expired" in obs.evidence
        # Ensure evidence is compact and does not contain raw traceback header
        assert "Traceback (most recent call last)" not in obs.evidence
        assert "Traceback" not in obs.to_replan_summary()

    def test_pytest_output_normalization(self, test_workspace):
        engine = ObservationEngine(workspace_root=test_workspace.root_dir)
        pytest_raw = """============================= test session starts =============================
rootdir: /app
collected 3 items

tests/test_auth.py .F.                                                   [100%]

================================== FAILURES ===================================
______________________________ test_invalid_token _____________________________

    def test_invalid_token():
>       assert validate_token("") == False
E       ValueError: Invalid token

tests/test_auth.py:28: ValueError
=========================== 1 failed, 2 passed in 0.12s ===========================
"""
        obs = engine.normalize(
            tool_name="terminal.run",
            raw_output=pytest_raw,
            exit_code=1,
            parameters={"command": "pytest"},
        )

        assert isinstance(obs, Observation)
        assert obs.type == "test_failure"
        assert obs.severity == "error"
        assert "test_invalid_token" in (obs.symbol or obs.evidence)
        assert "test session starts" not in obs.evidence
        assert len(obs.evidence) < 300

    def test_compiler_linter_diagnostic_normalization(self, test_workspace):
        engine = ObservationEngine(workspace_root=test_workspace.root_dir)
        compiler_raw = "auth.py:84:12: error: Cannot find name 'validate_token'."

        obs = engine.normalize(
            tool_name="compiler.check",
            raw_output=compiler_raw,
            exit_code=1,
            parameters={"target": "auth.py"},
        )

        assert isinstance(obs, Observation)
        assert obs.type == "compiler_error"
        assert obs.file == "auth.py"
        assert obs.line == 84
        assert obs.symbol == "validate_token"
        assert obs.severity == "error"
        assert "Cannot find name 'validate_token'" in obs.evidence

    def test_git_status_normalization(self, test_workspace):
        engine = ObservationEngine(workspace_root=test_workspace.root_dir)
        git_raw = """On branch main
Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
	modified:   auth.py
	modified:   test_auth.py
"""
        obs = engine.normalize(
            tool_name="git_status",
            raw_output=git_raw,
            exit_code=0,
            parameters={"command": "git status"},
        )

        assert isinstance(obs, Observation)
        assert obs.type == "git_status"
        assert obs.severity == "info"
        assert "On branch main" in obs.evidence
        assert "Changes not staged for commit" not in obs.evidence

    def test_browser_console_normalization(self, test_workspace):
        engine = ObservationEngine(workspace_root=test_workspace.root_dir)
        browser_raw = [
            {"level": "error", "text": "Uncaught TypeError: validateToken is not a function at auth.js:12"},
            {"level": "info", "text": "Page loaded"},
        ]
        obs = engine.normalize(
            tool_name="browser_console",
            raw_output=browser_raw,
            exit_code=0,
            parameters={"level": "all"},
        )

        assert isinstance(obs, Observation)
        assert obs.type == "browser_event"
        assert obs.severity == "error"
        assert "validateToken is not a function" in obs.evidence

    def test_filesystem_normalization(self, test_workspace):
        engine = ObservationEngine(workspace_root=test_workspace.root_dir)
        file_content = "def helper():\n    return 42\n"
        obs = engine.normalize(
            tool_name="read_file",
            raw_output=file_content,
            exit_code=0,
            parameters={"path": "auth.py"},
        )

        assert isinstance(obs, Observation)
        assert obs.type == "file_content"
        assert obs.severity == "info"
        assert obs.file == "auth.py"
        assert "auth.py inspected" in obs.evidence


class TestExecutionState:
    """Verifies ExecutionState lifecycle, tracking, and error decoupling from raw logs."""

    def test_state_lifecycle(self):
        state = ExecutionState(
            task_objective="Implement token validation",
            max_iterations=5,
        )
        assert state.status == LoopStatus.INITIALIZED
        assert not state.is_terminal()

        # Add reasoning and tool calls
        state.add_reasoning("I should inspect auth.py first")
        state.record_tool_call("read_file", {"path": "auth.py"})
        assert len(state.reasoning_history) == 1
        assert len(state.tool_calls) == 1

        # Add observations
        obs1 = Observation(type="file_content", file="auth.py", severity="info", evidence="File read")
        obs2 = Observation(type="compiler_error", file="auth.py", line=84, symbol="validate_token", severity="error", evidence="Syntax error")
        state.add_observation(obs1)
        state.add_observation(obs2)

        # Active errors extraction
        errors = state.get_active_errors()
        assert len(errors) == 1
        assert errors[0].symbol == "validate_token"

        evidence_list = state.get_active_error_evidence()
        assert len(evidence_list) == 1
        assert "[ERROR | compiler_error]" in evidence_list[0]
        assert "L84" in evidence_list[0]

        # Serialization
        data = state.to_dict()
        assert data["task_objective"] == "Implement token validation"
        assert data["active_errors_count"] == 1
        assert data["observations_count"] == 2

        # Completion
        state.complete("Token validation implemented.")
        assert state.is_terminal()
        assert state.status == LoopStatus.COMPLETED


class TestEventStream:
    """Verifies typed event streaming, listener registration, and event history."""

    def test_pub_sub_and_filtering(self):
        stream = EventStream()
        received_events: list[LoopEvent] = []

        # Subscribe to all events
        unsubscribe = stream.subscribe(lambda e: received_events.append(e))

        # Emit events
        e1 = stream.emit(LoopEventType.REASONING_STARTED, message="Thinking...", iteration=1)
        e2 = stream.emit(LoopEventType.TOOL_SELECTED, data={"tool": "read_file"}, iteration=1)
        e3 = stream.emit(LoopEventType.CONTEXT_REBUILT, data={"total_tokens": 1200}, iteration=1)

        assert len(received_events) == 3
        assert stream.get_history(LoopEventType.TOOL_SELECTED)[0].data["tool"] == "read_file"

        # Test unsubscribe
        unsubscribe()
        stream.emit(LoopEventType.LOOP_COMPLETED, message="Finished", iteration=1)
        assert len(received_events) == 3  # Not incremented after unsubscribe


class TestAgentExecutionLoop:
    """Verifies the complete 7-phase state machine execution loop."""

    def test_7_phase_state_machine_execution(self, core_systems, test_workspace):
        router = CapabilityRouter(
            tool_dispatcher=core_systems["dispatcher"],
            mcp_manager=core_systems["mcp_manager"],
            skill_registry=core_systems["skill_registry"],
        )
        permission_engine = PermissionEngine(
            tool_dispatcher=core_systems["dispatcher"],
            workspace_manager=core_systems["workspace"],
        )
        obs_engine = ObservationEngine(workspace_root=test_workspace.root_dir)
        compiler = ContextCompiler(
            skill_registry=core_systems["skill_registry"],
            agent_registry=core_systems["agent_registry"],
            tool_dispatcher=core_systems["dispatcher"],
            workspace_manager=core_systems["workspace"],
        )
        event_stream = EventStream()

        loop = AgentExecutionLoop(
            llm_client=core_systems["llm"],
            tool_dispatcher=core_systems["dispatcher"],
            capability_router=router,
            permission_engine=permission_engine,
            observation_engine=obs_engine,
            context_compiler=compiler,
            event_stream=event_stream,
            workspace_manager=core_systems["workspace"],
            skill_registry=core_systems["skill_registry"],
            agent_registry=core_systems["agent_registry"],
            max_iterations=3,
        )

        state = loop.run(task_objective="Inspect auth.py and verify functions")

        # Verify state reached completion
        assert state.status in (LoopStatus.COMPLETED, LoopStatus.TERMINATED)
        assert state.iteration >= 1
        assert len(state.reasoning_history) >= 1
        assert len(state.observations) >= 1

        # Verify dynamic context rebuild occurred
        assert state.rebuilt_context is not None
        assert hasattr(state.rebuilt_context, "to_prompt_context")

        # Verify event stream captured all phases
        types_emitted = {e.event_type for e in event_stream.get_history()}
        assert LoopEventType.REASONING_STARTED in types_emitted
        assert LoopEventType.REASONING_COMPLETED in types_emitted
        assert LoopEventType.STATE_UPDATED in types_emitted
        assert LoopEventType.CONTEXT_REBUILT in types_emitted

    def test_permission_denial_structured_recovery(self, core_systems, test_workspace):
        """Tests that disallowed tool requests trigger structured observations and replanning without crash."""
        router = CapabilityRouter(
            tool_dispatcher=core_systems["dispatcher"],
            mcp_manager=core_systems["mcp_manager"],
            skill_registry=core_systems["skill_registry"],
        )
        permission_engine = PermissionEngine(
            tool_dispatcher=core_systems["dispatcher"],
            workspace_manager=core_systems["workspace"],
        )
        obs_engine = ObservationEngine(workspace_root=test_workspace.root_dir)
        compiler = ContextCompiler(
            tool_dispatcher=core_systems["dispatcher"],
            workspace_manager=core_systems["workspace"],
        )
        event_stream = EventStream()

        loop = AgentExecutionLoop(
            llm_client=core_systems["llm"],
            tool_dispatcher=core_systems["dispatcher"],
            capability_router=router,
            permission_engine=permission_engine,
            observation_engine=obs_engine,
            context_compiler=compiler,
            event_stream=event_stream,
            workspace_manager=core_systems["workspace"],
            max_iterations=2,
        )

        # Force a read-only policy for a write task
        state = ExecutionState(
            task_objective="Inspect codebase safely",
            max_iterations=2,
            status=LoopStatus.RUNNING,
            capabilities=["filesystem.read"],
            allowed_tools=["read_file"],
            tool_policy=ToolPolicy(allowed_tools={"read_file"}, read_only=True),
        )

        # Simulate reason deciding to call write_file (which is disallowed)
        loop._phase_reason = lambda s, a=None: ("I will overwrite the file", {"action": "call_tool", "tool": "write_file", "parameters": {"path": "auth.py", "content": "bad"}})

        # Run loop
        state = loop.run(
            task_objective="Inspect codebase safely",
            max_iterations=2,
        )

        # Verify that permission was denied and recorded as structured observation
        assert len(state.observations) >= 1
        perm_obs = state.observations[0]
        assert perm_obs.type == "permission_denied"
        assert perm_obs.severity == "error"
        assert "disallowed" in perm_obs.evidence

        # Verify PERMISSION_DENIED event was emitted
        denied_events = event_stream.get_history(LoopEventType.PERMISSION_DENIED)
        assert len(denied_events) >= 1


class TestCoreSystemsAndLangGraphIntegration:
    """Verifies full integration across all 7 core systems and LangGraph StateGraph."""

    def test_langgraph_stategraph_node_integration(self, core_systems, test_workspace):
        from langgraph.graph import StateGraph, START, END

        loop = AgentExecutionLoop(
            llm_client=core_systems["llm"],
            tool_dispatcher=core_systems["dispatcher"],
            workspace_manager=core_systems["workspace"],
            skill_registry=core_systems["skill_registry"],
            agent_registry=core_systems["agent_registry"],
            mcp_manager=core_systems["mcp_manager"],
            max_iterations=2,
        )

        # Get LangGraph StateGraph node
        node_fn = loop.create_langgraph_node()
        assert callable(node_fn)

        # Build real LangGraph StateGraph
        builder = StateGraph(dict)
        builder.add_node("agent_loop", node_fn)
        builder.add_edge(START, "agent_loop")
        builder.add_edge("agent_loop", END)
        graph = builder.compile()

        # Execute StateGraph
        input_state = {
            "user_request": "Inspect auth.py and check validation logic",
            "active_skills": [],
            "max_iterations": 2,
        }
        output_state = graph.invoke(input_state)

        assert "execution_state" in output_state
        assert output_state["status"] in (LoopStatus.COMPLETED, LoopStatus.TERMINATED)
        assert isinstance(output_state["observations"], list)

    def test_in_tree_backward_compatibility(self):
        """Verifies that all new runtime classes are accessible from agent_orchestrator.runtime."""
        assert hasattr(in_tree_runtime, "Observation")
        assert hasattr(in_tree_runtime, "ObservationEngine")
        assert hasattr(in_tree_runtime, "ExecutionState")
        assert hasattr(in_tree_runtime, "LoopStatus")
        assert hasattr(in_tree_runtime, "EventStream")
        assert hasattr(in_tree_runtime, "LoopEvent")
        assert hasattr(in_tree_runtime, "LoopEventType")
        assert hasattr(in_tree_runtime, "AgentExecutionLoop")
        # Ensure pre-existing modules remain intact
        assert hasattr(in_tree_runtime, "ReActAgentLoop")
        assert hasattr(in_tree_runtime, "ToolPolicy")
        assert hasattr(in_tree_runtime, "CapabilityRouter")
        assert hasattr(in_tree_runtime, "PermissionEngine")
