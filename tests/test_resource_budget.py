"""
Unit and integration tests for Issue #82: Resource Budget and Usage Tracking.
Tests all 8 dimensions: max processes, max memory, max CPU, max disk, max network,
max tool calls, max tokens, and max runtime.
"""
import pytest
import time
from unittest.mock import MagicMock

from agent_orchestrator.security.resource_budget import (
    ResourceBudget,
    ResourceUsageTracker,
    ResourceBudgetDecision,
    ResourceBudgetExceededError,
    BudgetAction,
)
from agent_orchestrator.security.sandbox import SandboxPolicy, LocalProcessSandbox
from agent_orchestrator.security.network_policy import NetworkAccessPolicy
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.runtime.react_loop import ReActAgentLoop
from agent_orchestrator.runtime.task_graph import TaskPermissions, ExecutableTask
from agent_orchestrator.contracts import (
    ResourceBudgetContract,
    TaskPermissionsContract,
    ExecutableTaskContract,
)


class TestResourceBudgetBasics:
    def test_default_unconstrained_budget(self):
        budget = ResourceBudget.unconstrained()
        assert budget.is_unconstrained()
        assert budget.max_processes == 0
        assert budget.max_memory_mb == 0.0
        assert budget.max_tokens == 0
        assert budget.max_tool_calls_total == 0
        assert budget.max_disk_write_mb == 0.0
        assert budget.action == BudgetAction.HALT

    def test_budget_to_from_dict(self):
        budget = ResourceBudget(
            max_processes=10,
            max_memory_mb=512.0,
            max_cpu_percent=80.0,
            max_cpu_cores=2.0,
            max_disk_write_mb=50.0,
            max_single_file_mb=5.0,
            max_network_requests=100,
            max_network_mb=20.0,
            max_tool_calls_total=30,
            max_tool_calls_per_turn=5,
            max_tokens=50000,
            max_runtime_seconds=60.0,
            max_command_timeout_seconds=15.0,
            action=BudgetAction.WARN,
        )
        data = budget.to_dict()
        assert data["max_processes"] == 10
        assert data["max_memory_mb"] == 512.0
        assert data["max_tool_calls_per_turn"] == 5
        assert data["action"] == "WARN"

        restored = ResourceBudget.from_dict(data)
        assert restored.max_processes == 10
        assert restored.max_memory_mb == 512.0
        assert restored.action == BudgetAction.WARN


