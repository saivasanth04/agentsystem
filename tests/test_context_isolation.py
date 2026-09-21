"""
Tests for Context Isolation and On-Demand State Retrieval Engine (Issue #77).
Verifies the need-to-know context scoping principle: agents receive only what they need,
plus the ability to retrieve more on demand via query_specification, query_architecture, and get_task_artifact.
"""
import json
import pytest
from unittest.mock import MagicMock

from agent_orchestrator.state import OrchestratorState, ReplanRecord
from agent_orchestrator.runtime.task_graph import ExecutableTask, TaskDAG, TaskState
from agent_orchestrator.context.isolation import ContextIsolationEngine, IsolatedTaskContext
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.agents.coder import CoderAgent
from agent_orchestrator.agents.tester import TesterAgent


@pytest.fixture
def sample_orchestrator_state(tmp_path):
    state = OrchestratorState(user_request="Build an enterprise cache with auth and database persistence")

    state.specification_output = {
        "feature_name": "Enterprise Suite",
        "overview": "Multi-module enterprise system",
        "functional_requirements": [
            {
                "id": "FR-1",
                "description": "Implement in-memory LRU Cache with capacity eviction",
                "input_contract": "key: str, value: Any, ttl: int",
                "output_contract": "get(key) -> Any, put(key, value)",
            },
            {
                "id": "FR-2",
                "description": "Implement OAuth2 JWT authentication token verification",
                "input_contract": "token: str",
                "output_contract": "claims: Dict[str, Any]",
            },
            {
                "id": "FR-3",
                "description": "Implement PostgreSQL connection pool and transaction manager",
                "input_contract": "dsn: str, pool_size: int",
                "output_contract": "connection: Connection",
            },
        ],
        "edge_cases": [
            {
                "scenario": "Cache reaches exact capacity limit",
                "expected_behavior": "Least recently used item is evicted synchronously",
            },
            {
                "scenario": "Expired JWT token provided to auth validator",
                "expected_behavior": "Raises TokenExpiredError",
            },
        ],
        "acceptance_criteria": [
            "Given LRUCache(capacity=2), when 3 items added, then item 1 is evicted",
            "Given invalid JWT, when authenticate() called, then reject with 401",
        ],
        "non_functional_requirements": [
            {
                "id": "NFR-1",
                "category": "performance",
                "target": "Cache get/put operations must complete in O(1) time",
            },
        ],
    }

    state.architecture_output = {
        "system_title": "Enterprise Cache & Auth Architecture",
        "component_structure": [
            {
                "module_name": "cache.lru",
                "purpose": "In-memory LRU caching layer with doubly-linked list",
                "classes_or_functions": [
                    {"name": "LRUCache", "type": "class", "description": "Cache data structure"}
                ],
            },
            {
                "module_name": "auth.jwt",
                "purpose": "Security & token authorization services",
                "classes_or_functions": [
                    {"name": "JWTAuthenticator", "type": "class", "description": "Token verifier"}
                ],
            },
            {
                "module_name": "db.pool",
                "purpose": "Database connection pooling and migration management",
                "classes_or_functions": [
                    {"name": "ConnectionPool", "type": "class", "description": "Postgres pool"}
                ],
            },
        ],
        "file_layout": [
            {"filepath": "src/cache/lru.py", "description": "Core LRU Cache implementation"},
            {"filepath": "src/auth/jwt.py", "description": "JWT authentication handlers"},
            {"filepath": "src/db/pool.py", "description": "Database pool connector"},
        ],
    }

    state.step_results = {
        "T-01": {"module": "cache.lru", "status": "COMPLETED", "files": ["src/cache/lru.py"]},
        "T-02": {"module": "auth.jwt", "status": "COMPLETED", "files": ["src/auth/jwt.py"]},
        "T-03": {"module": "db.pool", "status": "COMPLETED", "files": ["src/db/pool.py"]},
    }

    state.replan_history = [
        ReplanRecord(
            iteration=1,
            trigger_reason="Auth test failed",
            feedback_summary="JWT token expiration was not properly handled in auth/jwt.py",
            remediation_plan=["Fix TokenExpiredError in auth/jwt.py", "Add test case for expired token"],
        )
    ]
    return state


