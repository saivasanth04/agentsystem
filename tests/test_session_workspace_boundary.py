"""Tests for Session Workspace Boundary and Isolation in Antigravity Agent Orchestrator.

Verifies:
1. resolve_session_workspace strictly binds to the session's workspace path.
2. Cross-session operations are isolated: modifying/checkpointing/rolling back Session A
   does not contaminate Session B.
3. Invalid sessions or non-existent workspace paths fail safely with 404 / 400.
4. Workspace file endpoints and terminal execution strictly respect the session workspace directory.
"""

import os
import shutil
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from agent_orchestrator.server import app, resolve_session_workspace, state_store
from agent_orchestrator.persistence.checkpoint_manager import WorkspaceCheckpointManager
from agent_orchestrator.persistence.artifact_store import ArtifactStore


@pytest.fixture
def temp_dirs():
    """Create temporary directories for Session A and Session B workspaces."""
    temp_root = tempfile.mkdtemp(prefix="agy_test_boundary_")
    ws_a = Path(temp_root) / "ws_session_a"
    ws_b = Path(temp_root) / "ws_session_b"
    ws_a.mkdir(parents=True, exist_ok=True)
    ws_b.mkdir(parents=True, exist_ok=True)

    yield ws_a, ws_b

    shutil.rmtree(temp_root, ignore_errors=True)


@pytest.fixture
def client():
    return TestClient(app)


