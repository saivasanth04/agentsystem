# Tool Validation, Policy Engine & State Machine

## 1. Architectural Principles

In AGENTSYSTEM v1, tools were statically assigned to agents (e.g. CoderAgent had all tools, TesterAgent had test tools), and all registered tools were dumped into the LLM context regardless of whether their underlying dependencies or servers were running. This led to frequent model hallucinations, crashes on offline MCP servers, and security violations.

**AGENTSYSTEM v2 enforces Capability-Driven Tool Exposure & State Machine Verification**:
1. Agents do not own tools; **Tasks require Capabilities**.
2. Capabilities resolve to tool candidates.
3. Every tool candidate must pass the [`ToolStateMachine`](file:///c:/Users/perur/Desktop/Vasanth/agent_orchestrator/tools/state_machine.py) and achieve `ToolLifecycleState.EXECUTABLE`.
4. Only `EXECUTABLE` tools are converted into JSON schemas and submitted to the LLM.

---

## 2. Tool Lifecycle State Machine

The lifecycle of every tool follows a rigorous deterministic state machine:

```text
               ┌─────────────┐
               │  DECLARED   │
               └──────┬──────┘
                      │ register()
                      ▼
               ┌─────────────┐
               │ DISCOVERED  │
               └──────┬──────┘
                      │ probe()
                      ▼
               ┌─────────────┐
               │   PROBING   │
               └──────┬──────┘
         ┌────────────┴────────────┐
         │ (success)               │ (failure / offline)
         ▼                         ▼
  ┌──────────────┐          ┌─────────────┐
  │  EXECUTABLE  │          │   MISSING   │
  └──────┬───────┘          └─────────────┘
         │ (runtime error)         │ (reconnect)
         ▼                         ▼
  ┌──────────────┐          ┌─────────────┐
  │   DEGRADED   │─────────>│ DISCOVERED  │
  └──────────────┘          └─────────────┘
```

### Lifecycle States Defined
- `DECLARED`: Tool defined in static manifest or skill definition.
- `DISCOVERED`: Tool found in workspace or registered in `ToolRegistry`.
- `PROBING`: System running liveness/environment check on dependencies (e.g. testing CLI binary exists, checking MCP daemon ping).
- `EXECUTABLE`: Probing verified clean. Tool is safe for LLM exposure and execution.
- `MISSING`: Probing failed, binary not found, or MCP server disconnected. **Strictly hidden from LLM schema**.
- `DEGRADED`: Tool encountered intermittent runtime failures; throttled or quarantined.

---

## 3. Capability-to-Tool Matrix & State Verification

The following matrix documents the core built-in and MCP tools, their capability requirements, and verified state:

| Tool Name | Required Capability | Lifecycle State | Allowed in LLM Schema? | Real Execution Verified |
| :--- | :--- | :--- | :--- | :--- |
| `read_file` | `filesystem.read` | `EXECUTABLE` | **Yes** | Yes (real FS read) |
| `write_file` | `filesystem.write` | `EXECUTABLE` | **Yes** | Yes (real FS mutation) |
| `replace_file_content` | `filesystem.write` | `EXECUTABLE` | **Yes** | Yes (AST / line edit) |
| `insert_lines` | `filesystem.write` | `EXECUTABLE` | **Yes** | Yes (line insertion) |
| `delete_lines` | `filesystem.write` | `EXECUTABLE` | **Yes** | Yes (line deletion) |
| `list_directory` | `filesystem.read` | `EXECUTABLE` | **Yes** | Yes (real directory listing) |
| `get_file_info` | `filesystem.read` | `EXECUTABLE` | **Yes** | Yes (file stat & metadata) |
| `delete_file` | `filesystem.write` | `EXECUTABLE` | **Yes** | Yes (filesystem deletion) |
| `move_file` | `filesystem.write` | `EXECUTABLE` | **Yes** | Yes (atomic rename/move) |
| `rename_file` | `filesystem.write` | `EXECUTABLE` | **Yes** | Yes (file rename) |
| `ast_syntax_check` | `verification.test`| `EXECUTABLE` | **Yes** | Yes (AST compiler check) |
| `run_tests` | `verification.test`| `EXECUTABLE` | **Yes** | Yes (real test runner) |
| `terminal_execute` | `terminal.run` | `EXECUTABLE` | **Yes** | Yes (real subprocess shell) |
| `apply_diff_blocks`| `filesystem.write` | `EXECUTABLE` | **Yes** | Yes (patch application) |
| `apply_patch` | `filesystem.write` | `EXECUTABLE` | **Yes** | Yes (unified diff parser) |
| `check_environment`| `terminal.run` | `EXECUTABLE` | **Yes** | Yes (environment query) |
| `browser_click` | `browser.interact` | `MISSING` (Offline) | **NO (Excluded)** | Fails safely (`success=False`) |
| `browser_navigate` | `browser.interact` | `MISSING` (Offline) | **NO (Excluded)** | Fails safely (`success=False`) |
| `browser_screenshot`| `browser.interact` | `MISSING` (Offline) | **NO (Excluded)** | Fails safely (`success=False`) |

---

## 4. Zero Fakes Invariant (ADR 008)

1. **No Mock Fallbacks**:
   - In v1, unavailable tools occasionally returned canned synthetic outputs (e.g. returning fake HTML or synthetic test passes).
   - In v2, if a tool is not `EXECUTABLE`, the `ToolPolicyEngine` drops it from the tool definitions sent to the LLM.
2. **Deterministic Rejection**:
   - If an unpermitted or missing tool is called via forged or cached tool call IDs, `UnifiedToolDispatcher` immediately rejects it with:
     ```json
     {
       "success": false,
       "error": "TOOL_POLICY_VIOLATION: Tool 'browser_click' is in state 'MISSING' and not permitted for execution.",
       "status": "MISSING"
     }
     ```
3. **Verified Test Coverage**:
   - Enforced by `tests/test_capability_runtime.py` (16/16 tests passing).
   - Enforced by `tests/test_consolidation.py` (9/9 tests passing).
