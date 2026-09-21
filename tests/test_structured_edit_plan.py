"""
Tests for Structured Edit Plan Engine (Issue #86).
Verifies:
1. Structured edit plan contracts and action data structures
2. Automated plan synthesis from task metadata & architecture blueprints
3. Topological sorting and dependency ordering
4. Policy pre-validation (MutationPolicy, FileAccessPolicy)
5. Live step progress tracking and status transitions
6. BuiltinToolRegistry tool integration (generate_edit_plan, get_edit_plan, update_edit_plan_step)
7. CoderAgent prompt context injection and execution
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from agent_orchestrator.contracts import (
    FileEditAction,
    FileEditActionType,
    FileEditStepStatus,
    StructuredEditPlanContract,
)
from agent_orchestrator.runtime.edit_plan import EditPlanManager
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.state import OrchestratorState
from agent_orchestrator.agents.coder import CoderAgent
from agent_orchestrator.security.file_access_policy import FileAccessPolicy, FileAccessMode, FileAccessDecision


def test_edit_plan_contract_serialization():
    action1 = FileEditAction(
        step_id="step-1",
        filepath="src/models.py",
        action_type=FileEditActionType.CREATE_FILE,
        target_symbols=["User", "Item"],
        description="Create models",
        dependencies=[],
        status=FileEditStepStatus.PENDING,
    )
    action2 = FileEditAction(
        step_id="step-2",
        filepath="src/service.py",
        action_type=FileEditActionType.MODIFY_SYMBOL,
        target_symbols=["process_order"],
        description="Add order logic",
        dependencies=["step-1"],
        status=FileEditStepStatus.PENDING,
    )
    plan = StructuredEditPlanContract(
        plan_id="plan-123",
        task_id="T-01",
        summary="Build models and service",
        edit_sequence=[action1, action2],
        invariants=["Ensure models are valid Python dataclasses"],
        verification_commands=["pytest tests/test_service.py"],
        estimated_risk="LOW",
    )

    d = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
    assert d["plan_id"] == "plan-123"
    assert len(d["edit_sequence"]) == 2
    assert d["edit_sequence"][0]["target_symbols"] == ["User", "Item"]

    restored = StructuredEditPlanContract(**d)
    assert restored.plan_id == "plan-123"
    assert restored.edit_sequence[1].dependencies == ["step-1"]


def test_edit_plan_manager_generate_plan(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    # Create an existing file
    existing_file = tmp_path / "src" / "utils.py"
    existing_file.parent.mkdir(parents=True, exist_ok=True)
    existing_file.write_text("def helper(): pass", encoding="utf-8")

    task_info = {
        "task_id": "T-01",
        "objective": "Add cache helper and service",
        "inputs": ["src/utils.py:helper"],
        "outputs": ["src/service.py:OrderService", "src/utils.py:cache_get"],
        "acceptance_tests": ["pytest tests/test_service.py"],
    }

    arch = {
        "component_structure": [
            {"module_name": "src/service.py", "description": "Core service"}
        ]
    }
    spec = {
        "edge_cases": [
            {"scenario": "Cache miss", "expected_behavior": "Fetch from db"}
        ]
    }

    plan = EditPlanManager.generate_plan(
        task_info=task_info,
        workspace=ws,
        architecture=arch,
        specification=spec,
    )

    assert plan.task_id == "T-01"
    assert len(plan.edit_sequence) >= 3  # service.py, utils.py, tests/test_service.py
    
    # Check that new file gets CREATE_FILE and existing file gets MODIFY_SYMBOL or REPLACE_BLOCK
    service_step = next(s for s in plan.edit_sequence if s.filepath == "src/service.py")
    assert service_step.action_type == FileEditActionType.CREATE_FILE
    assert "OrderService" in service_step.target_symbols

    utils_step = next(s for s in plan.edit_sequence if s.filepath == "src/utils.py")
    assert utils_step.action_type in (FileEditActionType.MODIFY_SYMBOL, FileEditActionType.REPLACE_BLOCK)

    # Invariants should capture edge case
    assert any("Cache miss" in inv for inv in plan.invariants)

    # Markdown representation
    prompt_md = EditPlanManager.to_prompt_context(plan)
    assert "Structured Mutation Plan" in prompt_md
    assert "src/service.py" in prompt_md
    assert "src/utils.py" in prompt_md
    assert "pytest tests/test_service.py" in prompt_md


def test_edit_plan_topological_sort():
    actions = [
        FileEditAction(step_id="step-3", filepath="src/app.py", dependencies=["step-2"]),
        FileEditAction(step_id="step-1", filepath="src/model.py", dependencies=[]),
        FileEditAction(step_id="step-2", filepath="src/service.py", dependencies=["step-1"]),
    ]

    sorted_actions = EditPlanManager.topological_sort(actions)
    step_ids = [a.step_id for a in sorted_actions]
    assert step_ids == ["step-1", "step-2", "step-3"]


def test_edit_plan_policy_prevalidation():
    policy = FileAccessPolicy(
        allowed_paths=["src/**"],
        blocked_paths=["secrets/**"],
    )

    plan = StructuredEditPlanContract(
        plan_id="plan-sec",
        task_id="T-SEC",
        edit_sequence=[
            FileEditAction(step_id="s1", filepath="src/app.py", action_type=FileEditActionType.CREATE_FILE),
            FileEditAction(step_id="s2", filepath="secrets/key.json", action_type=FileEditActionType.CREATE_FILE),
        ],
    )

    is_valid, violations = EditPlanManager.validate_against_policies(plan, file_access_policy=policy)
    assert not is_valid
    assert len(violations) == 1
    assert "secrets/key.json" in violations[0]


def test_edit_plan_step_status_update():
    plan = StructuredEditPlanContract(
        plan_id="plan-up",
        task_id="T-UP",
        edit_sequence=[
            FileEditAction(step_id="step-1", filepath="src/a.py", status=FileEditStepStatus.PENDING),
            FileEditAction(step_id="step-2", filepath="src/b.py", status=FileEditStepStatus.PENDING),
        ],
    )

    EditPlanManager.update_step_status(plan, "step-1", FileEditStepStatus.COMPLETED)
    EditPlanManager.update_step_status(plan, "step-2", FileEditStepStatus.FAILED, error="Syntax error on line 12")

    assert plan.edit_sequence[0].status == FileEditStepStatus.COMPLETED
    assert plan.edit_sequence[1].status == FileEditStepStatus.FAILED
    assert plan.edit_sequence[1].error == "Syntax error on line 12"


def test_builtin_tool_registry_edit_plan_tools(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    tools = BuiltinToolRegistry(workspace=ws)

    # Check tools are registered for CODER and TESTER
    coder_tools = [t.name for t in tools.get_tools_for_agent("CODER")]
    tester_tools = [t.name for t in tools.get_tools_for_agent("TESTER")]
    assert "generate_edit_plan" in coder_tools
    assert "get_edit_plan" in coder_tools
    assert "update_edit_plan_step" in coder_tools
    assert "generate_edit_plan" in tester_tools

    # 1. Generate plan tool
    gen_res = tools.execute("generate_edit_plan", {
        "task_id": "T-TOOL",
        "objective": "Create database handler",
        "outputs": ["src/db.py:DatabaseHandler"],
        "acceptance_tests": ["pytest tests/test_db.py"],
    })
    assert gen_res.success is True
    out_dict = gen_res.output
    assert out_dict["status"] == "SUCCESS"
    assert out_dict["plan"]["task_id"] == "T-TOOL"
    assert "Structured Mutation Plan" in out_dict["formatted_context"]

    # 2. Get plan tool
    get_res = tools.execute("get_edit_plan", {})
    assert get_res.success is True
    get_out = get_res.output
    assert get_out["status"] == "SUCCESS"
    assert get_out["plan"]["task_id"] == "T-TOOL"

    # 3. Update step status tool
    up_res = tools.execute("update_edit_plan_step", {
        "step_id": "step-1",
        "status": "COMPLETED",
    })
    assert up_res.success is True
    up_out = up_res.output
    assert up_out["status"] == "SUCCESS"
    assert up_out["new_status"] == "COMPLETED"


def test_coder_agent_context_assembly_with_edit_plan(tmp_path):
    ws = WorkspaceManager(root_dir=tmp_path)
    tools = BuiltinToolRegistry(workspace=ws)

    state = OrchestratorState(user_request="Implement user repository")
    state.specification_output = {"functional_requirements": ["FR-1: Store users"]}
    state.architecture_output = {"component_structure": [{"module_name": "src/repo.py"}]}

    coder = CoderAgent(workspace=ws, tool_registry=tools)

    # Mock react_loop to inspect prompt
    captured_prompts = []
    def mock_run(*args, **kwargs):
        captured_prompts.append(kwargs.get("user_prompt", ""))
        return {"final_output": {"summary": "Done"}, "turns_taken": 1, "history_events": []}

    coder.react_loop.run = mock_run

    task_info = {
        "task_id": "T-10",
        "objective": "Implement user repository",
        "outputs": ["src/repo.py:UserRepository"],
        "acceptance_tests": ["pytest tests/test_repo.py"],
    }

    coder.execute(state=state, task_info=task_info)

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "### Structured Mutation Plan" in prompt
    assert "src/repo.py" in prompt
    assert "pytest tests/test_repo.py" in prompt
    assert "Follow the Structured Mutation Plan step-by-step" in prompt
