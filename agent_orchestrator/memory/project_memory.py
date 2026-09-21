"""
Project Memory Manager: Repository Conventions, Architecture Decisions, and Environment Rules.
Maintains persistent project-level context across sessions, auto-seeded from discovery,
architecture blueprints, and discovered repository guidelines (e.g. CLAUDE.md, .cursorrules).
"""
from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Union


@dataclass
class ProjectRule:
    category: str  # CONVENTION, ADR, BUILD_COMMAND, TEST_COMMAND, ENVIRONMENT, CONSTRAINT
    title: str
    content: str
    source: str = "discovery"  # discovery, workspace_file, user, architecture
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectRule":
        return cls(
            category=data["category"],
            title=data["title"],
            content=data["content"],
            source=data.get("source", "discovery"),
            created_at=data.get("created_at", datetime.now().isoformat()),
        )


class ProjectMemoryManager:
    """
    Thread-safe repository and project memory manager.
    Actively primes agent prompts with project rules, conventions, ADRs, and environment configurations.
    """

    def __init__(self, workspace_dir: Optional[Union[str, Path]] = None):
        self.workspace_dir = Path(workspace_dir) if workspace_dir else None
        self._lock = threading.RLock()
        self._rules: List[ProjectRule] = []

    def record_rule(self, category: str, title: str, content: str, source: str = "manual") -> None:
        """Records a persistent project rule or convention."""
        with self._lock:
            cat_clean = category.upper().strip()
            title_clean = title.strip()
            content_clean = content.strip()

            # Prevent exact duplicate titles in the same category
            for r in self._rules:
                if r.category == cat_clean and r.title == title_clean:
                    r.content = content_clean
                    r.source = source
                    return

            self._rules.append(ProjectRule(
                category=cat_clean,
                title=title_clean,
                content=content_clean,
                source=source,
            ))

    def seed_from_environment(
        self,
        project_profile: Optional[Dict[str, Any]] = None,
        env_profile: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Auto-seeds project memory with discovered runtime and stack facts from Stage 0.
        """
        with self._lock:
            if project_profile:
                lang = project_profile.get("primary_language", "")
                if lang:
                    self.record_rule(
                        category="CONVENTION",
                        title=f"Primary Language: {lang.capitalize()}",
                        content=f"Project is written in {lang}. Framework: {project_profile.get('framework') or 'None'}.",
                        source="discovery",
                    )
                pkg_mgr = project_profile.get("package_manager", "")
                if pkg_mgr:
                    self.record_rule(
                        category="BUILD_COMMAND",
                        title=f"Package Manager: {pkg_mgr}",
                        content=f"Use {pkg_mgr} for dependency installation and build tasks.",
                        source="discovery",
                    )
                test_runner = project_profile.get("test_runner", "")
                if test_runner:
                    self.record_rule(
                        category="TEST_COMMAND",
                        title=f"Test Runner: {test_runner}",
                        content=f"Primary test runner is {test_runner}.",
                        source="discovery",
                    )

            if env_profile:
                os_info = env_profile.get("os", {})
                if os_info:
                    self.record_rule(
                        category="ENVIRONMENT",
                        title=f"Host OS: {os_info.get('system', '').capitalize()}",
                        content=f"Host system is {os_info.get('system')}, shell is {os_info.get('default_shell', 'powershell')}. Avoid incompatible POSIX/Windows shell syntax.",
                        source="discovery",
                    )

    def seed_from_workspace_files(self, workspace_path: Optional[Union[str, Path]] = None) -> None:
        """
        Scans workspace for guideline files (CLAUDE.md, .cursorrules, CONSTRAINTS.md, README.md).
        """
        target_dir = Path(workspace_path) if workspace_path else self.workspace_dir
        if not target_dir or not target_dir.is_dir():
            return

        with self._lock:
            guideline_files = [
                ("CLAUDE.md", "CONVENTION"),
                (".cursorrules", "CONVENTION"),
                ("CONSTRAINTS.md", "CONSTRAINT"),
                ("CONTRIBUTING.md", "CONVENTION"),
            ]
            for fname, cat in guideline_files:
                fpath = target_dir / fname
                if fpath.is_file():
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="replace").strip()
                        if content:
                            # Take first 1000 chars as high-priority rule summary
                            summary = content[:1500]
                            self.record_rule(
                                category=cat,
                                title=f"Guidelines from {fname}",
                                content=summary,
                                source="workspace_file",
                            )
                    except Exception:
                        pass

    def get_project_context_summary(self, task_objective: str = "", max_tokens: int = 1500) -> str:
        """
        Returns a structured markdown summary of project memory tailored for prompt context.
        """
        with self._lock:
            if not self._rules:
                return ""

            lines = ["**Project Conventions & Architecture Rules**:"]
            by_category: Dict[str, List[ProjectRule]] = {}
            for r in self._rules:
                by_category.setdefault(r.category, []).append(r)

            for cat, rules in by_category.items():
                lines.append(f"\n### {cat.capitalize()}s:")
                for r in rules:
                    lines.append(f"• **{r.title}**: {r.content}")

            summary = "\n".join(lines)
            max_chars = max_tokens * 4
            if len(summary) > max_chars:
                return summary[:max_chars] + "\n... [Project memory truncated] ..."
            return summary

    def list_rules(self, category: Optional[str] = None) -> List[ProjectRule]:
        with self._lock:
            if category:
                cat_clean = category.upper().strip()
                return [r for r in self._rules if r.category == cat_clean]
            return list(self._rules)

    def clear(self) -> None:
        with self._lock:
            self._rules.clear()

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {"rules": [r.to_dict() for r in self._rules]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectMemoryManager":
        inst = cls()
        inst._rules = [ProjectRule.from_dict(r) for r in data.get("rules", [])]
        return inst
