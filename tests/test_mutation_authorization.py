"""
Tests for Mutation Authorization Engine (Issue #84).
Verifies task scope enforcement, destructive shrinkage protection, pre-commit syntax validation,
protected file classification, prohibited extensions, and workspace integration.
"""
import os
from pathlib import Path
import shutil
import tempfile
import pytest

from agent_orchestrator.security.mutation_authorizer import (
    MutationAuthorizer,
    MutationPolicy,
    MutationType,
    MutationRiskLevel,
    MutationDecision,
    MutationAuthorizationError,
)
from agent_orchestrator.tools.workspace import WorkspaceManager


@pytest.fixture
def temp_workspace():
    """Creates a temporary workspace with starter files."""
    tmp_dir = tempfile.mkdtemp(prefix="test_mutation_auth_")
    ws_path = Path(tmp_dir)

    src_dir = ws_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    
    # 25-line source file for shrinkage tests
    long_content = "\n".join([f"def func_{i}():\n    return {i}" for i in range(25)])
    (src_dir / "calculator.py").write_text(long_content, encoding="utf-8")

    yield ws_path
    shutil.rmtree(tmp_dir, ignore_errors=True)


class TestMutationAuthorizerPolicy:
    """Tests core authorization rules in MutationAuthorizer."""

    def test_prohibited_extensions_blocked(self):
        authorizer = MutationAuthorizer()
        res = authorizer.authorize_mutation(
            filepath="bin/trojan.exe",
            new_content="binary blob",
        )
        assert res.allowed is False
        assert res.decision == MutationDecision.DENIED
        assert res.risk_level == MutationRiskLevel.CRITICAL
        assert "Prohibited file extension" in res.reason

    def test_forbidden_write_paths_blocked(self):
        policy = MutationPolicy(forbidden_write_paths=["*.secret", "config/master.key"])
        authorizer = MutationAuthorizer(policy=policy)

        res = authorizer.authorize_mutation(
            filepath="secrets/app.secret",
            new_content="private data",
        )
        assert res.allowed is False
        assert "matches forbidden write pattern" in res.reason

    def test_task_scope_enforcement_blocks_out_of_scope(self):
        policy = MutationPolicy(enforce_task_scope=True)
        authorizer = MutationAuthorizer(policy=policy)

        task_scope = {
            "inputs": ["src/calculator.py"],
            "outputs": ["src/formatter.py"],
        }

        # Out-of-scope file
        res = authorizer.authorize_mutation(
            filepath="src/auth/jwt.py",
            new_content="def auth(): pass",
            task_scope=task_scope,
        )
        assert res.allowed is False
        assert "outside the declared task scope" in res.reason

        # In-scope file
        res_valid = authorizer.authorize_mutation(
            filepath="src/formatter.py",
            new_content="def format_code(): pass",
            task_scope=task_scope,
        )
        assert res_valid.allowed is True

    def test_pre_commit_syntax_validation_blocks_broken_syntax(self):
        authorizer = MutationAuthorizer(policy=MutationPolicy(validate_syntax_pre_commit=True))

        # Broken Python syntax
        broken_code = "def broken_func(\n    print('missing closing paren')"
        res_py = authorizer.authorize_mutation(
            filepath="src/broken.py",
            new_content=broken_code,
        )
        assert res_py.allowed is False
        assert res_py.syntax_valid is False
        assert "Python AST SyntaxError" in res_py.reason

        # Broken JSON syntax
        broken_json = '{"key": "value", missing_quotes}'
        res_json = authorizer.authorize_mutation(
            filepath="config.json",
            new_content=broken_json,
        )
        assert res_json.allowed is False
        assert "JSON DecodeError" in res_json.reason

        # Valid Python & JSON
        valid_py = authorizer.authorize_mutation(filepath="src/ok.py", new_content="x = 10\nprint(x)")
        assert valid_py.allowed is True
        assert valid_py.syntax_valid is True

    def test_destructive_shrinkage_protection(self):
        policy = MutationPolicy(
            prevent_destructive_shrinkage=True,
            max_allowed_shrinkage_ratio=0.70,
            min_lines_for_shrinkage_check=10,
        )
        authorizer = MutationAuthorizer(policy=policy)

        old_code = "\n".join([f"line_{i} = {i}" for i in range(30)])
        tiny_replacement = "line_0 = 0\n"  # 1 line (96.7% shrinkage)

        # Unapproved destructive write
        res = authorizer.authorize_mutation(
            filepath="src/large_file.py",
            new_content=tiny_replacement,
            old_content=old_code,
            is_approved=False,
        )
        assert res.allowed is False
        assert "Destructive shrinkage detected" in res.reason
        assert res.shrinkage_ratio > 0.70

        # Approved destructive write
        res_approved = authorizer.authorize_mutation(
            filepath="src/large_file.py",
            new_content=tiny_replacement,
            old_content=old_code,
            is_approved=True,
        )
        assert res_approved.allowed is True

    def test_protected_patterns_and_approval_requirement(self):
        policy = MutationPolicy(require_approval_for_high_risk=True)
        authorizer = MutationAuthorizer(policy=policy)

        # Protected CI workflow
        res = authorizer.authorize_mutation(
            filepath=".github/workflows/ci.yml",
            new_content="name: CI\non: push",
            is_approved=False,
        )
        assert res.allowed is False
        assert res.decision == MutationDecision.APPROVAL_REQUIRED
        assert res.risk_level == MutationRiskLevel.HIGH_RISK

        # Approved CI workflow
        res_app = authorizer.authorize_mutation(
            filepath=".github/workflows/ci.yml",
            new_content="name: CI\non: push",
            is_approved=True,
        )
        assert res_app.allowed is True


class TestWorkspaceManagerMutationAuthorization:
    """Tests WorkspaceManager integration with MutationAuthorizer."""

    def test_workspace_write_enforces_mutation_authorizer(self, temp_workspace):
        policy = MutationPolicy(
            validate_syntax_pre_commit=True,
            forbidden_write_paths=["*.key"],
        )
        ws = WorkspaceManager(
            root_dir=temp_workspace,
            mutation_authorizer=policy,
        )

        # 1. Prohibited extension / forbidden path raises MutationAuthorizationError
        with pytest.raises(MutationAuthorizationError) as exc_info:
            ws.write_file("secrets/app.key", "supersecretkey")
        assert "Prohibited file extension" in str(exc_info.value) or "forbidden write pattern" in str(exc_info.value)

        # 2. Syntax error raises MutationAuthorizationError
        with pytest.raises(MutationAuthorizationError) as exc_info_syn:
            ws.write_file("src/bad.py", "def foo(:\n  pass")
        assert "Python AST SyntaxError" in str(exc_info_syn.value)

        # 3. Valid write succeeds
        res_path = ws.write_file("src/good.py", "def foo():\n    return 42\n")
        assert res_path.exists()
        assert "return 42" in res_path.read_text(encoding="utf-8")

    def test_workspace_write_backward_compatibility_without_authorizer(self, temp_workspace):
        ws = WorkspaceManager(root_dir=temp_workspace)
        # Without mutation authorizer explicitly configured, standard writes proceed normally
        res_path = ws.write_file("notes.txt", "Some note text")
        assert res_path.exists()
        assert res_path.read_text(encoding="utf-8") == "Some note text"