class TestResourceUsageTracker:
    def test_tool_call_limits(self):
        budget = ResourceBudget(max_tool_calls_total=3)
        tracker = ResourceUsageTracker(budget=budget)

        d1 = tracker.record_tool_call("read_file")
        assert d1.allowed
        assert tracker.total_tool_calls == 1

        d2 = tracker.record_tool_call("read_file")
        assert d2.allowed

        d3 = tracker.record_tool_call("write_file")
        assert d3.allowed

        # 4th call exceeds budget
        d4 = tracker.record_tool_call("run_command")
        assert not d4.allowed
        assert "Tool call budget exceeded" in d4.reason
        assert tracker.total_tool_calls == 4

        with pytest.raises(ResourceBudgetExceededError):
            tracker.assert_budget()

    def test_single_file_and_cumulative_disk_write(self):
        budget = ResourceBudget(
            max_single_file_mb=1.0,     # 1 MB max single file
            max_disk_write_mb=2.5,       # 2.5 MB cumulative max
        )
        tracker = ResourceUsageTracker(budget=budget)

        # 0.5 MB write - should pass
        half_mb = int(0.5 * 1024 * 1024)
        d1 = tracker.record_file_write("file1.txt", half_mb)
        assert d1.allowed

        # 1.5 MB write - single file limit exceeded
        large_mb = int(1.5 * 1024 * 1024)
        d2 = tracker.record_file_write("file2.bin", large_mb)
        assert not d2.allowed
        assert d2.resource_type == "single_file_size"

        # Another 0.8 MB write - single file okay, cumulative reaches 1.3 MB (under 2.5)
        d3 = tracker.record_file_write("file3.txt", int(0.8 * 1024 * 1024))
        assert d3.allowed

        # Another 1.5 MB write (single okay under 1 MB? No, 1.5 > 1.0; let's do 0.9 MB)
        # cumulative reaches 0.5 + 0.8 + 0.9 = 2.2 MB (under 2.5)
        d4 = tracker.record_file_write("file4.txt", int(0.9 * 1024 * 1024))
        assert d4.allowed

        # Another 0.5 MB write -> 2.7 MB cumulative > 2.5 MB limit
        d5 = tracker.record_file_write("file5.txt", int(0.5 * 1024 * 1024))
        assert not d5.allowed
        assert d5.resource_type == "disk_write"

    def test_network_request_and_bandwidth_limits(self):
        budget = ResourceBudget(
            max_network_requests=2,
            max_network_mb=1.0,
        )
        tracker = ResourceUsageTracker(budget=budget)

        # Request 1 (0.2 MB) - OK
        d1 = tracker.record_network_request("https://api.example.com", int(0.2 * 1024 * 1024))
        assert d1.allowed

        # Request 2 (0.3 MB) - OK
        d2 = tracker.record_network_request("https://api.example.com", int(0.3 * 1024 * 1024))
        assert d2.allowed

        # Request 3 - request count limit exceeded
        d3 = tracker.record_network_request("https://api.example.com", 100)
        assert not d3.allowed
        assert d3.resource_type == "network_requests"

    def test_token_ceiling_limits(self):
        budget = ResourceBudget(max_tokens=1000)
        tracker = ResourceUsageTracker(budget=budget)

        d1 = tracker.record_tokens(prompt_tokens=400, completion_tokens=200)
        assert d1.allowed
        assert tracker.total_tokens == 600

        d2 = tracker.record_tokens(prompt_tokens=300, completion_tokens=200)
        assert not d2.allowed
        assert tracker.total_tokens == 1100
        assert d2.resource_type == "tokens"

    def test_process_and_memory_sampling(self):
        budget = ResourceBudget(
            max_processes=5,
            max_memory_mb=256.0,
        )
        tracker = ResourceUsageTracker(budget=budget)

        # Normal sample
        d1 = tracker.record_process_sample(process_count=3, memory_mb=120.0, cpu_percent=15.0)
        assert d1.allowed

        # Process limit exceeded
        d2 = tracker.record_process_sample(process_count=8, memory_mb=120.0, cpu_percent=15.0)
        assert not d2.allowed
        assert d2.resource_type == "processes"

        # Memory limit exceeded
        d3 = tracker.record_process_sample(process_count=2, memory_mb=350.0, cpu_percent=15.0)
        assert not d3.allowed
        assert d3.resource_type == "memory"

    def test_runtime_budget(self):
        budget = ResourceBudget(max_runtime_seconds=0.05)
        tracker = ResourceUsageTracker(budget=budget)

        assert tracker.check_runtime().allowed
        time.sleep(0.08)
        dec = tracker.check_runtime()
        assert not dec.allowed
        assert dec.resource_type == "runtime"


class TestWorkspaceDiskBudgetIntegration:
    def test_workspace_blocks_large_single_file(self, tmp_path):
        budget = ResourceBudget(max_single_file_mb=0.01)  # ~10 KB limit
        ws = WorkspaceManager(tmp_path, resource_budget=budget)

        # Write 2 KB -> Allowed
        small_content = "A" * 2048
        res = ws.write_file("small.txt", small_content)
        assert res.exists()

        # Write 20 KB -> ResourceBudgetExceededError
        large_content = "B" * 20480
        with pytest.raises(ResourceBudgetExceededError) as exc_info:
            ws.write_file("large.txt", large_content)
        assert "Single file write size exceeded" in str(exc_info.value)

    def test_workspace_blocks_cumulative_disk_overage(self, tmp_path):
        budget = ResourceBudget(max_disk_write_mb=0.02)  # ~20 KB total
        ws = WorkspaceManager(tmp_path, resource_budget=budget)

        # 2 writes of 8 KB -> 16 KB total (OK)
        content_8k = "C" * 8192
        assert ws.write_file("file1.txt", content_8k).exists()
        assert ws.write_file("file2.txt", content_8k).exists()

        # 3rd write of 8 KB -> 24 KB total > 20 KB limit
        with pytest.raises(ResourceBudgetExceededError) as exc_info:
            ws.write_file("file3.txt", content_8k)
        assert "Cumulative workspace disk budget exceeded" in str(exc_info.value)


class TestLocalProcessSandboxResourceLimits:
    def test_sandbox_kills_process_exceeding_memory(self, tmp_path):
        # A tiny limit of 1.0 MB will be exceeded by a running Python interpreter
        budget = ResourceBudget(max_memory_mb=1.0)
        policy = SandboxPolicy(resource_budget=budget)
        sandbox = LocalProcessSandbox(working_dir=tmp_path, policy=policy)
        res = sandbox.run_command(["python", "-c", "import time; time.sleep(0.5)"])
        assert res.killed_due_to_limit is True
        assert "memory" in res.stderr.lower() or "resource" in res.stderr.lower()


