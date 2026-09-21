"""
Code and Test execution tool to execute tests in a workspace subprocess via an execution sandbox.
"""
from pathlib import Path
from typing import Dict, Any, Optional, Union, List

try:
    from ..security.sandbox import BaseExecutionSandbox, create_sandbox, SandboxResult
except (ImportError, ValueError):
    from agent_orchestrator.security.sandbox import BaseExecutionSandbox, create_sandbox, SandboxResult


class CodeExecutor:
    def __init__(self, working_dir: Union[Path, str], sandbox: Optional[BaseExecutionSandbox] = None):
        self.working_dir = Path(working_dir)
        self.sandbox = sandbox or create_sandbox(self.working_dir)

    def run_command(self, cmd: Union[List[str], str], timeout: int = 30) -> Dict[str, Any]:
        """Run a command in the workspace directory through the execution sandbox."""
        result = self.sandbox.run_command(cmd, timeout=timeout, cwd=self.working_dir)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.success,
        }

    def run_tests(self, test_file_pattern: str = "test_*.py", timeout: int = 30) -> Dict[str, Any]:
        """Run pytest or unittest discovery in the workspace through the sandbox."""
        result = self.sandbox.run_tests(pattern=test_file_pattern, timeout=timeout, cwd=self.working_dir)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.success,
        }
