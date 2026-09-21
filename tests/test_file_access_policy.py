"""
Tests for File Access Policy Engine (Issue #80).
Verifies deterministic access control across allowed_paths, blocked_paths,
read_only_paths, and sensitive_paths across:
- FileAccessPolicy core engine
- WorkspaceManager
- ToolPermissionPolicyEngine
- ReActAgentLoop task-level restrictions
- FilesystemMCPServer
"""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from agent_orchestrator.security.file_access_policy import (
    FileAccessPolicy,
    FileAccessMode,
    FileAccessDecision,
    FileAccessDeniedError,
)
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.runtime.permission_policy import (
    ToolPermissionPolicyEngine,
    PermissionPolicy,
    ToolOperationType,
)
from agent_orchestrator.mcp.servers.filesystem_server import FilesystemMCPServer


# =========================================================================
# 1. FileAccessPolicy Unit Tests
# =========================================================================

def test_file_access_policy_defaults():
    """Verifies default policy configuration and sensitive defaults."""
    policy = FileAccessPolicy()
    assert policy.allowed_paths == ["*"]
    assert any(".git" in p for p in policy.blocked_paths)
    assert any(".env" in p for p in policy.blocked_paths)
    assert any("*.pem" in p for p in policy.blocked_paths)
    assert any("id_rsa" in p for p in policy.blocked_paths)
    assert policy.read_only_paths == []
    assert any("secret" in p for p in policy.sensitive_paths)


def test_default_blocked_paths():
    """Verifies that .git, .env, private keys, and credential directories are blocked by default."""
    policy = FileAccessPolicy()

    blocked_samples = [
        ".git",
        ".git/config",
        ".git/HEAD",
        ".env",
        ".env.local",
        ".env.production",
        "subfolder/.env",
        "subfolder/.env.production",
        "certs/server.pem",
        "certs/private.key",
        ".aws/credentials",
        ".ssh/id_rsa",
        ".ssh/id_ed25519",
        "id_rsa",
    ]

    for path in blocked_samples:
        for mode in [FileAccessMode.READ, FileAccessMode.WRITE, FileAccessMode.DELETE, FileAccessMode.SEARCH]:
            decision = policy.evaluate(path, mode)
            assert not decision.allowed, f"Path '{path}' should be blocked for mode '{mode}'"
            assert "blocked" in decision.reason.lower()
            assert decision.suggested_action is not None


def test_template_file_exemption():
    """Verifies that documentation template files (.env.example, .env.sample, etc.) are allowed for reading."""
    policy = FileAccessPolicy()

    templates = [
        ".env.example",
        ".env.sample",
        ".env.template",
        ".env.defaults",
        "backend/.env.example",
        "nested/deep/.env.template",
    ]

    for path in templates:
        read_decision = policy.evaluate(path, FileAccessMode.READ)
        assert read_decision.allowed, f"Template file '{path}' should be allowed for READ"

        search_decision = policy.evaluate(path, FileAccessMode.SEARCH)
        assert search_decision.allowed, f"Template file '{path}' should be allowed for SEARCH"

        # Writing to template should still work unless in read_only_paths
        write_decision = policy.evaluate(path, FileAccessMode.WRITE)
        assert write_decision.allowed


def test_allowed_paths_confinement():
    """Verifies that setting allowed_paths confines access strictly within specified boundaries."""
    policy = FileAccessPolicy(
        allowed_paths=["src/**", "tests/**", "README.md"],
    )

    # Allowed paths
    assert policy.evaluate("src/main.py", FileAccessMode.READ).allowed
    assert policy.evaluate("src/utils/helpers.py", FileAccessMode.WRITE).allowed
    assert policy.evaluate("tests/test_main.py", FileAccessMode.READ).allowed
    assert policy.evaluate("README.md", FileAccessMode.WRITE).allowed

    # Denied paths
    denied = ["config/db.py", "scripts/deploy.sh", "package.json", "index.html"]
    for path in denied:
        decision = policy.evaluate(path, FileAccessMode.READ)
        assert not decision.allowed, f"Path '{path}' should be denied by allowed_paths"
        assert "outside authorized scope" in decision.reason


