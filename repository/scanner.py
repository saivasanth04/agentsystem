"""
Repository Scanner.
Discovers workspace files, tracks Git status via GitPython, monitors real-time changes via watchfiles,
and records incremental file metadata into the persistent repository database.
"""
from dataclasses import dataclass, field
import fnmatch
import hashlib
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union

import git
import watchfiles

logger = logging.getLogger("repository.scanner")

DEFAULT_IGNORE_PATTERNS = [
    ".git",
    ".git/*",
    ".orchestrator",
    ".orchestrator/*",
    "node_modules",
    "node_modules/*",
    "venv",
    "venv/*",
    ".venv",
    ".venv/*",
    "__pycache__",
    "__pycache__/*",
    ".pytest_cache",
    ".pytest_cache/*",
    ".mypy_cache",
    ".mypy_cache/*",
    ".ruff_cache",
    ".ruff_cache/*",
    "dist",
    "dist/*",
    "build",
    "build/*",
    ".next",
    ".next/*",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
]

EXTENSION_LANGUAGE_MAP: Dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".sh": "bash",
    ".bat": "batch",
    ".ps1": "powershell",
    ".sql": "sql",
}


@dataclass
class ScannedFile:
    """Metadata for a scanned workspace file."""
    path: str
    absolute_path: str
    language: str
    sha256: str
    size_bytes: int
    last_modified: float
    is_git_tracked: bool = False
    is_git_modified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "absolute_path": self.absolute_path,
            "language": self.language,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "last_modified": self.last_modified,
            "is_git_tracked": self.is_git_tracked,
            "is_git_modified": self.is_git_modified,
        }


@dataclass
class GitMetadata:
    """Git repository metadata obtained via GitPython."""
    is_repo: bool = False
    branch: Optional[str] = None
    commit_sha: Optional[str] = None
    commit_author: Optional[str] = None
    commit_message: Optional[str] = None
    is_dirty: bool = False
    untracked_files_count: int = 0
    modified_files_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_repo": self.is_repo,
            "branch": self.branch,
            "commit_sha": self.commit_sha,
            "commit_author": self.commit_author,
            "commit_message": self.commit_message,
            "is_dirty": self.is_dirty,
            "untracked_files_count": self.untracked_files_count,
            "modified_files_count": self.modified_files_count,
        }


