# Project Runtime Manager & Detection Validation

## 1. Architectural Role

The **AGENTSYSTEM v2** runtime must manage external development servers (e.g. Vite dev servers, Next.js runtimes, FastAPI/Flask backends) reliably within developer workspaces.

In AGENTSYSTEM v1:
- Processes were spawned with hardcoded ports (e.g., `3000` or `5173`), resulting in frequent `EADDRINUSE` port collision errors.
- Disconnected processes became zombie background tasks that locked file descriptors and consumed memory.
- Runtime detection was rudimentary and brittle.

In **AGENTSYSTEM v2**:
- [`RuntimeDetector`](file:///c:/Users/perur/Desktop/Vasanth/repository/runtime_detector.py) inspects the filesystem to identify project types, configuration files, and package manifests.
- [`ProjectRuntimeManager`](file:///c:/Users/perur/Desktop/Vasanth/agent_orchestrator/runtime/project_runtime.py) handles dynamic port allocation, health monitoring, and graceful process lifecycle termination.

---

## 2. Runtime Detection Matrix

The `RuntimeDetector` statically inspects workspace files using heuristics and manifest parsing:

| Framework / Language | Manifest / Config Triggers | Detected Builder / Command | Default Dev Command |
| :--- | :--- | :--- | :--- |
| **React + Vite** | `package.json` (`vite`, `react`), `vite.config.ts` | `vite` | `npx vite --port {port}` |
| **Next.js** | `package.json` (`next`), `next.config.js` | `next` | `npx next dev -p {port}` |
| **Node.js Express** | `package.json` (`express`), `server.js` | `node` | `node server.js` |
| **Python FastAPI** | `requirements.txt` / `pyproject.toml` (`fastapi`) | `uvicorn` | `uvicorn main:app --port {port}` |
| **Python Flask** | `requirements.txt` / `pyproject.toml` (`flask`) | `flask` | `flask run --port {port}` |
| **Pure Python** | `requirements.txt`, `pyproject.toml`, `setup.py` | `python` | `python -m pytest` |

---

## 3. Dynamic Port Allocation & Collision Prevention

`ProjectRuntimeManager` prevents `EADDRINUSE` conflicts via ephemeral socket probing:

```python
def find_available_port(self, preferred_port: int = 5173, max_attempts: int = 50) -> int:
    """
    Finds an open ephemeral port starting from preferred_port.
    Binds to (host, port) with SO_REUSEADDR to verify availability.
    """
    for port in range(preferred_port, preferred_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"Unable to find available port in range {preferred_port}-{preferred_port + max_attempts}")
```

- When a process starts, the resolved open port is injected into the CLI flags (`--port {port}`).
- Active processes are indexed in an internal registry: `{process_id: {"port": port, "pid": proc.pid, "status": "RUNNING"}}`.

---

## 4. Process Lifecycle, Health Checks & Zombie Cleanup

1. **Health Checking**:
   - `ProjectRuntimeManager.wait_until_ready(process_id, timeout=10.0)` polls the allocated port with exponential backoff until HTTP `200/404` or TCP connection acceptance is received.
2. **Graceful Teardown & Zombie Cleanup**:
   - On shutdown or task completion, `stop_process(process_id)` sends `SIGTERM` / `TerminateProcess`.
   - If the process does not terminate within a 2-second grace period, `SIGKILL` is issued.
   - All child processes spawned by shell wrappers (e.g. `npm run dev`) are tracked via process group trees to ensure child node processes do not outlive the test or session.
3. **Integration with IDE Facade**:
   - [`ProductionIDE`](file:///c:/Users/perur/Desktop/Vasanth/ide/production_ide.py) exposes `runtime_manager` directly, ensuring IDE servers run on non-conflicting ports with full telemetry.

---

## 5. Verification Test Proof

- `tests/test_production_ide.py::test_project_runtime_lifecycle`: Confirms dynamic port assignment, process spawn, health polling, and clean teardown.
- `tests/test_repository_intelligence.py::test_runtime_detection`: Confirms accurate classification of React, Vite, Node, and Python workspaces without false positives.
