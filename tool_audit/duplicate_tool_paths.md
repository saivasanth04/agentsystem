# Duplicate Tool Paths Audit & Consolidation

## 1. Findings

Tools were previously discoverable, manageable, and exposed across four uncoordinated mechanisms:
1. `agent_orchestrator/tools/builtin_tools.py` (`BuiltinToolRegistry`): Static dictionary of callable functions.
2. `agent_orchestrator/mcp/manager.py` (`MCPManager`): External MCP server connections.
3. `agent_orchestrator/agents/base.py`: Direct tool injection into agents via `self.tool_registry`.
4. `agent_orchestrator/tools/unified_dispatcher.py` (`UnifiedToolDispatcher`): Direct execution without mandatory lifecycle checks.

## 2. Consolidation Action

- **Single Authority**: `ToolPolicyEngine` is the exclusive authority for determining tool availability and schemas.
- **Strict Lifecycle**:
  ```text
  DECLARED ──> DISCOVERED ──> HEALTH CHECK ──> HEALTHY ──> AUTHORIZED ──> EXECUTABLE
  ```
  - Direct promotion from `DISCOVERED` to `HEALTHY` without executing a real probe is strictly prohibited.
  - Builtin tools must be checked for callability, implementation existence, workspace validity, and probe execution.
  - MCP tools must be checked for active connection, server liveness, tool existence, and invocation responsiveness.
- **Schema Pruning**: Only tools that achieve `ToolLifecycleState.EXECUTABLE` are converted to OpenAI/LiteLLM tool definitions.
- **Agent Access**: Agents never query `BuiltinToolRegistry` or `MCPManager` directly. All tool exposure flows through:
  ```text
  CapabilityRouter ──> ToolPolicyEngine ──> ToolStateMachine ──> Executable Tools
  ```