class RepositoryScanner:
    """
    Scans the repository filesystem using GitPython and watchfiles.
    Provides incremental scanning and persistent SQLite storage.
    """

    def __init__(self, root_dir: Union[str, Path], db_conn: Optional[sqlite3.Connection] = None):
        self.root_dir = Path(root_dir).resolve()
        self.db = db_conn
        self.ignore_patterns = list(DEFAULT_IGNORE_PATTERNS)
        self._load_gitignore()
        self.git_repo: Optional[git.Repo] = self._init_git_repo()

    def _init_git_repo(self) -> Optional[git.Repo]:
        """Initializes GitPython repo instance if a valid git repository exists."""
        try:
            if (self.root_dir / ".git").exists():
                return git.Repo(self.root_dir)
            return None
        except Exception:
            return None

    def _load_gitignore(self):
        """Loads repository .gitignore patterns if present."""
        gi_path = self.root_dir / ".gitignore"
        if gi_path.exists():
            try:
                lines = gi_path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        self.ignore_patterns.append(stripped)
            except Exception as e:
                logger.warning(f"Error reading .gitignore: {e}")

    def should_ignore(self, rel_path: str) -> bool:
        """Determines if a relative file path matches any ignore pattern."""
        norm_path = rel_path.replace("\\", "/")
        parts = norm_path.split("/")

        for pattern in self.ignore_patterns:
            clean_pat = pattern.rstrip("/")
            if fnmatch.fnmatch(norm_path, pattern) or fnmatch.fnmatch(norm_path, clean_pat):
                return True
            for part in parts:
                if fnmatch.fnmatch(part, clean_pat):
                    return True
        return False

    def get_git_metadata(self) -> GitMetadata:
        """Extracts repository Git metadata using GitPython."""
        if not self.git_repo:
            return GitMetadata(is_repo=False)

        try:
            r = self.git_repo
            branch = r.active_branch.name if not r.head.is_detached else "DETACHED"
            head_commit = r.head.commit if r.head.is_valid() else None
            untracked = r.untracked_files
            diff = r.index.diff(None)

            return GitMetadata(
                is_repo=True,
                branch=branch,
                commit_sha=head_commit.hexsha if head_commit else None,
                commit_author=str(head_commit.author) if head_commit else None,
                commit_message=head_commit.message.strip() if head_commit else None,
                is_dirty=r.is_dirty(),
                untracked_files_count=len(untracked),
                modified_files_count=len(diff),
            )
        except Exception as e:
            logger.warning(f"Failed to inspect git repository: {e}")
            return GitMetadata(is_repo=True, is_dirty=True)

    def scan(self, force_all: bool = False) -> List[ScannedFile]:
        """
        Scans all non-ignored files within the workspace root.
        Computes SHA-256 hashes and persists metadata to SQLite if a db connection is active.
        """
        scanned_files: List[ScannedFile] = []
        git_tracked: Set[str] = set()
        git_modified: Set[str] = set()

        if self.git_repo:
            try:
                for item in self.git_repo.index.entries:
                    git_tracked.add(Path(item[0]).as_posix())
                for diff_item in self.git_repo.index.diff(None):
                    if diff_item.a_path:
                        git_modified.add(Path(diff_item.a_path).as_posix())
            except Exception as e:
                logger.debug(f"Git index read error: {e}")

        for root, dirs, files in os.walk(self.root_dir):
            rel_dir = os.path.relpath(root, self.root_dir).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""

            # Prune ignored directories in-place for performance
            dirs[:] = [
                d for d in dirs
                if not self.should_ignore(f"{rel_dir}/{d}" if rel_dir else d)
            ]

            for file in files:
                rel_file = f"{rel_dir}/{file}" if rel_dir else file
                if self.should_ignore(rel_file):
                    continue

                abs_file = Path(root) / file
                try:
                    stat = abs_file.stat()
                    ext = abs_file.suffix.lower()
                    lang = EXTENSION_LANGUAGE_MAP.get(ext, "unknown")

                    content_bytes = abs_file.read_bytes()
                    file_sha = hashlib.sha256(content_bytes).hexdigest()
                    posix_path = Path(rel_file).as_posix()

                    scanned = ScannedFile(
                        path=posix_path,
                        absolute_path=str(abs_file),
                        language=lang,
                        sha256=file_sha,
                        size_bytes=stat.st_size,
                        last_modified=stat.st_mtime,
                        is_git_tracked=posix_path in git_tracked,
                        is_git_modified=posix_path in git_modified,
                    )
                    scanned_files.append(scanned)

                    if self.db:
                        self._persist_scanned_file(scanned)

                except (OSError, PermissionError) as e:
                    logger.debug(f"Could not read file {abs_file}: {e}")

        if self.db:
            try:
                self.db.commit()
            except Exception:
                pass

        return scanned_files

    def _persist_scanned_file(self, sf: ScannedFile):
        """Persists file metadata into SQLite database."""
        if not self.db:
            return
        self.db.execute(
            """
            INSERT INTO files (path, language, sha256, size_bytes, last_modified, is_git_tracked, is_git_modified)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                language=excluded.language,
                sha256=excluded.sha256,
                size_bytes=excluded.size_bytes,
                last_modified=excluded.last_modified,
                is_git_tracked=excluded.is_git_tracked,
                is_git_modified=excluded.is_git_modified
            """,
            (
                sf.path,
                sf.language,
                sf.sha256,
                sf.size_bytes,
                sf.last_modified,
                1 if sf.is_git_tracked else 0,
                1 if sf.is_git_modified else 0,
            ),
        )

    def watch_changes(self) -> Iterator[Tuple[str, str]]:
        """
        Uses watchfiles to stream real-time filesystem modifications.
        Yields: (change_type, rel_path) where change_type is 'added', 'modified', or 'deleted'.
        """
        type_names = {
            watchfiles.Change.added: "added",
            watchfiles.Change.modified: "modified",
            watchfiles.Change.deleted: "deleted",
        }
        for changes in watchfiles.watch(self.root_dir, recursive=True):
            for change_type, abs_path in changes:
                try:
                    rel_p = os.path.relpath(abs_path, self.root_dir).replace("\\", "/")
                    if not self.should_ignore(rel_p):
                        yield type_names.get(change_type, "modified"), rel_p
                except ValueError:
                    continue
