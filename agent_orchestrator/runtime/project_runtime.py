"""
ProjectRuntimeManager: Authoritative Process Lifecycle, Dynamic Port Discovery, and Health Probing Engine.
Manages long-running target project processes (dev servers, backend APIs, fullstack apps)
strictly bound to the session's workspace boundary. Prevents port collision with Agent System infrastructure.
"""
from collections import deque
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple, Union
import urllib.request
import uuid

from agent_orchestrator.persistence.state_store import SQLiteStateStore
from agent_orchestrator.runtime.event_bus import EventBus
from agent_orchestrator.runtime.project_detector import ProjectEnvironmentDetector

logger = logging.getLogger("orchestrator.runtime.project")

# Agent System Infrastructure Ports - NEVER assign or confuse these as project preview URLs
SYSTEM_PORTS: Set[int] = {3000, 8000, 8080}

PORT_REGEXES = [
    re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})", re.IGNORECASE),
    re.compile(r"(?:Local|Network|running at|listening on|ready on|listening at|port|Serving at):\s*(?:https?://[^\s:]+:)?(\d{2,5})", re.IGNORECASE),
    re.compile(r"Uvicorn running on https?://[^\s:]+:(\d{2,5})", re.IGNORECASE),
    re.compile(r"VITE v[^\s]+ ready in \d+ ms\s+➜\s+Local:\s+https?://[^:]+:(\d{2,5})", re.IGNORECASE),
    re.compile(r":(\d{4,5})\s*\(HTTP\)", re.IGNORECASE),
]


class RuntimeLogEntry:
    def __init__(self, stream: str, text: str, timestamp: Optional[str] = None):
        self.stream = stream  # 'stdout' | 'stderr' | 'system'
        self.text = text
        self.timestamp = timestamp or datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stream": self.stream,
            "text": self.text,
            "timestamp": self.timestamp,
        }


class ManagedRuntimeProcess:
    """Encapsulates a live project runtime process instance."""

    def __init__(
        self,
        runtime_id: str,
        session_id: str,
        workspace_dir: Path,
        command: str,
        framework: Optional[str] = None,
        requested_port: Optional[int] = None,
        event_bus: Optional[EventBus] = None,
        state_store: Optional[SQLiteStateStore] = None,
        max_log_lines: int = 1000,
    ):
        self.runtime_id = runtime_id
        self.session_id = session_id
        self.workspace_dir = Path(workspace_dir).resolve()
        self.command = command
        self.framework = framework
        self.requested_port = requested_port
        self.event_bus = event_bus
        self.state_store = state_store

        self.proc: Optional[subprocess.Popen] = None
        self.pid: Optional[int] = None
        self.port: Optional[int] = requested_port if (requested_port and requested_port not in SYSTEM_PORTS) else None
        self.preview_url: Optional[str] = f"http://localhost:{self.port}" if self.port else None
        self.status: str = "STARTING"
        self.health: str = "UNKNOWN"
        self.exit_code: Optional[int] = None

        self.started_at: str = datetime.now().isoformat()
        self.stopped_at: Optional[str] = None

        self.logs: Deque[RuntimeLogEntry] = deque(maxlen=max_log_lines)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._health_thread: Optional[threading.Thread] = None

    def append_log(self, stream: str, text: str):
        entry = RuntimeLogEntry(stream, text)
        with self._lock:
            self.logs.append(entry)

        if self.event_bus:
            try:
                self.event_bus.publish(
                    "RUNTIME_LOG",
                    payload={
                        "runtime_id": self.runtime_id,
                        "session_id": self.session_id,
                        "stream": stream,
                        "text": text,
                        "timestamp": entry.timestamp,
                    },
                )
            except Exception:
                pass

    def detect_port_from_line(self, line: str) -> Optional[int]:
        """Scans output line for a listening HTTP port number."""
        for pattern in PORT_REGEXES:
            match = pattern.search(line)
            if match:
                try:
                    p = int(match.group(1))
                    if 1024 <= p <= 65535 and p not in SYSTEM_PORTS:
                        return p
                except (ValueError, IndexError):
                    continue
        return None

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "runtime_id": self.runtime_id,
                "session_id": self.session_id,
                "workspace_dir": str(self.workspace_dir),
                "command": self.command,
                "framework": self.framework,
                "pid": self.pid,
                "port": self.port,
                "status": self.status,
                "preview_url": self.preview_url,
                "health": self.health,
                "exit_code": self.exit_code,
                "started_at": self.started_at,
                "stopped_at": self.stopped_at,
                "log_count": len(self.logs),
            }