def test_read_only_paths_enforcement():
    """Verifies that read_only_paths permits reading and searching, but strictly forbids writes and deletes."""
    policy = FileAccessPolicy(
        allowed_paths=["*"],
        read_only_paths=["docs/**", "contracts/*.json", "legacy/"],
    )

    # Reading allowed
    assert policy.evaluate("docs/architecture.md", FileAccessMode.READ).allowed
    assert policy.evaluate("docs/architecture.md", FileAccessMode.SEARCH).allowed
    assert policy.evaluate("contracts/api.json", FileAccessMode.READ).allowed
    assert policy.evaluate("legacy/old_module.py", FileAccessMode.READ).allowed

    # Writing / Deleting denied
    write_decision = policy.evaluate("docs/architecture.md", FileAccessMode.WRITE)
    assert not write_decision.allowed
    assert "read-only" in write_decision.reason.lower()
    assert write_decision.suggested_action is not None

    delete_decision = policy.evaluate("docs/architecture.md", FileAccessMode.DELETE)
    assert not delete_decision.allowed
    assert "read-only" in delete_decision.reason.lower()

    # Normal paths outside read-only can be written
    assert policy.evaluate("src/main.py", FileAccessMode.WRITE).allowed


def test_sensitive_paths_flagging():
    """Verifies that sensitive_paths tags operations with is_sensitive=True for audit and approval."""
    policy = FileAccessPolicy(
        sensitive_paths=["config/credentials.json", "secrets/**", "auth/keys.txt"],
    )

    sens_decision = policy.evaluate("config/credentials.json", FileAccessMode.READ)
    assert sens_decision.allowed
    assert sens_decision.is_sensitive is True

    normal_decision = policy.evaluate("src/index.js", FileAccessMode.READ)
    assert normal_decision.allowed
    assert normal_decision.is_sensitive is False


def test_path_normalization():
    """Verifies handling of windows backslashes, leading slashes, and relative symbols."""
    policy = FileAccessPolicy(
        allowed_paths=["src/**"],
        read_only_paths=["src/constants/**"],
    )

    # Windows backslashes
    assert policy.evaluate("src\\main.py", FileAccessMode.WRITE).allowed
    assert not policy.evaluate("src\\constants\\config.py", FileAccessMode.WRITE).allowed

    # Leading slashes and ./
    assert policy.evaluate("/src/main.py", FileAccessMode.READ).allowed
    assert policy.evaluate("./src/main.py", FileAccessMode.READ).allowed


def test_policy_serialization_round_trip():
    """Verifies to_dict and from_dict serialization."""
    policy = FileAccessPolicy(
        allowed_paths=["src/**", "tests/**"],
        blocked_paths=[".git/**", ".env*"],
        read_only_paths=["docs/**"],
        sensitive_paths=["secrets/**"],
    )

    d = policy.to_dict()
    restored = FileAccessPolicy.from_dict(d)

    assert restored.allowed_paths == policy.allowed_paths
    assert restored.blocked_paths == policy.blocked_paths
    assert restored.read_only_paths == policy.read_only_paths
    assert restored.sensitive_paths == policy.sensitive_paths


# =========================================================================
# 2. WorkspaceManager Integration Tests
# =========================================================================

