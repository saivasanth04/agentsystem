"""
Workspace file management tool for agents to read, write, and list code files safely.
Enforces strict workspace path boundaries and safe path resolution against directory traversal.
"""
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

try:
    from ..config import config
except (ImportError, ValueError):
    from agent_orchestrator.config import config

IGNORE_DIRS = {"__pycache__", ".git", ".pytest_cache", "node_modules", ".venv", "venv"}
IGNORE_EXTS = {".pyc", ".pyo", ".pyd", ".png", ".jpg", ".jpeg", ".ico", ".bin", ".exe"}


class PathTraversalError(ValueError):
    """Raised when an operation attempts to access or modify paths outside the designated workspace root."""
    pass


class MergeConflictError(Exception):
    """Raised when merging a sandboxed workspace into the main workspace encounters conflicting changes."""
    def __init__(
        self,
        filepath: str,
        message: str = "",
        base_hash: Optional[str] = None,
        current_hash: Optional[str] = None,
        sandbox_id: Optional[str] = None,
    ):
        self.filepath = filepath
        self.base_hash = base_hash
        self.current_hash = current_hash
        self.sandbox_id = sandbox_id
        super().__init__(message or f"Merge conflict on '{filepath}': main workspace was modified after sandbox '{sandbox_id}' branched.")


def three_way_merge_text(base_text: str, ours_text: str, theirs_text: str) -> Tuple[bool, str]:
    """
    Performs a 3-way line merge between base, ours (sandbox), and theirs (main).
    Returns (success: bool, merged_content: str).
    """
    if ours_text == theirs_text:
        return True, ours_text
    if ours_text == base_text:
        return True, theirs_text
    if theirs_text == base_text:
        return True, ours_text

    import difflib
    base_lines = base_text.splitlines(keepends=True)
    ours_lines = ours_text.splitlines(keepends=True)
    theirs_lines = theirs_text.splitlines(keepends=True)

    sm_ours = difflib.SequenceMatcher(None, base_lines, ours_lines)
    sm_theirs = difflib.SequenceMatcher(None, base_lines, theirs_lines)

    ours_changes = [
        (b1, b2, ours_lines[o1:o2])
        for tag, b1, b2, o1, o2 in sm_ours.get_opcodes()
        if tag != "equal"
    ]
    theirs_changes = [
        (b1, b2, theirs_lines[t1:t2])
        for tag, b1, b2, t1, t2 in sm_theirs.get_opcodes()
        if tag != "equal"
    ]

    # Check for overlapping base line ranges
    conflict = False
    for ob1, ob2, olines in ours_changes:
        for tb1, tb2, tlines in theirs_changes:
            overlaps = not (ob2 <= tb1 or tb2 <= ob1)
            same_insert_point = (ob1 == ob2 and tb1 == tb2 and ob1 == tb1)
            if overlaps or same_insert_point:
                if olines != tlines:
                    conflict = True
                    break
        if conflict:
            break

    if conflict:
        return False, ""

    # Combine non-overlapping changes and sort by base line
    all_changes = list(ours_changes)
    for b1, b2, lines in theirs_changes:
        if not any(cb1 == b1 and cb2 == b2 and clines == lines for cb1, cb2, clines in all_changes):
            all_changes.append((b1, b2, lines))

    all_changes.sort(key=lambda c: (c[0], c[1]))

    result = []
    curr = 0
    for b1, b2, lines in all_changes:
        if b1 > curr:
            result.extend(base_lines[curr:b1])
        result.extend(lines)
        curr = max(curr, b2)
    if curr < len(base_lines):
        result.extend(base_lines[curr:])

    return True, "".join(result)


def safe_resolve_path(root_dir: Union[str, Path], user_path: Union[str, Path]) -> Path:
    """
    Safely resolves user-provided paths relative to the specified root directory.
    Guarantees that the resolved path stays strictly within the root directory boundary.
    Supports valid absolute paths that reside inside root_dir.
    Raises PathTraversalError on traversal attempts or null byte injection.
    """
    if not user_path:
        raise PathTraversalError("Path cannot be empty.")

    str_path = str(user_path).strip()
    if "\0" in str_path:
        raise PathTraversalError("Null byte detected in path.")

    root = Path(root_dir).resolve()
    clean_path = Path(str_path)

    if clean_path.is_absolute():
        candidate = clean_path.resolve()
    else:
        candidate = (root / clean_path).resolve()

    try:
        if not candidate.is_relative_to(root):
            raise PathTraversalError(f"Path traversal denied: '{user_path}' is outside root '{root}'")
    except (ValueError, AttributeError):
        try:
            candidate.relative_to(root)
        except ValueError:
            raise PathTraversalError(f"Path traversal denied: '{user_path}' is outside root '{root}'")

    return candidate


