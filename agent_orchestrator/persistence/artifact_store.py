"""
ArtifactStore: Production-Grade Content-Addressed & Hierarchical Artifact Storage Engine.
Manages immutable artifacts partitioned across 7 canonical categories:
  - plans
  - specifications
  - patches
  - logs
  - test_results
  - reports
  - snapshots
"""
from datetime import datetime
from enum import Enum
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Dict, List, Optional, Union

from ..runtime.task_graph import ArtifactRecord


class ArtifactCategory(str, Enum):
    PLANS = "plans"
    SPECIFICATIONS = "specifications"
    PATCHES = "patches"
    LOGS = "logs"
    TEST_RESULTS = "test_results"
    REPORTS = "reports"
    SNAPSHOTS = "snapshots"

    @classmethod
    def from_str(cls, val: Union[str, "ArtifactCategory"]) -> "ArtifactCategory":
        if isinstance(val, cls):
            return val
        clean = str(val).lower().strip().replace("-", "_")
        for member in cls:
            if member.value == clean or member.name.lower() == clean:
                return member
        # Fallback mappings
        if "plan" in clean:
            return cls.PLANS
        elif "spec" in clean or "contract" in clean:
            return cls.SPECIFICATIONS
        elif "patch" in clean or "diff" in clean:
            return cls.PATCHES
        elif "log" in clean or "transcript" in clean:
            return cls.LOGS
        elif "test" in clean or "coverage" in clean:
            return cls.TEST_RESULTS
        elif "report" in clean or "review" in clean or "verdict" in clean or "audit" in clean:
            return cls.REPORTS
        elif "snapshot" in clean or "checkpoint" in clean or "backup" in clean:
            return cls.SNAPSHOTS
        return cls.REPORTS


DEFAULT_EXTENSIONS = {
    ArtifactCategory.PLANS: ".json",
    ArtifactCategory.SPECIFICATIONS: ".md",
    ArtifactCategory.PATCHES: ".patch",
    ArtifactCategory.LOGS: ".log",
    ArtifactCategory.TEST_RESULTS: ".json",
    ArtifactCategory.REPORTS: ".json",
    ArtifactCategory.SNAPSHOTS: ".tar.gz",
}

DEFAULT_MIME_TYPES = {
    ArtifactCategory.PLANS: "application/json",
    ArtifactCategory.SPECIFICATIONS: "text/markdown",
    ArtifactCategory.PATCHES: "text/x-diff",
    ArtifactCategory.LOGS: "text/plain",
    ArtifactCategory.TEST_RESULTS: "application/json",
    ArtifactCategory.REPORTS: "application/json",
    ArtifactCategory.SNAPSHOTS: "application/gzip",
}


