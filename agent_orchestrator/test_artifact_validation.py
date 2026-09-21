"""
Unit and Integration Tests for Artifact & File Validation Engine (Issue #30).
Validates:
  1. Non-existent / 0-byte file detection
  2. Content sanity (empty, placeholders, git merge conflicts)
  3. Syntax & compilation checks (Python AST, JSON)
  4. Path & policy enforcement (allowed_write_paths, forbidden paths)
  5. Task scope reconciliation (required vs created outputs)
  6. CoderAgent deliverable validation
  7. TaskVerificationGate artifact enforcement
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agent_orchestrator.runtime.artifact_validator import (
    ArtifactValidator,
    ArtifactValidationResult,
    ArtifactReconciliationResult,
)
from agent_orchestrator.tools.workspace import WorkspaceManager, PathTraversalError
from agent_orchestrator.agents.coder import CoderAgent
from agent_orchestrator.state import OrchestratorState
from agent_orchestrator.runtime.task_graph import ExecutableTask
from agent_orchestrator.runtime.verification import TaskVerificationGate


class TestArtifactValidator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_nonexistent_file_flagged_as_not_created(self):
        """Files that do not exist on disk must be flagged as not created."""
        ok, errs = ArtifactValidator.verify_on_disk(self.workspace, "nonexistent.py")
        self.assertFalse(ok)
        self.assertTrue(any("does not exist" in e for e in errs))

    def test_empty_file_rejected_on_disk(self):
        """Files that exist on disk but are 0 bytes must be flagged."""
        p = Path(self.temp_dir) / "empty.py"
        p.write_text("", encoding="utf-8")

        ok, errs = ArtifactValidator.verify_on_disk(self.workspace, "empty.py")
        self.assertFalse(ok)
        self.assertTrue(any("empty (0 bytes)" in e for e in errs))

    def test_invalid_content_empty_or_placeholders_rejected(self):
        """Empty content and content with lazy truncation placeholders must be rejected."""
        # Empty
        ok, errs = ArtifactValidator.validate_content("app.py", "   \n\t  ")
        self.assertFalse(ok)
        self.assertTrue(any("empty" in e for e in errs))

        # Truncation placeholder
        code_with_placeholder = (
            "def calculate(x, y):\n"
            "    # TODO: implement logic\n"
            "    # ... rest of code goes here ...\n"
            "    return x\n"
        )
        ok, errs = ArtifactValidator.validate_content("app.py", code_with_placeholder)
        self.assertFalse(ok)
        self.assertTrue(any("truncation placeholder" in e for e in errs))

    def test_git_conflict_markers_rejected(self):
        """Git conflict markers (<<<<<<<, =======, >>>>>>>) must be rejected."""
        code_with_conflict = (
            "def greeting():\n"
            "<<<<<<< HEAD\n"
            "    return 'Hello World'\n"
            "=======\n"
            "    return 'Hi World'\n"
            ">>>>>>> branch-a\n"
        )
        ok, errs = ArtifactValidator.validate_content("app.py", code_with_conflict)
        self.assertFalse(ok)
        self.assertTrue(any("git merge conflict markers" in e for e in errs))

    def test_syntax_error_rejected(self):
        """Invalid Python syntax must be rejected with line and column diagnostics."""
        broken_python = (
            "def foo():\n"
            "    if True\n"  # Missing colon
            "        return 1\n"
        )
        ok, errs, diags = ArtifactValidator.validate_syntax("bad.py", broken_python)
        self.assertFalse(ok)
        self.assertEqual(len(errs), 1)
        self.assertTrue("SyntaxError" in errs[0])
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["line"], 2)

    def test_valid_syntax_passes(self):
        """Valid Python code must pass syntax validation."""
        valid_python = (
            "def foo(a: int, b: int) -> int:\n"
            "    \"\"\"Add two numbers.\"\"\"\n"
            "    return a + b\n"
        )
        ok, errs, diags = ArtifactValidator.validate_syntax("good.py", valid_python)
        self.assertTrue(ok)
        self.assertEqual(len(errs), 0)
        self.assertEqual(len(diags), 0)

    def test_forbidden_path_rejected(self):
        """Paths matching forbidden patterns must be rejected."""
        forbidden_test_paths = [
            ".git/config",
            ".git/HEAD",
            ".env",
            ".env.production",
            "secrets.key",
            "cert.pem",
            "package-lock.json",
            "yarn.lock",
            "poetry.lock",
            ".orchestrator/state.db",
        ]
        for fp in forbidden_test_paths:
            self.assertTrue(
                ArtifactValidator.is_path_forbidden(fp),
                f"Path '{fp}' should have been detected as forbidden.",
            )

        # Non-forbidden files
        self.assertFalse(ArtifactValidator.is_path_forbidden("src/main.py"))
        self.assertFalse(ArtifactValidator.is_path_forbidden("tests/test_app.py"))

    def test_disallowed_path_rejected_by_permissions(self):
        """Files outside allowed_write_paths must be rejected."""
        allowed = ["src/api/*", "src/models/*"]
        self.assertTrue(ArtifactValidator.is_path_allowed("src/api/auth.py", allowed))
        self.assertTrue(ArtifactValidator.is_path_allowed("src/models/user.py", allowed))
        self.assertFalse(ArtifactValidator.is_path_allowed("src/database/db.py", allowed))
        self.assertFalse(ArtifactValidator.is_path_allowed("config.py", allowed))

    def test_required_task_outputs_reconciliation(self):
        """Reconciliation must catch missing outputs and forbidden files."""
        # Create one file
        self.workspace.write_file("foo.py", "x = 1\n")

        recon = ArtifactValidator.reconcile_task_outputs(
            workspace=self.workspace,
            required_outputs=["foo.py", "bar.py"],
            modified_files=["foo.py"],
        )
        self.assertFalse(recon.is_valid)
        self.assertIn("bar.py", recon.missing_files)
        self.assertIn("foo.py", recon.verified_files)

    def test_workspace_blocks_git_internals(self):
        """WorkspaceManager.write_file must raise PermissionError for .git internals."""
        with self.assertRaises(PermissionError):
            self.workspace.write_file(".git/config", "[core]\nrepositoryformatversion = 0\n")

    def test_coder_agent_safe_persist_with_artifact_validator(self):
        """CoderAgent must reject invalid files in deliverable and persist only valid ones."""
        coder = CoderAgent(workspace=self.workspace, tool_registry=MagicMock())
        coder.react_loop = MagicMock()
        coder.react_loop.run.return_value = {
            "final_output": {
                "files": [
                    {"filepath": "valid.py", "content": "def hello():\n    return 'world'\n"},
                    {"filepath": "syntax_err.py", "content": "def broken(:\n    pass\n"},
                    {"filepath": ".env", "content": "SECRET_KEY=12345\n"},
                ]
            },
            "history_events": [],
            "turns_taken": 1,
        }

        state = OrchestratorState(user_request="Implement valid and invalid files")
        summary = coder.execute(state, task_info={"outputs": ["valid.py"]})

        # valid.py should exist on disk
        self.assertTrue(self.workspace.file_exists("valid.py"))
        # syntax_err.py and .env should NOT exist on disk
        self.assertFalse(self.workspace.file_exists("syntax_err.py"))
        self.assertFalse(self.workspace.file_exists(".env"))

        # Verify reports in code_summary
        reports = summary.get("artifact_validation", [])
        self.assertEqual(len(reports), 3)
        valid_rep = next(r for r in reports if r["filepath"] == "valid.py")
        self.assertTrue(valid_rep["is_valid"])

        err_rep = next(r for r in reports if r["filepath"] == "syntax_err.py")
        self.assertFalse(err_rep["is_valid"])
        self.assertTrue(any("SyntaxError" in e for e in err_rep["errors"]))

        env_rep = next(r for r in reports if r["filepath"] == ".env")
        self.assertFalse(env_rep["is_valid"])
        self.assertTrue(any("forbidden or protected" in e for e in env_rep["errors"]))

    def test_task_verification_gate_fails_on_invalid_artifacts(self):
        """TaskVerificationGate must fail verification when output contains syntax error or conflict markers."""
        gate = TaskVerificationGate(workspace=self.workspace)

        # Write file with merge conflict
        self.workspace.write_file("conflict.py", "<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> branch\n")

        task = ExecutableTask(
            task_id="T-01",
            objective="Write math code",
            outputs=["conflict.py"],
        )

        v_res = gate.verify_task(task)
        self.assertFalse(v_res.passed)
        self.assertTrue(any("Artifact Content Error" in r for r in v_res.failure_reasons))


if __name__ == "__main__":
    unittest.main()
