"""
Git MCP Server.
Provides standardized, sandboxed Git repository inspection, history, diff generation,
branching, checkout, commits, restores, and patch management over MCP.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .base_server import BaseMCPServer

try:
    from ...security.sandbox import BaseExecutionSandbox, create_sandbox, SandboxResult
except (ImportError, ValueError):
    from agent_orchestrator.security.sandbox import BaseExecutionSandbox, create_sandbox, SandboxResult


class GitMCPServer(BaseMCPServer):
    """
    Standard MCP Git Server providing version control, diff inspection, branching,
    commit provenance, and safe rollback capabilities.
    """

    def __init__(
        self,
        repo_dir: Union[Path, str],
        name: str = "mcp-server-git",
        sandbox: Optional[BaseExecutionSandbox] = None,
    ):
        super().__init__(
            name=name,
            version="1.0.0",
            description="Git repository version control server for diffs, logs, branching, and commit status.",
        )
        self.repo_dir = Path(repo_dir).resolve()
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox = sandbox or create_sandbox(self.repo_dir)
        self._register_git_tools()

    def _run_git(self, args: List[str], timeout: int = 15, strip: bool = True) -> Dict[str, Any]:
        """Executes a Git command through the execution sandbox."""
        cmd = ["git"] + args
        result = self.sandbox.run_command(cmd, timeout=timeout, cwd=self.repo_dir)
        stdout = result.stdout.strip() if strip else result.stdout
        stderr = result.stderr.strip() if strip else result.stderr
        return {
            "exit_code": result.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "success": result.success,
        }

    def _register_git_tools(self) -> None:
        # 1. git_status
        self.register_tool(
            name="git_status",
            description="Returns working tree status (staged, unstaged, untracked files).",
            input_schema={
                "type": "object",
                "properties": {
                    "short": {"type": "boolean", "description": "Return short output format", "default": True},
                },
            },
            handler=self.git_status,
        )

        # 2. git_diff
        self.register_tool(
            name="git_diff",
            description="Generates diff of unstaged/staged changes or against a target branch/commit.",
            input_schema={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Branch, commit, or HEAD (default: HEAD)", "default": "HEAD"},
                    "staged": {"type": "boolean", "description": "Inspect staged changes only", "default": False},
                    "file_path": {"type": "string", "description": "Optional specific file path to diff"},
                },
            },
            handler=self.git_diff,
        )

        # 3. git_log
        self.register_tool(
            name="git_log",
            description="Returns recent commit history log.",
            input_schema={
                "type": "object",
                "properties": {
                    "max_count": {"type": "integer", "description": "Max number of commits", "default": 5},
                    "file_path": {"type": "string", "description": "Optional file path to inspect commit history for"},
                },
            },
            handler=self.git_log,
        )

        # 4. git_show
        self.register_tool(
            name="git_show",
            description="Inspects metadata, message, and diff of a specific commit.",
            input_schema={
                "type": "object",
                "properties": {
                    "commit": {"type": "string", "description": "Commit hash or reference (default: HEAD)", "default": "HEAD"},
                },
            },
            handler=self.git_show,
        )

        # 5. git_blame
        self.register_tool(
            name="git_blame",
            description="Inspects line-by-line commit authorship and provenance for a file.",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to file to blame"},
                    "start_line": {"type": "integer", "description": "Starting line number (1-based)"},
                    "end_line": {"type": "integer", "description": "Ending line number (1-based)"},
                },
                "required": ["file_path"],
            },
            handler=self.git_blame,
        )

        # 6. git_branch
        self.register_tool(
            name="git_branch",
            description="Lists existing branches, creates a new branch, or deletes a branch.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Branch name to create or delete"},
                    "delete": {"type": "boolean", "description": "Delete the specified branch", "default": False},
                },
            },
            handler=self.git_branch,
        )

        # 7. git_checkout
        self.register_tool(
            name="git_checkout",
            description="Switches branches or creates and switches to a new branch.",
            input_schema={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Target branch name or commit hash"},
                    "create_branch": {"type": "boolean", "description": "Create a new branch (-b)", "default": False},
                },
                "required": ["target"],
            },
            handler=self.git_checkout,
        )

        # 8. git_commit
        self.register_tool(
            name="git_commit",
            description="Stages modified files and creates a new commit with the given message.",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Commit message"},
                    "add_all": {"type": "boolean", "description": "Stage all modified files (-A)", "default": True},
                    "files": {"type": "array", "items": {"type": "string"}, "description": "Specific files to stage"},
                },
                "required": ["message"],
            },
            handler=self.git_commit,
        )

        # 9. git_restore
        self.register_tool(
            name="git_restore",
            description="Restores/discards modifications to working tree files or unstages files.",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to file to restore"},
                    "staged": {"type": "boolean", "description": "Unstage changes from index (--staged)", "default": False},
                },
                "required": ["file_path"],
            },
            handler=self.git_restore,
        )

        # 10. git_patch
        self.register_tool(
            name="git_patch",
            description="Exports a unified diff patch or applies a unified diff patch to the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "'export' (generate patch) or 'apply' (apply patch)", "enum": ["export", "apply"]},
                    "patch_content": {"type": "string", "description": "Unified diff content to apply (when action is 'apply')"},
                },
                "required": ["action"],
            },
            handler=self.git_patch,
        )

        # 11. git_init
        self.register_tool(
            name="git_init",
            description="Initializes a new Git repository in the workspace with safe default configuration.",
            input_schema={"type": "object", "properties": {}},
            handler=self.git_init,
        )

    def git_status(self, short: bool = True) -> Dict[str, Any]:
        args = ["status", "--short"] if short else ["status"]
        return self._run_git(args)

    def git_diff(
        self,
        target: str = "HEAD",
        staged: bool = False,
        file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        args = ["diff"]
        if staged:
            args.append("--staged")
        elif target and target != "HEAD":
            args.append(target)
        if file_path:
            args.extend(["--", file_path])
        return self._run_git(args)

    def git_log(self, max_count: int = 5, file_path: Optional[str] = None) -> Dict[str, Any]:
        args = ["log", f"-n{max_count}", "--oneline", "--decorate"]
        if file_path:
            args.extend(["--", file_path])
        return self._run_git(args)

    def git_show(self, commit: str = "HEAD") -> Dict[str, Any]:
        args = ["show", commit, "--stat", "-p"]
        return self._run_git(args)

    def git_blame(
        self,
        file_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> Dict[str, Any]:
        args = ["blame"]
        if start_line is not None and end_line is not None:
            args.extend(["-L", f"{start_line},{end_line}"])
        elif start_line is not None:
            args.extend(["-L", f"{start_line},+10"])
        args.extend(["--", file_path])
        return self._run_git(args)

    def git_branch(
        self,
        name: Optional[str] = None,
        delete: bool = False,
    ) -> Dict[str, Any]:
        if name:
            if delete:
                return self._run_git(["branch", "-D", name])
            else:
                return self._run_git(["branch", name])
        return self._run_git(["branch", "-a"])

    def git_checkout(self, target: str, create_branch: bool = False) -> Dict[str, Any]:
        if create_branch:
            return self._run_git(["checkout", "-b", target])
        return self._run_git(["checkout", target])

    def git_commit(
        self,
        message: str,
        add_all: bool = True,
        files: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if add_all:
            add_res = self._run_git(["add", "-A"])
            if not add_res["success"]:
                return add_res
        elif files:
            add_res = self._run_git(["add"] + files)
            if not add_res["success"]:
                return add_res
        return self._run_git(["commit", "-m", message])

    def git_restore(self, file_path: str, staged: bool = False) -> Dict[str, Any]:
        if staged:
            return self._run_git(["restore", "--staged", file_path])
        return self._run_git(["restore", file_path])

    def git_patch(
        self,
        action: str,
        patch_content: Optional[str] = None,
    ) -> Dict[str, Any]:
        if action == "export":
            # For patches, preserve trailing newlines for unified diff integrity
            cmd_res = self._run_git(["diff", "HEAD"], strip=False)
            patch_text = cmd_res["stdout"]
            if patch_text and not patch_text.endswith("\n"):
                patch_text += "\n"
            return {
                "action": "export",
                "patch": patch_text,
                "success": cmd_res["success"],
            }
        elif action == "apply":
            if not patch_content:
                return {"error": "patch_content is required for apply action", "success": False}

            clean_patch = patch_content
            if not clean_patch.endswith("\n"):
                clean_patch += "\n"

            patch_file = self.repo_dir / ".git_temp.patch"
            try:
                patch_file.write_text(clean_patch, encoding="utf-8")
                res = self._run_git(["apply", "--whitespace=nowarn", str(patch_file)])
                return {
                    "action": "apply",
                    "stdout": res["stdout"],
                    "stderr": res["stderr"],
                    "exit_code": res["exit_code"],
                    "success": res["success"],
                }
            finally:
                if patch_file.exists():
                    try:
                        patch_file.unlink()
                    except Exception:
                        pass
        return {"error": f"Unknown git_patch action: {action}", "success": False}

    def git_init(self) -> Dict[str, Any]:
        res = self._run_git(["init"])
        if res["success"]:
            self._run_git(["config", "user.name", "Agent"])
            self._run_git(["config", "user.email", "agent@orchestrator.local"])
        return res