class ProjectRuntimeManager:
    """
    Subsystem managing the build, start, port detection, log streaming,
    and process tree termination for user projects in session workspaces.
    """

    def __init__(
        self,
        state_store: Optional[SQLiteStateStore] = None,
        event_bus: Optional[EventBus] = None,
        workspace_root: Optional[Any] = None,
        workspace_dir: Optional[Any] = None,
        **kwargs: Any,
    ):
        self.state_store = state_store
        self.event_bus = event_bus
        self.workspace_root = Path(workspace_root) if workspace_root else (Path(workspace_dir) if workspace_dir else None)
        self.active_processes: Dict[str, ManagedRuntimeProcess] = {}  # runtime_id -> ManagedRuntimeProcess
        self._lock = threading.RLock()
        self._cleanup_stale_runtimes()

    def _cleanup_stale_runtimes(self):
        """Scans persisted runtimes in SQLite; marks any dead processes as STOPPED."""
        if not self.state_store:
            return
        try:
            stored = self.state_store.list_runtimes()
            for r in stored:
                if r.get("status") in ("STARTING", "RUNNING", "HEALTHY"):
                    pid = r.get("pid")
                    is_alive = False
                    if pid and pid > 0:
                        is_alive = self._is_pid_alive(pid)
                    if not is_alive:
                        self.state_store.update_runtime_status(
                            runtime_id=r["runtime_id"],
                            status="STOPPED",
                            exit_code=-1,
                            stopped_at=datetime.now().isoformat(),
                        )
        except Exception as e:
            logger.warning(f"Error cleaning up stale runtimes: {e}")

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        """Checks whether an OS process with the given PID is currently alive."""
        if pid <= 0:
            return False
        if os.name == "nt":
            try:
                # Use tasklist filter to check existence on Windows
                res = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                return str(pid) in res.stdout
            except Exception:
                return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    @classmethod
    def is_pid_alive(cls, pid: int) -> bool:
        return cls._is_pid_alive(pid)

    @classmethod
    def is_process_running(cls, pid: int) -> bool:
        return cls._is_pid_alive(pid)

    @staticmethod
    def inspect_listening_ports(pid: int) -> List[int]:
        """Discovers TCP ports in LISTENING state owned by PID or child processes."""
        ports = []
        if os.name == "nt":
            try:
                res = subprocess.run(
                    ["netstat", "-ano", "-p", "tcp"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                for line in res.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 5 and parts[3].upper() == "LISTENING":
                        try:
                            line_pid = int(parts[4])
                            if line_pid == pid:
                                local_addr = parts[1]
                                port = int(local_addr.split(":")[-1])
                                if port not in SYSTEM_PORTS and 1024 <= port <= 65535:
                                    ports.append(port)
                        except (ValueError, IndexError):
                            continue
            except Exception:
                pass
        else:
            try:
                res = subprocess.run(
                    ["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P", "-p", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                for line in res.stdout.splitlines():
                    match = re.search(r":(\d+)\s+\(LISTEN\)", line)
                    if match:
                        p = int(match.group(1))
                        if p not in SYSTEM_PORTS and 1024 <= p <= 65535:
                            ports.append(p)
            except Exception:
                pass
        return ports

    def detect_project_command(self, workspace_path: Path) -> Tuple[str, Optional[str], Optional[int]]:
        """
        Auto-detects project framework, default start command, and suggested port.
        Returns: (command, framework, default_port)
        """
        ws = Path(workspace_path).resolve()
        pkg_json = ws / "package.json"

        # 1. Node.js / Web frameworks
        if pkg_json.is_file():
            try:
                with open(pkg_json, "r", encoding="utf-8") as f:
                    pkg_data = json.load(f)
                scripts = pkg_data.get("scripts", {})
                deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}

                # Framework heuristics
                framework = "node"
                if "next" in deps:
                    framework = "nextjs"
                    # Next.js defaults to 3000, which collides with Agent System UI, so pass -p 3001
                    if "dev" in scripts:
                        return "npm run dev -- -p 3001", framework, 3001
                    return "npm start -- -p 3001", framework, 3001

                if "vite" in deps:
                    framework = "vite"
                    if "dev" in scripts:
                        return "npm run dev", framework, 5173
                    return "npm start", framework, 5173

                if "react-scripts" in deps:
                    framework = "react"
                    # React scripts uses PORT env var
                    if "start" in scripts:
                        return "npm start", framework, 3001

                if "vue" in deps or "@vue/cli-service" in deps:
                    framework = "vue"
                    if "serve" in scripts:
                        return "npm run serve", framework, 8081
                    if "dev" in scripts:
                        return "npm run dev", framework, 5173

                if "dev" in scripts:
                    return "npm run dev", framework, 5173
                if "start" in scripts:
                    return "npm start", framework, 5173
            except Exception as e:
                logger.warning(f"Error parsing package.json in {ws}: {e}")

        # 2. Python backend / fullstack
        pyproject = ws / "pyproject.toml"
        reqs = ws / "requirements.txt"
        has_python = pyproject.is_file() or reqs.is_file() or any(ws.glob("*.py"))

        if has_python:
            # Check for FastAPI / Uvicorn app files
            main_py = ws / "main.py"
            app_py = ws / "app.py"
            server_py = ws / "server.py"

            if (ws / "manage.py").is_file():
                return "python manage.py runserver 8001", "django", 8001

            # Check if any main/app has FastAPI or Flask
            for candidate in [main_py, app_py, server_py]:
                if candidate.is_file():
                    try:
                        content = candidate.read_text(encoding="utf-8", errors="ignore")
                        if "FastAPI" in content or "uvicorn" in content:
                            mod_name = candidate.stem
                            return f"python -m uvicorn {mod_name}:app --reload --port 8001", "fastapi", 8001
                        if "Flask" in content:
                            return f"python {candidate.name}", "flask", 5000
                    except Exception:
                        pass

            if main_py.is_file():
                return "python main.py", "python", 8001
            if app_py.is_file():
                return "python app.py", "python", 8001

        # 3. Rust Cargo
        if (ws / "Cargo.toml").is_file():
            return "cargo run", "rust", 8081

        # 4. Go
        if (ws / "go.mod").is_file() or (ws / "main.go").is_file():
            return "go run .", "go", 8081

        # 5. Static HTML website fallback
        if (ws / "index.html").is_file():
            return "python -m http.server 5173", "static", 5173

        # Default fallback command
        return "python -m http.server 5173", "generic", 5173

    def start_runtime(
        self,
        session_id: str,
        workspace_dir: Optional[Union[Path, str]] = None,
        custom_command: Optional[str] = None,
        requested_port: Optional[int] = None,
        **kwargs: Any,
    ) -> ManagedRuntimeProcess:
        """
        Spawns a new project dev server / runtime process bound to session workspace.
        Terminates any previously running process for this session first.
        """
        target_ws = workspace_dir or self.workspace_root
        if not target_ws:
            raise ValueError("No workspace directory specified for runtime execution.")
        ws = Path(target_ws).resolve()
        if not ws.exists() or not ws.is_dir():
            raise ValueError(f"Target workspace directory '{target_ws}' does not exist.")

        # Stop existing active runtimes for this session
        self.stop_session_runtimes(session_id)

        # Detect command & port if not specified
        detected_cmd, framework, default_port = self.detect_project_command(ws)
        command = custom_command.strip() if custom_command and custom_command.strip() else detected_cmd
        port = requested_port or default_port

        if port in SYSTEM_PORTS:
            port = 5173  # Shift away from system infrastructure

        runtime_id = f"rt-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        m_proc = ManagedRuntimeProcess(
            runtime_id=runtime_id,
            session_id=session_id,
            workspace_dir=ws,
            command=command,
            framework=framework,
            requested_port=port,
            event_bus=self.event_bus,
            state_store=self.state_store,
        )

        with self._lock:
            self.active_processes[runtime_id] = m_proc

        if self.state_store:
            self.state_store.save_runtime(m_proc.to_dict())

        # Spawn child process in background thread
        spawn_thread = threading.Thread(
            target=self._run_process_lifecycle,
            args=(m_proc,),
            daemon=True,
            name=f"RuntimeProc-{runtime_id}",
        )
        spawn_thread.start()

        if self.event_bus:
            self.event_bus.publish(
                "RUNTIME_STARTING",
                payload={
                    "runtime_id": runtime_id,
                    "session_id": session_id,
                    "workspace_dir": str(ws),
                    "command": command,
                    "framework": framework,
                },
            )

        return m_proc

    def _run_process_lifecycle(self, m_proc: ManagedRuntimeProcess):
        """Asynchronous process runner capturing stdout/stderr and tracking lifecycle."""
        env = os.environ.copy()
        # Set PORT environment variable to guide dev servers away from system ports
        if m_proc.port:
            env["PORT"] = str(m_proc.port)
            env["BROWSER"] = "none"  # Prevent dev server from auto-launching native browser window

        m_proc.append_log("system", f"Starting project runtime: {m_proc.command} (cwd: {m_proc.workspace_dir})")

        try:
            # On Windows, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP is helpful
            creation_flags = 0
            if os.name == "nt":
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

            proc = subprocess.Popen(
                m_proc.command,
                shell=True,
                cwd=str(m_proc.workspace_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                creationflags=creation_flags,
            )
            m_proc.proc = proc
            m_proc.pid = proc.pid
            m_proc.status = "RUNNING"

            if self.state_store:
                self.state_store.update_runtime_status(
                    runtime_id=m_proc.runtime_id,
                    status="RUNNING",
                    pid=proc.pid,
                )

            m_proc.append_log("system", f"Process spawned with PID {proc.pid}")

            # Launch stdout and stderr reader threads
            t_out = threading.Thread(target=self._stream_reader, args=(m_proc, proc.stdout, "stdout"), daemon=True)
            t_err = threading.Thread(target=self._stream_reader, args=(m_proc, proc.stderr, "stderr"), daemon=True)
            t_out.start()
            t_err.start()

            # Start health probing & port discovery thread
            h_thread = threading.Thread(target=self._health_probe_loop, args=(m_proc,), daemon=True)
            m_proc._health_thread = h_thread
            h_thread.start()

            # Wait for process exit
            exit_code = proc.wait()
            m_proc.exit_code = exit_code
            m_proc.status = "STOPPED" if exit_code == 0 else "FAILED"
            m_proc.stopped_at = datetime.now().isoformat()
            m_proc.append_log("system", f"Process exited with code {exit_code}")

            if self.state_store:
                self.state_store.update_runtime_status(
                    runtime_id=m_proc.runtime_id,
                    status=m_proc.status,
                    exit_code=exit_code,
                    stopped_at=m_proc.stopped_at,
                )

            if self.event_bus:
                self.event_bus.publish(
                    "RUNTIME_STOPPED",
                    payload={
                        "runtime_id": m_proc.runtime_id,
                        "session_id": m_proc.session_id,
                        "exit_code": exit_code,
                        "status": m_proc.status,
                    },
                )

        except Exception as e:
            logger.error(f"Error launching runtime {m_proc.runtime_id}: {e}", exc_info=True)
            m_proc.status = "FAILED"
            m_proc.exit_code = -1
            m_proc.stopped_at = datetime.now().isoformat()
            m_proc.append_log("system", f"Failed to start process: {e}")

            if self.state_store:
                self.state_store.update_runtime_status(
                    runtime_id=m_proc.runtime_id,
                    status="FAILED",
                    exit_code=-1,
                    stopped_at=m_proc.stopped_at,
                )
            if self.event_bus:
                self.event_bus.publish(
                    "RUNTIME_FAILED",
                    payload={
                        "runtime_id": m_proc.runtime_id,
                        "session_id": m_proc.session_id,
                        "error": str(e),
                    },
                )

    def _stream_reader(self, m_proc: ManagedRuntimeProcess, stream_pipe: Any, stream_name: str):
        """Reads pipe line by line and triggers port discovery."""
        if not stream_pipe:
            return
        try:
            for line in iter(stream_pipe.readline, ""):
                if not line:
                    break
                clean_line = line.rstrip("\r\n")
                m_proc.append_log(stream_name, clean_line)

                # Attempt port discovery if not yet bound
                if not m_proc.port or m_proc.status == "RUNNING":
                    detected_p = m_proc.detect_port_from_line(clean_line)
                    if detected_p and detected_p not in SYSTEM_PORTS:
                        self._on_port_discovered(m_proc, detected_p)
        except Exception:
            pass
        finally:
            try:
                stream_pipe.close()
            except Exception:
                pass

    def _on_port_discovered(self, m_proc: ManagedRuntimeProcess, port: int):
        """Called when a listening port is detected from process output or netstat."""
        if m_proc.port == port and m_proc.preview_url:
            return
        with m_proc._lock:
            m_proc.port = port
            m_proc.preview_url = f"http://localhost:{port}"
            m_proc.status = "PORT_DETECTED"
            m_proc.append_log("system", f"Discovered project preview port: {port} -> {m_proc.preview_url}")

        if self.state_store:
            self.state_store.update_runtime_status(
                runtime_id=m_proc.runtime_id,
                status="PORT_DETECTED",
                port=port,
                preview_url=m_proc.preview_url,
            )

        if self.event_bus:
            self.event_bus.publish(
                "RUNTIME_PORT_DETECTED",
                payload={
                    "runtime_id": m_proc.runtime_id,
                    "session_id": m_proc.session_id,
                    "port": port,
                    "preview_url": m_proc.preview_url,
                },
            )

    def _health_probe_loop(self, m_proc: ManagedRuntimeProcess):
        """Periodically tests HTTP reachability of m_proc.preview_url."""
        # Initial wait for server boot
        for _ in range(30):
            if m_proc._stop_event.is_set() or m_proc.exit_code is not None:
                return

            # Check OS listening sockets if port not detected from logs yet
            if not m_proc.port and m_proc.pid:
                ports = self.inspect_listening_ports(m_proc.pid)
                if ports:
                    self._on_port_discovered(m_proc, ports[0])

            if m_proc.preview_url:
                is_healthy = self._probe_http_health(m_proc.preview_url)
                if is_healthy:
                    with m_proc._lock:
                        m_proc.health = "HEALTHY"
                        m_proc.status = "HEALTHY"
                    m_proc.append_log("system", f"Health check PASS: {m_proc.preview_url} is active and responding.")

                    if self.state_store:
                        self.state_store.update_runtime_status(
                            runtime_id=m_proc.runtime_id,
                            status="HEALTHY",
                            health="HEALTHY",
                        )

                    if self.event_bus:
                        self.event_bus.publish(
                            "RUNTIME_HEALTHY",
                            payload={
                                "runtime_id": m_proc.runtime_id,
                                "session_id": m_proc.session_id,
                                "preview_url": m_proc.preview_url,
                                "port": m_proc.port,
                            },
                        )
                    break
            time.sleep(1.0)

        # Continuous background health monitor while alive
        while not m_proc._stop_event.is_set() and m_proc.exit_code is None:
            time.sleep(10.0)
            if m_proc.preview_url:
                ok = self._probe_http_health(m_proc.preview_url)
                new_health = "HEALTHY" if ok else "DEGRADED"
                if new_health != m_proc.health:
                    m_proc.health = new_health
                    if self.state_store:
                        self.state_store.update_runtime_status(
                            runtime_id=m_proc.runtime_id,
                            status=m_proc.status,
                            health=new_health,
                        )

    @staticmethod
    def _probe_http_health(url: str, timeout: float = 2.0) -> bool:
        """Sends an HTTP GET probe to check if the server responds."""
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Antigravity-HealthProbe/2.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status < 500
        except urllib.error.HTTPError as e:
            # Even 404 or 403 means the HTTP server is actively running!
            return e.code < 500
        except Exception:
            # Check TCP connect fallback
            try:
                match = re.search(r":(\d+)", url)
                if match:
                    port = int(match.group(1))
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(1.0)
                    res = s.connect_ex(("127.0.0.1", port))
                    s.close()
                    return res == 0
            except Exception:
                pass
            return False

    def stop_runtime(self, runtime_id: str) -> bool:
        """Cleanly terminates the entire process tree for a given runtime."""
        m_proc = self.active_processes.get(runtime_id)
        if not m_proc:
            # Check state store
            if self.state_store:
                r = self.state_store.get_runtime(runtime_id)
                if r and r.get("pid"):
                    self._kill_process_tree(r["pid"])
                    self.state_store.update_runtime_status(
                        runtime_id=runtime_id,
                        status="STOPPED",
                        stopped_at=datetime.now().isoformat(),
                    )
            return True

        m_proc._stop_event.set()
        m_proc.append_log("system", "Stopping project runtime process...")

        if m_proc.pid:
            self._kill_process_tree(m_proc.pid)

        if m_proc.proc:
            try:
                m_proc.proc.terminate()
            except Exception:
                pass

        m_proc.status = "STOPPED"
        m_proc.stopped_at = datetime.now().isoformat()

        if self.state_store:
            self.state_store.update_runtime_status(
                runtime_id=runtime_id,
                status="STOPPED",
                stopped_at=m_proc.stopped_at,
            )

        if self.event_bus:
            self.event_bus.publish(
                "RUNTIME_STOPPED",
                payload={"runtime_id": runtime_id, "session_id": m_proc.session_id, "status": "STOPPED"},
            )

        return True

    @staticmethod
    def _kill_process_tree(pid: int):
        """Kills the target process and all child processes spawned by it (tree kill)."""
        if pid <= 0:
            return
        if os.name == "nt":
            try:
                # Forcefully kill process tree on Windows
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                )
            except Exception as e:
                logger.warning(f"Error executing taskkill on PID {pid}: {e}")
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                time.sleep(0.5)
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass

    def stop_session_runtimes(self, session_id: str):
        """Stops all active runtimes associated with a session."""
        with self._lock:
            targets = [rid for rid, p in self.active_processes.items() if p.session_id == session_id]
        for rid in targets:
            self.stop_runtime(rid)

    def restart_runtime(self, runtime_id: str) -> ManagedRuntimeProcess:
        """Stops and restarts an existing project runtime."""
        m_proc = self.active_processes.get(runtime_id)
        if not m_proc and self.state_store:
            r = self.state_store.get_runtime(runtime_id)
            if r:
                return self.start_runtime(
                    session_id=r["session_id"],
                    workspace_dir=Path(r["workspace_dir"]),
                    custom_command=r.get("command"),
                    requested_port=r.get("port"),
                )
            raise ValueError(f"Runtime '{runtime_id}' not found.")

        session_id = m_proc.session_id
        ws_dir = m_proc.workspace_dir
        cmd = m_proc.command
        port = m_proc.port

        self.stop_runtime(runtime_id)
        time.sleep(0.5)
        return self.start_runtime(
            session_id=session_id,
            workspace_dir=ws_dir,
            custom_command=cmd,
            requested_port=port,
        )

    def get_runtime_status(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        """Returns runtime status dictionary."""
        m_proc = self.active_processes.get(runtime_id)
        if m_proc:
            return m_proc.to_dict()
        if self.state_store:
            return self.state_store.get_runtime(runtime_id)
        return None

    def get_session_runtimes(self, session_id: str) -> List[Dict[str, Any]]:
        """Returns all runtimes for a given session."""
        results = []
        with self._lock:
            for p in self.active_processes.values():
                if p.session_id == session_id:
                    results.append(p.to_dict())

        if not results and self.state_store:
            results = self.state_store.list_runtimes(session_id)
        return results

    def get_runtime_logs(self, runtime_id: str, tail: int = 100) -> List[Dict[str, Any]]:
        """Returns the most recent log entries for a runtime."""
        m_proc = self.active_processes.get(runtime_id)
        if m_proc:
            with m_proc._lock:
                return [entry.to_dict() for entry in list(m_proc.logs)[-tail:]]
        return []
