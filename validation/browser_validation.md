# Browser MCP & Integration Validation (ADR 008 Compliance)

## 1. Architectural Context & Problem Statement

In AGENTSYSTEM v1, browser interactions were prone to catastrophic silent failures:
- When Chrome DevTools or MCP browser servers were disconnected, adapters would fall back to returning hardcoded synthetic HTML (e.g. `<html><body>Fake DOM</body></html>`) or simulated click confirmations.
- These synthetic fallbacks deceived agents into believing actions succeeded, leading to false positive test verifications and silent failures in production CI/CD.

**AGENTSYSTEM v2 enforces Architecture Decision Record 008 (ADR 008 - Zero Fakes & Real Integration)**:
> *"No mock implementations, no synthetic success fallbacks, and no placeholder responses are permitted in any production adapter. When an external service or daemon is unreachable, the system must fail explicitly, transition tool state to MISSING, and inform the orchestrator."*

---

## 2. BrowserMCPAdapter Verification

[`BrowserMCPAdapter`](file:///c:/Users/perur/Desktop/Vasanth/agent_orchestrator/tools/browser_mcp.py) integrates Chrome DevTools MCP and Playwright/Puppeteer automation endpoints.

### Real Endpoint Detection & Liveness Probe
```python
def probe_liveness(self) -> bool:
    """
    Actively probes Chrome DevTools MCP endpoint via HTTP/WebSocket.
    Returns False immediately if connection refused; NO fake fallbacks.
    """
    try:
        response = requests.get(f"{self.endpoint}/health", timeout=1.0)
        return response.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False
```

### Authoritative Failure Handling When Disconnected
When Chrome DevTools is not running:
1. `probe_liveness()` evaluates to `False`.
2. `ToolStateMachine` moves browser tools to `ToolLifecycleState.MISSING`.
3. If invoked directly, `BrowserMCPAdapter` strictly returns real error responses:
   ```json
   {
     "success": false,
     "error": "BrowserMCPConnectionError: Unable to connect to Chrome DevTools MCP at http://127.0.0.1:9222. Service is offline.",
     "status": "MISSING",
     "metadata": {
       "endpoint": "http://127.0.0.1:9222",
       "retries": 0,
       "synthetic_fallback": false
     }
   }
   ```
4. **Zero Synthetic DOM**: The response contains `None` for DOM content, never a synthetic placeholder string.

---

## 3. Tool Policy & Schema Pruning

Because `BrowserMCPAdapter` reports `MISSING` when offline:
- [`ToolPolicyEngine`](file:///c:/Users/perur/Desktop/Vasanth/agent_orchestrator/tools/policy_engine.py) queries the lifecycle state before serializing tools to the LLM.
- All `browser.*` tools are omitted from the schema:
  ```json
  "verified_executable_tools": [
    "ast_syntax_check", "list_directory", "write_file", "replace_file_content",
    "insert_lines", "delete_file", "run_tests", "terminal_execute",
    "apply_diff_blocks", "move_file", "rename_file", "delete_lines",
    "apply_patch", "check_environment", "get_file_info", "read_file"
  ]
  ```
- **Result**: The LLM never receives `browser_click` or `browser_navigate` in its available function definitions, preventing hallucinated calls to nonexistent browser sessions.

---

## 4. Test Verification

Compliance is verified by the automated test suite:
- `tests/test_capability_runtime.py::test_disconnected_browser_tool_lifecycle`: Asserts that an unconfigured browser MCP transitions to `MISSING`, returns `success=False`, and is excluded from executable tool schemas.
- `tests/test_consolidation.py::test_zero_fakes_invariant`: Verifies that no synthetic HTML is returned by any tool adapter in the codebase.
