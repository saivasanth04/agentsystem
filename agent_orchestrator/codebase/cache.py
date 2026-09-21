"""
Incremental SQLite Code Cache.
Tracks file mtimes and SHA-256 hashes to enable instant re-indexing of unchanged files.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from .symbols import ReferenceEdge, SymbolNode


class IncrementalCodeCache:
    """
    SQLite-backed cache for parsed AST symbols, references, and module imports.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path(".cache/codebase_index.db")
        if self.db_path != Path(":memory:"):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = None
        else:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)

        self._init_schema()

    @contextmanager
    def _connection(self):
        """Yields an active SQLite connection. For disk databases, connection is closed after use to prevent file locks on Windows."""
        if self.db_path == Path(":memory:"):
            if self._conn is None:
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            yield self._conn
        else:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            try:
                yield conn
            finally:
                conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS file_cache (
                        filepath TEXT PRIMARY KEY,
                        mtime REAL,
                        sha256 TEXT,
                        indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cached_symbols (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filepath TEXT,
                        name TEXT,
                        kind TEXT,
                        start_line INTEGER,
                        end_line INTEGER,
                        parent TEXT,
                        signature TEXT,
                        docstring TEXT,
                        qualified_name TEXT,
                        data_json TEXT
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cached_imports (
                        filepath TEXT,
                        import_name TEXT
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cached_references (
                        filepath TEXT,
                        symbol_name TEXT,
                        line INTEGER,
                        kind TEXT,
                        context_snippet TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sym_fp ON cached_symbols(filepath)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_imp_fp ON cached_imports(filepath)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ref_fp ON cached_references(filepath)")

    @staticmethod
    def compute_sha256(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()

    def is_file_changed(self, filepath: str, current_mtime: float, current_sha256: str) -> bool:
        """Returns True if the file has not been cached or its hash/mtime has changed."""
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT mtime, sha256 FROM file_cache WHERE filepath = ?", (filepath,))
            row = cur.fetchone()
            if not row:
                return True
            cached_mtime, cached_sha = row
            if cached_sha != current_sha256:
                return True
            return False

    def get_file_metadata(self, filepath: str) -> Optional[Tuple[float, str]]:
        """Retrieves cached (mtime, sha256) for a file if present."""
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT mtime, sha256 FROM file_cache WHERE filepath = ?", (filepath,))
            row = cur.fetchone()
            return (row[0], row[1]) if row else None

    def delete_file(self, filepath: str) -> None:
        """Removes a single file and its cached symbols, references, and imports."""
        with self._connection() as conn:
            with conn:
                conn.execute("DELETE FROM file_cache WHERE filepath = ?", (filepath,))
                conn.execute("DELETE FROM cached_symbols WHERE filepath = ?", (filepath,))
                conn.execute("DELETE FROM cached_references WHERE filepath = ?", (filepath,))
                conn.execute("DELETE FROM cached_imports WHERE filepath = ?", (filepath,))

    def get_cached_file(
        self, filepath: str
    ) -> Optional[Tuple[List[SymbolNode], List[ReferenceEdge], Set[str]]]:
        """Retrieves cached symbols, references, and imports for an unchanged file."""
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT filepath FROM file_cache WHERE filepath = ?", (filepath,))
            if not cur.fetchone():
                return None

            # Symbols
            cur.execute("""
                SELECT name, kind, filepath, start_line, end_line, parent, signature, docstring, qualified_name, data_json
                FROM cached_symbols WHERE filepath = ?
            """, (filepath,))
            symbols: List[SymbolNode] = []
            for row in cur.fetchall():
                name, kind, fp, start, end, parent, sig, doc, qname, data_json = row
                extra = json.loads(data_json) if data_json else {}
                symbols.append(SymbolNode(
                    name=name,
                    kind=kind,
                    filepath=fp,
                    start_line=start,
                    end_line=end,
                    parent=parent,
                    signature=sig or "",
                    docstring=doc or "",
                    bases=extra.get("bases", []),
                    calls=extra.get("calls", []),
                    qualified_name=qname or "",
                    decorators=extra.get("decorators", []),
                    parameters=extra.get("parameters", []),
                    return_type=extra.get("return_type", ""),
                ))

            # References
            cur.execute("""
                SELECT symbol_name, filepath, line, kind, context_snippet
                FROM cached_references WHERE filepath = ?
            """, (filepath,))
            references = [
                ReferenceEdge(symbol_name=r[0], filepath=r[1], line=r[2], kind=r[3], context_snippet=r[4] or "")
                for r in cur.fetchall()
            ]

            # Imports
            cur.execute("SELECT import_name FROM cached_imports WHERE filepath = ?", (filepath,))
            imports = {r[0] for r in cur.fetchall()}

            return symbols, references, imports

    def save_file(
        self,
        filepath: str,
        mtime: float,
        sha256: str,
        symbols: List[SymbolNode],
        references: List[ReferenceEdge],
        imports: Set[str],
    ) -> None:
        """Saves parsed file data into the cache."""
        with self._connection() as conn:
            with conn:
                # Clean old records for this file
                conn.execute("DELETE FROM file_cache WHERE filepath = ?", (filepath,))
                conn.execute("DELETE FROM cached_symbols WHERE filepath = ?", (filepath,))
                conn.execute("DELETE FROM cached_references WHERE filepath = ?", (filepath,))
                conn.execute("DELETE FROM cached_imports WHERE filepath = ?", (filepath,))

                # Insert file metadata
                conn.execute(
                    "INSERT INTO file_cache (filepath, mtime, sha256) VALUES (?, ?, ?)",
                    (filepath, mtime, sha256)
                )

                # Insert symbols
                for s in symbols:
                    extra = {
                        "bases": s.bases,
                        "calls": s.calls,
                        "decorators": s.decorators,
                        "parameters": s.parameters,
                        "return_type": s.return_type,
                    }
                    conn.execute("""
                        INSERT INTO cached_symbols
                        (filepath, name, kind, start_line, end_line, parent, signature, docstring, qualified_name, data_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        s.filepath, s.name, s.kind, s.start_line, s.end_line, s.parent,
                        s.signature, s.docstring, s.qualified_name, json.dumps(extra)
                    ))

                # Insert references
                for r in references:
                    conn.execute("""
                        INSERT INTO cached_references (filepath, symbol_name, line, kind, context_snippet)
                        VALUES (?, ?, ?, ?, ?)
                    """, (r.filepath, r.symbol_name, r.line, r.kind, r.context_snippet))

                # Insert imports
                for imp in imports:
                    conn.execute(
                        "INSERT INTO cached_imports (filepath, import_name) VALUES (?, ?)",
                        (filepath, imp)
                    )

    def prune_deleted_files(self, active_filepaths: Set[str]) -> None:
        """Removes records for files that no longer exist in the workspace."""
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT filepath FROM file_cache")
            cached_files = {r[0] for r in cur.fetchall()}
            deleted = cached_files - active_filepaths

            if deleted:
                with conn:
                    for df in deleted:
                        conn.execute("DELETE FROM file_cache WHERE filepath = ?", (df,))
                        conn.execute("DELETE FROM cached_symbols WHERE filepath = ?", (df,))
                        conn.execute("DELETE FROM cached_references WHERE filepath = ?", (df,))
                        conn.execute("DELETE FROM cached_imports WHERE filepath = ?", (df,))

    def clear(self) -> None:
        with self._connection() as conn:
            with conn:
                conn.execute("DELETE FROM file_cache")
                conn.execute("DELETE FROM cached_symbols")
                conn.execute("DELETE FROM cached_references")
                conn.execute("DELETE FROM cached_imports")

    def close(self) -> None:
        try:
            if hasattr(self, "_conn") and self._conn is not None:
                self._conn.close()
                self._conn = None
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()
