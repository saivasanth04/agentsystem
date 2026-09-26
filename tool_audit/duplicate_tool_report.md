# Duplicate Tool Exposure & Lifecycle Bypass Audit Report

## 1. Executive Summary
The repository contained multiple disparate paths for exposing tools to agents and language models:
1. **Authoritative Lifecycle Pipeline** (`runtime/tool_state_machine.py` + `runtime/capability_router.py`):
   Enforces the 5-stage lifecycle state machine:
   $$\text{DECLARED} \longrightarrow \text{DISCOVERED} \longrightarrow \text{HEALTHY} \longrightarrow \text{AUTHORIZED} \longrightarrow \text{EXECUTABLE}$$
   Strict invariant: Only tools in `EXECUTABLE` state are returned in OpenAI model tool schemas.
2. **Legacy Tool Exposure Bypasses**:
   - `agent_orchestrator/tools/builtin_tools.py`: `BuiltinToolRegistry.get_all_tools()` exposed tools regardless of health or authorization.
   - `agent_orchestrator/tools/unified_dispatcher.py`: Directly enumerated all registered tools and MCP tools without lifecycle verification.
   - `agent_orchestrator/agents/dynamic_agent.py`: Queried raw registries directly, potentially exposing unverified or missing tools to the model.
   - Legacy browser adapters: Previously simulated browser success; now return `state="MISSING"`, but required centralized enforcement so no code could bypass the check.

---

## 2. Identified Tool Exposure Paths

| Component | Method | Lifecycle Enforced? | Status |
| :--- | :--- | :--- | :--- |
| **`CapabilityRouter`** | `get_tool_schemas_for_task` | YES (`EXECUTABLE` only) | Authoritative |
| **`ToolStateMachine`** | `get_executable_schemas` | YES (`EXECUTABLE` only) | Authoritative |
| **`DynamicAgent`** | `_build_tools_list` | NO (raw inventory) | Bypass to Eliminate |
| **`UnifiedToolDispatcher`**| `get_all_tools` / `list_tools` | NO (raw inventory) | Bypass to Eliminate |
| **`BuiltinToolRegistry`** | `get_definitions` | NO (raw builtins) | Bypass to Eliminate |

---

## 3. Detailed Call-Site Inventory

1. **`agent_orchestrator/agents/dynamic_agent.py`**:
   - Exposes tools to the LLM directly from `tool_registry` without querying the tool state machine or checking `ToolLifecycleState.EXECUTABLE`.
2. **`agent_orchestrator/tools/unified_dispatcher.py`**:
   - `list_tools()` directly concatenates builtin tools and MCP tools without checking if an MCP server is healthy or running.
3. **`agent_orchestrator/tools/builtin_tools.py`**:
   - Exposes raw function metadata without policy or permission filtering.

---

## 4. Consolidation & Migration Decision

1. **Authoritative Engine**: Implement `ToolPolicyEngine` (in `runtime/tool_policy.py` / `runtime/tool_state_machine.py`) unifying tool policy evaluation and executable state enforcement.
2. **Eliminate All Bypasses**:
   - `DynamicAgent`, `UnifiedToolDispatcher`, and `BuiltinToolRegistry` must delegate any model-facing tool exposure directly to `ToolPolicyEngine.get_executable_tools(...)` or `CapabilityRouter.get_tool_schemas_for_task(...)`.
3. **Strict Invariant**: No tool reaches the model unless it has successfully completed `DECLARED -> DISCOVERED -> HEALTHY -> AUTHORIZED -> EXECUTABLE`.
4. **Browser MCP Protection**: Browser tools that do not connect to a live Chrome DevTools or Puppeteer MCP server are flagged as `MISSING` and never sent in model tool schemas.