def _save_test_session(session_id: str, ws_path: Path):
    """Helper to insert a session with a designated workspace path into SQLiteStateStore."""
    conn = state_store._get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO sessions (session_id, user_request, status, verdict, workspace_dir, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(session_id) DO UPDATE SET workspace_dir = excluded.workspace_dir
            """,
            (session_id, "Test task boundary", "IN_PROGRESS", "PASS", str(ws_path)),
        )


def test_resolve_session_workspace_success(temp_dirs):
    """Test resolve_session_workspace successfully returns canonical path for registered session."""
    ws_a, _ = temp_dirs

    session_id = "test-session-bound-001"
    _save_test_session(session_id, ws_a)

    resolved = resolve_session_workspace(session_id)
    assert resolved == ws_a.resolve()
    assert resolved.exists()


def test_resolve_session_workspace_not_found():
    """Test resolve_session_workspace raises 404 when session does not exist."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        resolve_session_workspace("non-existent-session-xyz-999")
    assert exc_info.value.status_code == 404


def test_resolve_session_workspace_path_missing(temp_dirs):
    """Test resolve_session_workspace raises 400 when session points to non-existent folder."""
    from fastapi import HTTPException

    ws_a, _ = temp_dirs
    missing_path = ws_a / "does_not_exist_subfolder"

    session_id = "test-session-missing-dir"
    _save_test_session(session_id, missing_path)

    with pytest.raises(HTTPException) as exc_info:
        resolve_session_workspace(session_id)
    assert exc_info.value.status_code == 400


def test_cross_session_workspace_isolation(temp_dirs):
    """Test that operations on Session A's workspace do NOT affect Session B's workspace."""
    ws_a, ws_b = temp_dirs

    sess_a = "session-boundary-a"
    sess_b = "session-boundary-b"

    _save_test_session(sess_a, ws_a)
    _save_test_session(sess_b, ws_b)

    resolved_a = resolve_session_workspace(sess_a)
    resolved_b = resolve_session_workspace(sess_b)

    assert resolved_a != resolved_b

    # Create file in workspace A
    file_a = resolved_a / "file_in_a.txt"
    file_a.write_text("Hello from Session A", encoding="utf-8")

    # Create file in workspace B
    file_b = resolved_b / "file_in_b.txt"
    file_b.write_text("Hello from Session B", encoding="utf-8")

    assert file_a.exists()
    assert not (resolved_b / "file_in_a.txt").exists()
    assert file_b.exists()
    assert not (resolved_a / "file_in_b.txt").exists()


def test_session_scoped_checkpoint_and_rollback(temp_dirs):
    """Test that creating a checkpoint and rolling back Session A only modifies Workspace A."""
    ws_a, ws_b = temp_dirs

    sess_a = "sess-chk-a"
    sess_b = "sess-chk-b"

    _save_test_session(sess_a, ws_a)
    _save_test_session(sess_b, ws_b)

    artifact_dir = Path(tempfile.mkdtemp(prefix="agy_art_"))
    art_store = ArtifactStore(storage_root=artifact_dir)

    # Initial state
    (ws_a / "index.js").write_text("console.log('v1 in A');", encoding="utf-8")
    (ws_b / "index.js").write_text("console.log('v1 in B');", encoding="utf-8")

    # Checkpoint Session A
    chk_a = WorkspaceCheckpointManager(workspace_dir=str(ws_a), artifact_store=art_store)
    snap_a1 = chk_a.create_snapshot(checkpoint_id=f"snap_{sess_a}_1", metadata={"session_id": sess_a})

    # Modify Workspace A and Workspace B
    (ws_a / "index.js").write_text("console.log('v2 in A - modified');", encoding="utf-8")
    (ws_a / "new_file_a.txt").write_text("Created in A", encoding="utf-8")
    (ws_b / "index.js").write_text("console.log('v2 in B - modified');", encoding="utf-8")

    # Rollback Session A
    res = chk_a.rollback_to_checkpoint(snap_a1.checkpoint_id)
    assert len(res.get("restored_files", [])) > 0 or len(res.get("deleted_files", [])) > 0

    # Workspace A should be restored to v1
    assert (ws_a / "index.js").read_text(encoding="utf-8") == "console.log('v1 in A');"
    assert not (ws_a / "new_file_a.txt").exists()

    # Workspace B MUST NOT have been touched by Session A's rollback
    assert (ws_b / "index.js").read_text(encoding="utf-8") == "console.log('v2 in B - modified');"

    shutil.rmtree(artifact_dir, ignore_errors=True)


def test_api_workspace_files_bound_to_session(client, temp_dirs):
    """Test /api/workspace/files returns files strictly for the requested session."""
    ws_a, ws_b = temp_dirs

    sess_a = "api-ws-a"
    sess_b = "api-ws-b"

    _save_test_session(sess_a, ws_a)
    _save_test_session(sess_b, ws_b)

    (ws_a / "secret_a.py").write_text("# A only", encoding="utf-8")
    (ws_b / "secret_b.py").write_text("# B only", encoding="utf-8")

    # Request files for Session A
    res_a = client.get(f"/api/workspace/files?session_id={sess_a}")
    assert res_a.status_code == 200
    files_a = res_a.json()["tree"]
    file_names_a = [f["name"] for f in files_a]
    assert "secret_a.py" in file_names_a
    assert "secret_b.py" not in file_names_a

    # Request files for Session B
    res_b = client.get(f"/api/workspace/files?session_id={sess_b}")
    assert res_b.status_code == 200
    files_b = res_b.json()["tree"]
    file_names_b = [f["name"] for f in files_b]
    assert "secret_b.py" in file_names_b
    assert "secret_a.py" not in file_names_b


def test_api_terminal_bound_to_session(client, temp_dirs):
    """Test /api/terminal/run executes commands strictly inside the session workspace."""
    ws_a, _ = temp_dirs

    sess_a = "api-term-a"
    _save_test_session(sess_a, ws_a)

    # Run command that writes a file in CWD
    res = client.post(
        "/api/terminal/run",
        json={"command": "python -c \"open('created_by_terminal.txt', 'w').write('success')\"", "session_id": sess_a},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["exit_code"] == 0

    # Verify file was written to Workspace A
    assert (ws_a / "created_by_terminal.txt").exists()
    assert (ws_a / "created_by_terminal.txt").read_text() == "success"