class TestNetworkPolicyBudgetIntegration:
    def test_network_policy_blocks_requests_past_limit(self):
        budget = ResourceBudget(max_network_requests=1)
        tracker = ResourceUsageTracker(budget=budget)
        policy = NetworkAccessPolicy(
            allowlist=["api.github.com"],
            resource_budget=budget,
            resource_tracker=tracker,
        )

        # 1st request -> allowed
        dec1 = policy.evaluate_host("api.github.com", bytes_transferred=1024)
        assert dec1.allowed

        # 2nd request -> blocked by budget
        dec2 = policy.evaluate_host("api.github.com", bytes_transferred=1024)
        assert not dec2.allowed
        assert "Network request budget exceeded" in dec2.reason


class TestReActLoopResourceBudgetIntegration:
    def test_react_loop_throttles_tool_calls_per_turn(self):
        mock_llm = MagicMock()
        # LLM emits 4 tool calls in one turn
        mock_llm.chat_with_tools.return_value = {
            "content": "Executing operations",
            "tool_calls": [
                {"id": "1", "function": {"name": "read_file", "arguments": {"filepath": "a.txt"}}},
                {"id": "2", "function": {"name": "read_file", "arguments": {"filepath": "b.txt"}}},
                {"id": "3", "function": {"name": "read_file", "arguments": {"filepath": "c.txt"}}},
                {"id": "4", "function": {"name": "read_file", "arguments": {"filepath": "d.txt"}}},
            ],
        }

        mock_registry = MagicMock()
        mock_registry.call_tool.return_value = {"success": True, "content": "mock content"}

        budget = ResourceBudget(max_tool_calls_per_turn=2)
        loop = ReActAgentLoop(llm=mock_llm, tool_registry=mock_registry, max_turns=1)

        steps = []
        loop.on_step = lambda ev, data: steps.append((ev, data))

        res = loop.run(
            system_prompt="system",
            user_prompt="do task",
            resource_budget=budget,
        )

        # Check that tool calls throttled event fired
        throttled_events = [data for ev, data in steps if ev == "TOOL_CALLS_THROTTLED"]
        assert len(throttled_events) == 1
        assert throttled_events[0]["original_count"] == 4
        assert throttled_events[0]["throttled_count"] == 2
        # Only 2 tool calls should have been dispatched
        assert mock_registry.call_tool.call_count == 2
        assert "resource_usage" in res
        assert res["resource_usage"]["total_tool_calls"] == 2

    def test_react_loop_halts_on_token_ceiling(self):
        mock_llm = MagicMock()
        mock_llm.chat_with_tools.return_value = {
            "content": "X" * 400,  # ~100 completion tokens
        }

        budget = ResourceBudget(max_tokens=50, action=BudgetAction.HALT)
        loop = ReActAgentLoop(llm=mock_llm, tool_registry=MagicMock(), max_turns=2)

        steps = []
        loop.on_step = lambda ev, data: steps.append((ev, data))

        res = loop.run(
            system_prompt="system prompt",
            user_prompt="user prompt",
            resource_budget=budget,
        )

        exceeded_events = [data for ev, data in steps if ev == "RESOURCE_BUDGET_EXCEEDED"]
        assert len(exceeded_events) >= 1
        assert any("token" in err.lower() for err in res.get("errors", []))


class TestContractsAndSerialization:
    def test_contracts_support_resource_budget(self):
        rb_contract = ResourceBudgetContract(
            max_processes=4,
            max_memory_mb=1024.0,
            max_disk_write_mb=50.0,
            action="HALT",
        )
        perm_contract = TaskPermissionsContract(
            allowed_commands=["git status"],
            resource_budget=rb_contract,
        )
        task_contract = ExecutableTaskContract(
            task_id="T-100",
            objective="Compile build",
            permissions=perm_contract,
            resource_budget=rb_contract,
        )

        data = task_contract.model_dump()
        assert data["resource_budget"]["max_processes"] == 4
        assert data["permissions"]["resource_budget"]["max_memory_mb"] == 1024.0

    def test_task_graph_serialization_round_trip(self):
        budget = ResourceBudget(
            max_processes=8,
            max_memory_mb=512.0,
            max_tool_calls_total=25,
        )
        perms = TaskPermissions(resource_budget=budget)
        task = ExecutableTask(
            task_id="T-1",
            objective="Analyze code",
            permissions=perms,
            resource_budget=budget,
        )

        serialized = task.to_dict()
        assert serialized["resource_budget"]["max_processes"] == 8
        assert serialized["permissions"]["resource_budget"]["max_tool_calls_total"] == 25

        restored = ExecutableTask.from_dict(serialized)
        assert isinstance(restored.resource_budget, ResourceBudget)
        assert restored.resource_budget.max_processes == 8
        assert isinstance(restored.permissions.resource_budget, ResourceBudget)
        assert restored.permissions.resource_budget.max_tool_calls_total == 25
