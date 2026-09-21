"""
Provenance and Configuration Version Tracking Models.
Enables fine-grained attribution of every workspace mutation and tool execution to
the specific agent version, skill, MCP tool, model parameters, prompt template hash,
and base Git commit.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime
import json
import threading
from typing import Any, Dict, Generator, List, Optional


@dataclass
class ChangeProvenance:
    """
    Immutable lineage record attached to a workspace mutation or tool operation.
    """
    agent_name: str = "UNKNOWN"
    agent_version: str = "1.0.0"
    model_name: Optional[str] = None
    model_provider: str = "openai"
    model_seed: Optional[int] = None
    system_fingerprint: Optional[str] = None
    prompt_template_id: Optional[str] = None
    prompt_hash: Optional[str] = None
    skill_name: Optional[str] = None
    skill_version: Optional[str] = None
    tool_name: Optional[str] = None
    tool_version: Optional[str] = "1.0.0"
    tool_source: str = "builtin"  # builtin / mcp
    git_commit_sha: Optional[str] = None
    git_branch: Optional[str] = None
    snapshot_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChangeProvenance":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_git_trailers(self) -> str:
        """
        Formats provenance into Git commit message trailers (RFC 2822 style).
        """
        lines = []
        if self.agent_name:
            lines.append(f"X-Agent: {self.agent_name}/{self.agent_version}")
        if self.model_name:
            model_str = f"{self.model_name}"
            if self.model_seed is not None:
                model_str += f" (seed={self.model_seed})"
            lines.append(f"X-Model: {model_str}")
        if self.prompt_hash:
            p_str = f"{self.prompt_template_id or 'prompt'}#{self.prompt_hash[:12]}"
            lines.append(f"X-Prompt-Hash: {p_str}")
        if self.skill_name:
            lines.append(f"X-Skill: {self.skill_name}/{self.skill_version or '1.0'}")
        if self.tool_name:
            lines.append(f"X-Tool: {self.tool_name} ({self.tool_source})")
        if self.git_commit_sha:
            lines.append(f"X-Base-Commit: {self.git_commit_sha}")
        return "\n".join(lines)


class _ProvenanceContextManager:
    """
    Thread-local ambient provenance context carrier.
    Allows tool executions and workspace operations to automatically inherit active
    agent, model, prompt, skill, and git metadata.
    """
    def __init__(self):
        self._local = threading.local()

    def get_current(self) -> Optional[ChangeProvenance]:
        """Returns the active provenance record for the current thread, if set."""
        stack = getattr(self._local, "stack", None)
        if stack and len(stack) > 0:
            return stack[-1]
        return None

    @contextmanager
    def scope(
        self,
        agent_name: Optional[str] = None,
        agent_version: Optional[str] = None,
        model_name: Optional[str] = None,
        model_provider: Optional[str] = None,
        model_seed: Optional[int] = None,
        system_fingerprint: Optional[str] = None,
        prompt_template_id: Optional[str] = None,
        prompt_hash: Optional[str] = None,
        skill_name: Optional[str] = None,
        skill_version: Optional[str] = None,
        tool_name: Optional[str] = None,
        tool_version: Optional[str] = None,
        tool_source: Optional[str] = None,
        git_commit_sha: Optional[str] = None,
        git_branch: Optional[str] = None,
        snapshot_id: Optional[str] = None,
        provenance: Optional[ChangeProvenance] = None,
    ) -> Generator[ChangeProvenance, None, None]:
        """
        Enters a scoped provenance context block, merging provided attributes with
        any existing parent context.
        """
        if not hasattr(self._local, "stack") or self._local.stack is None:
            self._local.stack = []

        parent = self.get_current()

        if provenance:
            current = provenance
        elif parent:
            current = ChangeProvenance(
                agent_name=agent_name if agent_name is not None else parent.agent_name,
                agent_version=agent_version if agent_version is not None else parent.agent_version,
                model_name=model_name if model_name is not None else parent.model_name,
                model_provider=model_provider if model_provider is not None else parent.model_provider,
                model_seed=model_seed if model_seed is not None else parent.model_seed,
                system_fingerprint=system_fingerprint if system_fingerprint is not None else parent.system_fingerprint,
                prompt_template_id=prompt_template_id if prompt_template_id is not None else parent.prompt_template_id,
                prompt_hash=prompt_hash if prompt_hash is not None else parent.prompt_hash,
                skill_name=skill_name if skill_name is not None else parent.skill_name,
                skill_version=skill_version if skill_version is not None else parent.skill_version,
                tool_name=tool_name if tool_name is not None else parent.tool_name,
                tool_version=tool_version if tool_version is not None else parent.tool_version,
                tool_source=tool_source if tool_source is not None else parent.tool_source,
                git_commit_sha=git_commit_sha if git_commit_sha is not None else parent.git_commit_sha,
                git_branch=git_branch if git_branch is not None else parent.git_branch,
                snapshot_id=snapshot_id if snapshot_id is not None else parent.snapshot_id,
            )
        else:
            current = ChangeProvenance(
                agent_name=agent_name or "UNKNOWN",
                agent_version=agent_version or "1.0.0",
                model_name=model_name,
                model_provider=model_provider or "openai",
                model_seed=model_seed,
                system_fingerprint=system_fingerprint,
                prompt_template_id=prompt_template_id,
                prompt_hash=prompt_hash,
                skill_name=skill_name,
                skill_version=skill_version,
                tool_name=tool_name,
                tool_version=tool_version or "1.0.0",
                tool_source=tool_source or "builtin",
                git_commit_sha=git_commit_sha,
                git_branch=git_branch,
                snapshot_id=snapshot_id,
            )

        self._local.stack.append(current)
        try:
            yield current
        finally:
            if self._local.stack:
                self._local.stack.pop()


# Global ambient provenance context carrier
provenance_context = _ProvenanceContextManager()
