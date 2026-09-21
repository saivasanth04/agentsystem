"""
Diagnostic & DiagnosticParser: Standardized Structured Diagnostic Models and Parsers.
Converts outputs from various linters, type checkers, compilers, and SAST tools
(ruff, mypy, pyright, eslint, tsc, semgrep, clippy, go vet) into LSP-style structured diagnostics.
"""
from dataclasses import dataclass, field
from enum import Enum
import json
import re
from typing import Any, Dict, List, Optional, Union


class DiagnosticSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
    HINT = "HINT"


@dataclass
class Diagnostic:
    """Standardized LSP-style static analysis diagnostic record."""
    file_path: str
    line: int = 1
    column: int = 1
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    code: Optional[str] = None
    message: str = ""
    source_tool: str = "generic"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line": self.line,
            "column": self.column,
            "severity": self.severity.value if isinstance(self.severity, DiagnosticSeverity) else str(self.severity),
            "code": self.code,
            "message": self.message,
            "source_tool": self.source_tool,
        }

    def to_string(self) -> str:
        code_str = f" [{self.code}]" if self.code else ""
        sev_str = self.severity.value if isinstance(self.severity, DiagnosticSeverity) else str(self.severity)
        return f"{self.file_path}:{self.line}:{self.column}: [{sev_str}]{code_str} {self.message} ({self.source_tool})"

    def __str__(self) -> str:
        return self.to_string()


