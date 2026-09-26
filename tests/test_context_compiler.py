"""
Comprehensive Integration Tests for the Context Compiler Pipeline.
Verifies compliance with the 10 Non-Negotiable Contract clauses:
- Real executable classes (Zero mocks, zero placeholders)
- Mature libraries:
  1. tiktoken: Exact token counting, budgeting, and truncation
  2. numpy: Vectorized cosine similarity and MMR diversity ranking
  3. rapidfuzz: Fuzzy deduplication of errors, diffs, and memory items
- Strict Anti-Bloat rules:
  1. Never send entire repository (targeted symbols & call paths only)
  2. Never send entire conversation (recent turns only)
  3. Never send entire SKILL.md (relevant sections only)
- Integration across all 7 core systems:
  1. SkillRegistry
  2. AgentRegistry
  3. MCPManager
  4. UnifiedToolDispatcher
  5. WorkspaceManager
  6. LangGraph (StateGraph compilation & execution)
  7. LiteLLM Gateway (chat formatted context messages)
- In-tree backward compatibility via agent_orchestrator.context
"""
import os
from pathlib import Path
import tempfile
import numpy as np
import pytest
import tiktoken
from rapidfuzz import fuzz

from agent_orchestrator.registry.skill_registry import SkillRegistry, SkillManifest
from agent_orchestrator.registry.agent_registry import AgentRegistry
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.llm import LLMClient

from repository.repository_brain import RepositoryBrain

from context.budget import TokenCounter, ContextBudgetManager, BudgetAllocation
from context.ranking import ContextRanker, RankedCandidate
from context.dedup import ContextDeduplicator
from context.pack import (
    SkillSectionSlicer,
    RepositorySlicer,
    ConversationSlicer,
    OptimizedContextPackage,
)
from context.compiler import ContextCompiler


