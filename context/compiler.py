"""
Context Compiler.
Orchestrates the 7-stream context optimization pipeline:
Repository Brain + Working Memory + Task Objective + Verification State + Diff + Errors + Skills
                                  ↓
                           Context Compiler
                                  ↓
                      Optimized Context Package
Powered by mature libraries:
- tiktoken: Token counting & boundary slicing
- numpy: Vectorized relevance and MMR diversity ranking
- rapidfuzz: Fuzzy deduplication of errors, diffs, and state
Enforces strict anti-bloat rules:
- Never sends entire repository
- Never sends entire conversation
- Never sends entire SKILL.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from .budget import BudgetAllocation, ContextBudgetManager
from .dedup import ContextDeduplicator
from .pack import ConversationSlicer, OptimizedContextPackage, RepositorySlicer, SkillSectionSlicer
from .ranking import ContextRanker

logger = logging.getLogger("context.compiler")


class ContextCompiler:
    """
    Central compiler replacing static prompt concatenation with an active,
    budget-governed, deduplicated context package.
    """

    def __init__(
        self,
        total_budget: int = 8000,
        model_name: str = "cl100k_base",
        distribution: Optional[BudgetAllocation] = None,
        diversity_lambda: float = 0.7,
        dedup_threshold: float = 85.0,
        skill_registry: Optional[Any] = None,
        agent_registry: Optional[Any] = None,
        mcp_manager: Optional[Any] = None,
        tool_dispatcher: Optional[Any] = None,
        workspace_manager: Optional[Any] = None,
        llm_client: Optional[Any] = None,
    ):
        self.budget_mgr = ContextBudgetManager(
            total_budget=total_budget,
            distribution=distribution,
            model_name=model_name,
        )
        self.ranker = ContextRanker(diversity_lambda=diversity_lambda)
        self.deduplicator = ContextDeduplicator(default_threshold=dedup_threshold)
        self.conv_slicer = ConversationSlicer(self.budget_mgr.counter)

        # Core Systems Integration - lazy initialization if not provided
        if skill_registry is not None:
            self.skill_registry = skill_registry
        else:
            try:
                from agent_orchestrator.registry.skill_registry import SkillRegistry
                self.skill_registry = SkillRegistry()
            except Exception as e:
                logger.debug("SkillRegistry default initialization deferred: %s", e)
                self.skill_registry = None

        if agent_registry is not None:
            self.agent_registry = agent_registry
        else:
            try:
                from agent_orchestrator.registry.agent_registry import AgentRegistry
                self.agent_registry = AgentRegistry()
            except Exception as e:
                logger.debug("AgentRegistry default initialization deferred: %s", e)
                self.agent_registry = None

        self.mcp_manager = mcp_manager
        self.dispatcher = tool_dispatcher

        if workspace_manager is not None:
            self.workspace = workspace_manager
        else:
            try:
                from agent_orchestrator.tools.workspace import WorkspaceManager
                self.workspace = WorkspaceManager()
            except Exception as e:
                logger.debug("WorkspaceManager default initialization deferred: %s", e)
                self.workspace = None

        if llm_client is not None:
            self.llm = llm_client
        else:
            try:
                from agent_orchestrator.llm import LLMClient
                self.llm = LLMClient()
            except Exception as e:
                logger.debug("LLMClient default initialization deferred: %s", e)
                self.llm = None

    def compile(
        self,
        task_objective: str,
        verification_state: Optional[Union[Dict[str, Any], Any]] = None,
        errors: Optional[List[str]] = None,
        diff: Optional[str] = None,
        repository_brain: Optional[Any] = None,
        skills: Optional[List[Any]] = None,
        working_memory: Optional[Any] = None,
        conversation: Optional[List[Dict[str, Any]]] = None,
        agent_persona: Optional[str] = None,
    ) -> OptimizedContextPackage:
        """
        Executes the 7-stream optimization pipeline:
        1. Deduplicates errors, diffs, and memory items via rapidfuzz.
        2. Slices skill markdown (never entire SKILL.md).
        3. Slices repository context (never entire repo).
        4. Slices conversation (never entire history).
        5. Ranks candidate sections by relevance and diversity via numpy.
        6. Enforces exact token budgets via tiktoken.
        7. Packs into OptimizedContextPackage.
        """
        clean_objective = (task_objective or "").strip()

        # -------------------------------------------------------------
        # STREAM 1: Task Objective
        # -------------------------------------------------------------
        obj_text = f"Objective: {clean_objective}"
        if agent_persona:
            obj_text = f"Target Role: {agent_persona}\n{obj_text}"
        obj_sec = self.budget_mgr.allocate_and_truncate("task_objective", obj_text)

        # -------------------------------------------------------------
        # STREAM 2: Verification State
        # -------------------------------------------------------------
        verif_sec = ""
        if verification_state:
            v_data = verification_state if isinstance(verification_state, dict) else (
                verification_state.to_dict() if hasattr(verification_state, "to_dict") else str(verification_state)
            )
            v_str = json.dumps(v_data, indent=2) if isinstance(v_data, dict) else str(v_data)
            verif_sec = self.budget_mgr.allocate_and_truncate("verification_state", v_str)

        # -------------------------------------------------------------
        # STREAM 3: Errors (Deduplicated via rapidfuzz)
        # -------------------------------------------------------------
        err_sec = ""
        unique_errors: List[str] = []
        if errors:
            unique_errors = self.deduplicator.dedup_errors(errors)
            if unique_errors:
                # Rank errors if numerous
                if len(unique_errors) > 5:
                    cands = [{"id": f"err_{i}", "content": e} for i, e in enumerate(unique_errors)]
                    ranked_errs = self.ranker.rank(clean_objective, cands, top_k=5)
                    unique_errors = [r.content for r in ranked_errs]
                err_text = "\n".join([f"• {e}" for e in unique_errors])
                err_sec = self.budget_mgr.allocate_and_truncate("errors", err_text)

        # -------------------------------------------------------------
        # STREAM 4: Diff
        # -------------------------------------------------------------
        diff_sec = ""
        if diff:
            clean_diff = diff.strip()
            # If diff is excessively large, truncate to budget using tiktoken
            diff_sec = self.budget_mgr.allocate_and_truncate("diff", clean_diff)

        # -------------------------------------------------------------
        # STREAM 5: Repository Brain (Targeted slices only - Never entire repo)
        # -------------------------------------------------------------
        repo_sec = ""
        if repository_brain:
            relevant_symbols = []
            call_graph_info = None

            # Look up symbols mentioned in objective or errors
            terms = set(re.findall(r"[A-Za-z0-9_]{3,}", clean_objective))
            if errors:
                for e in errors[:3]:
                    terms.update(re.findall(r"[A-Za-z0-9_]{3,}", e))

            if hasattr(repository_brain, "find_symbol"):
                for term in list(terms)[:5]:
                    syms = repository_brain.find_symbol(term)
                    if syms:
                        relevant_symbols.extend(syms[:2])
                        if not call_graph_info and hasattr(repository_brain, "get_call_graph"):
                            call_graph_info = repository_brain.get_call_graph(term)

            repo_text = RepositorySlicer.slice_repository_context(relevant_symbols, call_graph_info)
            if repo_text:
                repo_sec = self.budget_mgr.allocate_and_truncate("repository_brain", repo_text)

        # -------------------------------------------------------------
        # STREAM 6: Skills (Relevant sections only - Never entire SKILL.md)
        # -------------------------------------------------------------
        skills_sec = ""
        if skills:
            skill_slices = []
            for sk in skills:
                sk_name = getattr(sk, "name", str(sk))
                sk_instructions = ""

                # Fetch instructions from manifest, registry, or string
                if hasattr(sk, "system_instructions") and sk.system_instructions:
                    sk_instructions = sk.system_instructions
                elif hasattr(sk, "instructions") and sk.instructions:
                    sk_instructions = sk.instructions
                elif self.skill_registry:
                    manifest = self.skill_registry.get_skill(sk_name)
                    if manifest and manifest.system_instructions:
                        sk_instructions = manifest.system_instructions

                if sk_instructions:
                    # SLICE: Retrieve only relevant sections
                    sliced = SkillSectionSlicer.get_relevant_sections(
                        sk_instructions,
                        query=clean_objective,
                        max_sections=2,
                    )
                    if sliced:
                        skill_slices.append(f"### Skill: {sk_name}\n{sliced}")

            if skill_slices:
                joined_skills = "\n\n".join(skill_slices)
                skills_sec = self.budget_mgr.allocate_and_truncate("skills", joined_skills)

        # -------------------------------------------------------------
        # STREAM 7: Working Memory & Conversation
        # -------------------------------------------------------------
        mem_sec = ""
        mem_parts = []
        if working_memory:
            m_data = working_memory if isinstance(working_memory, dict) else (
                working_memory.to_dict() if hasattr(working_memory, "to_dict") else {}
            )
            # Filter to top high-priority variables
            for k, v in list(m_data.items())[:6]:
                v_str = str(v)[:150]
                mem_parts.append(f"{k}: {v_str}")

        if conversation:
            conv_sliced = self.conv_slicer.slice_conversation(conversation, max_turns=3)
            if conv_sliced:
                mem_parts.append(f"Recent History:\n{conv_sliced}")

        if mem_parts:
            joined_mem = "\n".join(mem_parts)
            mem_sec = self.budget_mgr.allocate_and_truncate("working_memory", joined_mem)

        # Assemble the package
        breakdown = dict(self.budget_mgr.consumed)
        total_tokens = sum(breakdown.values())
        breakdown["total"] = total_tokens

        pkg = OptimizedContextPackage(
            task_objective=clean_objective,
            errors=unique_errors if errors else [],
            task_objective_section=obj_sec,
            verification_state_section=verif_sec,
            errors_section=err_sec,
            diff_section=diff_sec,
            repository_intelligence_section=repo_sec,
            skills_section=skills_sec,
            working_memory_section=mem_sec,
            token_breakdown=breakdown,
            total_tokens=total_tokens,
        )

        return pkg

    def compile_prompt_messages(
        self,
        task_objective: str,
        system_role: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, str]]:
        """
        Formats optimized context directly into standard OpenAI-compatible prompt messages
        for LiteLLM Gateway invocation.
        """
        pkg = self.compile(task_objective, **kwargs)
        rendered = pkg.render()

        sys_prompt = f"Role: {system_role or 'Autonomous Software Engineer'}\n\n{rendered}"
        return [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": task_objective},
        ]

    # Alias for compile_prompt_messages
    compile_chat_messages = compile_prompt_messages

    def create_langgraph_node(self) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        """
        Creates a LangGraph-compatible StateGraph node function.
        Replaces prompt concatenation in orchestrator state.
        """
        def context_compiler_node(state: Dict[str, Any]) -> Dict[str, Any]:
            objective = state.get("user_request") or state.get("objective") or "Execute task"
            errors = state.get("errors") or []
            diff = state.get("diff")
            verif = state.get("verification_state") or state.get("review_output")
            skills = state.get("active_skills") or []
            memory = state.get("working_memory") or {}
            history = state.get("messages") or []

            pkg = self.compile(
                task_objective=objective,
                verification_state=verif,
                errors=errors,
                diff=diff,
                skills=skills,
                working_memory=memory,
                conversation=history,
            )

            new_state = dict(state)
            new_state["optimized_context"] = pkg.render()
            new_state["context_package"] = pkg.to_dict()
            new_state["context_token_breakdown"] = pkg.token_breakdown
            return new_state

        return context_compiler_node