# ---------------------------------------------------------------------------
# 1. ContextIsolationEngine Unit Tests
# ---------------------------------------------------------------------------

def test_isolate_for_task_scopes_specification(sample_orchestrator_state):
    """Verifies that a cache task only receives cache requirements and not auth/db requirements."""
    task_info = {
        "task_id": "T-01",
        "objective": "Implement LRU Cache with capacity eviction",
        "inputs": ["src/cache/lru.py"],
        "outputs": ["src/cache/lru.py:LRUCache"],
    }

    isolated = ContextIsolationEngine.isolate_for_task(sample_orchestrator_state, task_info=task_info)
    assert isinstance(isolated, IsolatedTaskContext)
    assert isolated.task_id == "T-01"

    scoped_spec = isolated.scoped_specification
    assert scoped_spec is not None
    assert scoped_spec["isolated_view"] is True

    # Cache requirement FR-1 should be present
    fr_ids = [fr["id"] for fr in scoped_spec.get("functional_requirements", [])]
    assert "FR-1" in fr_ids
    # Unrelated requirements FR-2 (auth) and FR-3 (database) must be excluded!
    assert "FR-2" not in fr_ids
    assert "FR-3" not in fr_ids

    # Edge cases: only cache edge case should be present
    edge_scenarios = [ec["scenario"] for ec in scoped_spec.get("edge_cases", [])]
    assert any("capacity" in s.lower() for s in edge_scenarios)
    assert not any("jwt" in s.lower() for s in edge_scenarios)


def test_isolate_for_task_scopes_architecture(sample_orchestrator_state):
    """Verifies that an isolated task only receives component architecture for its targets."""
    task_info = {
        "task_id": "T-01",
        "objective": "Implement LRU Cache data structure",
        "inputs": ["src/cache/lru.py"],
        "outputs": ["src/cache/lru.py"],
    }

    isolated = ContextIsolationEngine.isolate_for_task(sample_orchestrator_state, task_info=task_info)
    scoped_arch = isolated.scoped_architecture
    assert scoped_arch is not None

    module_names = [c["module_name"] for c in scoped_arch.get("component_structure", [])]
    assert "cache.lru" in module_names
    # Unrelated modules should be excluded
    assert "auth.jwt" not in module_names
    assert "db.pool" not in module_names

    # File layout should only retain cache/lru.py
    layout_paths = [l["filepath"] for l in scoped_arch.get("file_layout", [])]
    assert "src/cache/lru.py" in layout_paths
    assert "src/auth/jwt.py" not in layout_paths


def test_strict_parent_dependencies_isolation(sample_orchestrator_state):
    """Verifies that an agent only receives artifacts from declared dependencies, not unrelated parallel tasks."""
    # Task T-04 depends ONLY on T-01
    task_info = {
        "task_id": "T-04",
        "objective": "Add TTL support to cache",
        "dependencies": ["T-01"],
        "previous_step_results": sample_orchestrator_state.step_results,
    }

    isolated = ContextIsolationEngine.isolate_for_task(sample_orchestrator_state, task_info=task_info)
    # parent_artifacts should contain T-01 and NOT T-02 or T-03
    assert "T-01" in isolated.parent_artifacts
    assert "T-02" not in isolated.parent_artifacts
    assert "T-03" not in isolated.parent_artifacts


def test_empty_dependencies_produces_empty_parent_artifacts(sample_orchestrator_state):
    """An independent task with no dependencies must NOT receive leaked previous step results."""
    task_info = {
        "task_id": "T-01",
        "objective": "Implement initial cache",
        "dependencies": [],
        "previous_step_results": sample_orchestrator_state.step_results,
    }

    isolated = ContextIsolationEngine.isolate_for_task(sample_orchestrator_state, task_info=task_info)
    assert isolated.parent_artifacts == {}


