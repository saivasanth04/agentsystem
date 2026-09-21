"""
Model Context Protocol (MCP) Transports.
Implements Stdio subprocess transport and In-Memory loopback transport.
"""
import abc
import json
import logging
import os
import queue
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Union

from .protocol import (
    JSONRPCError,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    MCPConnectionError,
    MCPTimeoutError,
)

logger = logging.getLogger("mcp.transport")


class BaseTransport(abc.ABC):
    """Abstract base transport for MCP communication."""

    @abc.abstractmethod
    def start(self) -> None:
        """Start or initialize the transport channel."""
        pass

    @abc.abstractmethod
    def send(self, message: Union[JSONRPCRequest, JSONRPCResponse, JSONRPCNotification, Dict[str, Any]]) -> None:
        """Send a message across the transport."""
        pass

    @abc.abstractmethod
    def receive(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Receive the next message from the transport, blocking up to timeout seconds."""
        pass

    @abc.abstractmethod
    def close(self) -> None:
        """Gracefully close the transport and clean up resources."""
        pass

    @abc.abstractmethod
    def is_alive(self) -> bool:
        """Returns True if the transport is open and operational."""
        pass


class StdioTransport(BaseTransport):
    """
    Standard I/O Transport executing an external server as a subprocess.
    Communicates via newline-delimited JSON-RPC over stdin/stdout.
    """

    def __init__(
        self,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ):
        self.command = command
        self.args = args or []
        self.env = env
        self.cwd = cwd
        self.process: Optional[subprocess.Popen] = None
        self.pid: Optional[int] = None
        self._receive_queue: queue.Queue = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        cmd_list = [self.command] + self.args
        try:
            self.process = subprocess.Popen(
                cmd_list,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=self.cwd,
                env=self.env,
                encoding="utf-8",
                errors="replace",
            )
            self.pid = self.process.pid
        except Exception as e:
            raise MCPConnectionError(f"Failed to spawn MCP server subprocess '{self.command}': {e}")

        self._stop_event.clear()
        self._reader_thread = threading.Thread(target=self._stdout_reader_loop, daemon=True)
        self._reader_thread.start()

        self._stderr_thread = threading.Thread(target=self._stderr_reader_loop, daemon=True)
        self._stderr_thread.start()

    def _stdout_reader_loop(self) -> None:
        if not self.process or not self.process.stdout:
            return
        for line in iter(self.process.stdout.readline, ""):
            if self._stop_event.is_set():
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                msg = json.loads(stripped)
                self._receive_queue.put(msg)
            except json.JSONDecodeError:
                logger.debug(f"[StdioTransport stdout non-json]: {stripped}")
        self.process.stdout.close()

    def _stderr_reader_loop(self) -> None:
        if not self.process or not self.process.stderr:
            return
        for line in iter(self.process.stderr.readline, ""):
            if self._stop_event.is_set():
                break
            stripped = line.strip()
            if stripped:
                logger.debug(f"[StdioTransport stderr]: {stripped}")
        self.process.stderr.close()

    def send(self, message: Union[JSONRPCRequest, JSONRPCResponse, JSONRPCNotification, Dict[str, Any]]) -> None:
        if not self.is_alive() or not self.process or not self.process.stdin:
            raise MCPConnectionError("Cannot send message: Stdio process is not alive.")
        payload = message.to_dict() if hasattr(message, "to_dict") else message
        json_line = json.dumps(payload) + "\n"
        try:
            self.process.stdin.write(json_line)
            self.process.stdin.flush()
        except Exception as e:
            raise MCPConnectionError(f"Failed writing to MCP server stdin: {e}")

    def receive(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        try:
            return self._receive_queue.get(block=True, timeout=timeout)
        except queue.Empty:
            if not self.is_alive():
                raise MCPConnectionError("Process terminated while waiting for response.")
            return None

    def close(self) -> None:
        self._stop_event.set()
        pid = self.pid
        if self.process:
            try:
                if self.process.stdin and not self.process.stdin.closed:
                    self.process.stdin.close()
            except Exception:
                pass

            # Terminate process tree cleanly
            try:
                if pid and os.name == "nt":
                    # On Windows, kill process tree so subshells/workers don't leak
                    subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(pid)],
                        capture_output=True,
                        timeout=5.0,
                    )
            except Exception:
                pass

            try:
                self.process.terminate()
                self.process.wait(timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            self.pid = None

    def is_alive(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None


class InMemoryTransport(BaseTransport):
    """
    In-memory dual-queue loopback transport.
    Permits embedding standard MCP server implementations without spawning external processes.
    """

    def __init__(self, server_handler: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None):
        self.server_handler = server_handler
        self._client_to_server: queue.Queue = queue.Queue()
        self._server_to_client: queue.Queue = queue.Queue()
        self._active = False
        self._dispatch_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._active = True
        if self.server_handler:
            self._dispatch_thread = threading.Thread(target=self._process_loop, daemon=True)
            self._dispatch_thread.start()

    def _process_loop(self) -> None:
        while self._active:
            try:
                msg = self._client_to_server.get(timeout=0.2)
            except queue.Empty:
                continue
            if not self._active:
                break
            if self.server_handler:
                try:
                    res = self.server_handler(msg)
                    if res is not None:
                        self._server_to_client.put(res)
                except Exception as e:
                    req_id = msg.get("id") if isinstance(msg, dict) else None
                    self._server_to_client.put({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": str(e)},
                    })

    def send(self, message: Union[JSONRPCRequest, JSONRPCResponse, JSONRPCNotification, Dict[str, Any]]) -> None:
        if not self._active:
            raise MCPConnectionError("InMemoryTransport is closed.")
        payload = message.to_dict() if hasattr(message, "to_dict") else message
        self._client_to_server.put(payload)

    def send_from_server(self, message: Union[JSONRPCRequest, JSONRPCResponse, JSONRPCNotification, Dict[str, Any]]) -> None:
        """Allow server to push an unsolicited message or notification to the client."""
        if not self._active:
            raise MCPConnectionError("InMemoryTransport is closed.")
        payload = message.to_dict() if hasattr(message, "to_dict") else message
        self._server_to_client.put(payload)

    def receive(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        try:
            return self._server_to_client.get(block=True, timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._active = False

    def is_alive(self) -> bool:
        return self._active
