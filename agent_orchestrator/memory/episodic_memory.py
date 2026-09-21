"""
Episodic Memory Engine: Experience Replay and Trajectory Retrieval.
Persists execution episodes (objectives, errors, resolution strategies, outcomes, lessons)
and provides full-text and semantic retrieval to ground future tasks in past experience.
"""
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional
import uuid


@dataclass
class EpisodeRecord:
    """
    Structured record of a single execution episode or remediation attempt.
    """
    objective: str
    episode_id: str = field(default_factory=lambda: f"ep-{uuid.uuid4().hex[:8]}")
    session_id: str = ""
    task_id: str = ""
    error_encountered: Optional[str] = None
    resolution_strategy: Optional[str] = None
    tools_used: List[str] = field(default_factory=list)
    outcome: str = "SUCCESS"  # SUCCESS or FAILURE
    lessons_learned: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "objective": self.objective,
            "error_encountered": self.error_encountered,
            "resolution_strategy": self.resolution_strategy,
            "tools_used": list(self.tools_used),
            "outcome": self.outcome,
            "lessons_learned": list(self.lessons_learned),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EpisodeRecord":
        return cls(
            episode_id=data.get("episode_id", f"ep-{uuid.uuid4().hex[:8]}"),
            session_id=data.get("session_id", ""),
            task_id=data.get("task_id", ""),
            objective=data["objective"],
            error_encountered=data.get("error_encountered"),
            resolution_strategy=data.get("resolution_strategy"),
            tools_used=list(data.get("tools_used", [])),
            outcome=data.get("outcome", "SUCCESS"),
            lessons_learned=list(data.get("lessons_learned", [])),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )


