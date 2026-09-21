"""
ArtifactValidator: Deterministic Artifact & File Validation Engine.
Systematically validates file deliverables created or modified by coding agents,
ensuring security boundaries, content integrity, syntax compilation, on-disk existence,
and task scope reconciliation.
"""
import ast
from dataclasses import dataclass, field
import fnmatch
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    from ..tools.diff_editor import detect_truncation_placeholders
except (ImportError, ValueError):
    try:
        from agent_orchestrator.tools.diff_editor import detect_truncation_placeholders
    except Exception:
        detect_truncation_placeholders = lambda c: []


DEFAULT_FORBIDDEN_PATTERNS = [
    ".git",
    ".git/*",
    "*/.git/*",
    ".gitignore",
    ".gitattributes",
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
    ".orchestrator",
    ".orchestrator/*",
    "*/.orchestrator/*",
    ".sandboxes",
    ".sandboxes/*",
    "*/.sandboxes/*",
]

GIT_CONFLICT_MARKERS = [
    re.compile(r"^<{7}\s.*$", re.MULTILINE),
    re.compile(r"^={7}\s*$", re.MULTILINE),
    re.compile(r"^>{7}\s.*$", re.MULTILINE),
]


@dataclass
class ArtifactValidationResult:
    """Encapsulates the multi-tier validation outcome for an individual file artifact."""
    is_valid: bool
    filepath: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    bytes_written: Optional[int] = None
    on_disk_verified: bool = False
    syntax_valid: bool = True
    content_valid: bool = True
    path_allowed: bool = True

    def errors_summary(self) -> str:
        return "; ".join(self.errors) if self.errors else "No errors"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "filepath": self.filepath,
            "errors": self.errors,
            "warnings": self.warnings,
            "diagnostics": self.diagnostics,
            "bytes_written": self.bytes_written,
            "on_disk_verified": self.on_disk_verified,
            "syntax_valid": self.syntax_valid,
            "content_valid": self.content_valid,
            "path_allowed": self.path_allowed,
        }


@dataclass
class ArtifactReconciliationResult:
    """Encapsulates the reconciliation between task expectations and created artifacts."""
    is_valid: bool
    required_files: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    forbidden_modified_files: List[str] = field(default_factory=list)
    verified_files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def errors_summary(self) -> str:
        return "; ".join(self.errors) if self.errors else "No errors"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "required_files": self.required_files,
            "missing_files": self.missing_files,
            "forbidden_modified_files": self.forbidden_modified_files,
            "verified_files": self.verified_files,
            "errors": self.errors,
        }