def test_workspace_manager_blocked_paths():
    """Verifies WorkspaceManager enforces blocked paths on read, write, delete, and exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = WorkspaceManager(tmpdir)

        # Write to .env should fail
        with pytest.raises(FileAccessDeniedError) as exc_info:
            ws.write_file(".env", "DB_PASSWORD=secret")
        assert exc_info.value.path == ".env"
        assert exc_info.value.mode == FileAccessMode.WRITE

        # Read from .env should fail
        with pytest.raises(FileAccessDeniedError):
            ws.read_file(".env")

        # file_exists on .env should return False
        assert ws.file_exists(".env") is False

        # delete_file on .env should fail
        with pytest.raises(FileAccessDeniedError):
            ws.delete_file(".env")


def test_workspace_manager_read_only_paths():
    """Verifies WorkspaceManager enforces read_only_paths across all mutation primitives."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = WorkspaceManager(
            tmpdir,
            file_access_policy=FileAccessPolicy(read_only_paths=["docs/**"]),
        )

        # Pre-create a doc file directly on disk
        doc_file = Path(tmpdir) / "docs" / "guide.md"
        doc_file.parent.mkdir(parents=True, exist_ok=True)
        doc_file.write_text("# Guide\nInitial content\n", encoding="utf-8")

        # Reading is allowed
        content = ws.read_file("docs/guide.md")
        assert "Initial content" in content

        # write_file fails
        with pytest.raises(FileAccessDeniedError) as exc_info:
            ws.write_file("docs/guide.md", "Overwritten")
        assert "read-only" in str(exc_info.value).lower()

        # delete_file fails
        with pytest.raises(FileAccessDeniedError):
            ws.delete_file("docs/guide.md")

        # replace_file_content fails
        with pytest.raises(FileAccessDeniedError):
            ws.replace_file_content(
                rel_path="docs/guide.md",
                target_content="Initial",
                replacement_content="Modified",
            )

        # insert_lines fails
        with pytest.raises(FileAccessDeniedError):
            ws.insert_lines(
                rel_path="docs/guide.md",
                line_number=1,
                content="New line",
            )

        # delete_lines fails
        with pytest.raises(FileAccessDeniedError):
            ws.delete_lines(
                rel_path="docs/guide.md",
                start_line=1,
                end_line=1,
            )

        # apply_diff_blocks fails
        diff_block = "<<<<<<< SEARCH\nInitial content\n=======\nReplaced\n>>>>>>> REPLACE"
        with pytest.raises(FileAccessDeniedError):
            ws.apply_diff_blocks(
                rel_path="docs/guide.md",
                diff_blocks=diff_block,
            )


def test_workspace_manager_allowed_paths_confinement():
    """Verifies WorkspaceManager restricts writes and reads when allowed_paths is set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = WorkspaceManager(
            tmpdir,
            file_access_policy=FileAccessPolicy(allowed_paths=["src/**"]),
        )

        # Writing within src/ succeeds
        p = ws.write_file("src/app.py", "print('hello')")
        assert p.exists()

        # Writing outside src/ fails
        with pytest.raises(FileAccessDeniedError):
            ws.write_file("scripts/run.sh", "echo 1")


def test_workspace_manager_list_files_filters_blocked():
    """Verifies WorkspaceManager.list_files hides blocked files like .env and .git."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = WorkspaceManager(tmpdir)

        # Create allowed file
        ws.write_file("src/index.js", "console.log('hi');")

        # Manually create files that would be blocked by default
        env_file = Path(tmpdir) / ".env"
        env_file.write_text("SECRET=1", encoding="utf-8")
        git_head = Path(tmpdir) / ".git" / "HEAD"
        git_head.parent.mkdir(parents=True, exist_ok=True)
        git_head.write_text("ref: refs/heads/main", encoding="utf-8")

        files = ws.list_files()
        assert "src/index.js" in files
        assert ".env" not in files
        assert not any(".git" in f for f in files)


# =========================================================================
# 3. ToolPermissionPolicyEngine Integration Tests
# =========================================================================

def test_tool_permission_policy_engine_evaluates_file_access():
    """Verifies that ToolPermissionPolicyEngine evaluates FileAccessPolicy on tool invocations."""
    policy = PermissionPolicy(
        allowed_operations={ToolOperationType.READ, ToolOperationType.WRITE, ToolOperationType.CONTROL},
        read_only_paths=["docs/**"],
        blocked_paths=[".env*", ".git/**"],
    )
    engine = ToolPermissionPolicyEngine(policy=policy)

    # 1. READ tool on blocked path
    decision = engine.evaluate_tool_invocation(
        tool_name="read_file",
        arguments={"filepath": ".env"},
        operation_type=ToolOperationType.READ,
    )
    assert not decision.allowed
    assert "blocked" in decision.reason.lower()

    # 2. WRITE tool on read_only path
    decision = engine.evaluate_tool_invocation(
        tool_name="write_file",
        arguments={"filepath": "docs/architecture.md", "content": "test"},
        operation_type=ToolOperationType.WRITE,
    )
    assert not decision.allowed
    assert "read-only" in decision.reason.lower()

    # 3. WRITE tool on normal path
    decision = engine.evaluate_tool_invocation(
        tool_name="write_file",
        arguments={"filepath": "src/app.py", "content": "test"},
        operation_type=ToolOperationType.WRITE,
    )
    assert decision.allowed


