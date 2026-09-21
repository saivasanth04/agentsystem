"""
ReproducibilityRecorder: Captures point-in-time execution snapshots across
Git VCS state, Model Configurations, Prompt Templates, Skill Versions, Tool Schemas, and System Environment.
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
import uuid
from typing import Any, Dict, List, Optional

from .manifest import (
    GitSnapshot,
    ModelMetadata,
    PromptTemplateMetadata,
    SkillVersionRecord,
    ToolVersionRecord,
    EnvironmentSnapshot,
    ExecutionSnapshot,
)


class ReproducibilityRecorder:
    """
    State collector for building immutable, verifiable execution snapshots.
    """

    def __init__(self, default_seed: Optional[int] = None):
        self.default_seed = default_seed

    def capture_git_snapshot(self, workspace_path: Optional[str] = None) -> Optional[GitSnapshot]:
        """
        Inspects the workspace Git repository status.
        Gracefully handles non-git repositories or missing git binaries.
        """
        cwd = os.path.abspath(workspace_path or os.getcwd())
        try:
            # Check if git is available and cwd is a git repository
            toplevel_proc = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if toplevel_proc.returncode != 0:
                return None

            toplevel = os.path.abspath(toplevel_proc.stdout.strip())
            if not os.path.exists(os.path.join(cwd, ".git")) and cwd != toplevel:
                return None

            head_proc = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if head_proc.returncode != 0:
                return None

            commit_sha = head_proc.stdout.strip()

            branch_proc = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            branch = branch_proc.stdout.strip() or "HEAD"

            status_proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            status_lines = [l for l in status_proc.stdout.splitlines() if l.strip()]
            is_dirty = len(status_lines) > 0
            staged_count = sum(1 for l in status_lines if l[0] in ("M", "A", "D", "R", "C"))
            untracked_count = sum(1 for l in status_lines if l.startswith("??"))

            # Compute diff patch hash for working tree changes
            diff_proc = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            diff_text = diff_proc.stdout
            diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest() if diff_text else None

            remote_proc = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            remote_url = remote_proc.stdout.strip() or None

            return GitSnapshot(
                commit_sha=commit_sha,
                branch=branch,
                is_dirty=is_dirty,
                staged_files_count=staged_count,
                untracked_files_count=untracked_count,
                diff_patch_hash=diff_hash,
                remote_url=remote_url,
            )
        except Exception:
            return None

    def capture_model_metadata(
        self,
        model_name: str,
        provider: str = "openai",
        temperature: float = 0.0,
        seed: Optional[int] = None,
        system_fingerprint: Optional[str] = None,
        max_tokens: Optional[int] = None,
        model_tier: str = "standard",
    ) -> ModelMetadata:
        return ModelMetadata(
            model_name=model_name,
            provider=provider,
            temperature=temperature,
            seed=seed if seed is not None else self.default_seed,
            system_fingerprint=system_fingerprint,
            max_tokens=max_tokens,
            model_tier=model_tier,
        )

    def capture_prompt_metadata(
        self,
        template_id: str,
        prompt_text: str,
        version: str = "1.0",
        variables: Optional[List[str]] = None,
    ) -> PromptTemplateMetadata:
        content_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        return PromptTemplateMetadata(
            template_id=template_id,
            version=version,
            content_hash=content_hash,
            variables=variables or [],
        )

    def capture_skill_versions(self, skill_registry: Any) -> Dict[str, SkillVersionRecord]:
        records: Dict[str, SkillVersionRecord] = {}
        if not skill_registry:
            return records

        # Handle SkillRegistry instance or dictionary
        skills_dict = {}
        if hasattr(skill_registry, "skills") and isinstance(skill_registry.skills, dict):
            skills_dict = skill_registry.skills
        elif hasattr(skill_registry, "list_skills"):
            for s in skill_registry.list_skills():
                if hasattr(s, "name"):
                    skills_dict[s.name] = s
                elif isinstance(s, dict) and "name" in s:
                    skills_dict[s["name"]] = s

        for name, skill in skills_dict.items():
            version = getattr(skill, "version", "1.0.0") if hasattr(skill, "version") else (skill.get("version", "1.0.0") if isinstance(skill, dict) else "1.0.0")
            content = str(getattr(skill, "instructions", "")) if hasattr(skill, "instructions") else (str(skill.get("instructions", "")) if isinstance(skill, dict) else str(skill))
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            records[name] = SkillVersionRecord(
                skill_name=name,
                version=str(version),
                content_hash=content_hash,
            )
        return records

    def capture_tool_schemas(
        self,
        tool_registry: Optional[Any] = None,
        mcp_manager: Optional[Any] = None,
    ) -> Dict[str, ToolVersionRecord]:
        records: Dict[str, ToolVersionRecord] = {}

        # 1. Builtin Tools
        if tool_registry:
            tools_list = []
            if hasattr(tool_registry, "get_all_tools"):
                tools_list = tool_registry.get_all_tools()
            elif hasattr(tool_registry, "list_tools"):
                tools_list = tool_registry.list_tools()
            elif hasattr(tool_registry, "tools") and isinstance(tool_registry.tools, dict):
                tools_list = list(tool_registry.tools.values())

            for tool in tools_list:
                name = getattr(tool, "name", "") or (tool.get("name") if isinstance(tool, dict) else str(tool))
                if not name:
                    continue
                schema = getattr(tool, "schema", None) or (tool.get("schema") if isinstance(tool, dict) else str(tool))
                schema_str = json.dumps(schema, sort_keys=True, default=str) if isinstance(schema, dict) else str(schema)
                schema_hash = hashlib.sha256(schema_str.encode("utf-8")).hexdigest()
                records[name] = ToolVersionRecord(
                    tool_name=name,
                    version=getattr(tool, "version", "1.0.0") if hasattr(tool, "version") else "1.0.0",
                    schema_hash=schema_hash,
                    source="builtin",
                )

        # 2. MCP Tools
        if mcp_manager:
            try:
                if hasattr(mcp_manager, "list_all_tools"):
                    mcp_tools = mcp_manager.list_all_tools()
                    for mtool in mcp_tools:
                        name = getattr(mtool, "name", "") or (mtool.get("name") if isinstance(mtool, dict) else str(mtool))
                        if not name:
                            continue
                        schema = getattr(mtool, "input_schema", None) or (mtool.get("input_schema") if isinstance(mtool, dict) else None)
                        schema_str = json.dumps(schema, sort_keys=True, default=str) if schema else ""
                        schema_hash = hashlib.sha256(schema_str.encode("utf-8")).hexdigest()
                        records[name] = ToolVersionRecord(
                            tool_name=name,
                            version="1.0.0",
                            schema_hash=schema_hash,
                            source="mcp",
                        )
            except Exception:
                pass

        return records

    def capture_environment(self) -> EnvironmentSnapshot:
        return EnvironmentSnapshot(
            python_version=sys.version.split()[0],
            os_platform=platform.platform(),
            installed_packages={},
        )

    def build_snapshot(
        self,
        session_id: str,
        task_id: Optional[str] = None,
        workspace_path: Optional[str] = None,
        skill_registry: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
        mcp_manager: Optional[Any] = None,
        models: Optional[Dict[str, ModelMetadata]] = None,
        prompts: Optional[Dict[str, PromptTemplateMetadata]] = None,
        seed: Optional[int] = None,
    ) -> ExecutionSnapshot:
        snapshot_id = f"snap-{uuid.uuid4().hex[:8]}"
        git_snap = self.capture_git_snapshot(workspace_path=workspace_path)
        skill_records = self.capture_skill_versions(skill_registry)
        tool_records = self.capture_tool_schemas(tool_registry, mcp_manager)
        env_snap = self.capture_environment()
        effective_seed = seed if seed is not None else self.default_seed

        snapshot = ExecutionSnapshot(
            snapshot_id=snapshot_id,
            session_id=session_id,
            task_id=task_id,
            git_snapshot=git_snap,
            model_metadata=models or {},
            prompt_templates=prompts or {},
            skills=skill_records,
            tools=tool_records,
            environment=env_snap,
            seed=effective_seed,
        )
        snapshot.manifest_hash = snapshot.compute_manifest_hash()
        return snapshot