@pytest.fixture
def test_workspace():
    """Provides a real temporary workspace with sample code files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = WorkspaceManager(root_dir=tmpdir)
        ws.write_file(
            "auth.py",
            (
                "import jwt\n\n"
                "class TokenManager:\n"
                "    def generate_token(self, user_id: str) -> str:\n"
                "        return f'token_{user_id}'\n\n"
                "    def verify_token(self, token: str) -> bool:\n"
                "        return token.startswith('token_')\n"
            ),
        )
        ws.write_file(
            "service.py",
            (
                "from auth import TokenManager\n\n"
                "def authenticate_request(headers: dict) -> bool:\n"
                "    mgr = TokenManager()\n"
                "    auth_header = headers.get('Authorization', '')\n"
                "    return mgr.verify_token(auth_header)\n"
            ),
        )
        yield ws


@pytest.fixture
def repo_brain(test_workspace):
    """Initializes a real persistent RepositoryBrain on the test workspace."""
    brain = RepositoryBrain(workspace_manager=test_workspace)
    brain.build_full_index()
    yield brain
    brain.close()


@pytest.fixture
def core_systems(test_workspace):
    """Initializes the real 7-component system stack without mocks."""
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


class TestTokenBudgeting:
    """Verifies tiktoken-backed token counting and strict budget enforcement."""

    def test_tiktoken_counter_accuracy(self):
        counter = TokenCounter(model_name="cl100k_base")
        assert counter.encoding is not None
        
        sample_text = "def calculate_hash(data: bytes) -> str:\n    return hashlib.sha256(data).hexdigest()"
        raw_tokens = counter.encoding.encode(sample_text)
        assert counter.count(sample_text) == len(raw_tokens)
        assert counter.count(sample_text) > 0

    def test_tiktoken_exact_truncation(self):
        counter = TokenCounter(model_name="cl100k_base")
        long_text = "word " * 500
        total_tokens = counter.count(long_text)
        assert total_tokens > 100

        truncated = counter.truncate(long_text, max_tokens=50)
        assert counter.count(truncated) <= 50
        assert len(truncated) < len(long_text)

    def test_budget_manager_allocations(self):
        mgr = ContextBudgetManager(total_budget=4000)
        alloc = mgr.allocate()

        # Check total budget matches sum of parts (allowing for rounding margin)
        sum_tokens = (
            alloc.task_objective
            + alloc.verification_state
            + alloc.errors
            + alloc.diff
            + alloc.repository_brain
            + alloc.skills
            + alloc.working_memory
            + alloc.conversation
        )
        assert sum_tokens <= 4000
        assert alloc.repository_brain == 1000
        assert alloc.skills == 800

    def test_budget_manager_fit_budget(self):
        mgr = ContextBudgetManager(total_budget=100)
        long_error = "Traceback (most recent call last):\n" + ("  File 'foo.py', line 12 in bar\n" * 40)
        fitted = mgr.fit_budget(long_error, max_tokens=30)
        assert mgr.count_tokens(fitted) <= 30


class TestRankingEngine:
    """Verifies numpy-backed vectorized cosine similarity and MMR diversity ranking."""

    def test_numpy_vectorized_scoring(self):
        ranker = ContextRanker(diversity_lambda=1.0) # Pure relevance
        query = "authentication token verification jwt"
        candidates = [
            "TokenManager generates and verifies jwt tokens for security",
            "Database connection pool setup and postgres queries",
            "Verify token authorization headers in service",
            "CSS styles and responsive grid layout",
        ]

        scored = ranker.rank_candidates(query, candidates, top_k=4)
        assert len(scored) == 4
        # Candidate 0 and Candidate 2 should be ranked highest due to vocabulary match
        top_candidates = [s.content for s in scored[:2]]
        assert any("TokenManager" in c for c in top_candidates)
        assert any("Verify token" in c for c in top_candidates)
        assert scored[0].score >= scored[1].score

    def test_mmr_diversity_selection(self):
        # With lower lambda, diverse items are promoted over redundant items
        ranker_diverse = ContextRanker(diversity_lambda=0.3)
        query = "database connection failure"
        candidates = [
            "database connection failure timed out on port 5432",
            "database connection failure timed out on port 5432 attempt 2",
            "database connection failure timed out on port 5432 attempt 3",
            "SSL certificate expired on host database server",
            "disk space full on log partition",
        ]

        scored = ranker_diverse.rank_candidates(query, candidates, top_k=3)
        # Should select the first error, then prefer SSL or disk space over the near-duplicate errors
        top_contents = [s.content for s in scored]
        assert len(top_contents) == 3
        # Candidate 0 is top
        assert top_contents[0] == candidates[0]
        # At least one non-duplicate should be in top 3
        assert (candidates[3] in top_contents) or (candidates[4] in top_contents)


class TestFuzzyDeduplication:
    """Verifies rapidfuzz-backed deduplication of errors, diffs, and memory items."""

    def test_rapidfuzz_error_deduplication(self):
        dedup = ContextDeduplicator(default_threshold=80.0)
        errors = [
            "ValidationError: Field 'email' is invalid at line 42",
            "ValidationError: Field 'email' is invalid at line 43",
            "ValidationError: Field 'email' is invalid at line 99",
            "ConnectionRefusedError: Failed to connect to Redis on localhost:6379",
            "TimeoutError: Request exceeded 30 seconds threshold",
        ]

        deduped = dedup.dedup_errors(errors)
        # The 3 ValidationError variations should be collapsed to 1 representative with count
        assert len(deduped) == 3
        validation_errors = [e for e in deduped if "ValidationError" in e]
        assert len(validation_errors) == 1
        assert "[x3 instances]" in validation_errors[0]

    def test_rapidfuzz_string_deduplication(self):
        dedup = ContextDeduplicator(default_threshold=85.0)
        strings = [
            "User requested database schema migration for PostgreSQL",
            "User requested database schema migration for Postgres",
            "Added API route for user authentication",
        ]
        result = dedup.dedup_strings(strings)
        assert len(result) == 2
        assert any("API route" in s for s in result)


class TestAntiBloatSlicers:
    """
    Verifies strict Anti-Bloat rules:
    - Never send entire repository (targeted symbols & call paths only)
    - Never send entire conversation (recent turns only)
    - Never send entire SKILL.md (relevant sections only)
    """

    def test_skill_never_sends_entire_file(self):
        counter = TokenCounter()
        slicer = SkillSectionSlicer(counter)

        full_skill_content = """# Comprehensive Python Architecture Skill
