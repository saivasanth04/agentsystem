# Architecture Decision Records: AGENTSYSTEM v2 Authoritative Migration

## ADR 001: Sole Authoritative ReAct Runtime Engine
- **Status**: Approved & Authoritative.
- **Decision**: `AgentExecutionLoop` (`runtime/agent_loop.py`) is the sole execution engine for all autonomous agent tasks in AGENTSYSTEM.
- **Rationale**: Having two execution runtimes (`ReActAgentLoop` and `AgentExecutionLoop`) caused fragmentation, bypassed the 7-phase state machine, prevented uniform observation normalization, and caused dev server tracking failures.
- **Migration**: `BaseAgent` instantiates `AgentExecutionLoop`. `ReActAgentLoop` is refactored into a compatibility wrapper delegating all calls to `AgentExecutionLoop`.

## ADR 002: Single LiteLLM Interface Standard
- **Status**: Approved & Authoritative.
- **Decision**: All model interactions must invoke `chat()`, `chat_with_tools()`, or `chat_json()` via the LiteLLM Gateway client. Ad-hoc wrappers (`chat_completion`, `generate_completion`, `raw_openai`) are strictly forbidden.
- **Rationale**: Ensures uniform token tracking, provider fallbacks, telemetry logging, and native OpenAI tool calling schemas across all agents.

## ADR 003: Mandatory Context Compilation
- **Status**: Approved & Authoritative.
- **Decision**: Every prompt assembly for model execution must call `ContextCompiler.compile(...)`. Manual section concatenation in agent classes is eliminated.
- **Rationale**: Guarantees strict adherence to token budgets, eliminates prompt pollution, and grounds context in `RepositoryBrain` symbols, ASTs, working memory, and verified diffs.
- **Migration**: Legacy modules in `agent_orchestrator/context/` are converted into thin delegation shims to `context/`.

## ADR 004: Skills as Executable Runtime Policies
- **Status**: Approved & Authoritative.
- **Decision**: Skills are resolved through `SkillResolver` into `SkillRuntime` to produce `ToolPolicy`, `ContextPolicy`, `RuntimePolicy`, and `ExecutionProcedure`. Passive markdown (`SKILL.md`) injection into system prompts is eliminated.
- **Rationale**: Markdown prose consumes massive context tokens without enforcing operational invariants. Runtime policies strictly control tool access and execution constraints.

## ADR 005: Automatic ProjectRuntime Registration
- **Status**: Approved & Authoritative.
- **Decision**: Execution of long-running development server commands (`npm run dev`, `vite`, `uvicorn`, `flask`, etc.) is automatically intercepted by the runtime, registered with `ProjectRuntimeManager`, allocated an isolated port, and monitored for health and preview URLs without human intervention.
- **Rationale**: Prevents terminal hanging in blocking subprocess calls and provides real-time frontend dev server lifecycle control.

## ADR 006: Authoritative Tool Lifecycle State Machine
- **Status**: Approved & Authoritative.
- **Decision**: Tools strictly progress through:
  $$\text{DECLARED} \longrightarrow \text{DISCOVERED} \longrightarrow \text{HEALTHY} \longrightarrow \text{AUTHORIZED} \longrightarrow \text{EXECUTABLE}$$
  Only tools that have achieved `EXECUTABLE` state are exposed in OpenAI tool schemas to the model.
- **Rationale**: Prevents hallucinated tool calls, broken MCP server execution, and unauthorized file/terminal mutations.

## ADR 007: Zero-Fake Browser MCP Integration
- **Status**: Approved & Authoritative.
- **Decision**: If a real Chrome DevTools or Puppeteer MCP server is unavailable, browser tools report `state="MISSING"` and `success=False`. Returning fabricated success without executing a real browser is strictly illegal.
- **Rationale**: Assures deterministic visual and DOM verification in production workflows.

## ADR 008: Elimination of Tool Exposure Bypasses
- **Status**: Approved & Authoritative.
- **Decision**: All tool exposure paths (`DynamicAgent`, `BuiltinToolRegistry`, `UnifiedToolDispatcher`) must delegate to `ToolPolicyEngine.get_executable_tools(...)`.
- **Rationale**: Guarantees no legacy subsystem can expose unverified or unauthorized tools to an agent.

## ADR 009: Compatibility Matrix Enforcement
- **Status**: Approved & Authoritative.
- **Decision**: `CompatibilityMatrixEnforcer` runs pre-flight validation on every required skill tool: `Skill -> Required Tool -> Exists? -> Healthy? -> Authorized? -> Executable?`. Failures halt execution immediately with structured diagnostics.
- **Rationale**: Eliminates silent tool substitution and runtime crashes midway through task execution.

## ADR 010: Consolidated Context Engine
- **Status**: Approved & Authoritative.
- **Decision**: `context/` (`compiler.py`, `ranking.py`, `budget.py`, `dedup.py`, `pack.py`) replaces `agent_orchestrator/context/`.
- **Rationale**: Removes 8 duplicate context files and unifies token budgeting and deduplication logic.

## ADR 011: ProductionIDE as a Façade
- **Status**: Approved & Authoritative.
- **Decision**: `ProductionIDE` is a high-level façade coordinating `TaskOrchestrator`, `AgentExecutionLoop`, `IDEVerificationPipeline`, and `IDERepairPipeline`. It does not maintain its own execution engine.
- **Rationale**: Enforces a single execution runtime across both headless orchestrator and interactive IDE sessions.

## ADR 012: Dynamic Telemetry-Derived Dry Runs
- **Status**: Approved & Authoritative.
- **Decision**: Task DAG dry runs derive costs and token estimates dynamically from `RepositoryBrain` analysis and `SkillResolver` policies, or return `estimate_available=False`. Hardcoded estimates are removed.
- **Rationale**: Faking precision damages planning reliability.

## ADR 013: Framework-Aware Verification
- **Status**: Approved & Authoritative.
- **Decision**: `IDEVerificationPipeline` dynamically selects linters, builders, test runners, and security scanners according to the workspace's detected `RuntimeProfile` (Python, React/Node.js, Java).
- **Rationale**: Real-world projects are polyglot and require ecosystem-appropriate verification.

## ADR 014: Evidence-Based 5-Stage Repair Contract
- **Status**: Approved & Authoritative.
- **Decision**: Autonomous repairs progress through:
  $$\text{DIAGNOSED} \longrightarrow \text{REPAIR\_PLANNED} \longrightarrow \text{PATCH\_GENERATED} \longrightarrow \text{PATCH\_APPLIED} \longrightarrow \text{VERIFICATION\_PASSED}$$
  Returning `success=True` is prohibited unless the verification stage passes.
- **Rationale**: Guarantees code fixes are empirically proven to resolve errors before reporting success.

## ADR 015: Complete Elimination of Dead Architecture
- **Status**: Approved & Authoritative.
- **Decision**: All duplicated runtimes, context builders, and tool exposure channels are migrated, consolidated, or safely shimmed for backward compatibility.
- **Rationale**: Eliminates technical debt and ensures 100% of production traffic travels through the authoritative execution spine.