class WorkspaceManager:
    """
    Manages workspace files, guaranteeing strict confinement to root_dir.
    Supports optimistic concurrency control (expected_hash) and idempotent mutations.
    Provides per-file re-entrant locking to serialize concurrent edits.
    """

    def __init__(
        self,
        root_dir: Optional[Union[Path, str]] = None,
        ledger: Optional[Any] = None,
        file_access_policy: Optional[Any] = None,
        resource_budget: Optional[Any] = None,
        resource_tracker: Optional[Any] = None,
        mutation_authorizer: Optional[Any] = None,
    ):
        self.root_dir = Path(root_dir).resolve() if root_dir else Path(config.workspace_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        from .change_tracker import ChangeJournal
        from ..security.file_access_policy import FileAccessPolicy
        if isinstance(file_access_policy, dict):
            self.file_access_policy = FileAccessPolicy.from_dict(file_access_policy)
        elif isinstance(file_access_policy, FileAccessPolicy):
            self.file_access_policy = file_access_policy
        else:
            self.file_access_policy = file_access_policy or FileAccessPolicy()
        self.resource_budget = resource_budget
        self.resource_tracker = resource_tracker
        if self.resource_budget and not self.resource_tracker:
            from ..security.resource_budget import ResourceUsageTracker
            self.resource_tracker = ResourceUsageTracker(budget=self.resource_budget)

        from ..security.mutation_authorizer import MutationAuthorizer, MutationPolicy
        if isinstance(mutation_authorizer, dict):
            self.mutation_authorizer = MutationAuthorizer(policy=MutationPolicy.from_dict(mutation_authorizer))
        elif isinstance(mutation_authorizer, MutationAuthorizer):
            self.mutation_authorizer = mutation_authorizer
        elif isinstance(mutation_authorizer, MutationPolicy):
            self.mutation_authorizer = MutationAuthorizer(policy=mutation_authorizer)
        else:
            self.mutation_authorizer = mutation_authorizer
        self.change_journal = ChangeJournal()
        self.file_change_listeners: List[Callable[[str, str], None]] = []
        self.ledger = ledger
        self.event_bus: Optional[Any] = None
        self.telemetry_engine: Optional[Any] = None
        self.accessed_files: Dict[str, Set[str]] = {"read": set(), "written": set(), "deleted": set()}
        self._meta_lock = threading.RLock()
        self._file_locks: Dict[str, threading.RLock] = {}
        self._file_revisions: Dict[str, int] = {}

    def set_mutation_authorizer(self, authorizer: Any) -> None:
        """Configures or updates the active MutationAuthorizer."""
        from ..security.mutation_authorizer import MutationAuthorizer, MutationPolicy
        if isinstance(authorizer, dict):
            self.mutation_authorizer = MutationAuthorizer(policy=MutationPolicy.from_dict(authorizer))
        elif isinstance(authorizer, MutationAuthorizer):
            self.mutation_authorizer = authorizer
        elif isinstance(authorizer, MutationPolicy):
            self.mutation_authorizer = MutationAuthorizer(policy=authorizer)
        else:
            self.mutation_authorizer = authorizer

    def set_resource_budget(self, budget: Any) -> None:
        """Configures or updates active ResourceBudget."""
        from ..security.resource_budget import ResourceBudget, ResourceUsageTracker
        if isinstance(budget, dict):
            self.resource_budget = ResourceBudget.from_dict(budget)
        elif isinstance(budget, ResourceBudget):
            self.resource_budget = budget
        else:
            self.resource_budget = budget

        if self.resource_budget:
            if not self.resource_tracker:
                self.resource_tracker = ResourceUsageTracker(budget=self.resource_budget)
            elif hasattr(self.resource_tracker, "set_budget"):
                self.resource_tracker.set_budget(self.resource_budget)

    def set_resource_tracker(self, tracker: Any) -> None:
        """Configures or updates active ResourceUsageTracker."""
        self.resource_tracker = tracker

    def set_file_access_policy(self, policy: Any) -> None:
        """Configures or updates the active FileAccessPolicy."""
        from ..security.file_access_policy import FileAccessPolicy
        if isinstance(policy, dict):
            self.file_access_policy = FileAccessPolicy.from_dict(policy)
        elif isinstance(policy, FileAccessPolicy):
            self.file_access_policy = policy
        else:
            self.file_access_policy = policy or FileAccessPolicy()

    def set_event_bus(self, event_bus: Any) -> None:
        """Attaches an EventBus instance to broadcast file mutations."""
        self.event_bus = event_bus

    def set_telemetry_engine(self, telemetry_engine: Any) -> None:
        """Attaches a TelemetryEngine instance to track file access footprints."""
        self.telemetry_engine = telemetry_engine

    def get_accessed_files(self) -> Dict[str, List[str]]:
        """Returns session file access footprint categorized by access type."""
        with self._meta_lock:
            r = self.accessed_files["read"]
            w = self.accessed_files["written"]
            d = self.accessed_files["deleted"]
            return {
                "read": sorted(list(r)),
                "written": sorted(list(w)),
                "deleted": sorted(list(d)),
                "distinct": sorted(list(r | w | d)),
            }

    def _get_file_lock(self, rel_path: Union[str, Path]) -> threading.RLock:
        """Returns a re-entrant lock specific to a normalized relative file path."""
        norm = str(rel_path).replace("\\", "/").strip("/").lower()
        with self._meta_lock:
            if norm not in self._file_locks:
                self._file_locks[norm] = threading.RLock()
            return self._file_locks[norm]

    def get_file_revision(self, rel_path: Union[str, Path]) -> int:
        """Returns the monotonic generation revision integer for a file (starting at 1)."""
        norm = str(rel_path).replace("\\", "/").strip("/").lower()
        with self._meta_lock:
            return self._file_revisions.get(norm, 1)

    def get_file_version_tag(self, rel_path: Union[str, Path]) -> str:
        """Returns a human-and-machine readable version tag, e.g. 'v1-a3f10c9b'."""
        norm = str(rel_path).replace("\\", "/").strip("/").lower()
        content = self.read_file(norm)
        if content is None:
            return "none"
        from .change_tracker import compute_sha256
        h = compute_sha256(content)
        rev = self.get_file_revision(norm)
        return f"v{rev}-{h[:8]}"

    def _validate_expected_version(
        self,
        rel_norm: str,
        expected_version: Optional[str] = None,
        expected_hash: Optional[str] = None,
        current_hash: Optional[str] = None,
    ) -> None:
        """
        Validates expected_version or expected_hash against current file state.
        Accepts:
        - Full 64-char SHA-256 hash
        - Short hex prefix (>= 6 chars)
        - Monotonic revision string (e.g. 'v1', 'v2', '1', '2')
        - Combined tag (e.g. 'v1-a3f10c9b')
        """
        target_check = expected_version if expected_version is not None else expected_hash
        if target_check is None:
            return

        from ..runtime.idempotency import ConcurrencyConflictError

        target_check_clean = str(target_check).strip()
        current_rev = self.get_file_revision(rel_norm)
        rev_tag = f"v{current_rev}"
        short_hash = current_hash[:12] if current_hash else ""
        full_tag = f"v{current_rev}-{current_hash[:8]}" if current_hash else "none"

        # 1. Check if file doesn't exist currently
        if current_hash is None:
            if target_check_clean.lower() not in ("none", "null", "new", ""):
                raise ConcurrencyConflictError(
                    f"Concurrency conflict on '{rel_norm}': File does not exist, but expected version was '{target_check_clean}'."
                )
            return

        # 2. Match against full SHA-256
        if target_check_clean.lower() == current_hash.lower():
            return

        # 3. Match against short SHA-256 prefix (min 6 chars)
        if len(target_check_clean) >= 6 and current_hash.lower().startswith(target_check_clean.lower()):
            return

        # 4. Match against monotonic revision (e.g. 'v1', '1')
        if target_check_clean.lower() in (rev_tag.lower(), str(current_rev)):
            return

        # 5. Match against combined tag or tag prefix
        if target_check_clean.lower() == full_tag.lower():
            return
        if target_check_clean.lower().startswith("v") and "-" in target_check_clean:
            parts = target_check_clean.split("-", 1)
            if parts[0].lower() == rev_tag.lower() and len(parts[1]) >= 6 and current_hash.lower().startswith(parts[1].lower()):
                return

        # Mismatch! Raise informative ConcurrencyConflictError
        raise ConcurrencyConflictError(
            f"Concurrency conflict on '{rel_norm}': File has changed since it was read. "
            f"Expected version: '{target_check_clean}', current version: '{short_hash}' ({rev_tag}). "
            f"Please call read_file('{rel_norm}') to inspect the latest content before editing."
        )

    def register_file_change_listener(self, listener: Callable[[str, str], None]) -> None:
        """Registers a callback to receive (rel_path: str, change_type: str) mutation events."""
        if listener not in self.file_change_listeners:
            self.file_change_listeners.append(listener)

    def unregister_file_change_listener(self, listener: Callable[[str, str], None]) -> None:
        """Unregisters a previously added mutation callback."""
        if listener in self.file_change_listeners:
            self.file_change_listeners.remove(listener)

    def get_git_info(self) -> Any:
        """Inspects and returns git repository status for the workspace root."""
        try:
            from ..runtime.repo_bootstrap import detect_git
            return detect_git(self.root_dir)
        except Exception:
            return None

    def _notify_file_changed(self, rel_path: str, change_type: str) -> None:
        """Dispatches file mutation event to all registered listeners."""
        norm = str(rel_path).replace("\\", "/").strip("/").lower()
        with self._meta_lock:
            prev = self._file_revisions.get(norm)
            if prev is None:
                self._file_revisions[norm] = 1 if change_type == "CREATED" else 2
            else:
                self._file_revisions[norm] = prev + 1
        for listener in self.file_change_listeners:
            try:
                listener(rel_path, change_type)
            except Exception:
                pass
        if getattr(self, "event_bus", None):
            try:
                from ..runtime.event_bus import EventType
                self.event_bus.publish(
                    EventType.FILE_CHANGED,
                    payload={
                        "filepath": str(rel_path).replace("\\", "/"),
                        "change_type": change_type,
                        "revision": self.get_file_revision(rel_path),
                        "version": self.get_file_version_tag(rel_path),
                    },
                )
            except Exception:
                pass

    def write_file(
        self,
        rel_path: str,
        content: str,
        expected_hash: Optional[str] = None,
        operation_id: Optional[str] = None,
        expected_version: Optional[str] = None,
        agent_role: Optional[str] = None,
        task_scope: Optional[Dict[str, Any]] = None,
        task_permissions: Optional[Any] = None,
        is_approved: bool = False,
        **kwargs: Any,
    ) -> Path:
        """Write content to a file safely within the workspace directory with OCC, versioning, idempotency, and mutation authorization."""
        target_path = safe_resolve_path(self.root_dir, rel_path)
        if target_path == self.root_dir:
            raise PathTraversalError("Cannot write directly to workspace root directory as a file.")

        rel_norm = self.get_relative_path(target_path)
        if rel_norm == ".git" or rel_norm.startswith(".git/"):
            raise PermissionError(f"Cannot write directly to git repository internals: '{rel_path}'")

        if hasattr(self, "mutation_authorizer") and self.mutation_authorizer:
            from ..security.mutation_authorizer import MutationAuthorizationError, MutationType
            before_content_peek = None
            if target_path.is_file():
                try:
                    with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                        before_content_peek = f.read()
                except Exception:
                    pass

            mut_res = self.mutation_authorizer.authorize_mutation(
                filepath=rel_norm,
                new_content=content,
                old_content=before_content_peek,
                mutation_type=MutationType.UPDATE_FILE if before_content_peek is not None else MutationType.CREATE_FILE,
                agent_role=agent_role,
                task_scope=task_scope,
                task_permissions=task_permissions,
                is_approved=is_approved,
            )
            if not mut_res.allowed:
                raise MutationAuthorizationError(
                    mut_res.reason,
                    path=rel_norm,
                    risk_level=mut_res.risk_level,
                    suggested_action=mut_res.suggested_action,
                )

        if hasattr(self, "file_access_policy") and self.file_access_policy:
            from ..security.file_access_policy import FileAccessMode, FileAccessDeniedError
            decision = self.file_access_policy.evaluate(rel_norm, FileAccessMode.WRITE)
            if not decision.allowed:
                raise FileAccessDeniedError(decision.reason, path=rel_norm, mode=FileAccessMode.WRITE)

        with self._get_file_lock(rel_norm):
            from .change_tracker import compute_sha256
            from ..runtime.idempotency import ConcurrencyConflictError, OperationRecord, global_operation_ledger

            before_content = self.read_file(rel_path)
            current_hash = compute_sha256(before_content) if before_content is not None else None

            # 1. Optimistic Concurrency Control (Compare-And-Swap / Version Check)
            self._validate_expected_version(
                rel_norm=rel_norm,
                expected_version=expected_version,
                expected_hash=expected_hash,
                current_hash=current_hash,
            )

            target_hash = compute_sha256(content)

            # 2. Idempotency Check: if identical content already exists, skip write and events
            if before_content is not None and current_hash == target_hash:
                effective_ledger = self.ledger or global_operation_ledger
                if operation_id:
                    effective_ledger.record(OperationRecord(
                        operation_id=operation_id,
                        tool_name="write_file",
                        filepath=rel_norm,
                        arguments={"filepath": rel_norm, "content": content},
                        before_hash=current_hash,
                        after_hash=target_hash,
                        result={"filepath": rel_norm, "bytes_written": len(content.encode("utf-8")), "no_op": True, "already_applied": True, "success": True},
                        status="NO_OP",
                        no_op=True,
                    ))
                return target_path

            # Check resource budget for disk write
            content_bytes = len(content.encode("utf-8")) if isinstance(content, str) else len(content)
            if hasattr(self, "resource_tracker") and self.resource_tracker:
                dec = self.resource_tracker.record_file_write(rel_norm, content_bytes)
                if not dec.allowed:
                    from ..security.resource_budget import ResourceBudgetExceededError
                    raise ResourceBudgetExceededError(
                        message=dec.reason,
                        resource_type=dec.resource_type,
                        limit=dec.limit,
                        current=dec.current,
                        suggested_action=dec.suggested_action,
                    )
            elif hasattr(self, "resource_budget") and self.resource_budget:
                rb = self.resource_budget
                single_mb = content_bytes / (1024.0 * 1024.0)
                if getattr(rb, "max_single_file_mb", 0) > 0 and single_mb > rb.max_single_file_mb:
                    from ..security.resource_budget import ResourceBudgetExceededError
                    raise ResourceBudgetExceededError(
                        message=f"Single file write size exceeded: {single_mb:.2f}MB > {rb.max_single_file_mb:.2f}MB limit for '{rel_norm}'.",
                        resource_type="single_file_size",
                        limit=rb.max_single_file_mb,
                        current=round(single_mb, 2),
                        suggested_action="Reduce file content size or increase max_single_file_mb.",
                    )

            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8", errors="replace")

            # Post-write assertion: verify file was created on disk
            if not target_path.exists() or not target_path.is_file():
                raise IOError(f"Failed to create file on disk: '{rel_path}'")

            change_type = "CREATED" if before_content is None else "MODIFIED"
            with self._meta_lock:
                self.accessed_files["written"].add(rel_norm)
            if self.telemetry_engine:
                try:
                    self.telemetry_engine.record_file_access(rel_norm, change_type)
                except Exception:
                    pass
            self.change_journal.record_mutation(rel_norm, before_content, content)
            self._notify_file_changed(rel_norm, change_type)

            effective_ledger = self.ledger or global_operation_ledger
            if operation_id:
                effective_ledger.record(OperationRecord(
                    operation_id=operation_id,
                    tool_name="write_file",
                    filepath=rel_norm,
                    arguments={"filepath": rel_norm, "content": content},
                    before_hash=current_hash,
                    after_hash=target_hash,
                    result={"filepath": rel_norm, "bytes_written": len(content.encode("utf-8")), "no_op": False, "already_applied": False, "success": True},
                    status="COMMITTED",
                    no_op=False,
                ))

            return target_path

    def read_file(self, rel_path: str) -> Optional[str]:
        """Read content of a file safely from within the workspace directory."""
        target_path = safe_resolve_path(self.root_dir, rel_path)
        rel_norm = self.get_relative_path(target_path)

        if hasattr(self, "file_access_policy") and self.file_access_policy:
            from ..security.file_access_policy import FileAccessMode, FileAccessDeniedError
            decision = self.file_access_policy.evaluate(rel_norm, FileAccessMode.READ)
            if not decision.allowed:
                raise FileAccessDeniedError(decision.reason, path=rel_norm, mode=FileAccessMode.READ)

        if target_path.exists() and target_path.is_file():
            if target_path.suffix.lower() in IGNORE_EXTS:
                return None
            with self._meta_lock:
                self.accessed_files["read"].add(rel_norm)
            if self.telemetry_engine:
                try:
                    self.telemetry_engine.record_file_access(rel_norm, "READ")
                except Exception:
                    pass
            with self._get_file_lock(rel_norm):
                try:
                    return target_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    return None
        return None

    def file_exists(self, rel_path: str) -> bool:
        """Check if a file exists safely within the workspace directory."""
        target_path = safe_resolve_path(self.root_dir, rel_path)
        if hasattr(self, "file_access_policy") and self.file_access_policy:
            from ..security.file_access_policy import FileAccessMode
            rel_norm = self.get_relative_path(target_path)
            if not self.file_access_policy.evaluate(rel_norm, FileAccessMode.READ).allowed:
                return False
        return target_path.exists() and target_path.is_file()

    def delete_file(
        self,
        rel_path: str,
        recursive: bool = True,
        expected_version: Optional[str] = None,
        expected_hash: Optional[str] = None,
        operation_id: Optional[str] = None,
        agent_role: Optional[str] = None,
        task_scope: Optional[Dict[str, Any]] = None,
        task_permissions: Optional[Any] = None,
        is_approved: bool = False,
        **kwargs: Any,
    ) -> bool:
        """Deletes a file or directory safely within the workspace directory with OCC, idempotency, and mutation authorization."""
        from ..runtime.idempotency import OperationRecord, global_operation_ledger
        effective_ledger = self.ledger or global_operation_ledger
        if operation_id:
            cached = effective_ledger.get(operation_id)
            if cached and cached.status in ("COMMITTED", "NO_OP"):
                return True

        target_path = safe_resolve_path(self.root_dir, rel_path)
        if target_path == self.root_dir:
            raise PathTraversalError("Cannot delete workspace root directory.")

        rel_norm = self.get_relative_path(target_path)
        if rel_norm == ".git" or rel_norm.startswith(".git/"):
            raise PermissionError(f"Cannot delete git repository internals: '{rel_path}'")

        if hasattr(self, "mutation_authorizer") and self.mutation_authorizer:
            from ..security.mutation_authorizer import MutationAuthorizationError, MutationType
            before_content_peek = None
            if target_path.is_file():
                try:
                    with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                        before_content_peek = f.read()
                except Exception:
                    pass

            mut_res = self.mutation_authorizer.authorize_mutation(
                filepath=rel_norm,
                new_content=None,
                old_content=before_content_peek,
                mutation_type=MutationType.DELETE_FILE,
                agent_role=agent_role,
                task_scope=task_scope,
                task_permissions=task_permissions,
                is_approved=is_approved,
            )
            if not mut_res.allowed:
                raise MutationAuthorizationError(
                    mut_res.reason,
                    path=rel_norm,
                    risk_level=mut_res.risk_level,
                    suggested_action=mut_res.suggested_action,
                )

        if hasattr(self, "file_access_policy") and self.file_access_policy:
            from ..security.file_access_policy import FileAccessMode, FileAccessDeniedError
            decision = self.file_access_policy.evaluate(rel_norm, FileAccessMode.DELETE)
            if not decision.allowed:
                raise FileAccessDeniedError(decision.reason, path=rel_norm, mode=FileAccessMode.DELETE)

        if not target_path.exists():
            if operation_id:
                effective_ledger.record(OperationRecord(
                    operation_id=operation_id,
                    tool_name="delete_file",
                    filepath=rel_norm,
                    arguments={"filepath": rel_norm},
                    result={"filepath": rel_norm, "no_op": True, "success": True},
                    status="NO_OP",
                    no_op=True,
                ))
            return False

        with self._get_file_lock(rel_norm):
            from .change_tracker import compute_sha256
            from ..runtime.idempotency import OperationRecord, global_operation_ledger

            before_content = None
            if target_path.is_file():
                try:
                    with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                        before_content = f.read()
                except Exception:
                    pass

            current_hash = compute_sha256(before_content) if before_content is not None else None

            # Validate OCC version / hash
            self._validate_expected_version(
                rel_norm=rel_norm,
                expected_version=expected_version,
                expected_hash=expected_hash,
                current_hash=current_hash,
            )

            if target_path.is_file():
                target_path.unlink()
            elif target_path.is_dir():
                shutil.rmtree(target_path)

            with self._meta_lock:
                self.accessed_files["deleted"].add(rel_norm)
            if self.telemetry_engine:
                try:
                    self.telemetry_engine.record_file_access(rel_norm, "DELETED")
                except Exception:
                    pass

            self.change_journal.record_mutation(rel_norm, before_content, None)
            self._notify_file_changed(rel_norm, "DELETED")

            effective_ledger = self.ledger or global_operation_ledger
            if operation_id:
                effective_ledger.record(OperationRecord(
                    operation_id=operation_id,
                    tool_name="delete_file",
                    filepath=rel_norm,
                    arguments={"filepath": rel_norm},
                    before_hash=current_hash,
                    after_hash=None,
                    result={"filepath": rel_norm, "deleted": True, "success": True},
                    status="COMMITTED",
                    no_op=False,
                ))

            return True

    def rename_file(
        self,
        old_path: str,
        new_path: str,
        expected_version: Optional[str] = None,
        expected_hash: Optional[str] = None,
        overwrite: bool = False,
        operation_id: Optional[str] = None,
        agent_role: Optional[str] = None,
        task_scope: Optional[Dict[str, Any]] = None,
        task_permissions: Optional[Any] = None,
        is_approved: bool = False,
    ) -> bool:
        """Renames a file safely within workspace with OCC and mutation authorization."""
        target_old = safe_resolve_path(self.root_dir, old_path)
        target_new = safe_resolve_path(self.root_dir, new_path)

        if target_old == self.root_dir or target_new == self.root_dir:
            raise PathTraversalError("Cannot rename workspace root directory.")

        old_norm = self.get_relative_path(target_old)
        new_norm = self.get_relative_path(target_new)

        if not target_old.exists():
            raise FileNotFoundError(f"Source file '{old_path}' not found.")

        if target_new.exists() and not overwrite:
            raise FileExistsError(f"Destination file '{new_path}' already exists. Set overwrite=True to replace.")

        # Authorize delete on old_path and create on new_path
        if hasattr(self, "mutation_authorizer") and self.mutation_authorizer:
            from ..security.mutation_authorizer import MutationAuthorizationError, MutationType
            before_content_peek = None
            if target_old.is_file():
                try:
                    with open(target_old, "r", encoding="utf-8", errors="replace") as f:
                        before_content_peek = f.read()
                except Exception:
                    pass

            # Check new path creation
            mut_res = self.mutation_authorizer.authorize_mutation(
                filepath=new_norm,
                new_content=before_content_peek,
                old_content=None,
                mutation_type=MutationType.CREATE_FILE,
                agent_role=agent_role,
                task_scope=task_scope,
                task_permissions=task_permissions,
                is_approved=is_approved,
            )
            if not mut_res.allowed:
                raise MutationAuthorizationError(
                    mut_res.reason,
                    path=new_norm,
                    risk_level=mut_res.risk_level,
                    suggested_action=mut_res.suggested_action,
                )

        if hasattr(self, "file_access_policy") and self.file_access_policy:
            from ..security.file_access_policy import FileAccessMode, FileAccessDeniedError
            dec_del = self.file_access_policy.evaluate(old_norm, FileAccessMode.DELETE)
            if not dec_del.allowed:
                raise FileAccessDeniedError(dec_del.reason, path=old_norm, mode=FileAccessMode.DELETE)
            dec_wr = self.file_access_policy.evaluate(new_norm, FileAccessMode.WRITE)
            if not dec_wr.allowed:
                raise FileAccessDeniedError(dec_wr.reason, path=new_norm, mode=FileAccessMode.WRITE)

        with self._get_file_lock(old_norm), self._get_file_lock(new_norm):
            from .change_tracker import compute_sha256
            from ..runtime.idempotency import OperationRecord, global_operation_ledger

            before_content = None
            if target_old.is_file():
                try:
                    with open(target_old, "r", encoding="utf-8", errors="replace") as f:
                        before_content = f.read()
                except Exception:
                    pass

            current_hash = compute_sha256(before_content) if before_content is not None else None
            self._validate_expected_version(
                rel_norm=old_norm,
                expected_version=expected_version,
                expected_hash=expected_hash,
                current_hash=current_hash,
            )

            target_new.parent.mkdir(parents=True, exist_ok=True)
            if target_new.exists() and overwrite:
                if target_new.is_file():
                    target_new.unlink()
                elif target_new.is_dir():
                    shutil.rmtree(target_new)

            shutil.move(str(target_old), str(target_new))

            with self._meta_lock:
                self.accessed_files["deleted"].add(old_norm)
                self.accessed_files["written"].add(new_norm)

            self.change_journal.record_mutation(old_norm, before_content, None)
            self.change_journal.record_mutation(new_norm, None, before_content)
            self._notify_file_changed(old_norm, "DELETED")
            self._notify_file_changed(new_norm, "CREATED")

            effective_ledger = self.ledger or global_operation_ledger
            if operation_id:
                effective_ledger.record(OperationRecord(
                    operation_id=operation_id,
                    tool_name="rename_file",
                    filepath=new_norm,
                    arguments={"old_path": old_norm, "new_path": new_norm},
                    before_hash=current_hash,
                    after_hash=current_hash,
                    result={"old_path": old_norm, "new_path": new_norm, "success": True},
                    status="COMMITTED",
                    no_op=False,
                ))

            return True

    def move_file(
        self,
        source_path: str,
        target_dir: str,
        expected_version: Optional[str] = None,
        expected_hash: Optional[str] = None,
        overwrite: bool = False,
        operation_id: Optional[str] = None,
        agent_role: Optional[str] = None,
        task_scope: Optional[Dict[str, Any]] = None,
        task_permissions: Optional[Any] = None,
        is_approved: bool = False,
    ) -> str:
        """Moves a file into a target directory."""
        src_path = safe_resolve_path(self.root_dir, source_path)
        dest_dir_path = safe_resolve_path(self.root_dir, target_dir)

        if not src_path.exists():
            raise FileNotFoundError(f"Source file '{source_path}' does not exist.")

        dest_file_path = dest_dir_path / src_path.name
        new_rel_path = self.get_relative_path(dest_file_path)

        self.rename_file(
            old_path=source_path,
            new_path=new_rel_path,
            expected_version=expected_version,
            expected_hash=expected_hash,
            overwrite=overwrite,
            operation_id=operation_id,
            agent_role=agent_role,
            task_scope=task_scope,
            task_permissions=task_permissions,
            is_approved=is_approved,
        )
        return new_rel_path

    def apply_patch(
        self,
        patch_content: str,
        fuzz_factor: int = 2,
        operation_id: Optional[str] = None,
        agent_role: Optional[str] = None,
        task_scope: Optional[Dict[str, Any]] = None,
        task_permissions: Optional[Any] = None,
        is_approved: bool = False,
    ) -> Dict[str, Any]:
        """Applies a unified diff patch across one or more files in the workspace atomically."""
        import re
        if not patch_content or not patch_content.strip():
            return {"success": False, "error": "Patch content cannot be empty.", "files_patched": 0, "modified_files": []}

        def _clean_diff_path(raw_path: str) -> Optional[str]:
            p = raw_path.split("\t")[0].strip()
            if p in ("/dev/null", "dev/null"):
                return None
            if p.startswith("a/") or p.startswith("b/"):
                p = p[2:]
            return p

        # 1. Parse unified diff structures
        files_to_patch = []
        lines = patch_content.splitlines()
        i = 0
        current_file = None

        while i < len(lines):
            line = lines[i]
            if line.startswith("diff --git"):
                parts = line.split()
                if len(parts) >= 4:
                    old_p = _clean_diff_path(parts[2])
                    new_p = _clean_diff_path(parts[3])
                    current_file = {"old_path": old_p, "new_path": new_p, "hunks": []}
                    files_to_patch.append(current_file)
                i += 1
                continue

            if line.startswith("--- "):
                old_p = _clean_diff_path(line[4:].strip())
                if not current_file or current_file.get("old_path") != old_p:
                    current_file = {"old_path": old_p, "new_path": None, "hunks": []}
                    files_to_patch.append(current_file)
                else:
                    current_file["old_path"] = old_p
                i += 1
                continue

            if line.startswith("+++ "):
                new_p = _clean_diff_path(line[4:].strip())
                if current_file:
                    current_file["new_path"] = new_p
                else:
                    current_file = {"old_path": None, "new_path": new_p, "hunks": []}
                    files_to_patch.append(current_file)
                i += 1
                continue

            if line.startswith("@@"):
                m = re.match(r"^@@\s*-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@", line)
                if m and current_file:
                    old_start = int(m.group(1))
                    old_count = int(m.group(2)) if m.group(2) is not None else 1
                    new_start = int(m.group(3))
                    new_count = int(m.group(4)) if m.group(4) is not None else 1
                    hunk = {
                        "old_start": old_start,
                        "old_count": old_count,
                        "new_start": new_start,
                        "new_count": new_count,
                        "lines": [],
                    }
                    current_file["hunks"].append(hunk)
                    i += 1
                    while i < len(lines):
                        hline = lines[i]
                        if hline.startswith("@@") or hline.startswith("--- ") or hline.startswith("diff --git"):
                            break
                        if hline.startswith("+"):
                            hunk["lines"].append(("+", hline[1:]))
                        elif hline.startswith("-"):
                            hunk["lines"].append(("-", hline[1:]))
                        elif hline.startswith(" "):
                            hunk["lines"].append((" ", hline[1:]))
                        elif hline.startswith("\\ No newline at end of file"):
                            pass
                        elif not hline:
                            hunk["lines"].append((" ", ""))
                        else:
                            break
                        i += 1
                    continue

            i += 1

        if not files_to_patch:
            return {"success": False, "error": "No valid unified diff hunks found in patch content.", "files_patched": 0, "modified_files": []}

        # 2. Compute candidate modifications in memory
        planned_writes: Dict[str, str] = {}
        planned_deletes: List[str] = []

        for f_entry in files_to_patch:
            target_rel = f_entry["new_path"] or f_entry["old_path"]
            if not target_rel:
                continue

            # Creation
            if f_entry["old_path"] is None or f_entry["old_path"] == "/dev/null":
                created_lines = []
                for hunk in f_entry["hunks"]:
                    for op, ltext in hunk["lines"]:
                        if op in ("+", " "):
                            created_lines.append(ltext)
                planned_writes[target_rel] = "\n".join(created_lines) + ("\n" if created_lines else "")
                continue

            # Deletion
            if f_entry["new_path"] is None or f_entry["new_path"] == "/dev/null":
                planned_deletes.append(f_entry["old_path"])
                continue

            # Modification
            orig_content = self.read_file(f_entry["old_path"])
            if orig_content is None:
                return {
                    "success": False,
                    "error": f"Target file for patch '{f_entry['old_path']}' not found in workspace.",
                    "files_patched": 0,
                    "modified_files": [],
                }

            orig_lines = orig_content.splitlines()
            current_lines = list(orig_lines)

            # Apply hunks sequentially with line offset tracking
            line_offset = 0
            for hunk_idx, hunk in enumerate(f_entry["hunks"]):
                expected_old = [ltext for op, ltext in hunk["lines"] if op in ("-", " ")]
                new_hunk_lines = [ltext for op, ltext in hunk["lines"] if op in ("+", " ")]

                # Attempt match at target location with offset
                target_idx = max(0, hunk["old_start"] - 1 + line_offset)
                match_pos = None

                # 1. Exact match at predicted target
                if target_idx + len(expected_old) <= len(current_lines):
                    if current_lines[target_idx : target_idx + len(expected_old)] == expected_old:
                        match_pos = target_idx

                # 2. Search within fuzz window
                if match_pos is None:
                    for delta in range(1, max(len(current_lines), fuzz_factor + 10)):
                        for candidate_pos in (target_idx + delta, target_idx - delta):
                            if 0 <= candidate_pos and candidate_pos + len(expected_old) <= len(current_lines):
                                if current_lines[candidate_pos : candidate_pos + len(expected_old)] == expected_old:
                                    match_pos = candidate_pos
                                    break
                        if match_pos is not None:
                            break

                # 3. Fallback: Strip leading/trailing whitespace comparison
                if match_pos is None:
                    norm_expected = [l.strip() for l in expected_old]
                    for candidate_pos in range(len(current_lines) - len(expected_old) + 1):
                        slice_norm = [l.strip() for l in current_lines[candidate_pos : candidate_pos + len(expected_old)]]
                        if slice_norm == norm_expected:
                            match_pos = candidate_pos
                            break

                if match_pos is None:
                    return {
                        "success": False,
                        "error": f"Patch hunk #{hunk_idx + 1} at line {hunk['old_start']} failed to match in '{f_entry['old_path']}'.",
                        "files_patched": 0,
                        "modified_files": [],
                    }

                # Apply hunk replacement
                current_lines[match_pos : match_pos + len(expected_old)] = new_hunk_lines
                line_offset += (len(new_hunk_lines) - len(expected_old))

            ends_with_newline = orig_content.endswith("\n")
            result_text = "\n".join(current_lines) + ("\n" if ends_with_newline and current_lines else "")
            planned_writes[target_rel] = result_text

        # 3. Apply all writes and deletes atomically with snapshot backup for rollback
        snapshot_backups: Dict[str, Optional[str]] = {}
        for p_path in list(planned_writes.keys()) + planned_deletes:
            snapshot_backups[p_path] = self.read_file(p_path)

        modified_list = []
        try:
            for p_path, p_content in planned_writes.items():
                self.write_file(
                    p_path,
                    p_content,
                    agent_role=agent_role,
                    task_scope=task_scope,
                    task_permissions=task_permissions,
                    is_approved=is_approved,
                )
                modified_list.append(p_path)

            for d_path in planned_deletes:
                self.delete_file(
                    d_path,
                    agent_role=agent_role,
                    task_scope=task_scope,
                    task_permissions=task_permissions,
                    is_approved=is_approved,
                )
                modified_list.append(d_path)

            return {
                "success": True,
                "files_patched": len(modified_list),
                "modified_files": modified_list,
                "count": len(modified_list),
            }
        except Exception as e:
            # Atomic rollback of all changes
            for b_path, b_content in snapshot_backups.items():
                try:
                    t_path = safe_resolve_path(self.root_dir, b_path)
                    if b_content is None:
                        if t_path.exists():
                            if t_path.is_file():
                                t_path.unlink()
                            elif t_path.is_dir():
                                shutil.rmtree(t_path)
                    else:
                        t_path.parent.mkdir(parents=True, exist_ok=True)
                        t_path.write_text(b_content, encoding="utf-8", errors="replace")
                except Exception:
                    pass

            return {
                "success": False,
                "error": f"Failed applying patch: {str(e)}",
                "files_patched": 0,
                "modified_files": [],
            }

    def get_relative_path(self, path: Union[str, Path]) -> str:
        """Returns safe relative path normalized with forward slashes."""
        resolved = safe_resolve_path(self.root_dir, path)
        return str(resolved.relative_to(self.root_dir)).replace("\\", "/")

    def list_files(self) -> List[str]:
        """List all text/code relative file paths in workspace, ignoring cache/binaries and blocked paths."""
        files = []
        for p in self.root_dir.rglob("*"):
            if p.is_file():
                rel = p.relative_to(self.root_dir)
                if any(part in IGNORE_DIRS for part in rel.parts):
                    continue
                if p.suffix.lower() in IGNORE_EXTS:
                    continue
                rel_str = str(rel).replace("\\", "/")
                if hasattr(self, "file_access_policy") and self.file_access_policy:
                    from ..security.file_access_policy import FileAccessMode
                    if not self.file_access_policy.evaluate(rel_str, FileAccessMode.LIST).allowed:
                        continue
                files.append(rel_str)
        return sorted(files)

    def get_all_code_context(self, max_tokens: int = 16000) -> str:
        """
        Collect workspace files into a formatted code context string bounded by max_tokens.
        (Deprecated: Prefer RelevanceRanker or targeted read_file / find_symbol tools).
        """
        files = self.list_files()
        if not files:
            return "No files in workspace."

        snippets = []
        accumulated_chars = 0
        max_chars = max_tokens * 4

        for f in files:
            content = self.read_file(f)
            if content is not None:
                snippet = f"--- File: {f} ---\n{content}\n"
                if accumulated_chars + len(snippet) > max_chars:
                    remaining_files = len(files) - len(snippets)
                    snippets.append(f"\n... [Remaining {remaining_files} workspace files omitted to respect context budget ({max_tokens} tokens). Use read_file to inspect specific files.] ...")
                    break
                snippets.append(snippet)
                accumulated_chars += len(snippet)

        return "\n".join(snippets)

    def replace_file_content(
        self,
        rel_path: str,
        target_content: str,
        replacement_content: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        allow_multiple: bool = False,
        fuzzy: bool = True,
        expected_hash: Optional[str] = None,
        operation_id: Optional[str] = None,
        expected_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Surgically replaces target_content with replacement_content in a workspace file with OCC, versioning, and idempotency."""
        from .diff_editor import search_and_replace
        from .change_tracker import compute_sha256
        from ..runtime.idempotency import ConcurrencyConflictError, OperationRecord, global_operation_ledger

        target_path = safe_resolve_path(self.root_dir, rel_path)
        if not target_path.exists() or not target_path.is_file():
            raise FileNotFoundError(f"File '{rel_path}' does not exist.")

        rel_norm = self.get_relative_path(target_path)
        if hasattr(self, "file_access_policy") and self.file_access_policy:
            from ..security.file_access_policy import FileAccessMode, FileAccessDeniedError
            decision = self.file_access_policy.evaluate(rel_norm, FileAccessMode.WRITE)
            if not decision.allowed:
                raise FileAccessDeniedError(decision.reason, path=rel_norm, mode=FileAccessMode.WRITE)

        with self._get_file_lock(rel_norm):
            content = target_path.read_text(encoding="utf-8", errors="replace")
            current_hash = compute_sha256(content)

            self._validate_expected_version(
                rel_norm=rel_norm,
                expected_version=expected_version,
                expected_hash=expected_hash,
                current_hash=current_hash,
            )

            new_content, count, diff = search_and_replace(
                content=content,
                search=target_content,
                replace=replacement_content,
                filepath=rel_norm,
                start_line=start_line,
                end_line=end_line,
                allow_multiple=allow_multiple,
                fuzzy=fuzzy,
            )

            effective_ledger = self.ledger or global_operation_ledger
            target_hash = compute_sha256(new_content)

            # Idempotency check: if no change occurred (e.g. replacement already applied)
            if new_content == content:
                result = {
                    "filepath": rel_norm,
                    "occurrences_replaced": count,
                    "diff": "",
                    "no_op": True,
                    "already_applied": True,
                    "success": True,
                }
                if operation_id:
                    effective_ledger.record(OperationRecord(
                        operation_id=operation_id,
                        tool_name="replace_file_content",
                        filepath=rel_norm,
                        arguments={"filepath": rel_norm, "target_content": target_content, "replacement_content": replacement_content},
                        before_hash=current_hash,
                        after_hash=target_hash,
                        result=result,
                        status="NO_OP",
                        no_op=True,
                    ))
                return result

            target_path.write_text(new_content, encoding="utf-8", errors="replace")
            with self._meta_lock:
                self.accessed_files["written"].add(rel_norm)
            if self.telemetry_engine:
                try:
                    self.telemetry_engine.record_file_access(rel_norm, "MODIFIED")
                except Exception:
                    pass
            self.change_journal.record_mutation(rel_norm, content, new_content)
            self._notify_file_changed(rel_norm, "MODIFIED")

            result = {
                "filepath": rel_norm,
                "occurrences_replaced": count,
                "diff": diff,
                "no_op": False,
                "already_applied": False,
                "success": True,
            }
            if operation_id:
                effective_ledger.record(OperationRecord(
                    operation_id=operation_id,
                    tool_name="replace_file_content",
                    filepath=rel_norm,
                    arguments={"filepath": rel_norm, "target_content": target_content, "replacement_content": replacement_content},
                    before_hash=current_hash,
                    after_hash=target_hash,
                    result=result,
                    status="COMMITTED",
                    no_op=False,
                ))
            return result

    def insert_lines(
        self,
        rel_path: str,
        line_number: int,
        content: str,
        position: str = "after",
        expected_hash: Optional[str] = None,
        operation_id: Optional[str] = None,
        expected_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inserts content before or after a line in a workspace file with OCC, versioning, and idempotency."""
        from .diff_editor import insert_lines as do_insert
        from .change_tracker import compute_sha256
        from ..runtime.idempotency import ConcurrencyConflictError, OperationRecord, global_operation_ledger

        target_path = safe_resolve_path(self.root_dir, rel_path)
        if not target_path.exists() or not target_path.is_file():
            raise FileNotFoundError(f"File '{rel_path}' does not exist.")

        rel_norm = self.get_relative_path(target_path)
        if hasattr(self, "file_access_policy") and self.file_access_policy:
            from ..security.file_access_policy import FileAccessMode, FileAccessDeniedError
            decision = self.file_access_policy.evaluate(rel_norm, FileAccessMode.WRITE)
            if not decision.allowed:
                raise FileAccessDeniedError(decision.reason, path=rel_norm, mode=FileAccessMode.WRITE)

        with self._get_file_lock(rel_norm):
            current_content = target_path.read_text(encoding="utf-8", errors="replace")
            current_hash = compute_sha256(current_content)

            self._validate_expected_version(
                rel_norm=rel_norm,
                expected_version=expected_version,
                expected_hash=expected_hash,
                current_hash=current_hash,
            )

            new_content, diff = do_insert(
                content=current_content,
                line_number=line_number,
                new_content=content,
                position=position,
                filepath=rel_norm,
            )

            effective_ledger = self.ledger or global_operation_ledger
            target_hash = compute_sha256(new_content)

            if new_content == current_content:
                result = {
                    "filepath": rel_norm,
                    "diff": "",
                    "no_op": True,
                    "already_applied": True,
                    "success": True,
                }
                if operation_id:
                    effective_ledger.record(OperationRecord(
                        operation_id=operation_id,
                        tool_name="insert_lines",
                        filepath=rel_norm,
                        arguments={"filepath": rel_norm, "line_number": line_number, "content": content, "position": position},
                        before_hash=current_hash,
                        after_hash=target_hash,
                        result=result,
                        status="NO_OP",
                        no_op=True,
                    ))
                return result

            target_path.write_text(new_content, encoding="utf-8", errors="replace")
            self.change_journal.record_mutation(rel_norm, current_content, new_content)
            self._notify_file_changed(rel_norm, "MODIFIED")

            result = {
                "filepath": rel_norm,
                "diff": diff,
                "no_op": False,
                "already_applied": False,
                "success": True,
            }
            if operation_id:
                effective_ledger.record(OperationRecord(
                    operation_id=operation_id,
                    tool_name="insert_lines",
                    filepath=rel_norm,
                    arguments={"filepath": rel_norm, "line_number": line_number, "content": content, "position": position},
                    before_hash=current_hash,
                    after_hash=target_hash,
                    result=result,
                    status="COMMITTED",
                    no_op=False,
                ))
            return result

    def delete_lines(
        self,
        rel_path: str,
        start_line: int,
        end_line: int,
        expected_hash: Optional[str] = None,
        operation_id: Optional[str] = None,
        expected_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deletes a line range from a workspace file with OCC, versioning, and idempotency."""
        from .diff_editor import delete_lines as do_delete
        from .change_tracker import compute_sha256
        from ..runtime.idempotency import ConcurrencyConflictError, OperationRecord, global_operation_ledger

        target_path = safe_resolve_path(self.root_dir, rel_path)
        if not target_path.exists() or not target_path.is_file():
            raise FileNotFoundError(f"File '{rel_path}' does not exist.")

        rel_norm = self.get_relative_path(target_path)
        if hasattr(self, "file_access_policy") and self.file_access_policy:
            from ..security.file_access_policy import FileAccessMode, FileAccessDeniedError
            decision = self.file_access_policy.evaluate(rel_norm, FileAccessMode.WRITE)
            if not decision.allowed:
                raise FileAccessDeniedError(decision.reason, path=rel_norm, mode=FileAccessMode.WRITE)

        with self._get_file_lock(rel_norm):
            current_content = target_path.read_text(encoding="utf-8", errors="replace")
            current_hash = compute_sha256(current_content)

            self._validate_expected_version(
                rel_norm=rel_norm,
                expected_version=expected_version,
                expected_hash=expected_hash,
                current_hash=current_hash,
            )

            new_content, diff = do_delete(
                content=current_content,
                start_line=start_line,
                end_line=end_line,
                filepath=rel_norm,
            )

            effective_ledger = self.ledger or global_operation_ledger
            target_hash = compute_sha256(new_content)

            if new_content == current_content:
                result = {
                    "filepath": rel_norm,
                    "diff": "",
                    "no_op": True,
                    "already_applied": True,
                    "success": True,
                }
                if operation_id:
                    effective_ledger.record(OperationRecord(
                        operation_id=operation_id,
                        tool_name="delete_lines",
                        filepath=rel_norm,
                        arguments={"filepath": rel_norm, "start_line": start_line, "end_line": end_line},
                        before_hash=current_hash,
                        after_hash=target_hash,
                        result=result,
                        status="NO_OP",
                        no_op=True,
                    ))
                return result

            target_path.write_text(new_content, encoding="utf-8", errors="replace")
            self.change_journal.record_mutation(rel_norm, current_content, new_content)
            self._notify_file_changed(rel_norm, "MODIFIED")

            result = {
                "filepath": rel_norm,
                "diff": diff,
                "no_op": False,
                "already_applied": False,
                "success": True,
            }
            if operation_id:
                effective_ledger.record(OperationRecord(
                    operation_id=operation_id,
                    tool_name="delete_lines",
                    filepath=rel_norm,
                    arguments={"filepath": rel_norm, "start_line": start_line, "end_line": end_line},
                    before_hash=current_hash,
                    after_hash=target_hash,
                    result=result,
                    status="COMMITTED",
                    no_op=False,
                ))
            return result

    def apply_diff_blocks(
        self,
        rel_path: str,
        diff_blocks: str,
        fuzzy: bool = True,
        expected_hash: Optional[str] = None,
        operation_id: Optional[str] = None,
        expected_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Applies Aider-style SEARCH/REPLACE diff blocks to a workspace file with OCC, versioning, and idempotency."""
        from .diff_editor import apply_diff_blocks as do_apply_blocks
        from .change_tracker import compute_sha256
        from ..runtime.idempotency import ConcurrencyConflictError, OperationRecord, global_operation_ledger

        target_path = safe_resolve_path(self.root_dir, rel_path)
        if not target_path.exists() or not target_path.is_file():
            raise FileNotFoundError(f"File '{rel_path}' does not exist.")

        rel_norm = self.get_relative_path(target_path)
        if hasattr(self, "file_access_policy") and self.file_access_policy:
            from ..security.file_access_policy import FileAccessMode, FileAccessDeniedError
            decision = self.file_access_policy.evaluate(rel_norm, FileAccessMode.WRITE)
            if not decision.allowed:
                raise FileAccessDeniedError(decision.reason, path=rel_norm, mode=FileAccessMode.WRITE)

        with self._get_file_lock(rel_norm):
            current_content = target_path.read_text(encoding="utf-8", errors="replace")
            current_hash = compute_sha256(current_content)

            self._validate_expected_version(
                rel_norm=rel_norm,
                expected_version=expected_version,
                expected_hash=expected_hash,
                current_hash=current_hash,
            )

            new_content, count, diff = do_apply_blocks(
                content=current_content,
                diff_blocks=diff_blocks,
                filepath=rel_norm,
                fuzzy=fuzzy,
            )

            effective_ledger = self.ledger or global_operation_ledger
            target_hash = compute_sha256(new_content)

            if new_content == current_content:
                result = {
                    "filepath": rel_norm,
                    "blocks_applied": count,
                    "diff": "",
                    "no_op": True,
                    "already_applied": True,
                    "success": True,
                }
                if operation_id:
                    effective_ledger.record(OperationRecord(
                        operation_id=operation_id,
                        tool_name="apply_diff_blocks",
                        filepath=rel_norm,
                        arguments={"filepath": rel_norm, "diff_blocks": diff_blocks},
                        before_hash=current_hash,
                        after_hash=target_hash,
                        result=result,
                        status="NO_OP",
                        no_op=True,
                    ))
                return result

            target_path.write_text(new_content, encoding="utf-8", errors="replace")
            self.change_journal.record_mutation(rel_norm, current_content, new_content)
            self._notify_file_changed(rel_norm, "MODIFIED")

            result = {
                "filepath": rel_norm,
                "blocks_applied": count,
                "diff": diff,
                "no_op": False,
                "already_applied": False,
                "success": True,
            }
            if operation_id:
                effective_ledger.record(OperationRecord(
                    operation_id=operation_id,
                    tool_name="apply_diff_blocks",
                    filepath=rel_norm,
                    arguments={"filepath": rel_norm, "diff_blocks": diff_blocks},
                    before_hash=current_hash,
                    after_hash=target_hash,
                    result=result,
                    status="COMMITTED",
                    no_op=False,
                ))
            return result

    def get_change_manifest(self, turn: Optional[int] = None):
        """Returns the cumulative ChangeManifest recorded in this workspace."""
        return self.change_journal.get_manifest(turn=turn)

    def clear_change_manifest(self):
        """Clears recorded change history."""
        self.change_journal.clear()

    def clear(self):
        """Clean all files in the workspace directory."""
        for p in self.root_dir.glob("*"):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)


class SandboxedWorkspace(WorkspaceManager):
    """
    Isolated ephemeral workspace sandbox for parallel agent execution.
    Copies workspace state, allows isolated file edits and command runs,
    and merges verified deliverables back to the main workspace with 3-way conflict detection.
    """

    def __init__(
        self,
        main_workspace: WorkspaceManager,
        sandbox_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        self.main_workspace = main_workspace
        self.sandbox_id = sandbox_id or task_id or "default_sandbox"
        self.base_hashes: Dict[str, str] = {}
        self._base_contents: Dict[str, str] = {}
        sandbox_path = (main_workspace.root_dir / ".sandboxes" / self.sandbox_id).resolve()
        super().__init__(root_dir=sandbox_path)
        self._sync_from_main()

    @property
    def sandbox_dir(self) -> Path:
        return self.root_dir

    def _is_ignored(self, rel_path: str) -> bool:
        norm = rel_path.replace("\\", "/").strip("/")
        ignored_patterns = [
            ".orchestrator",
            ".sandboxes",
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".venv",
            "node_modules",
            ".DS_Store",
        ]
        return any(norm == p or norm.startswith(p + "/") or ("/" + p + "/") in norm for p in ignored_patterns)

    def _sync_from_main(self):
        """Clones existing non-ignored files from main workspace into sandbox and tracks base versions."""
        from .change_tracker import compute_sha256
        self.root_dir.mkdir(parents=True, exist_ok=True)
        for rel_str in self.main_workspace.list_files():
            if self._is_ignored(rel_str):
                continue
            src = safe_resolve_path(self.main_workspace.root_dir, rel_str)
            dst = safe_resolve_path(self.root_dir, rel_str)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_file():
                shutil.copy2(src, dst)
                try:
                    text = src.read_text(encoding="utf-8", errors="replace")
                    self.base_hashes[rel_str] = compute_sha256(text)
                    self._base_contents[rel_str] = text
                except Exception:
                    pass

    def get_uncommitted_changes(self) -> Dict[str, Any]:
        """
        Identifies files created, modified, or deleted within this sandbox relative to main workspace.
        Returns backward-compatible {"created": [...], "modified": [...]} along with:
        "deleted": [...], and "manifest": full ChangeManifest dictionary (hashes, diffs, line counts, symbols).
        """
        from .change_tracker import diff_directories
        manifest = diff_directories(
            before_dir=self.main_workspace.root_dir,
            after_dir=self.root_dir,
            session_id=self.sandbox_id,
        )
        return {
            "created": manifest.created_files,
            "modified": manifest.modified_files,
            "deleted": manifest.deleted_files,
            "manifest": manifest.to_dict(),
        }

    def merge_into_main(self) -> List[str]:
        """
        Merges modified and newly created files from this sandbox back into main workspace
        with optimistic concurrency control and 3-way merge conflict detection.
        Raises MergeConflictError if overlapping line edits were made concurrently.
        """
        from .change_tracker import compute_sha256
        merged_files: List[str] = []

        for rel_str in sorted(self.list_files()):
            if self._is_ignored(rel_str):
                continue
            src = safe_resolve_path(self.root_dir, rel_str)
            dst = safe_resolve_path(self.main_workspace.root_dir, rel_str)

            if not src.is_file():
                continue

            lock = self.main_workspace._get_file_lock(rel_str)
            with lock:
                src_text = src.read_text(encoding="utf-8", errors="replace")
                src_hash = compute_sha256(src_text)

                if not dst.exists() or not dst.is_file():
                    # Brand new file created in sandbox
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_text(src_text, encoding="utf-8", errors="replace")
                    self.main_workspace.change_journal.record_mutation(rel_str, None, src_text)
                    self.main_workspace._notify_file_changed(rel_str, "CREATED")
                    merged_files.append(rel_str)
                    continue

                dst_text = dst.read_text(encoding="utf-8", errors="replace")
                dst_hash = compute_sha256(dst_text)

                # Identical content: already merged or identical change
                if src_hash == dst_hash:
                    continue

                base_hash = self.base_hashes.get(rel_str)
                base_text = self._base_contents.get(rel_str, "")

                # Fast-forward merge: main workspace has NOT changed since sandbox branched
                if base_hash is None or dst_hash == base_hash:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_text(src_text, encoding="utf-8", errors="replace")
                    self.main_workspace.change_journal.record_mutation(rel_str, dst_text, src_text)
                    self.main_workspace._notify_file_changed(rel_str, "MODIFIED")
                    merged_files.append(rel_str)
                    continue

                # Main workspace diverged since branch! Attempt 3-way merge
                can_merge, merged_text = three_way_merge_text(
                    base_text=base_text,
                    ours_text=src_text,
                    theirs_text=dst_text,
                )
                if can_merge:
                    dst.write_text(merged_text, encoding="utf-8", errors="replace")
                    self.main_workspace.change_journal.record_mutation(rel_str, dst_text, merged_text)
                    self.main_workspace._notify_file_changed(rel_str, "MODIFIED")
                    merged_files.append(rel_str)
                else:
                    raise MergeConflictError(
                        filepath=rel_str,
                        message=f"Concurrent write conflict on '{rel_str}': Task '{self.sandbox_id}' and another concurrent task modified overlapping lines.",
                        base_hash=base_hash,
                        current_hash=dst_hash,
                        sandbox_id=self.sandbox_id,
                    )

        return merged_files

    def cleanup(self):
        """Removes the ephemeral sandbox directory."""
        if self.root_dir.exists():
            shutil.rmtree(self.root_dir, ignore_errors=True)