This is an enormous 20,000 word guide with extensive historical commentary.

## Overview
High-level description of general programming concepts.

## Setup Instructions
1. Install Python 3.13.
2. Configure pyproject.toml.
3. Install dependencies.

## Error Recovery
When a ConnectionRefusedError occurs:
1. Check socket availability on localhost.
2. Inspect firewall policies.
3. Restart daemon worker.

## Advanced Metaprogramming
Extensive deep dive into metaclasses, bytecode manipulation, and CPython internals...
""" + ("Extra filler paragraphs...\n" * 100)

        # Objective is specifically error recovery
        sliced = slicer.slice_skill(
            skill_name="python-architecture",
            skill_content=full_skill_content,
            task_objective="Fix ConnectionRefusedError on localhost worker",
            max_tokens=250,
        )

        assert "Error Recovery" in sliced
        assert "Advanced Metaprogramming" not in sliced
        assert len(sliced) < len(full_skill_content)
        assert counter.count(sliced) <= 250

    def test_repo_never_sends_entire_codebase(self, repo_brain):
        counter = TokenCounter()
        slicer = RepositorySlicer(repo_brain, counter)

        # Slice repository for a specific authentication task
        sliced = slicer.slice_repository(
            task_objective="Verify jwt tokens with TokenManager in auth",
            max_tokens=400,
        )

        assert "TokenManager" in sliced or "verify_token" in sliced
        # Ensure we didn't just dump raw files
        assert counter.count(sliced) <= 400
        # Ensure targeted architecture / symbol layout
        assert "Repository Intelligence:" in sliced

    def test_conversation_never_sends_entire_history(self):
        counter = TokenCounter()
        slicer = ConversationSlicer(counter)

        # Construct 30 conversation messages
        messages = []
        for i in range(30):
            messages.append({"role": "user", "content": f"Turn {i}: How do I do step {i}?"})
            messages.append({"role": "assistant", "content": f"Response {i}: Here is the answer for step {i}."})

        # Slicer with max_turns=3 and max_tokens=150
        sliced = slicer.slice_conversation(messages, max_turns=3, max_tokens=150)

        # Only turns 27, 28, 29 should be present
        assert "Turn 29" in sliced or "Turn 28" in sliced
        assert "Turn 0" not in sliced
        assert "Turn 5" not in sliced
        assert counter.count(sliced) <= 150


class TestContextCompilerIntegration:
    """Verifies end-to-end ContextCompiler pipeline across all 7 streams."""

    def test_full_7_stream_compilation(self, repo_brain, core_systems):
        compiler = ContextCompiler(
            total_budget=3000,
            skill_registry=core_systems["skill_registry"],
            agent_registry=core_systems["agent_registry"],
            mcp_manager=core_systems["mcp_manager"],
            tool_dispatcher=core_systems["dispatcher"],
            workspace_manager=core_systems["workspace"],
            llm_client=core_systems["llm"],
        )

        task_objective = "Fix TokenManager authentication validation"
        verification_state = {"status": "FAILED", "failing_tests": ["tests/test_auth.py"]}
        errors = [
            "AuthError: Token validation failed for token_abc",
            "AuthError: Token validation failed for token_abc",
            "AuthError: Token validation failed for token_abc",
            "KeyError: 'Authorization' header missing in headers",
        ]
        diff = "--- a/auth.py\n+++ b/auth.py\n@@ -5,2 +5,2 @@\n-    return token.startswith('token_')\n+    return token.startswith('tok_')\n"
        skills = [
            SkillManifest(
                name="auth-verifier",
                description="Token security guidelines",
                category="security",
                system_instructions="## Token Rules\nVerify token signature and prefix format strictly.\n\n## Unrelated\nOther instructions.",
            )
        ]
        working_memory = {"current_step": "verification", "session_id": "test-session-123"}
        conversation = [
            {"role": "user", "content": "Initial prompt from long ago."},
            {"role": "user", "content": "Please inspect TokenManager and fix token validation."},
        ]

        # Compile through the active 7-stream pipeline
        pkg = compiler.compile(
            task_objective=task_objective,
            verification_state=verification_state,
            errors=errors,
            diff=diff,
            repository_brain=repo_brain,
            skills=skills,
            working_memory=working_memory,
            conversation=conversation,
        )

        assert isinstance(pkg, OptimizedContextPackage)
        assert pkg.total_tokens <= 3000
        assert pkg.task_objective == task_objective

        # Verify deduplication reduced errors
        assert len(pkg.errors) == 2  # 3 AuthErrors collapsed into 1 + 1 KeyError

        # Verify rendered output contains essential sections
        rendered = pkg.render()
        assert "# Task Objective" in rendered
        assert "# Verification State" in rendered
        assert "# Deduplicated Errors" in rendered
        assert "# Working Diff" in rendered
        assert "# Repository Intelligence" in rendered
        assert "# Active Skills" in rendered

        # Verify token breakdown dictionary
        breakdown = pkg.token_breakdown
        assert "task_objective" in breakdown
        assert "repository_brain" in breakdown
        assert "errors" in breakdown
        assert breakdown["total"] == pkg.total_tokens
        assert sum(v for k, v in breakdown.items() if k != "total") == pkg.total_tokens

    def test_compile_chat_messages_for_litellm(self, core_systems):
        compiler = ContextCompiler(
            total_budget=2000,
            llm_client=core_systems["llm"],
        )
        messages = compiler.compile_chat_messages(
            task_objective="Implement token caching in Redis",
            system_role="Senior Backend Architect",
            errors=["RedisConnectionError: connection timed out"],
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "Senior Backend Architect" in messages[0]["content"]
        assert "# Task Objective" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Implement token caching in Redis"


class TestLangGraphAndSystemIntegration:
    """Verifies LangGraph StateGraph integration and in-tree module compatibility."""

    def test_langgraph_node_in_stategraph(self, repo_brain, core_systems):
        from langgraph.graph import StateGraph, START, END

        compiler = ContextCompiler(
            total_budget=2500,
            skill_registry=core_systems["skill_registry"],
            agent_registry=core_systems["agent_registry"],
            mcp_manager=core_systems["mcp_manager"],
            tool_dispatcher=core_systems["dispatcher"],
            workspace_manager=core_systems["workspace"],
            llm_client=core_systems["llm"],
        )

        # Get LangGraph StateGraph node function
        context_node = compiler.create_langgraph_node()

        builder = StateGraph(dict)
        builder.add_node("context_compiler", context_node)
        builder.add_edge(START, "context_compiler")
        builder.add_edge("context_compiler", END)
        graph = builder.compile()

        input_state = {
            "user_request": "Refactor TokenManager in auth.py",
            "errors": ["TokenManager has syntax error at line 3"],
            "verification_state": {"tests_passing": False},
        }

        output_state = graph.invoke(input_state)

        assert "optimized_context" in output_state
        assert "context_package" in output_state
        assert "context_token_breakdown" in output_state
        assert output_state["context_token_breakdown"]["total"] > 0
        assert "# Task Objective" in output_state["optimized_context"]

    def test_in_tree_backward_compatibility(self):
        """Verifies that agent_orchestrator.context re-exports the modern context compiler suite."""
        from agent_orchestrator.context import (
            ContextCompiler as OrchestratorContextCompiler,
            TokenCounter as OrchestratorTokenCounter,
            ContextRanker as OrchestratorContextRanker,
            ContextDeduplicator as OrchestratorContextDeduplicator,
            OptimizedContextPackage as OrchestratorOptimizedContextPackage,
        )

        assert OrchestratorContextCompiler is ContextCompiler
        assert OrchestratorTokenCounter is TokenCounter
        assert OrchestratorContextRanker is ContextRanker
        assert OrchestratorContextDeduplicator is ContextDeduplicator
        assert OrchestratorOptimizedContextPackage is OptimizedContextPackage