def test_replan_context_scoping(sample_orchestrator_state):
    """Replan context should only be injected for tasks relevant to the replan issue."""
    # Cache task: replan was about auth, so cache task should NOT receive replan context
    cache_task = {"task_id": "T-01", "objective": "Cache task", "inputs": ["src/cache/lru.py"]}
    isolated_cache = ContextIsolationEngine.isolate_for_task(sample_orchestrator_state, task_info=cache_task)
    assert isolated_cache.replan_context is None

    # Auth task: replan was about auth, so auth task SHOULD receive replan context
    auth_task = {"task_id": "T-02", "objective": "Fix auth JWT verification", "inputs": ["src/auth/jwt.py"]}
    isolated_auth = ContextIsolationEngine.isolate_for_task(sample_orchestrator_state, task_info=auth_task)
    assert isolated_auth.replan_context is not None
    assert "JWT token expiration" in isolated_auth.replan_context


# ---------------------------------------------------------------------------
# 2. On-Demand Query Engine Unit Tests
# ---------------------------------------------------------------------------

def test_query_specification_on_demand(sample_orchestrator_state):
    """Verifies that an agent can pull specific functional requirements or edge cases on demand."""
    spec = sample_orchestrator_state.specification_output

    # Query for auth
    res_auth = ContextIsolationEngine.query_specification(spec, query="jwt")
    assert res_auth["found"] is True
    assert "functional_requirements" in res_auth["results"]
    assert any(fr["id"] == "FR-2" for fr in res_auth["results"]["functional_requirements"])

    # Query specifically for an ID
    res_id = ContextIsolationEngine.query_specification(spec, query="FR-3")
    assert res_id["found"] is True
    assert any("PostgreSQL" in fr["description"] for fr in res_id["results"]["functional_requirements"])

    # Query for non-existent concept
    res_missing = ContextIsolationEngine.query_specification(spec, query="blockchain")
    assert res_missing["found"] is False


def test_query_architecture_on_demand(sample_orchestrator_state):
    """Verifies that an agent can pull component topologies on demand."""
    arch = sample_orchestrator_state.architecture_output

    res = ContextIsolationEngine.query_architecture(arch, module_name="auth")
    assert res["found"] is True
    assert any(c["module_name"] == "auth.jwt" for c in res["components"])

    res_missing = ContextIsolationEngine.query_architecture(arch, module_name="nonexistent_service")
    assert res_missing["found"] is False


def test_get_task_artifact_on_demand(sample_orchestrator_state):
    """Verifies that an agent can retrieve a predecessor task's artifact on demand."""
    res = ContextIsolationEngine.get_task_artifact(sample_orchestrator_state, task_id="T-01")
    assert res["found"] is True
    assert res["task_id"] == "T-01"
    assert res["deliverable"]["module"] == "cache.lru"

    res_missing = ContextIsolationEngine.get_task_artifact(sample_orchestrator_state, task_id="T-99")
    assert res_missing["found"] is False


# ---------------------------------------------------------------------------
# 3. BuiltinToolRegistry Integration Tests
# ---------------------------------------------------------------------------

def test_registry_on_demand_tools(tmp_path, sample_orchestrator_state):
    """Verifies that BuiltinToolRegistry exposes and executes on-demand state retrieval tools."""
    workspace = WorkspaceManager(str(tmp_path))
    registry = BuiltinToolRegistry(workspace=workspace)
    registry.set_orchestrator_state(sample_orchestrator_state)

    # 1. query_specification
    tool_spec = registry.get("query_specification")
    assert tool_spec is not None
    spec_result = tool_spec.tool_instance.invoke({"query": "LRU"})
    assert spec_result["found"] is True
    assert any("LRU Cache" in fr["description"] for fr in spec_result["results"]["functional_requirements"])

    # 2. query_architecture
    tool_arch = registry.get("query_architecture")
    assert tool_arch is not None
    arch_result = tool_arch.tool_instance.invoke({"module_name": "cache"})
    assert arch_result["found"] is True
    assert any(c["module_name"] == "cache.lru" for c in arch_result["components"])

    # 3. get_task_artifact
    tool_artifact = registry.get("get_task_artifact")
    assert tool_artifact is not None
    artifact_result = tool_artifact.tool_instance.invoke({"task_id": "T-02"})
    assert artifact_result["found"] is True
    assert artifact_result["deliverable"]["module"] == "auth.jwt"


