"""
Change Tracking Engine for Autonomous Coding Agents.
Provides cryptographic integrity hashing, unified diff generation, line delta tracking,
and AST symbol impact analysis (classes, functions, methods added, modified, or removed).
"""
import ast
from dataclasses import asdict, dataclass, field
from datetime import datetime
import difflib
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union


def compute_sha256(content: Union[str, bytes]) -> str:
    """Computes SHA-256 hexadecimal digest of string or bytes content."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


@dataclass
class SymbolChange:
    name: str
    kind: str  # class | function | method | async_function
    change_type: str  # ADDED | MODIFIED | REMOVED
    start_line: int
    end_line: int
    parent: Optional[str] = None
    signature: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FileChangeRecord:
    filepath: str
    change_type: str  # CREATED | MODIFIED | DELETED
    before_hash: Optional[str] = None
    after_hash: Optional[str] = None
    diff: str = ""
    lines_added: int = 0
    lines_removed: int = 0
    changed_line_numbers: List[int] = field(default_factory=list)
    changed_symbols: List[SymbolChange] = field(default_factory=list)
    provenance: Optional[Any] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["changed_symbols"] = [s.to_dict() if hasattr(s, "to_dict") else s for s in self.changed_symbols]
        if self.provenance:
            d["provenance"] = self.provenance.to_dict() if hasattr(self.provenance, "to_dict") else self.provenance
        return d


@dataclass
class ChangeManifest:
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    turn: Optional[int] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    file_changes: Dict[str, FileChangeRecord] = field(default_factory=dict)
    total_lines_added: int = 0
    total_lines_removed: int = 0
    created_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "turn": self.turn,
            "timestamp": self.timestamp,
            "file_changes": {fp: rec.to_dict() for fp, rec in self.file_changes.items()},
            "total_lines_added": self.total_lines_added,
            "total_lines_removed": self.total_lines_removed,
            "created_files": self.created_files,
            "modified_files": self.modified_files,
            "deleted_files": self.deleted_files,
            "summary": self.summary,
        }


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self, filepath: str = ""):
        self.filepath = filepath
        self.symbols: Dict[str, Dict[str, Any]] = {}
        self.current_class: Optional[str] = None

    def visit_ClassDef(self, node: ast.ClassDef):
        sym_id = f"class:{node.name}"
        self.symbols[sym_id] = {
            "name": node.name,
            "kind": "class",
            "start_line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "parent": None,
            "signature": f"class {node.name}",
            "body_repr": ast.dump(node),
        }
        old_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._handle_func(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._handle_func(node, is_async=True)

    def _handle_func(self, node, is_async: bool):
        prefix = "async def" if is_async else "def"
        kind = "method" if self.current_class else ("async_function" if is_async else "function")
        full_name = f"{self.current_class}.{node.name}" if self.current_class else node.name
        sym_id = f"{kind}:{full_name}"

        args = [a.arg for a in node.args.args]
        sig = f"{prefix} {node.name}({', '.join(args)})"
        self.symbols[sym_id] = {
            "name": full_name,
            "kind": kind,
            "start_line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "parent": self.current_class,
            "signature": sig,
            "body_repr": ast.dump(node),
        }
        old_class = self.current_class
        self.generic_visit(node)
        self.current_class = old_class


def extract_ast_symbols(code: str, filepath: str = "") -> Dict[str, Dict[str, Any]]:
    """Parses code and extracts all classes, functions, and methods."""
    if not code:
        return {}
    try:
        tree = ast.parse(code, filename=filepath or "<string>")
        visitor = _SymbolVisitor(filepath)
        visitor.visit(tree)
        return visitor.symbols
    except Exception:
        return {}


def diff_ast_symbols(before_code: str, after_code: str, filepath: str = "") -> List[SymbolChange]:
    """
    Computes semantic AST symbol changes between before_code and after_code.
    Identifies ADDED, MODIFIED, and REMOVED classes, functions, and methods.
    """
    before_syms = extract_ast_symbols(before_code, filepath)
    after_syms = extract_ast_symbols(after_code, filepath)

    changes: List[SymbolChange] = []

    # 1. Check added or modified
    for sym_id, a_data in after_syms.items():
        if sym_id not in before_syms:
            changes.append(
                SymbolChange(
                    name=a_data["name"],
                    kind=a_data["kind"],
                    change_type="ADDED",
                    start_line=a_data["start_line"],
                    end_line=a_data["end_line"],
                    parent=a_data["parent"],
                    signature=a_data["signature"],
                )
            )
        else:
            b_data = before_syms[sym_id]
            if a_data["body_repr"] != b_data["body_repr"]:
                changes.append(
                    SymbolChange(
                        name=a_data["name"],
                        kind=a_data["kind"],
                        change_type="MODIFIED",
                        start_line=a_data["start_line"],
                        end_line=a_data["end_line"],
                        parent=a_data["parent"],
                        signature=a_data["signature"],
                    )
                )

    # 2. Check removed
    for sym_id, b_data in before_syms.items():
        if sym_id not in after_syms:
            changes.append(
                SymbolChange(
                    name=b_data["name"],
                    kind=b_data["kind"],
                    change_type="REMOVED",
                    start_line=b_data["start_line"],
                    end_line=b_data["end_line"],
                    parent=b_data["parent"],
                    signature=b_data["signature"],
                )
            )

    return sorted(changes, key=lambda s: (s.kind, s.name))


def compute_file_change(
    filepath: str,
    before_content: Optional[str],
    after_content: Optional[str],
    provenance: Optional[Any] = None,
) -> Optional[FileChangeRecord]:
    """
    Computes cryptographic hashes, line deltas, unified diff, and symbol modifications
    for a file between before_content and after_content.
    Returns None if no change occurred.
    """
    if before_content is None and after_content is None:
        return None

    if before_content is not None and after_content is not None and before_content == after_content:
        return None

    effective_provenance = provenance
    if effective_provenance is None:
        try:
            from ..reproducibility.provenance import provenance_context
            effective_provenance = provenance_context.get_current()
        except Exception:
            effective_provenance = None

    before_hash = compute_sha256(before_content) if before_content is not None else None
    after_hash = compute_sha256(after_content) if after_content is not None else None

    if before_content is None:
        change_type = "CREATED"
        before_str = ""
        after_str = after_content or ""
    elif after_content is None:
        change_type = "DELETED"
        before_str = before_content or ""
        after_str = ""
    else:
        change_type = "MODIFIED"
        before_str = before_content
        after_str = after_content

    # Generate unified diff
    orig_lines = [l + "\n" for l in before_str.splitlines()]
    mod_lines = [l + "\n" for l in after_str.splitlines()]
    diff_gen = difflib.unified_diff(
        orig_lines,
        mod_lines,
        fromfile=f"a/{filepath}",
        tofile=f"b/{filepath}",
        lineterm="\n",
    )
    diff_text = "".join(diff_gen)

    # Compute lines added and removed
    lines_added = 0
    lines_removed = 0
    changed_line_numbers: List[int] = []

    curr_line = 0
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            # Extract starting line from @@ -A,B +C,D @@
            try:
                plus_part = line.split("+")[1].split()[0]
                curr_line = int(plus_part.split(",")[0])
            except Exception:
                pass
        elif line.startswith("+") and not line.startswith("+++"):
            lines_added += 1
            if curr_line > 0:
                changed_line_numbers.append(curr_line)
                curr_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            lines_removed += 1
        elif not line.startswith("-") and not line.startswith("+"):
            if curr_line > 0:
                curr_line += 1

    # Compute symbol diff if python file
    changed_symbols: List[SymbolChange] = []
    if filepath.endswith(".py"):
        changed_symbols = diff_ast_symbols(before_str, after_str, filepath=filepath)

    return FileChangeRecord(
        filepath=filepath,
        change_type=change_type,
        before_hash=before_hash,
        after_hash=after_hash,
        diff=diff_text,
        lines_added=lines_added,
        lines_removed=lines_removed,
        changed_line_numbers=sorted(list(set(changed_line_numbers))),
        changed_symbols=changed_symbols,
        provenance=effective_provenance,
    )


class ChangeJournal:
    """
    In-memory session/task change journal tracking cumulative file modifications.
    """

    def __init__(self, session_id: Optional[str] = None, task_id: Optional[str] = None):
        self.session_id = session_id
        self.task_id = task_id
        self.initial_snapshots: Dict[str, Optional[str]] = {}
        self.current_contents: Dict[str, Optional[str]] = {}

    def record_mutation(
        self,
        filepath: str,
        before_content: Optional[str],
        after_content: Optional[str],
    ) -> Optional[FileChangeRecord]:
        """Records a mutation event for a file."""
        if filepath not in self.initial_snapshots:
            self.initial_snapshots[filepath] = before_content
        self.current_contents[filepath] = after_content
        return compute_file_change(filepath, before_content, after_content)

    def get_manifest(self, turn: Optional[int] = None) -> ChangeManifest:
        """Computes the cumulative ChangeManifest across all recorded mutations."""
        file_changes: Dict[str, FileChangeRecord] = {}
        created: List[str] = []
        modified: List[str] = []
        deleted: List[str] = []
        total_added = 0
        total_removed = 0

        for fp in set(list(self.initial_snapshots.keys()) + list(self.current_contents.keys())):
            before = self.initial_snapshots.get(fp)
            after = self.current_contents.get(fp)
            change_rec = compute_file_change(fp, before, after)
            if change_rec:
                file_changes[fp] = change_rec
                total_added += change_rec.lines_added
                total_removed += change_rec.lines_removed
                if change_rec.change_type == "CREATED":
                    created.append(fp)
                elif change_rec.change_type == "MODIFIED":
                    modified.append(fp)
                elif change_rec.change_type == "DELETED":
                    deleted.append(fp)

        summary_parts = []
        if created:
            summary_parts.append(f"Created {len(created)} file(s): {created}")
        if modified:
            summary_parts.append(f"Modified {len(modified)} file(s): {modified}")
        if deleted:
            summary_parts.append(f"Deleted {len(deleted)} file(s): {deleted}")
        summary_str = "; ".join(summary_parts) or "No changes detected."

        return ChangeManifest(
            session_id=self.session_id,
            task_id=self.task_id,
            turn=turn,
            file_changes=file_changes,
            total_lines_added=total_added,
            total_lines_removed=total_removed,
            created_files=sorted(created),
            modified_files=sorted(modified),
            deleted_files=sorted(deleted),
            summary=summary_str,
        )

    def clear(self):
        self.initial_snapshots.clear()
        self.current_contents.clear()


def diff_directories(
    before_dir: Union[str, Path],
    after_dir: Union[str, Path],
    ignore_dirs: Optional[Set[str]] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> ChangeManifest:
    """
    Deeply compares two directory trees and returns a complete ChangeManifest.
    """
    b_path = Path(before_dir).resolve()
    a_path = Path(after_dir).resolve()
    ignores = ignore_dirs or {".git", ".orchestrator", ".sandboxes", "__pycache__", ".pytest_cache", ".venv"}

    def _collect_files(root: Path) -> Dict[str, str]:
        files: Dict[str, str] = {}
        if not root.exists():
            return files
        for p in root.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(root)).replace("\\", "/")
                if any(part in ignores for part in Path(rel).parts):
                    continue
                try:
                    files[rel] = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
        return files

    before_files = _collect_files(b_path)
    after_files = _collect_files(a_path)

    all_rel = set(list(before_files.keys()) + list(after_files.keys()))
    file_changes: Dict[str, FileChangeRecord] = {}
    created: List[str] = []
    modified: List[str] = []
    deleted: List[str] = []
    total_added = 0
    total_removed = 0

    for rel in all_rel:
        b_content = before_files.get(rel)
        a_content = after_files.get(rel)
        change_rec = compute_file_change(rel, b_content, a_content)
        if change_rec:
            file_changes[rel] = change_rec
            total_added += change_rec.lines_added
            total_removed += change_rec.lines_removed
            if change_rec.change_type == "CREATED":
                created.append(rel)
            elif change_rec.change_type == "MODIFIED":
                modified.append(rel)
            elif change_rec.change_type == "DELETED":
                deleted.append(rel)

    summary_parts = []
    if created:
        summary_parts.append(f"Created {len(created)} file(s): {created}")
    if modified:
        summary_parts.append(f"Modified {len(modified)} file(s): {modified}")
    if deleted:
        summary_parts.append(f"Deleted {len(deleted)} file(s): {deleted}")
    summary_str = "; ".join(summary_parts) or "No changes detected."

    return ChangeManifest(
        session_id=session_id,
        task_id=task_id,
        file_changes=file_changes,
        total_lines_added=total_added,
        total_lines_removed=total_removed,
        created_files=sorted(created),
        modified_files=sorted(modified),
        deleted_files=sorted(deleted),
        summary=summary_str,
    )
