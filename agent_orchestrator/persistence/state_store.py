"""
SQLiteStateStore: Embedded SQLite State Persistence Engine with Write-Ahead Logging (WAL).
Persists sessions, tasks, DAG execution trees, task attempts, tool observations,
workspace checkpoints, and agent-to-agent message buses.
"""
from datetime import datetime
import hashlib
import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from ..state import OrchestratorState, TaskStatus, ReviewVerdict, AgentMessage, ReplanRecord
from ..runtime.task_graph import (
    TaskDAG,
    ExecutableTask,
    TaskState,
    TaskPermissions,
    RetryPolicy,
    TokenUsage,
    ObservationRecord,
    CheckpointRecord,
    TaskAttemptRecord,
    ArtifactRecord,
)
from ..runtime.messaging import StructuredMessage, MessageType


class SQLiteStateStore:
    """
    Thread-safe, zero-external-dependency SQLite storage engine for multi-agent workflows.
    Operates in WAL (Write-Ahead Logging) mode with foreign keys and index optimizations.
    """

    def __init__(self, db_path: Optional[str] = None, workspace_dir: Optional[str] = None):
        if db_path:
            self.db_path = db_path
        elif workspace_dir:
            dot_dir = os.path.join(workspace_dir, ".orchestrator")
            os.makedirs(dot_dir, exist_ok=True)
            self.db_path = os.path.join(dot_dir, "orchestrator_state.db")
        else:
            self.db_path = ":memory:"

        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        self._local = threading.local()
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Returns a thread-local SQLite connection with row_factory enabled."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            if self.db_path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            self._local.conn = conn
        return self._local.conn

    def close(self):
        """Closes thread-local database connection if open."""
        with self._lock:
            if hasattr(self._local, "conn") and self._local.conn is not None:
                try:
                    self._local.conn.close()
                except Exception:
                    pass
                self._local.conn = None

    def _init_db(self):
        """Initializes database schema tables and indices."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_request TEXT NOT NULL,
                    status TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    project_profile_json TEXT,
                    environment_profile_json TEXT,
                    task_understanding_json TEXT,
                    plan_output_json TEXT,
                    specification_output_json TEXT,
                    architecture_output_json TEXT,
                    code_output_json TEXT,
                    test_output_json TEXT,
                    review_output_json TEXT,
                    replan_history_json TEXT,
                    current_iteration INTEGER DEFAULT 0,
                    max_iterations INTEGER DEFAULT 3,
                    total_tokens_json TEXT,
                    total_cost_usd REAL DEFAULT 0.0,
                    total_duration_seconds REAL DEFAULT 0.0,
                    reproducibility_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    parent_task_id TEXT,
                    objective TEXT NOT NULL,
                    state TEXT NOT NULL,
                    dependencies_json TEXT,
                    required_capabilities_json TEXT,
                    required_tools_json TEXT,
                    preferred_skills_json TEXT,
                    inputs_json TEXT,
                    outputs_json TEXT,
                    acceptance_tests_json TEXT,
                    owner_agent TEXT,
                    max_turns INTEGER DEFAULT 15,
                    current_retry INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 2,
                    permissions_json TEXT,
                    result_data_json TEXT,
                    artifacts_json TEXT,
                    error_message TEXT,
                    token_usage_json TEXT,
                    cost_usd REAL DEFAULT 0.0,
                    duration_seconds REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    PRIMARY KEY (session_id, task_id),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS task_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    duration_seconds REAL DEFAULT 0.0,
                    tools_used_json TEXT,
                    skills_used_json TEXT,
                    errors_json TEXT,
                    verification_result_json TEXT,
                    token_usage_json TEXT,
                    FOREIGN KEY (session_id, task_id) REFERENCES tasks(session_id, task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    attempt_id TEXT,
                    turn INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    input_args_json TEXT,
                    output_result_json TEXT,
                    is_error INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'SUCCESS',
                    duration_seconds REAL DEFAULT 0.0,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (session_id, task_id) REFERENCES tasks(session_id, task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    file_hashes_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (session_id, task_id) REFERENCES tasks(session_id, task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    attempt_id TEXT,
                    name TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    category TEXT DEFAULT 'reports',
                    uri_or_path TEXT,
                    content_hash TEXT,
                    size_bytes INTEGER DEFAULT 0,
                    mime_type TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id, task_id) REFERENCES tasks(session_id, task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS task_step_transcripts (
                    transcript_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    thought TEXT,
                    tool_name TEXT,
                    tool_args_json TEXT,
                    tool_result_json TEXT,
                    messages_json TEXT,
                    observations_json TEXT,
                    checkpoint_id TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (session_id, task_id) REFERENCES tasks(session_id, task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    payload_json TEXT,
                    correlation_id TEXT,
                    in_reply_to TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
                CREATE INDEX IF NOT EXISTS idx_attempts_task ON task_attempts(session_id, task_id);
                CREATE INDEX IF NOT EXISTS idx_observations_task ON observations(session_id, task_id);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_task ON checkpoints(session_id, task_id);
                CREATE INDEX IF NOT EXISTS idx_transcripts_task ON task_step_transcripts(session_id, task_id);
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
                CREATE INDEX IF NOT EXISTS idx_messages_recipient ON messages(session_id, recipient);
                CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id);
                CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(session_id, task_id);

                CREATE TABLE IF NOT EXISTS file_changes (
                    change_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    filepath TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    before_hash TEXT,
                    after_hash TEXT,
                    lines_added INTEGER DEFAULT 0,
                    lines_removed INTEGER DEFAULT 0,
                    changed_lines INTEGER DEFAULT 0,
                    diff TEXT,
                    changed_symbols_json TEXT,
                    agent_name TEXT,
                    agent_version TEXT,
                    model_name TEXT,
                    prompt_hash TEXT,
                    skill_name TEXT,
                    tool_source TEXT,
                    git_commit_sha TEXT,
                    provenance_json TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (session_id, task_id) REFERENCES tasks(session_id, task_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_file_changes_task ON file_changes(session_id, task_id);
                CREATE INDEX IF NOT EXISTS idx_file_changes_file ON file_changes(session_id, filepath);

                CREATE TABLE IF NOT EXISTS rollback_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_id TEXT,
                    checkpoint_id TEXT NOT NULL,
                    reason TEXT,
                    restored_files_json TEXT,
                    deleted_files_json TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_rollback_session ON rollback_events(session_id);

                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    task_id TEXT,
                    transaction_id TEXT,
                    tool_name TEXT NOT NULL,
                    filepath TEXT,
                    arguments_json TEXT,
                    before_hash TEXT,
                    after_hash TEXT,
                    result_json TEXT,
                    status TEXT NOT NULL,
                    no_op INTEGER DEFAULT 0,
                    provenance_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_operations_task ON operations(task_id);
                CREATE INDEX IF NOT EXISTS idx_operations_session ON operations(session_id);
                CREATE INDEX IF NOT EXISTS idx_operations_tx ON operations(transaction_id);

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_id TEXT,
                    event_type TEXT NOT NULL,
                    agent_name TEXT,
                    payload_json TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
                CREATE INDEX IF NOT EXISTS idx_events_type ON events(session_id, event_type);
                CREATE INDEX IF NOT EXISTS idx_events_task ON events(session_id, task_id);

                CREATE TABLE IF NOT EXISTS telemetry_snapshots (
                    session_id TEXT PRIMARY KEY,
                    telemetry_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_telemetry_session ON telemetry_snapshots(session_id);

                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    metadata_json TEXT,
                    total_spans INTEGER DEFAULT 0,
                    duration_seconds REAL DEFAULT 0.0,
                    trace_tree_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id);
                """)

                try:
                    conn.execute("ALTER TABLE artifacts ADD COLUMN category TEXT DEFAULT 'reports'")
                except Exception:
                    pass
                try:
                    conn.execute("ALTER TABLE artifacts ADD COLUMN mime_type TEXT")
                except Exception:
                    pass
                try:
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_category ON artifacts(category)")
                except Exception:
                    pass
                try:
                    conn.execute("ALTER TABLE task_attempts ADD COLUMN model_used TEXT")
                except Exception:
                    pass
                try:
                    conn.execute("ALTER TABLE task_attempts ADD COLUMN llm_latency_seconds REAL DEFAULT 0.0")
                except Exception:
                    pass
                try:
                    conn.execute("ALTER TABLE sessions ADD COLUMN project_profile_json TEXT")
                except Exception:
                    pass
                try:
                    conn.execute("ALTER TABLE sessions ADD COLUMN environment_profile_json TEXT")
                except Exception:
                    pass
                try:
                    conn.execute("ALTER TABLE sessions ADD COLUMN reproducibility_json TEXT")
                except Exception:
                    pass
                for col in [
                    "agent_name TEXT",
                    "agent_version TEXT",
                    "model_name TEXT",
                    "prompt_hash TEXT",
                    "skill_name TEXT",
                    "tool_source TEXT",
                    "git_commit_sha TEXT",
                    "provenance_json TEXT",
                ]:
                    try:
                        conn.execute(f"ALTER TABLE file_changes ADD COLUMN {col}")
                    except Exception:
                        pass
                try:
                    conn.execute("ALTER TABLE operations ADD COLUMN provenance_json TEXT")
                except Exception:
                    pass
                try:
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_changes_agent ON file_changes(agent_name)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_changes_model ON file_changes(model_name)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_changes_prompt ON file_changes(prompt_hash)")
                except Exception:
                    pass

    # ==========================================
    # SESSION CRUD
    # ==========================================
    def save_session(self, session_id: str, state: OrchestratorState):
        """Persists full OrchestratorState and all associated DAG tasks to SQLite."""
        with self._lock:
            conn = self._get_connection()
            now_iso = datetime.now().isoformat()
            repro_json = None
            if state.execution_snapshot:
                if hasattr(state.execution_snapshot, "to_dict"):
                    repro_json = json.dumps(state.execution_snapshot.to_dict(), default=str)
                elif isinstance(state.execution_snapshot, dict):
                    repro_json = json.dumps(state.execution_snapshot, default=str)
                elif isinstance(state.execution_snapshot, str):
                    repro_json = state.execution_snapshot

            with conn:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        session_id, user_request, status, verdict,
                        project_profile_json, environment_profile_json,
                        task_understanding_json, plan_output_json, specification_output_json,
                        architecture_output_json, code_output_json, test_output_json,
                        review_output_json, replan_history_json, current_iteration,
                        max_iterations, total_tokens_json, total_cost_usd,
                        total_duration_seconds, reproducibility_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        status = excluded.status,
                        verdict = excluded.verdict,
                        project_profile_json = excluded.project_profile_json,
                        environment_profile_json = excluded.environment_profile_json,
                        task_understanding_json = excluded.task_understanding_json,
                        plan_output_json = excluded.plan_output_json,
                        specification_output_json = excluded.specification_output_json,
                        architecture_output_json = excluded.architecture_output_json,
                        code_output_json = excluded.code_output_json,
                        test_output_json = excluded.test_output_json,
                        review_output_json = excluded.review_output_json,
                        replan_history_json = excluded.replan_history_json,
                        current_iteration = excluded.current_iteration,
                        max_iterations = excluded.max_iterations,
                        total_tokens_json = excluded.total_tokens_json,
                        total_cost_usd = excluded.total_cost_usd,
                        total_duration_seconds = excluded.total_duration_seconds,
                        reproducibility_json = excluded.reproducibility_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        session_id,
                        state.user_request,
                        state.status.value if isinstance(state.status, TaskStatus) else str(state.status),
                        state.verdict.value if isinstance(state.verdict, ReviewVerdict) else str(state.verdict),
                        json.dumps(state.project_profile, default=str) if state.project_profile else None,
                        json.dumps(state.environment_profile, default=str) if state.environment_profile else None,
                        json.dumps(state.task_understanding, default=str) if state.task_understanding else None,
                        json.dumps(state.plan_output, default=str) if state.plan_output else None,
                        json.dumps(state.specification_output, default=str) if state.specification_output else None,
                        json.dumps(state.architecture_output, default=str) if state.architecture_output else None,
                        json.dumps(state.code_output, default=str) if state.code_output else None,
                        json.dumps(state.test_output, default=str) if state.test_output else None,
                        json.dumps(state.review_output, default=str) if state.review_output else None,
                        json.dumps([r.__dict__ if hasattr(r, "__dict__") else r for r in state.replan_history], default=str),
                        state.current_iteration,
                        state.max_iterations,
                        json.dumps(state.total_token_usage.to_dict()),
                        state.total_cost_usd,
                        state.total_duration_seconds,
                        repro_json,
                        state.created_at or now_iso,
                        now_iso,
                    ),
                )

                # Persist tasks if DAG present
                if state.task_dag:
                    for task in state.task_dag.list_tasks():
                        self._save_task_internal(conn, session_id, task)

                # Persist messages
                for msg in state.messages:
                    if isinstance(msg, AgentMessage):
                        struct_msg = StructuredMessage(
                            sender=msg.agent_name,
                            recipient="*",
                            message_type=MessageType.BROADCAST,
                            content=msg.content,
                            payload=msg.structured_data or {},
                            timestamp=msg.timestamp,
                        )
                        self._save_message_internal(conn, session_id, struct_msg)

    def load_session(self, session_id: str) -> Optional[OrchestratorState]:
        """Loads and re-hydrates OrchestratorState and TaskDAG from SQLite."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if not row:
                return None

            state = OrchestratorState(user_request=row["user_request"])
            state.status = TaskStatus(row["status"]) if row["status"] in TaskStatus.__members__ else TaskStatus.PENDING
            state.verdict = ReviewVerdict(row["verdict"]) if row["verdict"] in ReviewVerdict.__members__ else ReviewVerdict.UNDECIDED
            state.project_profile = json.loads(row["project_profile_json"]) if "project_profile_json" in row.keys() and row["project_profile_json"] else None
            state.environment_profile = json.loads(row["environment_profile_json"]) if "environment_profile_json" in row.keys() and row["environment_profile_json"] else None
            state.task_understanding = json.loads(row["task_understanding_json"]) if row["task_understanding_json"] else None
            state.plan_output = json.loads(row["plan_output_json"]) if row["plan_output_json"] else None
            state.specification_output = json.loads(row["specification_output_json"]) if row["specification_output_json"] else None
            state.architecture_output = json.loads(row["architecture_output_json"]) if row["architecture_output_json"] else None
            state.code_output = json.loads(row["code_output_json"]) if row["code_output_json"] else None
            state.test_output = json.loads(row["test_output_json"]) if row["test_output_json"] else None
            state.review_output = json.loads(row["review_output_json"]) if row["review_output_json"] else None
            state.current_iteration = row["current_iteration"]
            state.max_iterations = row["max_iterations"]
            state.total_cost_usd = float(row["total_cost_usd"] or 0.0)
            state.total_duration_seconds = float(row["total_duration_seconds"] or 0.0)
            state.created_at = row["created_at"]
            state.updated_at = row["updated_at"]

            if "reproducibility_json" in row.keys() and row["reproducibility_json"]:
                try:
                    from ..reproducibility.manifest import ExecutionSnapshot
                    state.execution_snapshot = ExecutionSnapshot.from_json(row["reproducibility_json"])
                except Exception:
                    try:
                        state.execution_snapshot = json.loads(row["reproducibility_json"])
                    except Exception:
                        state.execution_snapshot = None

            if row["total_tokens_json"]:
                state.total_token_usage = TokenUsage.from_dict(json.loads(row["total_tokens_json"]))

            if row["replan_history_json"]:
                raw_replan = json.loads(row["replan_history_json"])
                state.replan_history = [
                    ReplanRecord(
                        iteration=r.get("iteration", 0),
                        trigger_reason=r.get("trigger_reason", ""),
                        feedback_summary=r.get("feedback_summary", ""),
                        remediation_plan=r.get("remediation_plan", []),
                        timestamp=r.get("timestamp", datetime.now().isoformat()),
                    )
                    for r in raw_replan
                ]

            # Re-hydrate tasks into TaskDAG
            tasks = self.load_tasks(session_id)
            if tasks:
                state.task_dag = TaskDAG(tasks)
                state.task_decomposition = state.task_dag.to_list()

            # Re-hydrate messages
            msgs = self.get_messages(session_id)
            state.messages = [
                AgentMessage(
                    agent_name=m.sender,
                    stage=m.topic,
                    content=m.content,
                    structured_data=m.payload,
                    timestamp=m.timestamp,
                )
                for m in msgs
            ]

            return state

    def save_execution_snapshot(self, session_id: str, snapshot: Any):
        """Saves execution snapshot to session record."""
        with self._lock:
            conn = self._get_connection()
            repro_json = None
            if hasattr(snapshot, "to_dict"):
                repro_json = json.dumps(snapshot.to_dict(), default=str)
            elif isinstance(snapshot, dict):
                repro_json = json.dumps(snapshot, default=str)
            elif isinstance(snapshot, str):
                repro_json = snapshot

            with conn:
                conn.execute(
                    "UPDATE sessions SET reproducibility_json = ?, updated_at = ? WHERE session_id = ?",
                    (repro_json, datetime.now().isoformat(), session_id),
                )

    def get_execution_snapshot(self, session_id: str) -> Optional[Any]:
        """Retrieves and deserializes execution snapshot for a session."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute("SELECT reproducibility_json FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if not row or not row["reproducibility_json"]:
                return None
            try:
                from ..reproducibility.manifest import ExecutionSnapshot
                return ExecutionSnapshot.from_json(row["reproducibility_json"])
            except Exception:
                try:
                    return json.loads(row["reproducibility_json"])
                except Exception:
                    return None

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Lists all stored orchestration sessions with summary metrics."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute("SELECT session_id, user_request, status, verdict, total_cost_usd, total_duration_seconds, created_at, updated_at FROM sessions ORDER BY updated_at DESC").fetchall()
            return [dict(r) for r in rows]

    def delete_session(self, session_id: str):
        """Deletes session and cascades to tasks, attempts, observations, and checkpoints."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

    # ==========================================
    # TASK CRUD
    # ==========================================
    def save_task(self, session_id: str, task: ExecutableTask):
        """Saves a single ExecutableTask, attempts, observations, and checkpoints."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                self._save_task_internal(conn, session_id, task)

    def _save_task_internal(self, conn: sqlite3.Connection, session_id: str, task: ExecutableTask):
        now_iso = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO sessions (session_id, user_request, status, verdict, created_at, updated_at)
            VALUES (?, ?, 'PENDING', 'UNDECIDED', ?, ?)
            ON CONFLICT(session_id) DO NOTHING
            """,
            (session_id, "Active session", task.created_at or now_iso, now_iso),
        )
        conn.execute(
            """
            INSERT INTO tasks (
                session_id, task_id, parent_task_id, objective, state,
                dependencies_json, required_capabilities_json, required_tools_json,
                preferred_skills_json, inputs_json, outputs_json, acceptance_tests_json,
                owner_agent, max_turns, current_retry, max_retries, permissions_json,
                result_data_json, artifacts_json, error_message, token_usage_json,
                cost_usd, duration_seconds, created_at, started_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, task_id) DO UPDATE SET
                parent_task_id = excluded.parent_task_id,
                objective = excluded.objective,
                state = excluded.state,
                dependencies_json = excluded.dependencies_json,
                required_capabilities_json = excluded.required_capabilities_json,
                required_tools_json = excluded.required_tools_json,
                preferred_skills_json = excluded.preferred_skills_json,
                inputs_json = excluded.inputs_json,
                outputs_json = excluded.outputs_json,
                acceptance_tests_json = excluded.acceptance_tests_json,
                owner_agent = excluded.owner_agent,
                max_turns = excluded.max_turns,
                current_retry = excluded.current_retry,
                max_retries = excluded.max_retries,
                permissions_json = excluded.permissions_json,
                result_data_json = excluded.result_data_json,
                artifacts_json = excluded.artifacts_json,
                error_message = excluded.error_message,
                token_usage_json = excluded.token_usage_json,
                cost_usd = excluded.cost_usd,
                duration_seconds = excluded.duration_seconds,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at
            """,
            (
                session_id,
                task.task_id,
                task.parent_task_id,
                task.objective,
                task.state.value if isinstance(task.state, TaskState) else str(task.state),
                json.dumps(task.dependencies),
                json.dumps(task.required_capabilities),
                json.dumps(task.required_tools),
                json.dumps(task.preferred_skills),
                json.dumps(task.inputs),
                json.dumps(task.outputs),
                json.dumps(task.acceptance_tests),
                task.owner_agent,
                task.max_turns,
                task.retry_policy.current_retry if task.retry_policy else 0,
                task.retry_policy.max_retries if task.retry_policy else 2,
                json.dumps(task.permissions.to_dict() if hasattr(task.permissions, "to_dict") else task.permissions),
                json.dumps(task.result_data, default=str) if task.result_data else None,
                json.dumps(task.artifacts, default=str),
                task.error_message,
                json.dumps(task.token_usage.to_dict()),
                task.cost,
                task.duration_seconds,
                task.created_at,
                task.started_at,
                task.completed_at,
            ),
        )

        # Persist attempts
        for attempt in task.attempts:
            attempt_id = getattr(attempt, "attempt_id", None) or f"att-{session_id}-{task.task_id}-{attempt.attempt_number}"
            conn.execute(
                """
                INSERT INTO task_attempts (
                    attempt_id, session_id, task_id, attempt_number, agent_name,
                    status, started_at, completed_at, duration_seconds,
                    tools_used_json, skills_used_json, errors_json,
                    verification_result_json, token_usage_json,
                    model_used, llm_latency_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_id) DO UPDATE SET
                    status = excluded.status,
                    completed_at = excluded.completed_at,
                    duration_seconds = excluded.duration_seconds,
                    tools_used_json = excluded.tools_used_json,
                    skills_used_json = excluded.skills_used_json,
                    errors_json = excluded.errors_json,
                    verification_result_json = excluded.verification_result_json,
                    token_usage_json = excluded.token_usage_json,
                    model_used = excluded.model_used,
                    llm_latency_seconds = excluded.llm_latency_seconds
                """,
                (
                    attempt_id,
                    session_id,
                    task.task_id,
                    attempt.attempt_number,
                    attempt.agent_name,
                    attempt.status,
                    attempt.started_at,
                    attempt.completed_at,
                    attempt.duration_seconds,
                    json.dumps(attempt.tools_used),
                    json.dumps(attempt.skills_used),
                    json.dumps(attempt.errors),
                    json.dumps(attempt.verification_result, default=str) if attempt.verification_result else None,
                    json.dumps(attempt.token_usage.to_dict()),
                    getattr(attempt, "model_used", None),
                    float(getattr(attempt, "llm_latency_seconds", 0.0) or 0.0),
                ),
            )

        # Persist observations
        for obs in task.observations:
            obs_id = getattr(obs, "observation_id", None) or f"obs-{session_id}-{task.task_id}-{obs.turn}-{obs.tool_name}-{hash(obs.timestamp)}"
            obs_att_id = getattr(obs, "attempt_id", None)
            if not obs_att_id and task.attempts:
                obs_att_id = getattr(task.attempts[-1], "attempt_id", None) or f"att-{session_id}-{task.task_id}-{task.attempts[-1].attempt_number}"
            conn.execute(
                """
                INSERT INTO observations (
                    observation_id, session_id, task_id, attempt_id, turn,
                    tool_name, input_args_json, output_result_json, is_error,
                    status, duration_seconds, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id) DO NOTHING
                """,
                (
                    obs_id,
                    session_id,
                    task.task_id,
                    obs_att_id,
                    obs.turn,
                    obs.tool_name,
                    json.dumps(obs.input_args, default=str),
                    json.dumps(obs.output_result, default=str),
                    1 if obs.is_error else 0,
                    "ERROR" if obs.is_error else "SUCCESS",
                    obs.duration_seconds,
                    obs.timestamp,
                ),
            )

        # Persist checkpoints
        for ckpt in task.checkpoints:
            conn.execute(
                """
                INSERT INTO checkpoints (
                    checkpoint_id, session_id, task_id, stage, file_hashes_json, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    file_hashes_json = excluded.file_hashes_json
                """,
                (
                    ckpt.checkpoint_id,
                    session_id,
                    task.task_id,
                    ckpt.stage,
                    json.dumps(ckpt.file_hashes),
                    ckpt.timestamp,
                ),
            )

        # Persist typed artifacts
        for art in getattr(task, "typed_artifacts", []):
            conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, session_id, task_id, attempt_id, name,
                    artifact_type, category, uri_or_path, content_hash, size_bytes,
                    mime_type, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    attempt_id = excluded.attempt_id,
                    name = excluded.name,
                    artifact_type = excluded.artifact_type,
                    category = excluded.category,
                    uri_or_path = excluded.uri_or_path,
                    content_hash = excluded.content_hash,
                    size_bytes = excluded.size_bytes,
                    mime_type = excluded.mime_type,
                    metadata_json = excluded.metadata_json
                """,
                (
                    art.artifact_id,
                    session_id,
                    task.task_id,
                    art.attempt_id,
                    art.name,
                    art.artifact_type,
                    getattr(art, "category", "reports"),
                    art.uri_or_path,
                    art.content_hash,
                    art.size_bytes,
                    getattr(art, "mime_type", None),
                    json.dumps(art.metadata, default=str),
                    art.created_at,
                ),
            )

    def load_tasks(self, session_id: str) -> List[ExecutableTask]:
        """Loads all tasks for a session with nested attempts, observations, checkpoints, and artifacts."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute("SELECT * FROM tasks WHERE session_id = ? ORDER BY task_id ASC", (session_id,)).fetchall()
            tasks: List[ExecutableTask] = []

            for r in rows:
                t_id = r["task_id"]
                state_enum = TaskState(r["state"]) if r["state"] in TaskState.__members__ else TaskState.PENDING

                # Load attempts
                att_rows = conn.execute(
                    "SELECT * FROM task_attempts WHERE session_id = ? AND task_id = ? ORDER BY attempt_number ASC",
                    (session_id, t_id),
                ).fetchall()
                attempts: List[TaskAttemptRecord] = []
                for ar in att_rows:
                    attempts.append(TaskAttemptRecord(
                        attempt_id=ar["attempt_id"],
                        execution_id=ar["session_id"],
                        attempt_number=ar["attempt_number"],
                        agent_name=ar["agent_name"],
                        started_at=ar["started_at"],
                        completed_at=ar["completed_at"],
                        status=ar["status"],
                        tools_used=json.loads(ar["tools_used_json"]) if ar["tools_used_json"] else [],
                        skills_used=json.loads(ar["skills_used_json"]) if ar["skills_used_json"] else [],
                        errors=json.loads(ar["errors_json"]) if ar["errors_json"] else [],
                        verification_result=json.loads(ar["verification_result_json"]) if ar["verification_result_json"] else None,
                        token_usage=TokenUsage.from_dict(json.loads(ar["token_usage_json"])) if ar["token_usage_json"] else TokenUsage(),
                        duration_seconds=float(ar["duration_seconds"] or 0.0),
                        model_used=ar["model_used"] if "model_used" in ar.keys() else None,
                        llm_latency_seconds=float(ar["llm_latency_seconds"] or 0.0) if "llm_latency_seconds" in ar.keys() else 0.0,
                    ))

                # Load observations
                obs_rows = conn.execute(
                    "SELECT * FROM observations WHERE session_id = ? AND task_id = ? ORDER BY turn ASC, timestamp ASC",
                    (session_id, t_id),
                ).fetchall()
                observations: List[ObservationRecord] = []
                for obr in obs_rows:
                    observations.append(ObservationRecord(
                        observation_id=obr["observation_id"],
                        attempt_id=obr["attempt_id"],
                        execution_id=obr["session_id"],
                        turn=obr["turn"],
                        tool_name=obr["tool_name"],
                        input_args=json.loads(obr["input_args_json"]) if obr["input_args_json"] else {},
                        output_result=json.loads(obr["output_result_json"]) if obr["output_result_json"] else None,
                        is_error=bool(obr["is_error"]),
                        duration_seconds=float(obr["duration_seconds"] or 0.0),
                        timestamp=obr["timestamp"],
                    ))

                # Load checkpoints
                ckpt_rows = conn.execute(
                    "SELECT * FROM checkpoints WHERE session_id = ? AND task_id = ? ORDER BY timestamp ASC",
                    (session_id, t_id),
                ).fetchall()
                checkpoints = [
                    CheckpointRecord(
                        checkpoint_id=ckr["checkpoint_id"],
                        stage=ckr["stage"],
                        file_hashes=json.loads(ckr["file_hashes_json"]) if ckr["file_hashes_json"] else {},
                        timestamp=ckr["timestamp"],
                    )
                    for ckr in ckpt_rows
                ]

                # Load typed artifacts
                art_rows = conn.execute(
                    "SELECT * FROM artifacts WHERE session_id = ? AND task_id = ? ORDER BY created_at ASC",
                    (session_id, t_id),
                ).fetchall()
                typed_artifacts: List[ArtifactRecord] = []
                for art_r in art_rows:
                    cat_val = art_r["category"] if "category" in art_r.keys() and art_r["category"] else "reports"
                    mime_val = art_r["mime_type"] if "mime_type" in art_r.keys() else None
                    typed_artifacts.append(ArtifactRecord(
                        artifact_id=art_r["artifact_id"],
                        name=art_r["name"],
                        artifact_type=art_r["artifact_type"],
                        category=cat_val,
                        uri_or_path=art_r["uri_or_path"],
                        content_hash=art_r["content_hash"],
                        size_bytes=art_r["size_bytes"] or 0,
                        mime_type=mime_val,
                        metadata=json.loads(art_r["metadata_json"]) if art_r["metadata_json"] else {},
                        task_id=art_r["task_id"],
                        attempt_id=art_r["attempt_id"],
                        execution_id=art_r["session_id"],
                        created_at=art_r["created_at"],
                    ))

                # Reconstitute ExecutableTask
                perm_dict = json.loads(r["permissions_json"]) if r["permissions_json"] else {}
                task = ExecutableTask(
                    task_id=t_id,
                    objective=r["objective"],
                    parent_task_id=r["parent_task_id"],
                    execution_id=session_id,
                    state=state_enum,
                    dependencies=json.loads(r["dependencies_json"]) if r["dependencies_json"] else [],
                    required_capabilities=json.loads(r["required_capabilities_json"]) if r["required_capabilities_json"] else [],
                    required_tools=json.loads(r["required_tools_json"]) if r["required_tools_json"] else [],
                    preferred_skills=json.loads(r["preferred_skills_json"]) if r["preferred_skills_json"] else [],
                    inputs=json.loads(r["inputs_json"]) if r["inputs_json"] else [],
                    outputs=json.loads(r["outputs_json"]) if r["outputs_json"] else [],
                    acceptance_tests=json.loads(r["acceptance_tests_json"]) if r["acceptance_tests_json"] else [],
                    owner_agent=r["owner_agent"],
                    max_turns=r["max_turns"],
                    retry_policy=RetryPolicy(max_retries=r["max_retries"], current_retry=r["current_retry"]),
                    permissions=TaskPermissions.from_dict(perm_dict) if perm_dict else TaskPermissions(),
                    result_data=json.loads(r["result_data_json"]) if r["result_data_json"] else None,
                    artifacts=json.loads(r["artifacts_json"]) if r["artifacts_json"] else [],
                    typed_artifacts=typed_artifacts,
                    error_message=r["error_message"],
                    token_usage=TokenUsage.from_dict(json.loads(r["token_usage_json"])) if r["token_usage_json"] else TokenUsage(),
                    cost=float(r["cost_usd"] or 0.0),
                    duration_seconds=float(r["duration_seconds"] or 0.0),
                    created_at=r["created_at"],
                    started_at=r["started_at"],
                    completed_at=r["completed_at"],
                    attempts=attempts,
                    observations=observations,
                    checkpoints=checkpoints,
                    tools_used=list({t for a in attempts for t in a.tools_used}),
                    skills_used=list({s for a in attempts for s in a.skills_used}),
                    errors=list({e for a in attempts for e in a.errors}),
                )
                tasks.append(task)

            return tasks

    # ==========================================
    # ARTIFACT CRUD
    # ==========================================
    def save_artifact(self, artifact: ArtifactRecord, session_id: str, task_id: str = "") -> None:
        """Persists a single ArtifactRecord to the database."""
        eff_task_id = (task_id or artifact.task_id or "").strip() or "root"
        with self._lock:
            conn = self._get_connection()
            with conn:
                # Ensure session stub exists
                conn.execute(
                    """
                    INSERT INTO sessions (session_id, user_request, status, verdict, created_at, updated_at)
                    VALUES (?, ?, 'PENDING', 'UNDECIDED', ?, ?)
                    ON CONFLICT(session_id) DO NOTHING
                    """,
                    (session_id, "Active session", artifact.created_at, artifact.created_at),
                )
                # Ensure task stub exists to satisfy foreign key
                conn.execute(
                    """
                    INSERT INTO tasks (session_id, task_id, objective, state, created_at)
                    VALUES (?, ?, 'Artifact producer task', 'COMPLETED', ?)
                    ON CONFLICT(session_id, task_id) DO NOTHING
                    """,
                    (session_id, eff_task_id, artifact.created_at),
                )

                conn.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, session_id, task_id, attempt_id, name,
                        artifact_type, category, uri_or_path, content_hash, size_bytes,
                        mime_type, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(artifact_id) DO UPDATE SET
                        attempt_id = excluded.attempt_id,
                        name = excluded.name,
                        artifact_type = excluded.artifact_type,
                        category = excluded.category,
                        uri_or_path = excluded.uri_or_path,
                        content_hash = excluded.content_hash,
                        size_bytes = excluded.size_bytes,
                        mime_type = excluded.mime_type,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        artifact.artifact_id,
                        session_id,
                        eff_task_id,
                        artifact.attempt_id,
                        artifact.name,
                        artifact.artifact_type,
                        getattr(artifact, "category", "reports"),
                        artifact.uri_or_path,
                        artifact.content_hash,
                        artifact.size_bytes,
                        getattr(artifact, "mime_type", None),
                        json.dumps(artifact.metadata, default=str),
                        artifact.created_at,
                    ),
                )

    def get_artifact(self, artifact_id: str) -> Optional[ArtifactRecord]:
        """Retrieves a single ArtifactRecord by its artifact_id."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
            if not row:
                return None
            cat_val = row["category"] if "category" in row.keys() and row["category"] else "reports"
            mime_val = row["mime_type"] if "mime_type" in row.keys() else None
            return ArtifactRecord(
                artifact_id=row["artifact_id"],
                name=row["name"],
                artifact_type=row["artifact_type"],
                category=cat_val,
                uri_or_path=row["uri_or_path"],
                content_hash=row["content_hash"],
                size_bytes=row["size_bytes"] or 0,
                mime_type=mime_val,
                metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                task_id=row["task_id"],
                attempt_id=row["attempt_id"],
                execution_id=row["session_id"],
                created_at=row["created_at"],
            )

    def list_artifacts(
        self,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[ArtifactRecord]:
        """Queries stored artifacts with optional filters."""
        query = "SELECT * FROM artifacts WHERE 1=1"
        params: List[Any] = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY created_at ASC"

        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(query, tuple(params)).fetchall()
            artifacts: List[ArtifactRecord] = []
            for row in rows:
                cat_val = row["category"] if "category" in row.keys() and row["category"] else "reports"
                mime_val = row["mime_type"] if "mime_type" in row.keys() else None
                artifacts.append(ArtifactRecord(
                    artifact_id=row["artifact_id"],
                    name=row["name"],
                    artifact_type=row["artifact_type"],
                    category=cat_val,
                    uri_or_path=row["uri_or_path"],
                    content_hash=row["content_hash"],
                    size_bytes=row["size_bytes"] or 0,
                    mime_type=mime_val,
                    metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                    task_id=row["task_id"],
                    attempt_id=row["attempt_id"],
                    execution_id=row["session_id"],
                    created_at=row["created_at"],
                ))
            return artifacts

    # ==========================================
    # MESSAGE BUS CRUD
    # ==========================================
    def save_message(self, session_id: str, message: StructuredMessage):
        """Dual-writes a message to SQLite message history table."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                self._save_message_internal(conn, session_id, message)

    def _save_message_internal(self, conn: sqlite3.Connection, session_id: str, message: StructuredMessage):
        # Ensure session stub exists so foreign key constraints are satisfied
        conn.execute(
            """
            INSERT INTO sessions (session_id, user_request, status, verdict, created_at, updated_at)
            VALUES (?, ?, 'PENDING', 'UNDECIDED', ?, ?)
            ON CONFLICT(session_id) DO NOTHING
            """,
            (session_id, "Active session", message.timestamp, message.timestamp),
        )
        conn.execute(
            """
            INSERT INTO messages (
                message_id, session_id, sender, recipient, topic, message_type,
                content, payload_json, correlation_id, in_reply_to, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO NOTHING
            """,
            (
                message.message_id,
                session_id,
                message.sender,
                message.recipient,
                message.topic,
                message.message_type.value if hasattr(message.message_type, "value") else str(message.message_type),
                message.content,
                json.dumps(message.payload, default=str),
                message.correlation_id,
                message.in_reply_to,
                message.timestamp,
            ),
        )

    def get_messages(self, session_id: str, recipient: Optional[str] = None) -> List[StructuredMessage]:
        """Retrieves structured message history for a session or recipient inbox."""
        with self._lock:
            conn = self._get_connection()
            if recipient:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? AND (recipient = ? OR recipient = '*' OR recipient = 'all') ORDER BY timestamp ASC",
                    (session_id, recipient),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
                    (session_id,),
                ).fetchall()

            return [
                StructuredMessage(
                    message_id=r["message_id"],
                    sender=r["sender"],
                    recipient=r["recipient"],
                    topic=r["topic"],
                    message_type=r["message_type"],
                    content=r["content"],
                    payload=json.loads(r["payload_json"]) if r["payload_json"] else {},
                    correlation_id=r["correlation_id"],
                    in_reply_to=r["in_reply_to"],
                    timestamp=r["timestamp"],
                )
                for r in rows
            ]

    # ==========================================
    # STEP-LEVEL CHECKPOINTS & TRANSCRIPT CRUD
    # ==========================================
    def save_task_step(
        self,
        session_id: str,
        task_id: str,
        turn: int,
        stage: str,
        thought: Optional[str] = None,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        tool_result: Optional[Any] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        observations: Optional[List[ObservationRecord]] = None,
        checkpoint_id: Optional[str] = None,
    ):
        """Persists a real-time micro-step checkpoint for a running task turn."""
        import uuid
        with self._lock:
            conn = self._get_connection()
            now_iso = datetime.now().isoformat()
            transcript_id = f"tr-{session_id}-{task_id}-t{turn}-{uuid.uuid4().hex[:6]}"
            with conn:
                # Ensure session & task rows exist
                conn.execute(
                    """
                    INSERT INTO sessions (session_id, user_request, status, verdict, created_at, updated_at)
                    VALUES (?, ?, 'PENDING', 'UNDECIDED', ?, ?)
                    ON CONFLICT(session_id) DO NOTHING
                    """,
                    (session_id, f"Session {session_id}", now_iso, now_iso),
                )
                conn.execute(
                    """
                    INSERT INTO tasks (session_id, task_id, objective, state, created_at)
                    VALUES (?, ?, ?, 'RUNNING', ?)
                    ON CONFLICT(session_id, task_id) DO UPDATE SET state = 'RUNNING'
                    """,
                    (session_id, task_id, f"Task {task_id}", now_iso),
                )
                conn.execute(
                    """
                    INSERT INTO task_step_transcripts (
                        transcript_id, session_id, task_id, turn, stage, thought,
                        tool_name, tool_args_json, tool_result_json,
                        messages_json, observations_json, checkpoint_id, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        transcript_id,
                        session_id,
                        task_id,
                        turn,
                        stage,
                        thought,
                        tool_name,
                        json.dumps(tool_args, default=str) if tool_args is not None else None,
                        json.dumps(tool_result, default=str) if tool_result is not None else None,
                        json.dumps(messages, default=str) if messages is not None else None,
                        json.dumps([o.to_dict() if hasattr(o, "to_dict") else o for o in observations], default=str) if observations is not None else None,
                        checkpoint_id,
                        now_iso,
                    ),
                )

    def load_task_steps(self, session_id: str, task_id: str) -> List[Dict[str, Any]]:
        """Loads all recorded step transcripts for a given task ordered by turn and timestamp."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT * FROM task_step_transcripts WHERE session_id = ? AND task_id = ? ORDER BY turn ASC, timestamp ASC",
                (session_id, task_id),
            ).fetchall()
            return [
                {
                    "transcript_id": r["transcript_id"],
                    "session_id": r["session_id"],
                    "task_id": r["task_id"],
                    "turn": r["turn"],
                    "stage": r["stage"],
                    "thought": r["thought"],
                    "tool_name": r["tool_name"],
                    "tool_args": json.loads(r["tool_args_json"]) if r["tool_args_json"] else None,
                    "tool_result": json.loads(r["tool_result_json"]) if r["tool_result_json"] else None,
                    "messages": json.loads(r["messages_json"]) if r["messages_json"] else None,
                    "observations": json.loads(r["observations_json"]) if r["observations_json"] else None,
                    "checkpoint_id": r["checkpoint_id"],
                    "timestamp": r["timestamp"],
                }
                for r in rows
            ]

    def load_latest_task_transcript(self, session_id: str, task_id: str) -> Optional[Dict[str, Any]]:
        """Returns the most recent step transcript containing full messages and observations."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                """
                SELECT * FROM task_step_transcripts
                WHERE session_id = ? AND task_id = ? AND messages_json IS NOT NULL
                ORDER BY turn DESC, timestamp DESC
                LIMIT 1
                """,
                (session_id, task_id),
            ).fetchone()
            if not row:
                return None
            return {
                "transcript_id": row["transcript_id"],
                "session_id": row["session_id"],
                "task_id": row["task_id"],
                "turn": row["turn"],
                "stage": row["stage"],
                "thought": row["thought"],
                "tool_name": row["tool_name"],
                "tool_args": json.loads(row["tool_args_json"]) if row["tool_args_json"] else None,
                "tool_result": json.loads(row["tool_result_json"]) if row["tool_result_json"] else None,
                "messages": json.loads(row["messages_json"]) if row["messages_json"] else [],
                "observations": [
                    ObservationRecord.from_dict(o) if isinstance(o, dict) else o
                    for o in (json.loads(row["observations_json"]) if row["observations_json"] else [])
                ],
                "checkpoint_id": row["checkpoint_id"],
                "timestamp": row["timestamp"],
            }

    def load_single_task(self, session_id: str, task_id: str) -> Optional[ExecutableTask]:
        """Loads a single task object with its attempts, checkpoints, and observations."""
        tasks = self.load_tasks(session_id)
        for t in tasks:
            if t.task_id == task_id:
                return t
        return None

    def save_observation(self, session_id: str, task_id: str, observation: ObservationRecord, attempt_id: Optional[str] = None):
        """Persists a single ObservationRecord to the observations table in real time."""
        import uuid
        with self._lock:
            conn = self._get_connection()
            now_iso = datetime.now().isoformat()
            obs_id = f"obs-{session_id}-{task_id}-{uuid.uuid4().hex[:8]}"
            with conn:
                conn.execute(
                    """
                    INSERT INTO sessions (session_id, user_request, status, verdict, created_at, updated_at)
                    VALUES (?, ?, 'PENDING', 'UNDECIDED', ?, ?)
                    ON CONFLICT(session_id) DO NOTHING
                    """,
                    (session_id, f"Session {session_id}", now_iso, now_iso),
                )
                conn.execute(
                    """
                    INSERT INTO tasks (session_id, task_id, objective, state, created_at)
                    VALUES (?, ?, ?, 'RUNNING', ?)
                    ON CONFLICT(session_id, task_id) DO NOTHING
                    """,
                    (session_id, task_id, f"Task {task_id}", now_iso),
                )
                conn.execute(
                    """
                    INSERT INTO observations (
                        observation_id, session_id, task_id, attempt_id, turn,
                        tool_name, input_args_json, output_result_json,
                        is_error, status, duration_seconds, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        obs_id,
                        session_id,
                        task_id,
                        attempt_id,
                        getattr(observation, "turn", 1),
                        observation.tool_name,
                        json.dumps(observation.input_args, default=str),
                        json.dumps(observation.output_result, default=str),
                        1 if getattr(observation, "is_error", False) else 0,
                        getattr(observation, "status", "SUCCESS"),
                        getattr(observation, "duration_seconds", 0.0),
                        getattr(observation, "timestamp", now_iso),
                    ),
                )

    def find_session_for_task(self, task_id: str) -> Optional[str]:
        """Looks up the session_id for a given task_id across all recorded tasks."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT session_id FROM tasks WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if row:
                return row["session_id"]
            # Fallback check in task_step_transcripts
            row_tr = conn.execute(
                "SELECT session_id FROM task_step_transcripts WHERE task_id = ? ORDER BY timestamp DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if row_tr:
                return row_tr["session_id"]
            return None

    # ==========================================
    # FILE CHANGE TRACKING CRUD
    # ==========================================
    def save_file_change(
        self,
        session_id: str,
        task_id: str,
        change: Union[Any, Dict[str, Any]],
    ) -> str:
        """Persists a file change record (hashes, diff, symbol changes) to the database."""
        import uuid
        with self._lock:
            conn = self._get_connection()
            now_iso = datetime.now().isoformat()
            change_id = f"chg-{session_id}-{task_id}-{uuid.uuid4().hex[:8]}"

            if hasattr(change, "to_dict"):
                c_dict = change.to_dict()
            elif isinstance(change, dict):
                c_dict = change
            else:
                c_dict = {}

            filepath = getattr(change, "filepath", c_dict.get("filepath", ""))
            operation = getattr(change, "change_type", c_dict.get("change_type", c_dict.get("operation", "MODIFIED")))
            before_hash = getattr(change, "before_hash", c_dict.get("before_hash"))
            after_hash = getattr(change, "after_hash", c_dict.get("after_hash"))
            lines_added = getattr(change, "lines_added", c_dict.get("lines_added", 0))
            lines_removed = getattr(change, "lines_removed", c_dict.get("lines_removed", 0))
            changed_lines_list = getattr(change, "changed_line_numbers", c_dict.get("changed_line_numbers", []))
            changed_lines = len(changed_lines_list) if isinstance(changed_lines_list, list) else int(c_dict.get("changed_lines", 0))
            diff_text = getattr(change, "diff", c_dict.get("diff", ""))
            changed_symbols = getattr(change, "changed_symbols", c_dict.get("changed_symbols", []))
            if isinstance(changed_symbols, list):
                symbols_serialized = [s.to_dict() if hasattr(s, "to_dict") else s for s in changed_symbols]
            else:
                symbols_serialized = []
            timestamp = getattr(change, "timestamp", c_dict.get("timestamp", now_iso))

            # Extract Provenance (Issue #94)
            prov = getattr(change, "provenance", c_dict.get("provenance"))
            if not prov:
                try:
                    from ..reproducibility.provenance import provenance_context
                    prov = provenance_context.get_current()
                except Exception:
                    prov = None

            agent_name = None
            agent_version = None
            model_name = None
            prompt_hash = None
            skill_name = None
            tool_source = None
            git_commit_sha = None
            prov_json = None

            if prov:
                if hasattr(prov, "to_dict"):
                    p_d = prov.to_dict()
                    agent_name = p_d.get("agent_name")
                    agent_version = p_d.get("agent_version")
                    model_name = p_d.get("model_name")
                    prompt_hash = p_d.get("prompt_hash")
                    skill_name = p_d.get("skill_name")
                    tool_source = p_d.get("tool_source")
                    git_commit_sha = p_d.get("git_commit_sha")
                    prov_json = json.dumps(p_d, default=str)
                elif isinstance(prov, dict):
                    agent_name = prov.get("agent_name")
                    agent_version = prov.get("agent_version")
                    model_name = prov.get("model_name")
                    prompt_hash = prov.get("prompt_hash")
                    skill_name = prov.get("skill_name")
                    tool_source = prov.get("tool_source")
                    git_commit_sha = prov.get("git_commit_sha")
                    prov_json = json.dumps(prov, default=str)

            with conn:
                conn.execute(
                    """
                    INSERT INTO sessions (session_id, user_request, status, verdict, created_at, updated_at)
                    VALUES (?, ?, 'PENDING', 'UNDECIDED', ?, ?)
                    ON CONFLICT(session_id) DO NOTHING
                    """,
                    (session_id, f"Session {session_id}", now_iso, now_iso),
                )
                conn.execute(
                    """
                    INSERT INTO tasks (session_id, task_id, objective, state, created_at)
                    VALUES (?, ?, ?, 'RUNNING', ?)
                    ON CONFLICT(session_id, task_id) DO NOTHING
                    """,
                    (session_id, task_id, f"Task {task_id}", now_iso),
                )
                conn.execute(
                    """
                    INSERT INTO file_changes (
                        change_id, session_id, task_id, filepath, operation,
                        before_hash, after_hash, lines_added, lines_removed,
                        changed_lines, diff, changed_symbols_json,
                        agent_name, agent_version, model_name, prompt_hash,
                        skill_name, tool_source, git_commit_sha, provenance_json,
                        timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        change_id,
                        session_id,
                        task_id,
                        filepath,
                        operation,
                        before_hash,
                        after_hash,
                        lines_added,
                        lines_removed,
                        changed_lines,
                        diff_text,
                        json.dumps(symbols_serialized, default=str),
                        agent_name,
                        agent_version,
                        model_name,
                        prompt_hash,
                        skill_name,
                        tool_source,
                        git_commit_sha,
                        prov_json,
                        timestamp,
                    ),
                )
            return change_id

    def save_change_manifest(
        self,
        session_id: str,
        task_id: str,
        manifest: Union[Any, Dict[str, Any]],
    ) -> List[str]:
        """Saves all file change records contained within a ChangeManifest."""
        if hasattr(manifest, "file_changes"):
            f_changes = manifest.file_changes
            if isinstance(f_changes, dict):
                records = list(f_changes.values())
            else:
                records = list(f_changes)
        elif isinstance(manifest, dict) and "file_changes" in manifest:
            f_changes = manifest["file_changes"]
            if isinstance(f_changes, dict):
                records = list(f_changes.values())
            else:
                records = list(f_changes)
        else:
            records = []

        change_ids = []
        for rec in records:
            cid = self.save_file_change(session_id, task_id, rec)
            change_ids.append(cid)
        return change_ids

    def get_task_changes(self, session_id: str, task_id: str) -> List[Dict[str, Any]]:
        """Retrieves all recorded file changes for a specific task."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                """
                SELECT * FROM file_changes
                WHERE session_id = ? AND task_id = ?
                ORDER BY timestamp ASC
                """,
                (session_id, task_id),
            ).fetchall()
            return [
                self._format_file_change_row(r)
                for r in rows
            ]

    def _format_file_change_row(self, r: Any) -> Dict[str, Any]:
        """Helper to format a file_changes row into a dict with provenance."""
        keys = r.keys() if hasattr(r, "keys") else []
        prov = None
        if "provenance_json" in keys and r["provenance_json"]:
            try:
                prov = json.loads(r["provenance_json"])
            except Exception:
                pass

        return {
            "change_id": r["change_id"],
            "session_id": r["session_id"],
            "task_id": r["task_id"],
            "filepath": r["filepath"],
            "operation": r["operation"],
            "before_hash": r["before_hash"],
            "after_hash": r["after_hash"],
            "lines_added": r["lines_added"],
            "lines_removed": r["lines_removed"],
            "changed_lines": r["changed_lines"],
            "diff": r["diff"],
            "changed_symbols": json.loads(r["changed_symbols_json"]) if "changed_symbols_json" in keys and r["changed_symbols_json"] else [],
            "agent_name": r["agent_name"] if "agent_name" in keys else None,
            "agent_version": r["agent_version"] if "agent_version" in keys else None,
            "model_name": r["model_name"] if "model_name" in keys else None,
            "prompt_hash": r["prompt_hash"] if "prompt_hash" in keys else None,
            "skill_name": r["skill_name"] if "skill_name" in keys else None,
            "tool_source": r["tool_source"] if "tool_source" in keys else None,
            "git_commit_sha": r["git_commit_sha"] if "git_commit_sha" in keys else None,
            "provenance": prov,
            "timestamp": r["timestamp"],
        }

    def get_file_change(self, change_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single file change record by change_id."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute("SELECT * FROM file_changes WHERE change_id = ?", (change_id,)).fetchone()
            if not row:
                return None
            return self._format_file_change_row(row)

    def get_session_changes(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieves all recorded file changes across all tasks in a session."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                """
                SELECT * FROM file_changes
                WHERE session_id = ?
                ORDER BY timestamp ASC
                """,
                (session_id,),
            ).fetchall()
            return [
                self._format_file_change_row(r)
                for r in rows
            ]

    def get_file_attribution(
        self,
        filepath: str,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Agent Blame: Retrieves complete historical attribution timeline for a specific file,
        showing which agent version, model, prompt hash, skill, and git commit authored each change.
        """
        with self._lock:
            conn = self._get_connection()
            if session_id:
                rows = conn.execute(
                    """
                    SELECT * FROM file_changes
                    WHERE filepath = ? AND session_id = ?
                    ORDER BY timestamp ASC
                    """,
                    (filepath, session_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM file_changes
                    WHERE filepath = ?
                    ORDER BY timestamp ASC
                    """,
                    (filepath,),
                ).fetchall()
            return [self._format_file_change_row(r) for r in rows]

    def get_changes_by_model(
        self,
        model_name: str,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves all file changes produced by a specific LLM model."""
        with self._lock:
            conn = self._get_connection()
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM file_changes WHERE model_name = ? AND session_id = ? ORDER BY timestamp ASC",
                    (model_name, session_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM file_changes WHERE model_name = ? ORDER BY timestamp ASC",
                    (model_name,),
                ).fetchall()
            return [self._format_file_change_row(r) for r in rows]

    def get_changes_by_prompt(
        self,
        prompt_hash: str,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves all file changes produced under a specific prompt template hash."""
        with self._lock:
            conn = self._get_connection()
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM file_changes WHERE prompt_hash = ? AND session_id = ? ORDER BY timestamp ASC",
                    (prompt_hash, session_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM file_changes WHERE prompt_hash = ? ORDER BY timestamp ASC",
                    (prompt_hash,),
                ).fetchall()
            return [self._format_file_change_row(r) for r in rows]

    def get_changes_by_skill(
        self,
        skill_name: str,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves all file changes authored while a specific skill was active."""
        with self._lock:
            conn = self._get_connection()
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM file_changes WHERE skill_name = ? AND session_id = ? ORDER BY timestamp ASC",
                    (skill_name, session_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM file_changes WHERE skill_name = ? ORDER BY timestamp ASC",
                    (skill_name,),
                ).fetchall()
            return [self._format_file_change_row(r) for r in rows]

    def get_changes_by_agent(
        self,
        agent_name: str,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves all file changes authored by a specific agent persona."""
        with self._lock:
            conn = self._get_connection()
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM file_changes WHERE UPPER(agent_name) = UPPER(?) AND session_id = ? ORDER BY timestamp ASC",
                    (agent_name, session_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM file_changes WHERE UPPER(agent_name) = UPPER(?) ORDER BY timestamp ASC",
                    (agent_name,),
                ).fetchall()
            return [self._format_file_change_row(r) for r in rows]

    # ==========================================
    # ROLLBACK AUDIT TRAIL CRUD
    # ==========================================
    def save_rollback_event(
        self,
        session_id: str,
        checkpoint_id: str,
        task_id: Optional[str] = None,
        reason: str = "",
        trigger_reason: Optional[str] = None,
        restored_files: Optional[List[str]] = None,
        deleted_files: Optional[List[str]] = None,
    ) -> str:
        """Persists a rollback event audit record to the database."""
        effective_reason = trigger_reason or reason or ""
        import uuid
        with self._lock:
            conn = self._get_connection()
            now_iso = datetime.now().isoformat()
            event_id = f"rb-{session_id}-{uuid.uuid4().hex[:8]}"

            with conn:
                conn.execute(
                    """
                    INSERT INTO sessions (session_id, user_request, status, verdict, created_at, updated_at)
                    VALUES (?, ?, 'PENDING', 'UNDECIDED', ?, ?)
                    ON CONFLICT(session_id) DO NOTHING
                    """,
                    (session_id, f"Session {session_id}", now_iso, now_iso),
                )
                conn.execute(
                    """
                    INSERT INTO rollback_events (
                        event_id, session_id, task_id, checkpoint_id,
                        reason, restored_files_json, deleted_files_json, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        session_id,
                        task_id,
                        checkpoint_id,
                        effective_reason,
                        json.dumps(restored_files or []),
                        json.dumps(deleted_files or []),
                        now_iso,
                    ),
                )
            return event_id

    def get_rollback_events(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieves all recorded rollback events for a session."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                """
                SELECT * FROM rollback_events
                WHERE session_id = ?
                ORDER BY timestamp ASC
                """,
                (session_id,),
            ).fetchall()
            return [
                {
                    "event_id": r["event_id"],
                    "session_id": r["session_id"],
                    "task_id": r["task_id"],
                    "checkpoint_id": r["checkpoint_id"],
                    "reason": r["reason"],
                    "restored_files": json.loads(r["restored_files_json"]) if r["restored_files_json"] else [],
                    "deleted_files": json.loads(r["deleted_files_json"]) if r["deleted_files_json"] else [],
                    "timestamp": r["timestamp"],
                }
                for r in rows
            ]

    # ==========================================
    # OPERATIONS CRUD (Idempotency & Auditing)
    # ==========================================
    def save_operation(
        self,
        op: Any,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> str:
        """Persists an OperationRecord to SQLite for distributed deduplication and auditing."""
        now_iso = datetime.now().isoformat()
        raw_op_id = getattr(op, "operation_id", None)
        op_id = str(raw_op_id) if isinstance(raw_op_id, (str, int)) else str(uuid.uuid4())
        raw_tool = getattr(op, "tool_name", "unknown")
        tool_name = str(raw_tool) if isinstance(raw_tool, (str, int)) else "unknown"
        raw_fp = getattr(op, "filepath", None)
        filepath = str(raw_fp) if isinstance(raw_fp, (str, int)) else None
        raw_args = getattr(op, "arguments", {})
        arguments = raw_args if isinstance(raw_args, (dict, list, str, int, float, bool)) else {}
        raw_bh = getattr(op, "before_hash", None)
        before_hash = str(raw_bh) if isinstance(raw_bh, (str, int)) else None
        raw_ah = getattr(op, "after_hash", None)
        after_hash = str(raw_ah) if isinstance(raw_ah, (str, int)) else None
        result = getattr(op, "result", None)
        raw_st = getattr(op, "status", "COMMITTED")
        status = str(raw_st) if isinstance(raw_st, (str, int)) else "COMMITTED"
        no_op = 1 if getattr(op, "no_op", False) is True else 0
        raw_tx = getattr(op, "transaction_id", None)
        tx_id = str(raw_tx) if isinstance(raw_tx, (str, int)) else None
        raw_ts = getattr(op, "timestamp", None)
        created_at = str(raw_ts) if isinstance(raw_ts, (str, int)) else now_iso

        prov = getattr(op, "provenance", None)
        if not prov:
            try:
                from ..reproducibility.provenance import provenance_context
                prov = provenance_context.get_current()
            except Exception:
                prov = None

        prov_json = None
        if prov:
            if hasattr(prov, "to_dict"):
                prov_json = json.dumps(prov.to_dict(), default=str)
            elif isinstance(prov, dict):
                prov_json = json.dumps(prov, default=str)

        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO operations (
                        operation_id, session_id, task_id, transaction_id,
                        tool_name, filepath, arguments_json, before_hash,
                        after_hash, result_json, status, no_op, provenance_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(operation_id) DO UPDATE SET
                        status = excluded.status,
                        no_op = excluded.no_op,
                        after_hash = excluded.after_hash,
                        result_json = excluded.result_json,
                        provenance_json = COALESCE(excluded.provenance_json, operations.provenance_json),
                        transaction_id = COALESCE(excluded.transaction_id, operations.transaction_id)
                    """,
                    (
                        op_id,
                        session_id,
                        task_id,
                        tx_id,
                        tool_name,
                        filepath,
                        json.dumps(arguments, default=str),
                        before_hash,
                        after_hash,
                        json.dumps(result, default=str) if result is not None else None,
                        status,
                        no_op,
                        prov_json,
                        created_at,
                    ),
                )
        return op_id

    def get_operation(self, operation_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single operation by ID."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if not row:
                return None
            keys = row.keys()
            prov = None
            if "provenance_json" in keys and row["provenance_json"]:
                try:
                    prov = json.loads(row["provenance_json"])
                except Exception:
                    pass
            return {
                "operation_id": row["operation_id"],
                "session_id": row["session_id"],
                "task_id": row["task_id"],
                "transaction_id": row["transaction_id"],
                "tool_name": row["tool_name"],
                "filepath": row["filepath"],
                "arguments": json.loads(row["arguments_json"]) if row["arguments_json"] else {},
                "before_hash": row["before_hash"],
                "after_hash": row["after_hash"],
                "result": json.loads(row["result_json"]) if row["result_json"] else None,
                "status": row["status"],
                "no_op": bool(row["no_op"]),
                "provenance": prov,
                "created_at": row["created_at"],
            }

    def get_operations(
        self,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        transaction_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves operations matching session, task, or transaction."""
        query = "SELECT * FROM operations WHERE 1=1"
        params = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)
        if transaction_id:
            query += " AND transaction_id = ?"
            params.append(transaction_id)
        query += " ORDER BY created_at ASC"

        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(query, params).fetchall()
            results = []
            for r in rows:
                keys = r.keys()
                prov = None
                if "provenance_json" in keys and r["provenance_json"]:
                    try:
                        prov = json.loads(r["provenance_json"])
                    except Exception:
                        pass
                results.append({
                    "operation_id": r["operation_id"],
                    "session_id": r["session_id"],
                    "task_id": r["task_id"],
                    "transaction_id": r["transaction_id"],
                    "tool_name": r["tool_name"],
                    "filepath": r["filepath"],
                    "arguments": json.loads(r["arguments_json"]) if r["arguments_json"] else {},
                    "before_hash": r["before_hash"],
                    "after_hash": r["after_hash"],
                    "result": json.loads(r["result_json"]) if r["result_json"] else None,
                    "status": r["status"],
                    "no_op": bool(r["no_op"]),
                    "provenance": prov,
                    "created_at": r["created_at"],
                })
            return results

    # ==========================================
    # LIFECYCLE EVENT CRUD (Issue #51)
    # ==========================================
    def save_event(self, session_id: str, event: Any) -> None:
        """Persists an ExecutionEvent to SQLite."""
        evt_id = getattr(event, "event_id", None) or f"evt-{uuid.uuid4().hex[:8]}"
        evt_type = getattr(event, "event_type_value", None) or (
            event.event_type.value if hasattr(getattr(event, "event_type", None), "value") else str(getattr(event, "event_type", "UNKNOWN"))
        )
        t_id = getattr(event, "task_id", None)
        a_name = getattr(event, "agent_name", None)
        p_dict = getattr(event, "payload", {})
        ts = getattr(event, "timestamp", None) or datetime.now().isoformat()

        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO sessions (session_id, user_request, status, verdict, created_at, updated_at)
                    VALUES (?, 'Event Stream Session', 'IN_PROGRESS', 'UNDECIDED', ?, ?)
                    ON CONFLICT(session_id) DO NOTHING
                    """,
                    (session_id, ts, ts),
                )
                conn.execute(
                    """
                    INSERT INTO events (
                        event_id, session_id, task_id, event_type, agent_name, payload_json, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        task_id = excluded.task_id,
                        event_type = excluded.event_type,
                        agent_name = excluded.agent_name,
                        payload_json = excluded.payload_json
                    """,
                    (
                        evt_id,
                        session_id,
                        t_id,
                        evt_type,
                        a_name,
                        json.dumps(p_dict, default=str),
                        ts,
                    ),
                )

    def get_events(
        self,
        session_id: str,
        event_type: Optional[Any] = None,
        task_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Any]:
        """Retrieves ExecutionEvents from SQLite matching session, type, and/or task."""
        from ..runtime.event_bus import ExecutionEvent
        query = "SELECT * FROM events WHERE session_id = ?"
        params: List[Any] = [session_id]

        if event_type:
            t_val = event_type.value if hasattr(event_type, "value") else str(event_type).upper()
            query += " AND event_type = ?"
            params.append(t_val)
        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)

        query += " ORDER BY timestamp ASC"
        if limit and limit > 0:
            query += f" LIMIT {int(limit)}"

        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(query, params).fetchall()
            events = []
            for r in rows:
                p = {}
                if r["payload_json"]:
                    try:
                        p = json.loads(r["payload_json"])
                    except Exception:
                        p = {}
                events.append(
                    ExecutionEvent(
                        event_id=r["event_id"],
                        session_id=r["session_id"],
                        task_id=r["task_id"],
                        event_type=r["event_type"],
                        agent_name=r["agent_name"],
                        payload=p,
                        timestamp=r["timestamp"],
                    )
                )
            return events

    # ==========================================
    # TELEMETRY SNAPSHOT CRUD (Issue #52)
    # ==========================================
    def save_telemetry_snapshot(self, session_id: str, snapshot: Any) -> None:
        """Persists or updates a TelemetrySnapshot in SQLite."""
        with self._lock:
            conn = self._get_connection()
            if hasattr(snapshot, "to_dict"):
                snap_dict = snapshot.to_dict()
            elif isinstance(snapshot, dict):
                snap_dict = snapshot
            else:
                snap_dict = {}

            now_iso = datetime.now().isoformat()
            with conn:
                conn.execute(
                    """
                    INSERT INTO sessions (session_id, user_request, status, verdict, created_at, updated_at)
                    VALUES (?, 'Telemetry session', 'PENDING', 'UNDECIDED', ?, ?)
                    ON CONFLICT(session_id) DO NOTHING
                    """,
                    (session_id, now_iso, now_iso),
                )
                conn.execute(
                    """
                    INSERT INTO telemetry_snapshots (session_id, telemetry_json, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        telemetry_json = excluded.telemetry_json,
                        created_at = excluded.created_at
                    """,
                    (session_id, json.dumps(snap_dict, default=str), now_iso),
                )

    def get_telemetry_snapshot(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves stored TelemetrySnapshot dictionary from SQLite."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT telemetry_json FROM telemetry_snapshots WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not row or not row["telemetry_json"]:
                return None
            try:
                return json.loads(row["telemetry_json"])
            except Exception:
                return None

    # ==========================================
    # TRACE CRUD (Issue #54)
    # ==========================================
    def save_trace(self, trace_id: str, trace_tree_dict: Dict[str, Any], session_id: Optional[str] = None):
        """Persists a full TraceTree dictionary to SQLite."""
        with self._lock:
            conn = self._get_connection()
            now_iso = datetime.now().isoformat()
            meta = trace_tree_dict.get("metadata", {})
            total_spans = trace_tree_dict.get("total_spans", 0)
            duration_sec = float(trace_tree_dict.get("duration_seconds", 0.0) or 0.0)

            with conn:
                if session_id:
                    conn.execute(
                        """
                        INSERT INTO sessions (session_id, user_request, status, verdict, created_at, updated_at)
                        VALUES (?, 'Trace session', 'PENDING', 'UNDECIDED', ?, ?)
                        ON CONFLICT(session_id) DO NOTHING
                        """,
                        (session_id, now_iso, now_iso),
                    )
                conn.execute(
                    """
                    INSERT INTO traces (
                        trace_id, session_id, metadata_json, total_spans,
                        duration_seconds, trace_tree_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trace_id) DO UPDATE SET
                        session_id = excluded.session_id,
                        metadata_json = excluded.metadata_json,
                        total_spans = excluded.total_spans,
                        duration_seconds = excluded.duration_seconds,
                        trace_tree_json = excluded.trace_tree_json
                    """,
                    (
                        trace_id,
                        session_id,
                        json.dumps(meta, default=str),
                        total_spans,
                        duration_sec,
                        json.dumps(trace_tree_dict, default=str),
                        now_iso,
                    ),
                )

    def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves stored TraceTree dictionary by trace_id."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT trace_tree_json FROM traces WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
            if not row or not row["trace_tree_json"]:
                return None
            try:
                return json.loads(row["trace_tree_json"])
            except Exception:
                return None

    def get_session_traces(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieves all TraceTree dictionaries associated with a session_id."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT trace_tree_json FROM traces WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
            results = []
            for r in rows:
                if r["trace_tree_json"]:
                    try:
                        results.append(json.loads(r["trace_tree_json"]))
                    except Exception:
                        pass
            return results