# ---------------------------------------------------------------------------
# 4. Agent Prompt Scoping & Token Reduction Benchmark
# ---------------------------------------------------------------------------

def test_coder_prompt_isolation_and_token_reduction(tmp_path, sample_orchestrator_state):
    """
    Verifies that Coder receives a scoped prompt for a specific subtask,
    reducing token bloat while providing notice of on-demand retrieval tools.
    """
    workspace = WorkspaceManager(str(tmp_path))
    coder = CoderAgent(workspace=workspace)

    task_info = {
        "task_id": "T-01",
        "objective": "Implement LRU Cache data structure with eviction",
        "inputs": ["src/cache/lru.py"],
        "outputs": ["src/cache/lru.py:LRUCache"],
    }

    # Mock react_loop to inspect generated prompt
    captured_prompt = {}
    def mock_run(*args, **kwargs):
        captured_prompt["user_prompt"] = kwargs.get("user_prompt", "")
        return {"final_output": {"summary": "Implemented LRU Cache"}}

    coder.react_loop.run = mock_run

    coder.execute(sample_orchestrator_state, task_info=task_info)
    user_prompt = captured_prompt.get("user_prompt", "")

    # 1. Scoped Specification must be present
    assert "Specification (Scoped)" in user_prompt
    assert "FR-1" in user_prompt

    # 2. Unrelated specifications (Auth FR-2, DB FR-3) must NOT be present in prompt!
    assert "FR-2" not in user_prompt
    assert "FR-3" not in user_prompt
    assert "OAuth2 JWT" not in user_prompt
    assert "PostgreSQL" not in user_prompt

    # 3. Scoped Architecture must be present
    assert "Architecture Blueprint (Scoped)" in user_prompt
    assert "cache.lru" in user_prompt
    assert "auth.jwt" not in user_prompt
    assert "db.pool" not in user_prompt

    # 4. Instructions must inform agent about on-demand retrieval
    assert "query_specification" in user_prompt
    assert "query_architecture" in user_prompt
    assert "get_task_artifact" in user_prompt


def test_tester_prompt_isolation(tmp_path, sample_orchestrator_state):
    """Verifies that Tester receives a scoped specification for testing an individual module."""
    workspace = WorkspaceManager(str(tmp_path))
    tester = TesterAgent(workspace=workspace)

    task_info = {
        "task_id": "T-01",
        "objective": "Write unit tests for LRU Cache",
        "inputs": ["src/cache/lru.py"],
        "outputs": ["tests/test_cache.py"],
    }

    captured_prompt = {}
    def mock_run(*args, **kwargs):
        captured_prompt["user_prompt"] = kwargs.get("user_prompt", "")
        return {"final_output": {"summary": "Tests written"}}

    tester.react_loop.run = mock_run

    tester.execute(sample_orchestrator_state, task_info=task_info)
    user_prompt = captured_prompt.get("user_prompt", "")

    assert "Specification & Acceptance Criteria (Scoped)" in user_prompt
    assert "FR-1" in user_prompt
    assert "FR-2" not in user_prompt
    assert "FR-3" not in user_prompt
    assert "query_specification" in user_prompt


def test_backward_compatibility_without_task_info(tmp_path, sample_orchestrator_state):
    """Verifies that calling coder.execute(state) without task_info still succeeds gracefully."""
    workspace = WorkspaceManager(str(tmp_path))
    coder = CoderAgent(workspace=workspace)

    captured_prompt = {}
    def mock_run(*args, **kwargs):
        captured_prompt["user_prompt"] = kwargs.get("user_prompt", "")
        return {"final_output": {"summary": "Code written without task_info"}}

    coder.react_loop.run = mock_run

    res = coder.execute(sample_orchestrator_state)
    assert res is not None
    assert "Specification" in captured_prompt.get("user_prompt", "")
