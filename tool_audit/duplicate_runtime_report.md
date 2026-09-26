# Duplicate Runtime Audit Report

## 1. Executive Summary
An exhaustive audit of the 218 Python modules, 3,771 import edges, and 21,842 function calls identified a critical architectural defect: **Dual Execution Runtimes**. While `AgentExecutionLoop` was created in `runtime/agent_loop.py` as the Claude-style 7-phase state machine runtime, all existing agent personas (`BaseAgent`, `CoderAgent`, `TesterAgent`, `ReviewerAgent`, `PlannerAgent`, `SpecificationAgent`, `ArchitectureAgent`, and `DynamicAgent`) were still instantiating and executing `ReActAgentLoop` from `agent_orchestrator/runtime/react_loop.py`.

This dual runtime architecture meant that production tasks scheduled by `ConcurrentDAGScheduler` or executed by `BaseAgent` bypassed:
- The 7-phase state machine (`Reason -> Select Tool -> Execute -> Observation -> State Update -> Context Rebuild -> Reason Again`)
- `ObservationEngine` structured normalization (allowing raw terminal/stdout dumps)
- Dynamic iterative context compilation through `ContextCompiler`
- Automatic dev server interception and registration with `ProjectRuntimeManager`

---

## 2. Identified Runtime Duplications

| Component | Location | Implementation Type | Current Callers |
| :--- | :--- | :--- | :--- |
| **`AgentExecutionLoop`** | `runtime/agent_loop.py` | Authoritative 7-Phase State Machine | `tests/test_agent_loop.py`, `tests/test_consolidation.py` |
| **`ReActAgentLoop`** | `agent_orchestrator/runtime/react_loop.py` | Legacy Multi-Turn Loop (1,982 lines) | `BaseAgent`, `CoderAgent`, `TesterAgent`, `ReviewerAgent`, `PlannerAgent`, `SpecificationAgent`, `ArchitectureAgent`, `DynamicAgent`, `ConcurrentDAGScheduler` |

---

## 3. Detailed Call-Site Inventory

1. **`agent_orchestrator/agents/base.py`**:
   - Line 11: `from ..runtime.react_loop import ReActAgentLoop`
   - Line 42: `self.react_loop = ReActAgentLoop(self.llm, self.tool_registry, approval_gate=self.approval_gate)`
   - Line 156: `res = self.react_loop.run(...)`
2. **`agent_orchestrator/agents/coder.py`**:
   - Line 320: `loop_result = self.react_loop.run(...)`
3. **`agent_orchestrator/agents/tester.py`**:
   - Line 146: `loop_result = self.react_loop.run(...)`
4. **`agent_orchestrator/agents/reviewer.py`**:
   - Line 292: `loop_result = self.react_loop.run(...)`
5. **`agent_orchestrator/agents/planner.py`**:
   - Line 146: `loop_result = self.react_loop.run(...)`
6. **`agent_orchestrator/agents/specification.py`**:
   - Line 99: `loop_result = self.react_loop.run(...)`
7. **`agent_orchestrator/agents/architecture.py`**:
   - Line 114: `loop_result = self.react_loop.run(...)`
8. **`agent_orchestrator/agents/dynamic_agent.py`**:
   - Line 188: `loop_result = self.react_loop.run(...)`
9. **`agent_orchestrator/runtime/dag_scheduler.py`**:
   - Line 303: Calls `agent.execute(...)`, which directly invokes `self.react_loop.run(...)`.

---

## 4. Consolidation & Migration Decision

1. **Authoritative Runtime**: `AgentExecutionLoop` in `runtime/agent_loop.py` is designated as the sole production runtime.
2. **BaseAgent Migration**: `BaseAgent` must instantiate `AgentExecutionLoop`. `BaseAgent.execute()` and all subclass workflows will execute via `AgentExecutionLoop`.
3. **Compatibility Adapter**: `ReActAgentLoop` is refactored into a compatibility adapter that forwards its `.run()` and `.execute()` calls directly to `AgentExecutionLoop`.
4. **Contract Verification**: No production agent module may instantiate `ReActAgentLoop`. A search for `ReActAgentLoop(` must return matches only in compatibility shims and deprecated adapter tests.
