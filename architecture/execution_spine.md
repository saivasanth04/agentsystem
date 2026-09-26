# Authoritative Execution Spine

## 1. End-to-End Pipeline

The end-to-end execution flow from incoming user request down to verified code delivery operates along a single, unbroken execution spine:

```
User Request
     ↓
TaskOrchestrator
     ↓
SkillResolver (Dynamic Capability Selection)
     ↓
SkillRuntime (Executable Policies & Requirements)
     ↓
ContextCompiler (7-Stream Token Budgeted Package)
     ↓
AgentExecutionLoop (Claude-Style 7-Phase ReAct Engine)
     ↓
ToolPolicy (Strict Tool Lifecycle: EXECUTABLE only)
     ↓
LiteLLM Gateway (chat / chat_with_tools)
     ↓
UnifiedToolDispatcher / ProjectRuntimeManager (Dev Server Port Allocation)
     ↓
ObservationEngine (Strict Structured Normalization)
     ↓
ExecutionState
     ↓
IDEVerificationPipeline (Framework-Aware: Python, Node/React, Java)
     ↓
   [Pass] ──> Done / Deliverable
   [Fail] ──> IDERepairPipeline (Diagnosed -> Planned -> Patch Generated -> Applied -> Verified)
```

## 2. Component Integration Matrix

| Core System | Integration Point in Execution Spine |
| :--- | :--- |
| **SkillRegistry** | Ingested by `SkillResolver` and `ContextCompiler` to dynamically activate domain skills. |
| **AgentRegistry** | Configures agent role definitions, permissions, and tool access per task. |
| **MCPManager** | Discovers and validates external tool servers (filesystem, Chrome DevTools, git). |
| **UnifiedToolDispatcher** | Routes tool calls to built-in tools or active MCP server processes. |
| **WorkspaceManager** | Governs transactional workspace isolation, delta modifications, and checkpoints. |
| **LangGraph** | Provides graph-based orchestration nodes across TaskDAG execution stages. |
| **LiteLLM Gateway** | Standardized model inference client supporting native tool-calling schemas. |
| **RepositoryBrain** | SQLite-backed AST symbol index, call graph, and component topology. |
| **ProjectRuntimeManager** | Intercepts long-running servers (`npm run dev`, `vite`, `uvicorn`) with health checking. |
| **ObservationEngine** | Transforms stdout, tracebacks, and diagnostics into structured `Observation` objects. |
| **IDEVerificationPipeline** | 6-stage lifecycle (`edit` -> `build` -> `lint` -> `tests` -> `security` -> `review`). |
| **IDERepairPipeline** | 5-stage closed loop (`DIAGNOSED` -> `REPAIR_PLANNED` -> `PATCH_GENERATED` -> `PATCH_APPLIED` -> `VERIFICATION_PASSED`). |
