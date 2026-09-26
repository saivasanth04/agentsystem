# Architectural Cleanup: Duplicate Execution Paths

This document formalizes the architectural consolidation plan for eliminating redundant execution paths in `saivasanth04/agentsystem`.

## Master Consolidation Actions

| Duplicate Path | Authoritative Component | Action | Detailed Plan |
| :--- | :--- | :--- | :--- |
| **ReAct runtime** (`agent_orchestrator/runtime/react_loop.py`) | `runtime/agent_loop.py` (`AgentExecutionLoop`) | **Compatibility shim** | `ReActAgentLoop` forwards all calls (`run()`, `step()`) directly to `AgentExecutionLoop`. All internal logic delegates to the single execution spine. |
| **Legacy context** (`agent_orchestrator/context/budget_allocator.py`, `relevance_ranker.py`) | `context/compiler.py` (`ContextCompiler`) | **Delegate** | Legacy `ContextAssembler` and persona prompt builders delegate context compilation to `ContextCompiler.compile()`. |
| **Legacy skill execution** (`agent_orchestrator/runtime/dag_scheduler.py` calling `SkillRegistry.discover()`) | `skills/runtime.py` (`SkillRuntime.execute()`) | **Remove** | Remove direct calls to `SkillRegistry.discover()` and passing `active_skills` to agents. Every task enters `SkillRuntime.execute()`. |
| **Legacy tool exposure** (`BaseAgent.tool_registry`, `MCPManager.get_tools_for_agent()`) | `runtime/tool_policy.py` (`ToolPolicyEngine`) | **Delegate** | Agents obtain tools exclusively via `ToolPolicyEngine.get_executable_schemas()`. `BuiltinToolRegistry` and `MCPManager` are accessed only through `ToolPolicyEngine`. |
| **Legacy verification** (scheduler inline verification & dependency healing) | `ide/verification_pipeline.py` (`IDEVerificationPipeline`) | **Remove** | Scheduler delegates verification entirely to `IDEVerificationPipeline.run_lifecycle()`. Inline verification code in scheduler is removed/delegated. |

---

## Behavioral Invariants

- **Public APIs Preserved**: No public classes or functions are deleted; all legacy interfaces become thin delegating wrappers.
- **Single Authority**: Only `ContextCompiler` outputs the final prompt package. Only `ToolPolicyEngine` exposes tools. Only `SkillRuntime` executes skills. Only `VerificationPipeline` validates code.
