"""
Unified Agent Memory Engine: 5-Tier Memory Architecture Facade.
Coordinates Short-Term Working Memory, Task Memory, Episodic Memory,
Semantic Memory, and Project Memory under a single active API.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .working_memory import WorkingMemory
from .task_memory import TaskMemory, TaskManagerMemoryStore
from .episodic_memory import EpisodicMemoryEngine, EpisodeRecord
from .semantic_memory import SemanticMemoryStore, SemanticItem
from .project_memory import ProjectMemoryManager


class AgentMemoryEngine:
    """
    Unified facade governing all 5 memory tiers:
    1. WorkingMemory (STM: scratchpad, hypotheses, verified facts, active key-value state)
    2. TaskMemory (task-level constraints, decisions, and takeaways for dependents)
    3. EpisodicMemory (trajectory history, error resolution strategies, and experience retrieval)
    4. SemanticMemory (conceptual knowledge, framework idioms, and library contracts)
    5. ProjectMemory (persistent repository conventions, ADRs, and environment rules)
    """

    def __init__(
        self,
        workspace_dir: Optional[Union[str, Path]] = None,
        db_path: Optional[str] = None,
    ):
        self.workspace_dir = Path(workspace_dir) if workspace_dir else None
        self.working = WorkingMemory()
        self.task_store = TaskManagerMemoryStore()
        self.episodic = EpisodicMemoryEngine(db_path=db_path)
        self.semantic = SemanticMemoryStore()
        self.project = ProjectMemoryManager(workspace_dir=self.workspace_dir)

    def seed_from_discovery(
        self,
        project_profile: Optional[Dict[str, Any]] = None,
        env_profile: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Seeds project memory and working memory with discovered runtime and repository facts.
        """
        if project_profile:
            self.working.set("project_profile", project_profile)
        if env_profile:
            self.working.set("environment_profile", env_profile)

        self.project.seed_from_environment(project_profile=project_profile, env_profile=env_profile)
        if self.workspace_dir:
            self.project.seed_from_workspace_files(self.workspace_dir)

    def prime_context_for_task(self, task_info: Dict[str, Any]) -> Dict[str, str]:
        """
        Active pre-flight priming: Generates formatted prompt sections across all 5 memory tiers.
        """
        task_id = task_info.get("task_id", "")
        objective = task_info.get("objective") or task_info.get("subtask_name") or str(task_info.get("description", ""))
        dependencies = task_info.get("dependencies", []) or []

        # 1. Working Memory
        wm_summary = self.working.get_summary(max_tokens=1500)

        # 2. Task Memory from dependencies
        dependent_memories = self.task_store.get_memories_for_tasks(dependencies)
        task_memory_lines = []
        if dependent_memories:
            task_memory_lines.append("**Context from Completed Dependency Tasks**:")
            for dep_mem in dependent_memories:
                dist = dep_mem.distill_for_dependents()
                if dist.get("key_takeaways"):
                    task_memory_lines.append(f"• *From Task [{dep_mem.task_id}]*: {'; '.join(dist['key_takeaways'])}")
                if dist.get("discovered_constraints"):
                    task_memory_lines.append(f"  - Constraints: {'; '.join(dist['discovered_constraints'])}")
                if dist.get("technical_decisions"):
                    task_memory_lines.append(f"  - Decisions: {'; '.join(dist['technical_decisions'])}")

        current_task_mem = self.task_store.get(task_id)
        if current_task_mem and (current_task_mem.assumptions or current_task_mem.discovered_constraints):
            task_memory_lines.append(current_task_mem.get_summary(max_tokens=500))

        task_mem_summary = "\n".join(task_memory_lines) if task_memory_lines else ""

        # 3. Episodic Memory (Experience Replay)
        episodes = self.episodic.retrieve_relevant_episodes(objective=objective, top_k=3)
        episodic_summary = self.episodic.format_episodes_for_prompt(episodes, max_tokens=1000)

        # 4. Project Memory (Conventions & Rules)
        project_summary = self.project.get_project_context_summary(task_objective=objective, max_tokens=1500)

        # 5. Semantic Memory (Conceptual Knowledge & Contracts)
        semantic_items = self.semantic.search(query=objective, top_k=3)
        semantic_summary = self.semantic.format_for_prompt(semantic_items, max_tokens=800)

        return {
            "working_memory": wm_summary,
            "task_memory": task_mem_summary,
            "episodic_experience": episodic_summary,
            "project_memory": project_summary,
            "semantic_knowledge": semantic_summary,
        }

    def distill_task_outcome(
        self,
        task_id: str,
        task_info: Dict[str, Any],
        result_data: Dict[str, Any],
        success: bool,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Post-task automatic distillation: Extracts verified facts, pitfalls, decisions,
        and lessons learned, storing them across the appropriate memory tiers.
        """
        objective = task_info.get("objective") or task_info.get("subtask_name") or task_id
        summary = result_data.get("summary", "") if isinstance(result_data, dict) else str(result_data)
        tools_used = task_info.get("tools_used", []) if isinstance(task_info, dict) else []

        # 1. Update Working Memory
        if success:
            fact_msg = f"Task [{task_id}] '{objective}' succeeded: {summary[:120]}" if summary else f"Task [{task_id}] '{objective}' succeeded."
            self.working.record_fact(fact_msg)
        else:
            pitfall_msg = f"Task [{task_id}] '{objective}' failed: {error_message or summary[:120]}"
            self.working.record_pitfall(pitfall_msg)

        written_files = result_data.get("written_files", []) if isinstance(result_data, dict) else []
        for wf in written_files:
            self.working.record_modified_symbol(wf)

        # 2. Update Task Memory
        t_mem = self.task_store.get_or_create(task_id, goal=objective)
        if summary:
            t_mem.record_takeaway(summary[:200])
        for wf in written_files:
            t_mem.record_artifact(wf)

        # 3. Update Episodic Memory
        lessons = []
        if success:
            lessons.append(f"Successfully implemented {objective}")
            if written_files:
                lessons.append(f"Produced files: {', '.join(written_files)}")
        else:
            lessons.append(f"Failed with error: {error_message or 'Unspecified failure'}")

        self.episodic.record_episode(EpisodeRecord(
            task_id=task_id,
            session_id=task_info.get("session_id", ""),
            objective=objective,
            error_encountered=error_message if not success else None,
            resolution_strategy=summary if success and error_message else None,
            tools_used=tools_used,
            outcome="SUCCESS" if success else "FAILURE",
            lessons_learned=lessons,
        ))

    def clear(self) -> None:
        self.working.clear()
        self.task_store.clear()
        self.episodic.clear()
        self.semantic.clear()
        self.project.clear()