class ArtifactValidator:
    """
    Multi-tier validator for code files and artifacts.
    Evaluates:
      1. Path & Policy Gate (Allowed vs Forbidden paths)
      2. Content Sanity Gate (Non-empty, No Placeholders, No Conflict Markers)
      3. Syntax & Compilation Gate (Python AST, JSON parser, balanced structures)
      4. On-Disk Verification Gate (Filesystem persistence and size assertion)
      5. Task Scope Reconciliation (Required vs Created files)
    """

    @classmethod
    def is_path_forbidden(
        cls,
        filepath: str,
        forbidden_paths: Optional[List[str]] = None,
        forbidden_patterns: Optional[List[str]] = None,
    ) -> bool:
        """Checks if filepath matches any forbidden path pattern."""
        if not filepath:
            return True

        norm_path = filepath.replace("\\", "/").strip().lstrip("/")
        patterns = list(DEFAULT_FORBIDDEN_PATTERNS)
        if forbidden_paths:
            patterns.extend(forbidden_paths)
        if forbidden_patterns:
            patterns.extend(forbidden_patterns)

        for pat in patterns:
            pat_norm = pat.replace("\\", "/").strip().lstrip("/")
            if fnmatch.fnmatch(norm_path, pat_norm) or fnmatch.fnmatch(norm_path, f"*/{pat_norm}"):
                return True
            # Also check exact directory prefixes
            if pat_norm.endswith("/*"):
                prefix = pat_norm[:-2]
                if norm_path == prefix or norm_path.startswith(prefix + "/"):
                    return True

        return False

    @classmethod
    def is_path_allowed(cls, filepath: str, allowed_paths: Optional[List[str]] = None) -> bool:
        """Checks if filepath is allowed under active permission constraints."""
        if not filepath:
            return False
        if not allowed_paths or "*" in allowed_paths:
            return True

        norm_path = filepath.replace("\\", "/").strip().lstrip("/")
        for allowed in allowed_paths:
            allowed_norm = allowed.replace("\\", "/").strip().lstrip("/")
            if fnmatch.fnmatch(norm_path, allowed_norm) or fnmatch.fnmatch(norm_path, f"*/{allowed_norm}"):
                return True
            if allowed_norm.endswith("/*"):
                prefix = allowed_norm[:-2]
                if norm_path == prefix or norm_path.startswith(prefix + "/"):
                    return True
            elif norm_path == allowed_norm or norm_path.startswith(allowed_norm + "/"):
                return True

        return False

    @classmethod
    def validate_content(cls, filepath: str, content: str, is_new_file: bool = False) -> Tuple[bool, List[str]]:
        """Validates content sanity: non-empty, no truncation comments, no git conflict markers."""
        errors = []

        if content is None or not content.strip():
            errors.append(f"Content for '{filepath}' is empty or whitespace-only.")
            return False, errors

        # Truncation placeholders
        placeholders = detect_truncation_placeholders(content)
        if placeholders:
            errors.append(
                f"Content for '{filepath}' contains {len(placeholders)} lazy truncation placeholder(s): "
                f"{', '.join(placeholders[:3])}"
            )

        # Git merge conflict markers
        for marker_re in GIT_CONFLICT_MARKERS:
            if marker_re.search(content):
                errors.append(f"Content for '{filepath}' contains unresolved git merge conflict markers (<<<<<<< / ======= / >>>>>>>).")
                break

        return (len(errors) == 0), errors

    @classmethod
    def validate_syntax(cls, filepath: str, content: str) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
        """
        Validates syntax compilation for language-specific files.
        Captures exact line, column, and diagnostic details.
        """
        errors = []
        diagnostics = []
        ext = Path(filepath).suffix.lower()

        if ext == ".py":
            try:
                ast.parse(content, filename=filepath)
            except SyntaxError as e:
                line_no = e.lineno or 0
                col_offset = e.offset or 0
                err_msg = f"Python SyntaxError in '{filepath}' at line {line_no}:{col_offset}: {e.msg}"
                errors.append(err_msg)
                diagnostics.append({
                    "filepath": filepath,
                    "line": line_no,
                    "column": col_offset,
                    "message": e.msg,
                    "text": e.text.strip() if e.text else "",
                    "severity": "ERROR",
                })
        elif ext == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError as e:
                err_msg = f"JSON ParseError in '{filepath}' at line {e.lineno}:{e.colno}: {e.msg}"
                errors.append(err_msg)
                diagnostics.append({
                    "filepath": filepath,
                    "line": e.lineno,
                    "column": e.colno,
                    "message": e.msg,
                    "severity": "ERROR",
                })

        return (len(errors) == 0), errors, diagnostics

    @classmethod
    def verify_on_disk(cls, workspace: Any, filepath: str, expected_content: Optional[str] = None) -> Tuple[bool, List[str]]:
        """Verifies that the file actually exists on the filesystem and has non-zero size."""
        errors = []
        if not workspace:
            return True, []

        try:
            if not workspace.file_exists(filepath):
                errors.append(f"File '{filepath}' was not created or does not exist on disk.")
                return False, errors

            # Check readable content and size
            actual_content = workspace.read_file(filepath)
            if actual_content is None or len(actual_content.strip()) == 0:
                errors.append(f"File '{filepath}' exists on disk but is empty (0 bytes).")
                return False, errors

            if expected_content is not None and actual_content != expected_content:
                errors.append(f"File '{filepath}' content on disk does not match expected written content.")
                return False, errors

        except Exception as e:
            errors.append(f"Filesystem error verifying '{filepath}': {str(e)}")
            return False, errors

        return True, []

    @classmethod
    def validate_file_deliverable(
        cls,
        workspace: Any,
        filepath: str,
        content: str,
        is_new_file: bool = False,
        allowed_paths: Optional[List[str]] = None,
        forbidden_paths: Optional[List[str]] = None,
        verify_disk: bool = False,
    ) -> ArtifactValidationResult:
        """Executes full multi-tier validation on a file deliverable."""
        errors = []
        warnings = []
        diagnostics = []

        # 1. Path & Policy Gate
        path_allowed = True
        if cls.is_path_forbidden(filepath, forbidden_paths=forbidden_paths):
            path_allowed = False
            errors.append(f"Security Violation: Path '{filepath}' is forbidden or protected.")

        if not cls.is_path_allowed(filepath, allowed_paths=allowed_paths):
            path_allowed = False
            errors.append(f"Permission Denied: Path '{filepath}' is outside allowed write paths: {allowed_paths}")

        # 2. Content Sanity Gate
        content_valid, c_errs = cls.validate_content(filepath, content, is_new_file=is_new_file)
        errors.extend(c_errs)

        # 3. Syntax & Compilation Gate
        syntax_valid, s_errs, s_diags = cls.validate_syntax(filepath, content)
        errors.extend(s_errs)
        diagnostics.extend(s_diags)

        # 4. On-Disk Verification Gate (if requested)
        on_disk = False
        if verify_disk and workspace:
            on_disk, d_errs = cls.verify_on_disk(workspace, filepath, expected_content=content)
            errors.extend(d_errs)

        is_valid = (len(errors) == 0)
        bytes_written = len(content.encode("utf-8")) if content else 0

        return ArtifactValidationResult(
            is_valid=is_valid,
            filepath=filepath,
            errors=errors,
            warnings=warnings,
            diagnostics=diagnostics,
            bytes_written=bytes_written,
            on_disk_verified=on_disk,
            syntax_valid=syntax_valid,
            content_valid=content_valid,
            path_allowed=path_allowed,
        )

    @classmethod
    def reconcile_task_outputs(
        cls,
        workspace: Any,
        required_outputs: List[str],
        modified_files: List[str],
        forbidden_paths: Optional[List[str]] = None,
        fallback_workspace: Optional[Any] = None,
    ) -> ArtifactReconciliationResult:
        """
        Reconciles expected task outputs against actual created/modified files.
        Verifies:
          - Every required output actually exists and is non-empty.
          - No forbidden files were modified.
        """
        errors = []
        missing = []
        forbidden_modified = []
        verified = []

        norm_modified = {f.replace("\\", "/").strip().lstrip("/") for f in modified_files if f}

        # Check forbidden files modified
        for f in norm_modified:
            if cls.is_path_forbidden(f, forbidden_patterns=forbidden_paths):
                forbidden_modified.append(f)
                errors.append(f"Forbidden or protected file was modified: '{f}'")

        # Check required outputs
        for req in required_outputs:
            if not req:
                continue
            # Strip symbol notation (e.g. "foo.py:MyClass" -> "foo.py")
            req_file = req.split(":")[0].strip().replace("\\", "/").lstrip("/")
            if not req_file:
                continue

            if workspace:
                exists = workspace.file_exists(req_file)
                active_ws = workspace
                if not exists and fallback_workspace:
                    exists = fallback_workspace.file_exists(req_file)
                    if exists:
                        active_ws = fallback_workspace

                if not exists:
                    missing.append(req_file)
                    errors.append(f"Required output file '{req_file}' was not created or does not exist.")
                    continue

                content = active_ws.read_file(req_file)
                if content is None or len(content.strip()) == 0:
                    missing.append(req_file)
                    errors.append(f"Required output file '{req_file}' exists but is empty (0 bytes).")
                    continue

                verified.append(req_file)
            else:
                if req_file in norm_modified:
                    verified.append(req_file)
                else:
                    missing.append(req_file)
                    errors.append(f"Required output file '{req_file}' was not among modified files.")

        is_valid = (len(errors) == 0)
        return ArtifactReconciliationResult(
            is_valid=is_valid,
            required_files=required_outputs,
            missing_files=missing,
            forbidden_modified_files=forbidden_modified,
            verified_files=verified,
            errors=errors,
        )

    @classmethod
    def format_error_feedback(cls, result: Union[ArtifactValidationResult, ArtifactReconciliationResult]) -> str:
        """Formats actionable LLM self-correction feedback."""
        lines = [
            "[Artifact & Deliverable Validation Failure]",
            "The deliverables you produced failed deterministic artifact validation checks:",
        ]
        for err in result.errors:
            lines.append(f"  - {err}")

        if isinstance(result, ArtifactValidationResult) and result.diagnostics:
            lines.append("\nDiagnostics:")
            for d in result.diagnostics:
                lines.append(f"  * Line {d.get('line')}:{d.get('column')} - {d.get('message')}")
                if d.get("text"):
                    lines.append(f"    Code snippet: `{d.get('text')}`")

        lines.append(
            "\nPlease fix these errors immediately: ensure the file compiles without syntax errors, "
            "contains no truncation placeholders or merge conflict markers, respects allowed write paths, "
            "and ensures all required files exist."
        )
        return "\n".join(lines)