class EpisodicMemoryEngine:
    """
    Thread-safe SQLite + FTS5 store for execution experiences and trajectory retrieval.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or ":memory:"
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        self._local = threading.local()
        self._lock = threading.RLock()
        self._has_fts5 = False
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            if self.db_path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id TEXT UNIQUE NOT NULL,
                    session_id TEXT,
                    task_id TEXT,
                    objective TEXT NOT NULL,
                    error_encountered TEXT,
                    resolution_strategy TEXT,
                    tools_json TEXT DEFAULT '[]',
                    outcome TEXT NOT NULL,
                    lessons_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ep_obj ON episodes(objective);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ep_outcome ON episodes(outcome);")

                try:
                    conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                        objective, error_encountered, resolution_strategy, lessons_json,
                        content='episodes', content_rowid='id'
                    );
                    """)
                    self._has_fts5 = True
                except Exception:
                    self._has_fts5 = False

    def record_episode(self, episode: EpisodeRecord) -> str:
        """Persists a new episode record."""
        with self._lock:
            conn = self._get_connection()
            tools_json = json.dumps(episode.tools_used)
            lessons_json = json.dumps(episode.lessons_learned)
            with conn:
                cur = conn.execute(
                    """
                    INSERT INTO episodes (
                        episode_id, session_id, task_id, objective,
                        error_encountered, resolution_strategy, tools_json,
                        outcome, lessons_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        episode.episode_id,
                        episode.session_id,
                        episode.task_id,
                        episode.objective.strip(),
                        episode.error_encountered.strip() if episode.error_encountered else None,
                        episode.resolution_strategy.strip() if episode.resolution_strategy else None,
                        tools_json,
                        episode.outcome.upper().strip(),
                        lessons_json,
                        episode.timestamp,
                    ),
                )
                row_id = cur.lastrowid
                if self._has_fts5 and row_id:
                    try:
                        conn.execute(
                            """
                            INSERT INTO episodes_fts(rowid, objective, error_encountered, resolution_strategy, lessons_json)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                row_id,
                                episode.objective.strip(),
                                episode.error_encountered or "",
                                episode.resolution_strategy or "",
                                lessons_json,
                            ),
                        )
                    except Exception:
                        pass
            return episode.episode_id

    def retrieve_relevant_episodes(
        self,
        objective: str,
        error_signature: Optional[str] = None,
        top_k: int = 3,
    ) -> List[EpisodeRecord]:
        """
        Retrieves relevant past episodes matching the objective or error signature.
        """
        query_parts = []
        if objective:
            query_parts.append(objective.strip())
        if error_signature:
            query_parts.append(error_signature.strip())

        query_text = " ".join(query_parts).strip()
        if not query_text:
            return self.list_all(limit=top_k)

        with self._lock:
            conn = self._get_connection()
            results: List[EpisodeRecord] = []

            if self._has_fts5:
                try:
                    words = [w for w in query_text.split() if w.isalnum() and len(w) > 2]
                    if words:
                        safe_query = " OR ".join(f'"{w}"' for w in words[:8])
                        sql = """
                        SELECT e.* FROM episodes_fts f
                        JOIN episodes e ON f.rowid = e.id
                        WHERE episodes_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?
                        """
                        cur = conn.execute(sql, (safe_query, top_k))
                        for r in cur.fetchall():
                            results.append(self._row_to_record(r))
                        if results:
                            return results
                except Exception:
                    pass

            # Fallback to LIKE keyword search
            terms = [t.lower() for t in query_text.split() if len(t) > 2][:5]
            if not terms:
                terms = [query_text.lower()[:20]]

            where_clauses = ["(LOWER(objective) LIKE ? OR LOWER(error_encountered) LIKE ? OR LOWER(resolution_strategy) LIKE ? OR LOWER(lessons_json) LIKE ?)"] * len(terms)
            sql = f"""
            SELECT * FROM episodes
            WHERE {' OR '.join(where_clauses)}
            ORDER BY id DESC LIMIT ?
            """
            params: List[Any] = []
            for t in terms:
                pat = f"%{t}%"
                params.extend([pat, pat, pat, pat])
            params.append(top_k)

            cur = conn.execute(sql, params)
            for r in cur.fetchall():
                results.append(self._row_to_record(r))

            return results

    def list_all(self, limit: int = 50) -> List[EpisodeRecord]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.execute("SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (limit,))
            return [self._row_to_record(r) for r in cur.fetchall()]

    def format_episodes_for_prompt(self, episodes: List[EpisodeRecord], max_tokens: int = 1000) -> str:
        """
        Formats retrieved episodes into an actionable prompt section.
        """
        if not episodes:
            return ""

        lines = ["**Relevant Past Experiences & Trajectories**:"]
        for ep in episodes:
            outcome_icon = "✅" if ep.outcome == "SUCCESS" else "❌"
            lines.append(f"\n• {outcome_icon} **Task**: {ep.objective}")
            if ep.error_encountered:
                lines.append(f"  - *Encountered Error*: {ep.error_encountered}")
            if ep.resolution_strategy:
                lines.append(f"  - *Resolution Strategy*: {ep.resolution_strategy}")
            if ep.lessons_learned:
                lines.append(f"  - *Lessons Learned*: {'; '.join(ep.lessons_learned)}")

        summary = "\n".join(lines)
        max_chars = max_tokens * 4
        if len(summary) > max_chars:
            return summary[:max_chars] + "\n... [Past episodes truncated] ..."
        return summary

    def clear(self) -> None:
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute("DELETE FROM episodes;")
                if self._has_fts5:
                    try:
                        conn.execute("DELETE FROM episodes_fts;")
                    except Exception:
                        pass

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> EpisodeRecord:
        tools = []
        lessons = []
        try:
            tools = json.loads(row["tools_json"] or "[]")
        except Exception:
            tools = []
        try:
            lessons = json.loads(row["lessons_json"] or "[]")
        except Exception:
            lessons = []

        return EpisodeRecord(
            episode_id=row["episode_id"],
            session_id=row["session_id"] or "",
            task_id=row["task_id"] or "",
            objective=row["objective"],
            error_encountered=row["error_encountered"],
            resolution_strategy=row["resolution_strategy"],
            tools_used=tools,
            outcome=row["outcome"],
            lessons_learned=lessons,
            timestamp=row["created_at"],
        )