# =========================================================================
# 4. FilesystemMCPServer Integration Tests
# =========================================================================

def test_filesystem_mcp_server_file_access_enforcement():
    """Verifies that FilesystemMCPServer enforces FileAccessPolicy on MCP tool handlers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        server = FilesystemMCPServer(
            root_dir=Path(tmpdir),
            file_access_policy=FileAccessPolicy(read_only_paths=["docs/**"]),
        )

        # 1. Attempt read on .env (default blocked)
        res = server.read_file(path=".env")
        assert not res["success"]
        assert "denied" in res["error"].lower()

        # 2. Attempt write on .env (default blocked)
        res = server.write_file(path=".env", content="SECRET=1")
        assert not res["success"]
        assert "denied" in res["error"].lower()

        # 3. Pre-create a docs file
        doc_path = Path(tmpdir) / "docs" / "api.md"
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text("API Reference\n", encoding="utf-8")

        # 4. Read on docs file succeeds
        res = server.read_file(path="docs/api.md")
        assert res["success"]
        assert "API Reference" in res["content"]

        # 5. Write on docs file is denied (read-only)
        res = server.write_file(path="docs/api.md", content="Overwritten")
        assert not res["success"]
        assert "read-only" in res["error"].lower()

        # 6. Delete on docs file is denied (read-only)
        res = server.delete_file(path="docs/api.md")
        assert not res["success"]
        assert "read-only" in res["error"].lower()

        # 7. replace_file_content on docs file is denied (read-only)
        res = server.replace_file_content(
            path="docs/api.md",
            target_content="API",
            replacement_content="MODIFIED",
        )
        assert not res["success"]
        assert "read-only" in res["error"].lower()

        # 8. list_directory hides blocked files
        env_file = Path(tmpdir) / ".env"
        env_file.write_text("SECRET=1", encoding="utf-8")
        list_res = server.list_directory()
        assert list_res["success"]
        entry_paths = [e["path"] for e in list_res["entries"]]
        assert not any(".env" in p for p in entry_paths)


# =========================================================================
# 5. ReActAgentLoop Task-Level Path Enforcement
# =========================================================================

def test_react_loop_check_permission_task_paths():
    """Verifies that ReActAgentLoop._check_permission enforces task-level FileAccessPolicy constraints."""
    from agent_orchestrator.runtime.react_loop import ReActAgentLoop
    loop = ReActAgentLoop(llm=MagicMock(), tool_registry=MagicMock())

    # 1. Task with allowed_paths=["src/**"]
    task_perms = {"allowed_paths": ["src/**"]}
    denial = loop._check_permission(
        tool_name="write_file",
        args={"filepath": "config/db.py", "content": "secret"},
        permissions=task_perms,
        agent_name="CODER",
    )
    assert denial is not None
    assert "outside authorized scope" in denial.lower()

    # Allowed inside src/**
    allowed = loop._check_permission(
        tool_name="write_file",
        args={"filepath": "src/db.py", "content": "secret"},
        permissions=task_perms,
        agent_name="CODER",
    )
    assert allowed is None

    # 2. Task with read_only_paths=["docs/**"]
    ro_perms = {"read_only_paths": ["docs/**"]}
    ro_denial = loop._check_permission(
        tool_name="write_file",
        args={"filepath": "docs/architecture.md", "content": "new"},
        permissions=ro_perms,
        agent_name="CODER",
    )
    assert ro_denial is not None
    assert "read-only" in ro_denial.lower()