class DiagnosticParser:
    """
    Parses CLI stdout/stderr from diverse static analysis tools into List[Diagnostic].
    Supports both JSON outputs and regex-based human-readable outputs.
    """

    @classmethod
    def parse(cls, output: str, tool_name: str) -> List[Diagnostic]:
        if not output or not output.strip():
            return []

        tool = tool_name.lower().strip()
        if "ruff" in tool:
            return cls.parse_ruff(output)
        elif "mypy" in tool:
            return cls.parse_mypy(output)
        elif "pyright" in tool:
            return cls.parse_pyright(output)
        elif "eslint" in tool:
            return cls.parse_eslint(output)
        elif "tsc" in tool or "typescript" in tool:
            return cls.parse_tsc(output)
        elif "semgrep" in tool:
            return cls.parse_semgrep(output)
        elif "clippy" in tool or "cargo" in tool:
            return cls.parse_clippy(output)
        elif "go vet" in tool or "govet" in tool:
            return cls.parse_govet(output)
        else:
            return cls.parse_generic(output, source_tool=tool_name)

    @classmethod
    def parse_ruff(cls, output: str) -> List[Diagnostic]:
        """Parses ruff check JSON or standard text output."""
        diagnostics: List[Diagnostic] = []
        # Try JSON first
        trimmed = output.strip()
        if trimmed.startswith("[") and trimmed.endswith("]"):
            try:
                data = json.loads(trimmed)
                for item in data:
                    loc = item.get("location", {})
                    diagnostics.append(Diagnostic(
                        file_path=item.get("filename", ""),
                        line=loc.get("row", 1),
                        column=loc.get("column", 1),
                        severity=DiagnosticSeverity.WARNING if item.get("fix") else DiagnosticSeverity.ERROR,
                        code=item.get("code"),
                        message=item.get("message", ""),
                        source_tool="ruff",
                    ))
                return diagnostics
            except Exception:
                pass

        # Text format: path/to/file.py:line:col: CODE Message
        pattern = re.compile(r"^([^:\n]+):(\d+):(\d+):\s*([A-Z]\d+|[A-Z]+[0-9]+)\s*(.*)$", re.MULTILINE)
        for match in pattern.finditer(output):
            file_path, line, col, code, message = match.groups()
            diagnostics.append(Diagnostic(
                file_path=file_path.strip(),
                line=int(line),
                column=int(col),
                severity=DiagnosticSeverity.ERROR if code.startswith(("E", "F", "B")) else DiagnosticSeverity.WARNING,
                code=code.strip(),
                message=message.strip(),
                source_tool="ruff",
            ))

        if not diagnostics:
            diagnostics.extend(cls.parse_generic(output, source_tool="ruff"))
        return diagnostics

    @classmethod
    def parse_mypy(cls, output: str) -> List[Diagnostic]:
        """Parses mypy text output: file:line: severity: message [code]"""
        diagnostics: List[Diagnostic] = []
        pattern = re.compile(
            r"^([^:\n]+):(\d+)(?::(\d+))?:\s*(error|warning|note):\s*(.*?)(?:\s*\[(.*?)\])?$",
            re.MULTILINE
        )
        for match in pattern.finditer(output):
            file_path, line, col, sev_str, message, code = match.groups()
            sev = DiagnosticSeverity.ERROR if sev_str == "error" else (
                DiagnosticSeverity.WARNING if sev_str == "warning" else DiagnosticSeverity.INFO
            )
            diagnostics.append(Diagnostic(
                file_path=file_path.strip(),
                line=int(line),
                column=int(col) if col else 1,
                severity=sev,
                code=code.strip() if code else None,
                message=message.strip(),
                source_tool="mypy",
            ))

        if not diagnostics and "error:" in output:
            diagnostics.extend(cls.parse_generic(output, source_tool="mypy"))
        return diagnostics

    @classmethod
    def parse_pyright(cls, output: str) -> List[Diagnostic]:
        """Parses pyright JSON or text output."""
        diagnostics: List[Diagnostic] = []
        trimmed = output.strip()
        if trimmed.startswith("{") and "generalDiagnostics" in trimmed:
            try:
                data = json.loads(trimmed)
                for diag in data.get("generalDiagnostics", []):
                    range_info = diag.get("range", {}).get("start", {})
                    sev_raw = diag.get("severity", "error").lower()
                    sev = DiagnosticSeverity.ERROR if sev_raw == "error" else DiagnosticSeverity.WARNING
                    diagnostics.append(Diagnostic(
                        file_path=diag.get("file", ""),
                        line=range_info.get("line", 0) + 1,
                        column=range_info.get("character", 0) + 1,
                        severity=sev,
                        code=diag.get("rule"),
                        message=diag.get("message", ""),
                        source_tool="pyright",
                    ))
                return diagnostics
            except Exception:
                pass

        # Text format: path/to/file.py:line:col - error/warning: message (code)
        pattern = re.compile(
            r"^([^:\n]+):(\d+):(\d+)\s*-\s*(error|warning|info):\s*(.*?)(?:\s*\((.*?)\))?$",
            re.MULTILINE
        )
        for match in pattern.finditer(output):
            file_path, line, col, sev_str, message, code = match.groups()
            sev = DiagnosticSeverity.ERROR if sev_str == "error" else DiagnosticSeverity.WARNING
            diagnostics.append(Diagnostic(
                file_path=file_path.strip(),
                line=int(line),
                column=int(col),
                severity=sev,
                code=code.strip() if code else None,
                message=message.strip(),
                source_tool="pyright",
            ))

        if not diagnostics:
            diagnostics.extend(cls.parse_generic(output, source_tool="pyright"))
        return diagnostics

    @classmethod
    def parse_eslint(cls, output: str) -> List[Diagnostic]:
        """Parses eslint JSON or standard text output."""
        diagnostics: List[Diagnostic] = []
        trimmed = output.strip()
        if trimmed.startswith("[") and trimmed.endswith("]"):
            try:
                data = json.loads(trimmed)
                for file_entry in data:
                    fpath = file_entry.get("filePath", "")
                    for msg in file_entry.get("messages", []):
                        # severity: 1 = warning, 2 = error
                        sev = DiagnosticSeverity.ERROR if msg.get("severity") == 2 else DiagnosticSeverity.WARNING
                        diagnostics.append(Diagnostic(
                            file_path=fpath,
                            line=msg.get("line", 1),
                            column=msg.get("column", 1),
                            severity=sev,
                            code=msg.get("ruleId"),
                            message=msg.get("message", ""),
                            source_tool="eslint",
                        ))
                return diagnostics
            except Exception:
                pass

        # Text format: line:col error/warning message ruleId
        curr_file = ""
        for line in output.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            if line_str.startswith("/") or (len(line_str) > 1 and line_str[1] == ":") or line_str.endswith((".js", ".ts", ".jsx", ".tsx")):
                curr_file = line_str
                continue
            match = re.match(r"^(\d+):(\d+)\s+(error|warning)\s+(.*?)(?:\s{2,}(\S+))?$", line_str)
            if match and curr_file:
                lno, col, sev_str, msg, rule = match.groups()
                sev = DiagnosticSeverity.ERROR if sev_str == "error" else DiagnosticSeverity.WARNING
                diagnostics.append(Diagnostic(
                    file_path=curr_file,
                    line=int(lno),
                    column=int(col),
                    severity=sev,
                    code=rule if rule else None,
                    message=msg.strip(),
                    source_tool="eslint",
                ))

        if not diagnostics:
            diagnostics.extend(cls.parse_generic(output, source_tool="eslint"))
        return diagnostics

    @classmethod
    def parse_tsc(cls, output: str) -> List[Diagnostic]:
        """Parses tsc output: file(line,col): error TS1234: message"""
        diagnostics: List[Diagnostic] = []
        pattern = re.compile(
            r"^([^(]+)\((\d+),(\d+)\):\s*(error|warning)\s*(TS\d+):\s*(.*)$",
            re.MULTILINE
        )
        for match in pattern.finditer(output):
            file_path, line, col, sev_str, code, message = match.groups()
            sev = DiagnosticSeverity.ERROR if sev_str == "error" else DiagnosticSeverity.WARNING
            diagnostics.append(Diagnostic(
                file_path=file_path.strip(),
                line=int(line),
                column=int(col),
                severity=sev,
                code=code.strip(),
                message=message.strip(),
                source_tool="tsc",
            ))

        if not diagnostics:
            diagnostics.extend(cls.parse_generic(output, source_tool="tsc"))
        return diagnostics

    @classmethod
    def parse_semgrep(cls, output: str) -> List[Diagnostic]:
        """Parses semgrep JSON or text output."""
        diagnostics: List[Diagnostic] = []
        trimmed = output.strip()
        if trimmed.startswith("{") and "results" in trimmed:
            try:
                data = json.loads(trimmed)
                for res in data.get("results", []):
                    check_id = res.get("check_id", "")
                    path = res.get("path", "")
                    start = res.get("start", {})
                    extra = res.get("extra", {})
                    sev_str = extra.get("severity", "ERROR").upper()
                    sev = DiagnosticSeverity.ERROR if "ERROR" in sev_str else DiagnosticSeverity.WARNING
                    diagnostics.append(Diagnostic(
                        file_path=path,
                        line=start.get("line", 1),
                        column=start.get("col", 1),
                        severity=sev,
                        code=check_id,
                        message=extra.get("message", ""),
                        source_tool="semgrep",
                    ))
                return diagnostics
            except Exception:
                pass

        # Text format: path:line:col: check_id message
        pattern = re.compile(r"^([^:\n]+):(\d+):(\d+):\s*([a-zA-Z0-9_\-\.]+)\s*(.*)$", re.MULTILINE)
        for match in pattern.finditer(output):
            file_path, line, col, code, msg = match.groups()
            diagnostics.append(Diagnostic(
                file_path=file_path.strip(),
                line=int(line),
                column=int(col),
                severity=DiagnosticSeverity.ERROR,
                code=code.strip(),
                message=msg.strip(),
                source_tool="semgrep",
            ))

        if not diagnostics:
            diagnostics.extend(cls.parse_generic(output, source_tool="semgrep"))
        return diagnostics

    @classmethod
    def parse_clippy(cls, output: str) -> List[Diagnostic]:
        """Parses cargo clippy / cargo check output."""
        diagnostics: List[Diagnostic] = []
        # Look for: error[E0308]: ... --> file.rs:line:col
        # or: warning: ... --> file.rs:line:col
        pattern = re.compile(
            r"^(error|warning)(?:\[([A-Za-z0-9_]+)\])?:\s*(.*?)\n\s*-->\s*([^:\n]+):(\d+):(\d+)",
            re.MULTILINE
        )
        for match in pattern.finditer(output):
            sev_str, code, msg, file_path, line, col = match.groups()
            sev = DiagnosticSeverity.ERROR if sev_str == "error" else DiagnosticSeverity.WARNING
            diagnostics.append(Diagnostic(
                file_path=file_path.strip(),
                line=int(line),
                column=int(col),
                severity=sev,
                code=code.strip() if code else None,
                message=msg.strip(),
                source_tool="clippy",
            ))

        if not diagnostics:
            diagnostics.extend(cls.parse_generic(output, source_tool="clippy"))
        return diagnostics

    @classmethod
    def parse_govet(cls, output: str) -> List[Diagnostic]:
        """Parses go vet output: file.go:line:col: message"""
        diagnostics: List[Diagnostic] = []
        pattern = re.compile(r"^([^:\n]+\.go):(\d+):(\d+):\s*(.*)$", re.MULTILINE)
        for match in pattern.finditer(output):
            file_path, line, col, msg = match.groups()
            diagnostics.append(Diagnostic(
                file_path=file_path.strip(),
                line=int(line),
                column=int(col),
                severity=DiagnosticSeverity.ERROR,
                code="govet",
                message=msg.strip(),
                source_tool="govet",
            ))

        if not diagnostics:
            diagnostics.extend(cls.parse_generic(output, source_tool="govet"))
        return diagnostics

    @classmethod
    def parse_generic(cls, output: str, source_tool: str = "generic") -> List[Diagnostic]:
        """Generic fallback line parser: file:line[:col]: (error|warning): message"""
        diagnostics: List[Diagnostic] = []
        pattern = re.compile(
            r"^([^:\n\s]+):(\d+)(?::(\d+))?:\s*(?:(error|warning|fatal):\s*)?(.*)$",
            re.IGNORECASE | re.MULTILINE
        )
        for match in pattern.finditer(output):
            file_path, line, col, sev_str, msg = match.groups()
            msg_str = msg.strip() if msg else ""
            if not msg_str:
                continue
            sev = DiagnosticSeverity.ERROR
            if sev_str and "warn" in sev_str.lower():
                sev = DiagnosticSeverity.WARNING
            diagnostics.append(Diagnostic(
                file_path=file_path.strip(),
                line=int(line),
                column=int(col) if col else 1,
                severity=sev,
                code=None,
                message=msg_str,
                source_tool=source_tool,
            ))
        return diagnostics
