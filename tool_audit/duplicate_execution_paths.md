# Duplicate Execution Paths Audit & Consolidation Matrix

## 1. Executive Summary

This audit identifies all redundant, non-authoritative, or parallel execution pathways within the `saivasanth04/agentsystem` repository. Every identified duplicate is categorized and assigned a consolidation action (Compatibility Shim, Delegation, or Removal) to ensure that the entire codebase operates strictly through **one authoritative execution spine**:

$$\text{TaskOrchestrator} \longrightarrow \text{ConcurrentDAGScheduler} \longrightarrow \text{SkillResolver} \longrightarrow \text{SkillRuntime} \longrightarrow \text{ContextCompiler} \longrightarrow \text{AgentExecutionLoop} \longrightarrow \text{UnifiedToolDispatcher}$$

---

## 2. Duplicate Execution Paths Matrix

| Component Area | Legacy / Duplicate Path | Authoritative Path | Consolidation Action | Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **Agent Runtime** | `agent_orchestrator/runtime/react_loop.py` (`ReActAgentLoop`) | `runtime/agent_loop.py` (`AgentExecutionLoop`) | **Compatibility Shim** | Convert all public methods into thin wrappers delegating to `AgentExecutionLoop`. Preserve API compatibility for external tests. |
| **Skill Execution** | `agent_orchestrator/runtime/dag_scheduler.py` calling `SkillRegistry.discover()` & passing `active_skills` | `skills/resolver.py` (`SkillResolver`) + `skills/runtime.py` (`SkillRuntime.execute()`) | **Replace with SkillRuntime** | The scheduler must never discover raw skills or pass raw markdown. Skill execution belongs exclusively to `SkillRuntime`. |
| **Context Assembly** | Persona modules (`CoderAgent`, `TesterAgent`, `ContextAssembler`, `RelevanceRanker`) assembling context strings | `context/compiler.py` (`ContextCompiler.compile()`) | **Delegate to ContextCompiler** | Personas contribute only objectives and constraints; `ContextCompiler` is the single authority for prompt construction. |
| **Tool Scoping & Schema** | `BaseAgent.tool_registry`, `BuiltinToolRegistry`, `MCPManager.get_tools_for_agent()` | `runtime/tool_policy.py` (`ToolPolicyEngine`) + `runtime/tool_state_machine.py` (`ToolStateMachine`) | **Delegate to ToolPolicyEngine** | Single point of authority for tool lifecycle and schema generation. Only `EXECUTABLE` tools reach the LLM. |
| **Task Verification** | `agent_orchestrator/runtime/dag_scheduler.py` running verification gate and dependency healing | `ide/verification_pipeline.py` (`IDEVerificationPipeline`) | **Remove from Scheduler** | Scheduler is strictly responsible for dependency ordering and task wave dispatching; verification belongs entirely to `VerificationPipeline`. |
| **IDE Orchestration** | `ide/production_ide.py` instantiating redundant execution loops or state graphs | `ide/production_ide.py` as Service Layer consuming `TaskOrchestrator` & `AgentExecutionLoop` | **Refactor to Service Layer** | ProductionIDE is an application service/facade, not an independent runtime engine. |
| **LLM Invocation** | `AgentExecutionLoop` calling provider-specific fallbacks like `chat_completion()` | `agent_orchestrator/llm.py` (`chat()`, `chat_with_tools()`, `chat_json()`) | **Remove Fallback** | Runtime loop uses standard protocol methods only. Gateway handles provider routing. |

---

## 3. Deprecation & Delegation Guarantees

1. **Zero Breaking Changes**: Public signatures of `ReActAgentLoop`, `BaseAgent.execute()`, and `ProductionIDE` remain intact.
2. **Deterministic State Transitions**: Tools must pass real health checks before entering `HEALTHY` or `EXECUTABLE` states.
3. **No Prompt Bleed**: Raw markdown from `SKILL.md` is replaced by minimal capability summaries and runtime policies.