class ArtifactStore:
    """
    Physical & Content-Addressed Storage engine for orchestrator deliverables.
    Features:
      - Canonical category partitioning
      - SHA-256 CAS content deduplication
      - Uniform URI scheme: artifact://{category}/{artifact_id}/{filename}
      - JSON sidecar metadata for zero-dependency inspection
      - Optional integration with SQLiteStateStore
    """

    def __init__(self, storage_root: Union[str, Path], state_store: Optional[Any] = None):
        self.storage_root = Path(storage_root).resolve()
        self.state_store = state_store
        self._lock = threading.RLock()
        self._index: Dict[str, ArtifactRecord] = {}

        # Initialize categorical subdirectories
        for cat in ArtifactCategory:
            cat_dir = self.storage_root / cat.value
            cat_dir.mkdir(parents=True, exist_ok=True)

        # Index any existing artifacts on disk
        self._scan_existing_artifacts()

    def _get_category_dir(self, category: ArtifactCategory) -> Path:
        cat_dir = self.storage_root / category.value
        cat_dir.mkdir(parents=True, exist_ok=True)
        return cat_dir

    def _scan_existing_artifacts(self) -> None:
        """Loads metadata sidecars from disk into memory index on startup."""
        with self._lock:
            for cat in ArtifactCategory:
                cat_dir = self.storage_root / cat.value
                if not cat_dir.exists():
                    continue
                for meta_file in cat_dir.glob("*.meta.json"):
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        rec = ArtifactRecord.from_dict(data)
                        self._index[rec.artifact_id] = rec
                    except Exception:
                        continue

    def format_uri(self, category: ArtifactCategory, artifact_id: str, name: str) -> str:
        safe_name = name.replace("/", "_").replace("\\", "_")
        return f"artifact://{category.value}/{artifact_id}/{safe_name}"

    def parse_uri(self, uri: str) -> Dict[str, str]:
        """Parses artifact://{category}/{artifact_id}/{name} URI."""
        if not uri.startswith("artifact://"):
            raise ValueError(f"Invalid artifact URI: '{uri}'")
        parts = uri[len("artifact://"):].split("/", 2)
        return {
            "category": parts[0] if len(parts) > 0 else "",
            "artifact_id": parts[1] if len(parts) > 1 else "",
            "name": parts[2] if len(parts) > 2 else "",
        }

    def put(
        self,
        category: Union[ArtifactCategory, str],
        name: str,
        content: Union[str, bytes, Dict[str, Any], List[Any]],
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        mime_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        artifact_type: str = "VERIFIED_OUTPUT",
    ) -> ArtifactRecord:
        """
        Stores content into the appropriate artifact category and returns an immutable ArtifactRecord.
        Deduplicates identical content in the same category (CAS).
        """
        cat_enum = ArtifactCategory.from_str(category)

        # 1. Normalize content to bytes
        if isinstance(content, str):
            content_bytes = content.encode("utf-8", errors="replace")
            inferred_mime = mime_type or DEFAULT_MIME_TYPES.get(cat_enum, "text/plain")
        elif isinstance(content, (bytes, bytearray)):
            content_bytes = bytes(content)
            inferred_mime = mime_type or DEFAULT_MIME_TYPES.get(cat_enum, "application/octet-stream")
        else:
            content_bytes = json.dumps(content, default=str, indent=2, sort_keys=True).encode("utf-8")
            inferred_mime = mime_type or "application/json"

        # 2. Content-addressed identity
        chash = hashlib.sha256(content_bytes).hexdigest()
        aid = f"art-{chash[:12]}"
        uri = self.format_uri(cat_enum, aid, name)

        cat_dir = self._get_category_dir(cat_enum)
        ext = os.path.splitext(name)[1] or DEFAULT_EXTENSIONS.get(cat_enum, ".bin")
        content_path = cat_dir / f"{aid}_{Path(name).stem}{ext}"
        meta_path = cat_dir / f"{aid}.meta.json"

        meta_payload = dict(metadata or {})
        meta_payload["storage_path"] = str(content_path)
        meta_payload["relative_path"] = str(content_path.relative_to(self.storage_root)).replace("\\", "/")

        record = ArtifactRecord(
            artifact_id=aid,
            name=name,
            artifact_type=artifact_type,
            category=cat_enum.value,
            uri_or_path=uri,
            content_hash=chash,
            size_bytes=len(content_bytes),
            mime_type=inferred_mime,
            metadata=meta_payload,
            task_id=task_id,
            attempt_id=attempt_id,
            execution_id=session_id,
            created_at=datetime.now().isoformat(),
        )

        with self._lock:
            # Write physical content
            with open(content_path, "wb") as f:
                f.write(content_bytes)

            # Write metadata sidecar
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(record.to_dict(), f, indent=2, default=str)

            self._index[aid] = record

            # Synchronize with SQLiteStateStore if available
            if self.state_store and hasattr(self.state_store, "save_artifact") and session_id:
                try:
                    self.state_store.save_artifact(record, session_id=session_id, task_id=task_id or "")
                except Exception:
                    pass

        return record

    def put_file(
        self,
        category: Union[ArtifactCategory, str],
        file_path: Union[str, Path],
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        mime_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        artifact_type: str = "VERIFIED_OUTPUT",
    ) -> ArtifactRecord:
        """Stores a physical file from the workspace or filesystem into the ArtifactStore."""
        src_path = Path(file_path).resolve()
        if not src_path.exists() or not src_path.is_file():
            raise FileNotFoundError(f"Source artifact file '{file_path}' does not exist.")

        with open(src_path, "rb") as f:
            content_bytes = f.read()

        chosen_name = name or src_path.name
        guessed_mime, _ = mimetypes.guess_type(str(src_path))
        effective_mime = mime_type or guessed_mime

        return self.put(
            category=category,
            name=chosen_name,
            content=content_bytes,
            session_id=session_id,
            task_id=task_id,
            attempt_id=attempt_id,
            mime_type=effective_mime,
            metadata=metadata,
            artifact_type=artifact_type,
        )

    def get(self, artifact_id: str) -> Optional[ArtifactRecord]:
        """Retrieves ArtifactRecord metadata by artifact_id."""
        with self._lock:
            if artifact_id in self._index:
                return self._index[artifact_id]

            # Try query from SQLite if not in memory
            if self.state_store and hasattr(self.state_store, "get_artifact"):
                try:
                    rec = self.state_store.get_artifact(artifact_id)
                    if rec:
                        self._index[artifact_id] = rec
                        return rec
                except Exception:
                    pass
        return None

    def _resolve_content_path(self, record: ArtifactRecord) -> Path:
        """Resolves the physical filesystem path for an ArtifactRecord."""
        stored_path = record.metadata.get("storage_path")
        if stored_path and os.path.isfile(stored_path):
            return Path(stored_path)

        cat_enum = ArtifactCategory.from_str(record.category)
        cat_dir = self._get_category_dir(cat_enum)
        matches = list(cat_dir.glob(f"{record.artifact_id}_*"))
        if matches:
            return matches[0]

        raise FileNotFoundError(f"Underlying content file for artifact '{record.artifact_id}' not found in {cat_dir}.")

    def read_bytes(self, artifact_id: str) -> bytes:
        """Reads raw bytes for an artifact."""
        rec = self.get(artifact_id)
        if not rec:
            raise KeyError(f"Artifact '{artifact_id}' not found in store.")
        content_path = self._resolve_content_path(rec)
        with open(content_path, "rb") as f:
            return f.read()

    def read_text(self, artifact_id: str, encoding: str = "utf-8") -> str:
        """Reads artifact content as decoded string."""
        raw = self.read_bytes(artifact_id)
        return raw.decode(encoding, errors="replace")

    def read_json(self, artifact_id: str) -> Any:
        """Reads and parses artifact content as JSON."""
        txt = self.read_text(artifact_id)
        return json.loads(txt)

    def list_artifacts(
        self,
        category: Optional[Union[ArtifactCategory, str]] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
    ) -> List[ArtifactRecord]:
        """Queries and filters stored artifacts."""
        target_cat = ArtifactCategory.from_str(category).value if category else None
        with self._lock:
            results = list(self._index.values())

        if target_cat:
            results = [r for r in results if r.category == target_cat]
        if session_id:
            results = [r for r in results if r.execution_id == session_id]
        if task_id:
            results = [r for r in results if r.task_id == task_id]
        if attempt_id:
            results = [r for r in results if r.attempt_id == attempt_id]

        results.sort(key=lambda x: x.created_at)
        return results

    def resolve_uri(self, uri: str) -> Optional[ArtifactRecord]:
        """Resolves an artifact URI to its ArtifactRecord."""
        try:
            parsed = self.parse_uri(uri)
            aid = parsed.get("artifact_id")
            if aid:
                return self.get(aid)
        except Exception:
            pass
        return None

    def delete(self, artifact_id: str) -> bool:
        """Deletes an artifact and its sidecar file."""
        with self._lock:
            rec = self.get(artifact_id)
            if not rec:
                return False
            try:
                content_path = self._resolve_content_path(rec)
                if content_path.exists():
                    content_path.unlink()
            except Exception:
                pass

            cat_enum = ArtifactCategory.from_str(rec.category)
            cat_dir = self._get_category_dir(cat_enum)
            meta_path = cat_dir / f"{artifact_id}.meta.json"
            if meta_path.exists():
                meta_path.unlink()

            self._index.pop(artifact_id, None)
            return True

    def delete_session_artifacts(self, session_id: str) -> int:
        """Deletes all artifacts associated with a session from disk and cache."""
        deleted_count = 0
        with self._lock:
            # Find all matching artifact IDs
            matching_ids = []
            for aid, rec in self._index.items():
                if rec.execution_id == session_id or (rec.metadata and rec.metadata.get("session_id") == session_id):
                    matching_ids.append(aid)

            for aid in matching_ids:
                if self.delete(aid):
                    deleted_count += 1

            # Also scan category directories for any orphaned sidecar/files containing session_id
            for cat in ArtifactCategory:
                cat_dir = self._get_category_dir(cat)
                if cat_dir.exists():
                    for meta_file in list(cat_dir.glob("*.meta.json")):
                        try:
                            with open(meta_file, "r", encoding="utf-8") as f:
                                meta_data = json.load(f)
                            if meta_data.get("session_id") == session_id or meta_data.get("execution_id") == session_id:
                                aid = meta_data.get("artifact_id")
                                if aid:
                                    self.delete(aid)
                                else:
                                    meta_file.unlink(missing_ok=True)
                                deleted_count += 1
                        except Exception:
                            pass

        return deleted_count


    def get_summary(self) -> Dict[str, Any]:
        """Returns statistical overview of stored artifacts across all categories."""
        summary: Dict[str, Any] = {
            "total_artifacts": 0,
            "total_size_bytes": 0,
            "categories": {},
        }
        for cat in ArtifactCategory:
            summary["categories"][cat.value] = {"count": 0, "size_bytes": 0}

        with self._lock:
            for rec in self._index.values():
                summary["total_artifacts"] += 1
                summary["total_size_bytes"] += rec.size_bytes
                c_key = rec.category
                if c_key in summary["categories"]:
                    summary["categories"][c_key]["count"] += 1
                    summary["categories"][c_key]["size_bytes"] += rec.size_bytes

        return summary
