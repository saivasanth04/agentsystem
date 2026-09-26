# Duplicate Context System Audit Report

## 1. Executive Summary
The codebase contains two parallel context assembly systems:
1. The modern `context/` pipeline (`compiler.py`, `ranking.py`, `budget.py`, `dedup.py`, `pack.py`), which constructs budget-constrained `OptimizedContextPackage` instances using `RepositoryBrain` symbol extraction, working memory, error evidence, and active diffs.
2. The legacy `agent_orchestrator/context/` package (`budget_allocator.py`, `compressor.py`, `deduplicator.py`, `isolation.py`, `relevance_ranker.py`, `retrieval_hierarchy.py`, `sufficiency_oracle.py`, `token_estimator.py`), which performs manual string chunking, heuristic priority sectioning, and ad-hoc prompt concatenation.

In `agent_orchestrator/agents/coder.py` lines 54–220, `CoderAgent` was completely bypassing `ContextCompiler` to manually instantiate `ContextBudget`, `ContextAssembler`, and `RelevanceRanker`, concatenating 14 separate sections into a bloated raw prompt.

---

## 2. Identified Context Duplications

| Capability | Legacy Module (`agent_orchestrator/context/`) | Authoritative Module (`context/`) |
| :--- | :--- | :--- |
| **Context Compilation** | `budget_allocator.py` (`ContextAssembler`) | `compiler.py` (`ContextCompiler`) |
| **Relevance & Ranking** | `relevance_ranker.py` (`RelevanceRanker`) | `ranking.py` (`RelevanceRanker`, `ContextTier`) |
| **Token Budgeting** | `budget_allocator.py` (`ContextBudget`) | `budget.py` (`ContextBudget`, `ContextSection`) |
| **Deduplication** | `deduplicator.py` (`ContextDeduplicator`) | `dedup.py` (`ContextDeduplicator`) |
| **Content Packing** | `compressor.py` (`JSONCompressor`, `DiffCompressor`) | `pack.py` (`ContextPacker`, `PackageFormat`) |

---

## 3. Detailed Call-Site Inventory

1. **`agent_orchestrator/agents/coder.py`**:
   - Lines 54-58: Imports `ContextBudget`, `ContextSection`, `ContextAssembler`, `JSONCompressor`, `RelevanceRanker`, `ContextTier`.
   - Lines 68-73: Direct instantiation of legacy `ContextBudget` and `ContextAssembler`.
   - Lines 78-88: Direct call to `RelevanceRanker.rank_context_for_task(...)`.
   - Lines 160-220: Manually building 14 `ContextSection` objects.
2. **`agent_orchestrator/agents/dynamic_agent.py`**:
   - Ad-hoc prompt concatenation of task descriptions and string summaries.
3. **`runtime/agent_loop.py`**:
   - Lines 605-640 (`_phase_context_rebuild`): Successfully uses `ContextCompiler.compile(...)`, but only when `AgentExecutionLoop` is the active runtime.

---

## 4. Consolidation & Migration Decision

1. **Authoritative Package**: All context operations must flow through `context/compiler.py` (`ContextCompiler.compile(...)`).
2. **Legacy Delegation Shims**: Every module under `agent_orchestrator/context/` is replaced with a backward-compatibility wrapper that re-exports or forwards to the corresponding module in `context/`.
3. **Agent Refactoring**: Remove manual section assembly from `CoderAgent.execute` and other agents; replace with `ContextCompiler.compile(...)`.
4. **Mandatory Invariant**: Zero manual string concatenation of skills or file contents directly into system prompts.
