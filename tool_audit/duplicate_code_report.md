# Duplicate Code and Redundant Implementations Audit Report

## 1. Executive Summary

This report identifies architectural duplication, mock implementations, dummy wrappers, and divergent execution paths across AGENTSYSTEM v2, documenting their resolution under the Non-Negotiable Consolidation Contract.

---

## 2. Catalog of Duplications and Resolutions

### 2.1 ReAct Loop Divergence
* **Location A**: `runtime/agent_loop.py` (`AgentExecutionLoop` - 7-phase state machine: Reason -> Select Tool -> Execute -> Observation -> State Update -> Context Rebuild -> Reason Again).
* **Location B**: `agent_orchestrator/runtime/react_loop.py` (`ReActAgentLoop` - legacy multi-turn loop).
* **Status**: **CONSOLIDATED**.
* **Resolution**: Unified execution onto `AgentExecutionLoop` as the authoritative runtime engine. `ReActAgentLoop` acts as a backward-compatible adapter. All agent personas (`BaseAgent`, `CoderAgent`, etc.) now execute through the standardized runtime with LiteLLM gateway integration.

### 2.2 LLM Invocation Inconsistencies
* **Location**: Multiple divergent method calls across components (`llm.chat`, `llm.chat_completion`, `llm.chat_with_tools`, `llm.generate`).
* **Status**: **STANDARDIZED**.
* **Resolution**: Standardized `AgentExecutionLoop` to call `chat_with_tools` whenever OpenAI tool schemas are present, and `chat` for general completions, with deterministic fallbacks when operating in headless or verification mode.

### 2.3 Browser MCP Mocking & Fakes
* **Location**: `runtime/capability_router.py` (`BrowserMCPAdapter`).
* **Status**: **RESOLVED (ZERO FAKES)**.
* **Resolution**: Removed fabricated `{"status": "ready", "success": True}` stub responses. Replaced with real MCP connectivity checks to `chrome-devtools` or `puppeteer`. When absent, marks state as `MISSING` and returns explicit diagnostic error.

### 2.4 Tool State Exposure & Policy Divergence
* **Location**: Tools previously exposed to LLM without verifying health or authorization.
* **Status**: **CONSOLIDATED**.
* **Resolution**: Created `runtime/tool_state_machine.py` with strict lifecycle:
  $$\text{DECLARED} \longrightarrow \text{DISCOVERED} \longrightarrow \text{HEALTHY} \longrightarrow \text{AUTHORIZED} \longrightarrow \text{EXECUTABLE}$$
  Only tools in `EXECUTABLE` state are exposed in model tool schemas.

### 2.5 Long-Running Dev Server Handling
* **Location**: Terminal commands like `npm run dev`, `vite`, `uvicorn` previously caused synchronous blocking/timeout in execution loops.
* **Status**: **CONSOLIDATED**.
* **Resolution**: Intercepted in `AgentExecutionLoop._phase_execute`, automatically handing off process lifecycle to `ProjectRuntimeManager` for port allocation, non-conflicting preview URLs, and background streaming.

### 2.6 Framework-Aware Verification & Pipeline Duplication
* **Location**: `ide/verification_pipeline.py` previously hardcoded Python checks (`mypy`, `ruff`, `pytest`, `bandit`) regardless of repository language.
* **Status**: **RESOLVED**.
* **Resolution**: Integrated `RuntimeDetector` to auto-detect Python, React/Node, and Java ecosystems, executing framework-native builders (`tsc`, `npm run build`, `mvn`), linters (`eslint`, `checkstyle`), test runners (`vitest`, `jest`, `JUnit`), and security scanners (`npm audit`, `dependency-check`).

### 2.7 Evidence-Based Repair Contract
* **Location**: `ide/repair_pipeline.py` previously returned `success=True` prematurely without verification.
* **Status**: **RESOLVED**.
* **Resolution**: Enforced 5-stage lifecycle `DIAGNOSED -> REPAIR_PLANNED -> PATCH_GENERATED -> PATCH_APPLIED -> VERIFICATION_PASSED`. Contract guarantees `success=True` is returned ONLY when state reaches `VERIFICATION_PASSED`.

### 2.8 Static Dry-Run Task Decomposition
* **Location**: `agent_orchestrator/server.py` (`/api/tasks/dry-run`) hardcoded dummy tasks `T-01` to `T-04`.
* **Status**: **RESOLVED**.
* **Resolution**: Dynamically derives preview TaskDAG from `RepositoryBrain` (symbols, components, import graph) and `SkillResolver` (runtime policy).

### 2.9 Context Management Duplication
* **Location A**: `agent_orchestrator/context/`
* **Location B**: `context/`
* **Status**: **CONSOLIDATED**.
* **Resolution**: Canonicalized the 7-stream budget-enforced context pipeline in `context/` (`ContextCompiler`, `ContextBudgetManager`, `ContextRanker`, `ContextDeduplicator`, `OptimizedContextPackage`), re-exported via `agent_orchestrator/context/` for backward compatibility.
