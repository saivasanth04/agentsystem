"""
Terminal Execution MCP Server.
Provides isolated command execution, automated test runner discovery, and environment checks over MCP via execution sandbox.
"""
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .base_server import BaseMCPServer

try:
    from ...security.sandbox import BaseExecutionSandbox, create_sandbox, SandboxResult
except (ImportError, ValueError):
    from agent_orchestrator.security.sandbox import BaseExecutionSandbox, create_sandbox, SandboxResult


class TerminalMCPServer(BaseMCPServer):
    """
    Standard MCP Terminal Execution Server for running builds, linters, and test suites.
    """

    def __init__(
        self,
        working_dir: Union[Path, str],
        name: str = "mcp-server-terminal",
        sandbox: Optional[BaseExecutionSandbox] = None,
    ):
        super().__init__(
            name=name,
            version="1.0.0",
            description="Terminal command and test suite runner with timeout controls and exit status reporting.",
        )
        self.working_dir = Path(working_dir).resolve()
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox = sandbox or create_sandbox(self.working_dir)
        self._register_terminal_tools()

    def _register_terminal_tools(self) -> None:
        # 1. terminal_execute
        self.register_tool(
            name="terminal_execute",
            description="Executes a shell command in the workspace directory.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)", "default": 30},
                },
                "required": ["command"],
            },
            handler=self.terminal_execute,
        )

        # 2. run_tests
        self.register_tool(
            name="run_tests",
            description="Executes automated unit test discovery across the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Test filename pattern (e.g. test_*.py)", "default": "test_*.py"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
                },
            },
            handler=self.run_tests,
        )

        # 3. check_environment
        self.register_tool(
            name="check_environment",
            description="Reports current Python environment, working directory, and installed packages.",
            input_schema={"type": "object", "properties": {}},
            handler=self.check_environment,
        )

    def terminal_execute(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        result = self.sandbox.run_command(command, timeout=timeout, cwd=self.working_dir)
        return {
            "command": command,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.success,
        }

    def run_tests(self, pattern: str = "test_*.py", timeout: int = 30) -> Dict[str, Any]:
        result = self.sandbox.run_tests(pattern=pattern, timeout=timeout, cwd=self.working_dir)
        return {
            "command": f'unittest discover -p "{pattern}"',
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.success,
        }

    def check_environment(self) -> Dict[str, Any]:
        return {
            "python_version": sys.version,
            "executable": sys.executable,
            "working_directory": str(self.working_dir),
            "platform": sys.platform,
            "success": True,
        }
