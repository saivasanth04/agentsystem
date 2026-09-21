"""
Long-Term Episodic Memory & Semantic Knowledge Store.
Persists architectural decisions, bug patterns, repository conventions, and API contracts
across sessions in SQLite with full-text search (FTS5).
"""
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional


class LongTermMemory:
    """
    Thread-safe SQLite-backed long-term memory engine with full-text search.
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
                CREATE TABLE IF NOT EXISTS project_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags_json TEXT DEFAULT '[]',
                    session_id TEXT,
                    created_at TEXT NOT NULL
                );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_cat ON project_memory(category);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_sess ON project_memory(session_id);")

                # Check if FTS5 is supported
                try:
                    conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS project_memory_fts USING fts5(
                        title, content, tags_json, content='project_memory', content_rowid='id'
                    );
                    """)
                    self._has_fts5 = True
                except Exception:
                    self._has_fts5 = False

    def store(
        self,
        category: str,
        title: str,
        content: str,
        tags: Optional[List[str]] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """Stores a new episodic memory item."""
        with self._lock:
            conn = self._get_connection()
            tags_json = json.dumps(tags or [])
            now_iso = datetime.now().isoformat()
            with conn:
                cur = conn.execute(
                    """
                    INSERT INTO project_memory (category, title, content, tags_json, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (category.upper().strip(), title.strip(), content.strip(), tags_json, session_id, now_iso),
                )
                mem_id = cur.lastrowid
                if self._has_fts5 and mem_id:
                    try:
                        conn.execute(
                            "INSERT INTO project_memory_fts(rowid, title, content, tags_json) VALUES (?, ?, ?, ?)",
                            (mem_id, title.strip(), content.strip(), tags_json),
                        )
                    except Exception:
                        pass
                return mem_id or 0

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Searches memory using full-text search (FTS5) or LIKE fallback.
        """
        query_clean = query.strip()
        if not query_clean:
            return self.list_all(category=category, limit=top_k)

        with self._lock:
            conn = self._get_connection()
            results: List[Dict[str, Any]] = []

            if self._has_fts5:
                try:
                    # Sanitize FTS query
                    safe_query = " ".join(f'"{w}"' for w in query_clean.split() if w.isalnum())
                    sql = """
                    SELECT m.id, m.category, m.title, m.content, m.tags_json, m.session_id, m.created_at
                    FROM project_memory_fts f
                    JOIN project_memory m ON f.rowid = m.id
                    WHERE project_memory_fts MATCH ?
                    """
                    params: List[Any] = [safe_query]
                    if category:
                        sql += " AND m.category = ?"
                        params.append(category.upper().strip())
                    sql += " LIMIT ?"
                    params.append(top_k)

                    cur = conn.execute(sql, params)
                    for r in cur.fetchall():
                        results.append(self._row_to_dict(r))
                    if results:
                        return results
                except Exception:
                    pass

            # Fallback to LIKE keyword search
            terms = [t.lower() for t in query_clean.split() if len(t) > 2]
            if not terms:
                terms = [query_clean.lower()]

            where_clauses = ["(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(tags_json) LIKE ?)"] * len(terms)
            sql = f"""
            SELECT id, category, title, content, tags_json, session_id, created_at
            FROM project_memory
            WHERE {' AND '.join(where_clauses)}
            """
            params = []
            for t in terms:
                pat = f"%{t}%"
                params.extend([pat, pat, pat])

            if category:
                sql += " AND category = ?"
                params.append(category.upper().strip())

            sql += " ORDER BY id DESC LIMIT ?"
            params.append(top_k)

            cur = conn.execute(sql, params)
            for r in cur.fetchall():
                results.append(self._row_to_dict(r))

            return results

    def list_all(self, category: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            if category:
                cur = conn.execute(
                    "SELECT * FROM project_memory WHERE category = ? ORDER BY id DESC LIMIT ?",
                    (category.upper().strip(), limit),
                )
            else:
                cur = conn.execute("SELECT * FROM project_memory ORDER BY id DESC LIMIT ?", (limit,))
            return [self._row_to_dict(r) for r in cur.fetchall()]

    def delete(self, memory_id: int) -> bool:
        with self._lock:
            conn = self._get_connection()
            with conn:
                cur = conn.execute("DELETE FROM project_memory WHERE id = ?", (memory_id,))
                if self._has_fts5:
                    try:
                        conn.execute("DELETE FROM project_memory_fts WHERE rowid = ?", (memory_id,))
                    except Exception:
                        pass
                return cur.rowcount > 0

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        tags = []
        try:
            tags = json.loads(row["tags_json"] or "[]")
        except Exception:
            tags = []
        return {
            "id": row["id"],
            "category": row["category"],
            "title": row["title"],
            "content": row["content"],
            "tags": tags,
            "session_id": row["session_id"],
            "created_at": row["created_at"],
        }


# Global singleton
long_term_memory = LongTermMemory()
