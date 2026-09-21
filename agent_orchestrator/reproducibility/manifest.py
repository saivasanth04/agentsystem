"""
Reproducibility Manifest and Snapshot Data Models.
Enables deterministic tracking of VCS states, model configs, prompt versions,
skill hashes, tool schemas, and environment fingerprints.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
import hashlib
import json
from typing import Any, Dict, List, Optional


@dataclass
class GitSnapshot:
    commit_sha: Optional[str] = None
    branch: Optional[str] = None
    is_dirty: bool = False
    staged_files_count: int = 0
    untracked_files_count: int = 0
    diff_patch_hash: Optional[str] = None
    remote_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GitSnapshot":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ModelMetadata:
    model_name: str
    provider: str = "openai"
    temperature: float = 0.0
    seed: Optional[int] = None
    system_fingerprint: Optional[str] = None
    max_tokens: Optional[int] = None
    model_tier: str = "standard"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelMetadata":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class PromptTemplateMetadata:
    template_id: str
    version: str = "1.0"
    content_hash: str = ""
    system_prompt_hash: Optional[str] = None
    variables: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PromptTemplateMetadata":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SkillVersionRecord:
    skill_name: str
    version: str = "1.0.0"
    manifest_hash: str = ""
    content_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillVersionRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ToolVersionRecord:
    tool_name: str
    version: str = "1.0.0"
    schema_hash: str = ""
    source: str = "builtin"  # builtin / mcp

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolVersionRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class EnvironmentSnapshot:
    python_version: str = ""
    os_platform: str = ""
    installed_packages: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EnvironmentSnapshot":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ExecutionSnapshot:
    snapshot_id: str
    session_id: str
    task_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    git_snapshot: Optional[GitSnapshot] = None
    model_metadata: Dict[str, ModelMetadata] = field(default_factory=dict)
    prompt_templates: Dict[str, PromptTemplateMetadata] = field(default_factory=dict)
    skills: Dict[str, SkillVersionRecord] = field(default_factory=dict)
    tools: Dict[str, ToolVersionRecord] = field(default_factory=dict)
    environment: Optional[EnvironmentSnapshot] = None
    seed: Optional[int] = None
    manifest_hash: str = ""

    def compute_manifest_hash(self) -> str:
        """Computes a deterministic SHA-256 fingerprint over the snapshot's state."""
        canonical_repr = {
            "session_id": self.session_id,
            "git": self.git_snapshot.to_dict() if self.git_snapshot else None,
            "models": {k: v.to_dict() for k, v in sorted(self.model_metadata.items())},
            "prompts": {k: v.to_dict() for k, v in sorted(self.prompt_templates.items())},
            "skills": {k: v.to_dict() for k, v in sorted(self.skills.items())},
            "tools": {k: v.to_dict() for k, v in sorted(self.tools.items())},
            "seed": self.seed,
        }
        raw = json.dumps(canonical_repr, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "git_snapshot": self.git_snapshot.to_dict() if self.git_snapshot else None,
            "model_metadata": {k: v.to_dict() for k, v in self.model_metadata.items()},
            "prompt_templates": {k: v.to_dict() for k, v in self.prompt_templates.items()},
            "skills": {k: v.to_dict() for k, v in self.skills.items()},
            "tools": {k: v.to_dict() for k, v in self.tools.items()},
            "environment": self.environment.to_dict() if self.environment else None,
            "seed": self.seed,
            "manifest_hash": self.manifest_hash or self.compute_manifest_hash(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionSnapshot":
        git_snap = GitSnapshot.from_dict(data["git_snapshot"]) if data.get("git_snapshot") else None
        env_snap = EnvironmentSnapshot.from_dict(data["environment"]) if data.get("environment") else None
        
        models = {
            k: ModelMetadata.from_dict(v)
            for k, v in data.get("model_metadata", {}).items()
        }
        prompts = {
            k: PromptTemplateMetadata.from_dict(v)
            for k, v in data.get("prompt_templates", {}).items()
        }
        skills = {
            k: SkillVersionRecord.from_dict(v)
            for k, v in data.get("skills", {}).items()
        }
        tools = {
            k: ToolVersionRecord.from_dict(v)
            for k, v in data.get("tools", {}).items()
        }

        return cls(
            snapshot_id=data["snapshot_id"],
            session_id=data["session_id"],
            task_id=data.get("task_id"),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
            git_snapshot=git_snap,
            model_metadata=models,
            prompt_templates=prompts,
            skills=skills,
            tools=tools,
            environment=env_snap,
            seed=data.get("seed"),
            manifest_hash=data.get("manifest_hash", ""),
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> "ExecutionSnapshot":
        return cls.from_dict(json.loads(json_str))


def compare_snapshots(a: ExecutionSnapshot, b: ExecutionSnapshot) -> Dict[str, Any]:
    """
    Compares two execution snapshots and returns differences across all tracking dimensions.
    """
    differences: List[str] = []
    git_diff: Dict[str, Any] = {}
    model_diff: Dict[str, Any] = {}
    prompt_diff: Dict[str, Any] = {}
    skill_diff: Dict[str, Any] = {}
    tool_diff: Dict[str, Any] = {}
    seed_diff: Dict[str, Any] = {}

    # 1. Seed diff
    if a.seed != b.seed:
        differences.append(f"Seed mismatch: {a.seed} vs {b.seed}")
        seed_diff = {"snapshot_a": a.seed, "snapshot_b": b.seed}

    # 2. Git diff
    if (a.git_snapshot is None) != (b.git_snapshot is None):
        differences.append("Git snapshot presence mismatch")
        git_diff = {"snapshot_a": a.git_snapshot is not None, "snapshot_b": b.git_snapshot is not None}
    elif a.git_snapshot and b.git_snapshot:
        if a.git_snapshot.commit_sha != b.git_snapshot.commit_sha:
            differences.append(f"Git commit SHA mismatch: {a.git_snapshot.commit_sha} vs {b.git_snapshot.commit_sha}")
            git_diff["commit_sha"] = {"a": a.git_snapshot.commit_sha, "b": b.git_snapshot.commit_sha}
        if a.git_snapshot.diff_patch_hash != b.git_snapshot.diff_patch_hash:
            differences.append("Git uncommitted changes diff hash mismatch")
            git_diff["diff_patch_hash"] = {"a": a.git_snapshot.diff_patch_hash, "b": b.git_snapshot.diff_patch_hash}

    # 3. Model diff
    all_model_keys = set(a.model_metadata.keys()) | set(b.model_metadata.keys())
    for k in all_model_keys:
        m_a = a.model_metadata.get(k)
        m_b = b.model_metadata.get(k)
        if not m_a or not m_b:
            differences.append(f"Model metadata agent mismatch for '{k}'")
            model_diff[k] = {"in_a": bool(m_a), "in_b": bool(m_b)}
        elif m_a != m_b:
            differences.append(f"Model configuration mismatch for '{k}': {m_a.model_name}(temp={m_a.temperature}, seed={m_a.seed}) vs {m_b.model_name}(temp={m_b.temperature}, seed={m_b.seed})")
            model_diff[k] = {"a": m_a.to_dict(), "b": m_b.to_dict()}

    # 4. Prompt diff
    all_prompt_keys = set(a.prompt_templates.keys()) | set(b.prompt_templates.keys())
    for k in all_prompt_keys:
        p_a = a.prompt_templates.get(k)
        p_b = b.prompt_templates.get(k)
        if not p_a or not p_b:
            differences.append(f"Prompt template presence mismatch for '{k}'")
            prompt_diff[k] = {"in_a": bool(p_a), "in_b": bool(p_b)}
        elif p_a.content_hash != p_b.content_hash:
            differences.append(f"Prompt template hash mismatch for '{k}'")
            prompt_diff[k] = {"hash_a": p_a.content_hash, "hash_b": p_b.content_hash}

    # 5. Skill diff
    all_skill_keys = set(a.skills.keys()) | set(b.skills.keys())
    for k in all_skill_keys:
        s_a = a.skills.get(k)
        s_b = b.skills.get(k)
        if not s_a or not s_b:
            differences.append(f"Skill presence mismatch for '{k}'")
            skill_diff[k] = {"in_a": bool(s_a), "in_b": bool(s_b)}
        elif s_a.content_hash != s_b.content_hash or s_a.version != s_b.version:
            differences.append(f"Skill version or hash mismatch for '{k}'")
            skill_diff[k] = {"a": s_a.to_dict(), "b": s_b.to_dict()}

    # 6. Tool diff
    all_tool_keys = set(a.tools.keys()) | set(b.tools.keys())
    for k in all_tool_keys:
        t_a = a.tools.get(k)
        t_b = b.tools.get(k)
        if not t_a or not t_b:
            differences.append(f"Tool presence mismatch for '{k}'")
            tool_diff[k] = {"in_a": bool(t_a), "in_b": bool(t_b)}
        elif t_a.schema_hash != t_b.schema_hash:
            differences.append(f"Tool schema hash mismatch for '{k}'")
            tool_diff[k] = {"hash_a": t_a.schema_hash, "hash_b": t_b.schema_hash}

    return {
        "match": len(differences) == 0,
        "differences_count": len(differences),
        "differences": differences,
        "git_diff": git_diff,
        "model_diff": model_diff,
        "prompt_diff": prompt_diff,
        "skill_diff": skill_diff,
        "tool_diff": tool_diff,
        "seed_diff": seed_diff,
    }
