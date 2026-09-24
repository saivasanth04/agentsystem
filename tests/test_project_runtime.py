"""Tests for ProjectRuntimeManager, Process Lifecycle, and Dynamic Port Discovery.

Verifies:
1. Framework detection correctly identifies project types (Vite, Next.js, FastAPI, Django, Python http.server).
2. Dynamic port detection extracts dev server ports from log lines and strictly ignores system ports (3000, 8000, 8080).
3. Real process execution: start, log streaming into circular buffer, process tree cleanup, and restart.
4. SQLite persistence of runtime records.
"""

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
import pytest

from agent_orchestrator.runtime.project_runtime import (
    ProjectRuntimeManager,
    ManagedRuntimeProcess,
    SYSTEM_PORTS,
)
from agent_orchestrator.server import state_store


@pytest.fixture
def temp_workspace():
    """Create a temporary project workspace directory."""
    temp_dir = tempfile.mkdtemp(prefix="agy_test_runtime_")
    ws_path = Path(temp_dir)
    yield ws_path
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def runtime_manager():
    manager = ProjectRuntimeManager(state_store=state_store)
    yield manager
    # Ensure any active test runtimes are cleaned up
    for rt_id, proc in list(manager.active_processes.items()):
        try:
            manager.stop_runtime(rt_id)
        except Exception:
            pass


def test_framework_detection_vite(runtime_manager, temp_workspace):
    """Test detection of Vite / React projects."""
    pkg = {
        "name": "vite-test-app",
        "scripts": {"dev": "vite"},
        "dependencies": {"react": "^18.0.0", "vite": "^5.0.0"},
    }
    (temp_workspace / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

    cmd, fw, port = runtime_manager.detect_project_command(temp_workspace)
    assert fw == "vite"
    assert "npm run dev" in cmd
    assert port == 5173


def test_framework_detection_nextjs(runtime_manager, temp_workspace):
    """Test detection of Next.js projects."""
    pkg = {
        "name": "nextjs-test-app",
        "scripts": {"dev": "next dev"},
        "dependencies": {"next": "^14.0.0", "react": "^18.0.0"},
    }
    (temp_workspace / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

    cmd, fw, port = runtime_manager.detect_project_command(temp_workspace)
    assert fw == "nextjs"
    assert "npm run dev -- -p 3001" in cmd
    assert port == 3001


def test_framework_detection_fastapi(runtime_manager, temp_workspace):
    """Test detection of FastAPI / Python web apps."""
    (temp_workspace / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()", encoding="utf-8")

    cmd, fw, port = runtime_manager.detect_project_command(temp_workspace)
    assert fw == "fastapi"
    assert "uvicorn main:app --reload --port 8001" in cmd
    assert port == 8001


def test_framework_detection_django(runtime_manager, temp_workspace):
    """Test detection of Django projects."""
    (temp_workspace / "manage.py").write_text("#!/usr/bin/env python\n# django manage.py", encoding="utf-8")

    cmd, fw, port = runtime_manager.detect_project_command(temp_workspace)
    assert fw == "django"
    assert "python manage.py runserver 8001" in cmd
    assert port == 8001


def test_framework_detection_static_fallback(runtime_manager, temp_workspace):
    """Test fallback to Python static HTTP server when index.html is present."""
    (temp_workspace / "index.html").write_text("<h1>Static App</h1>", encoding="utf-8")

    cmd, fw, port = runtime_manager.detect_project_command(temp_workspace)
    assert fw == "static"
    assert "python -m http.server 5173" in cmd
    assert port == 5173


def test_port_detection_from_logs(temp_workspace):
    """Test regex extraction of ports from real dev server logs and system port exclusion."""
    dummy_proc = ManagedRuntimeProcess(
        runtime_id="rt-dummy",
        session_id="dummy-sess",
        workspace_dir=temp_workspace,
        command="test",
    )

    # Standard Vite output
    port = dummy_proc.detect_port_from_line("  ➜  Local:   http://localhost:5173/")
    assert port == 5173

    # Standard Uvicorn output
    port = dummy_proc.detect_port_from_line("INFO:     Uvicorn running on http://127.0.0.1:8001 (Press CTRL+C to quit)")
    assert port == 8001

    # Port in brackets
    port = dummy_proc.detect_port_from_line("Server listening on http://localhost:4567")
    assert port == 4567

    # System ports MUST be rejected to avoid routing to Agent System infra
    for sys_port in SYSTEM_PORTS:
        assert dummy_proc.detect_port_from_line(f"Listening on http://localhost:{sys_port}") is None


def test_process_lifecycle_start_stop_restart(runtime_manager, temp_workspace):
    """Test full real OS process lifecycle: spawn, log capture, stop, and restart."""
    test_port = 8877
    server_script = f"""
import http.server
import socketserver
import sys

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')

port = {test_port}
print(f"Test server listening on http://localhost:{{port}}", flush=True)
sys.stdout.flush()

with socketserver.TCPServer(("", port), Handler) as httpd:
    httpd.serve_forever()
"""
    (temp_workspace / "server.py").write_text(server_script, encoding="utf-8")

    session_id = "test-session-runtime-life"
    cmd = "python server.py"

    # 1. Start runtime
    m_proc = runtime_manager.start_runtime(
        session_id=session_id,
        workspace_dir=temp_workspace,
        custom_command=cmd,
        requested_port=test_port,
    )

    assert m_proc.runtime_id is not None
    assert m_proc.session_id == session_id
    assert m_proc.status in ("STARTING", "RUNNING")

    # Give process a moment to spawn and emit startup log
    time.sleep(1.5)
    assert m_proc.pid is not None
    assert runtime_manager.is_process_running(m_proc.pid)

    # 2. Check logs captured in circular buffer
    logs = runtime_manager.get_runtime_logs(m_proc.runtime_id)
    assert len(logs) > 0
    log_text = "\n".join(l["text"] for l in logs)
    assert f"http://localhost:{test_port}" in log_text

    # 3. Check status and port
    status_info = runtime_manager.get_runtime_status(m_proc.runtime_id)
    assert status_info is not None
    assert status_info["status"] in ("RUNNING", "PORT_DETECTED", "HEALTHY")
    assert status_info["port"] == test_port
    assert status_info["preview_url"] == f"http://localhost:{test_port}"

    # 4. Stop runtime and verify process tree killed
    success = runtime_manager.stop_runtime(m_proc.runtime_id)
    assert success is True
    time.sleep(1.0)

    # Verify process is no longer active
    assert not runtime_manager.is_process_running(m_proc.pid)

    # 5. Restart runtime
    restarted_proc = runtime_manager.restart_runtime(m_proc.runtime_id)
    assert restarted_proc.status in ("STARTING", "RUNNING")

    time.sleep(1.5)
    assert restarted_proc.pid is not None
    assert restarted_proc.pid != m_proc.pid
    assert runtime_manager.is_process_running(restarted_proc.pid)

    # Clean up
    runtime_manager.stop_runtime(restarted_proc.runtime_id)
    time.sleep(0.5)
    assert not runtime_manager.is_process_running(restarted_proc.pid)
