# Duplicate Skill System Audit Report

## 1. Executive Summary
The repository contained two conflicting paradigms for skills:
1. **Passive Markdown Prompt Injection** (`agent_orchestrator/registry/skill_registry.py`):
   Loads raw `SKILL.md` files from disk via `SkillManager.load_many(skills)` and concatenates extensive prose instructions directly into the LLM system prompt (`BaseAgent.build_system_prompt()` lines 58–63).
2. **Capability-Driven Executable Runtime Policies** (`skills/`):
   - `skills/resolver.py` (`SkillResolver`): Resolves task objectives into applicable skill sets.
   - `skills/runtime.py` (`SkillRuntime`): Executes capability plans governed by `RuntimePolicy`, `ToolPolicy`, and `ExecutionProcedure`.
   - `skills/compiler.py` (`SkillCompiler`): Compiles skill manifests into formal execution contracts.
   - `skills/policy.py` (`ToolPolicy`, `RuntimePolicy`): Defines allowed tools, required approvals, and execution constraints.

Because the legacy injection path in `BaseAgent` was never decommissioned, skills remained passive markdown documentation rather than authoritative runtime constraints.

---

## 2. Identified Skill System Duplications

| Aspect | Legacy Approach | Authoritative Approach (`skills/`) |
| :--- | :--- | :--- |
| **Skill Representation** | Raw markdown text (`SKILL.md`) | Structured `SkillManifest` & `ExecutionProcedure` |
| **Invocation Model** | Concatenated into system prompt | Capability Policy -> Tool Policy -> Context Policy |
| **Tool Scoping** | Uncontrolled (all tools available) | Scoped via `ToolPolicy` (allowed tools set) |
| **Verification** | Ignored at prompt level | Enforced via `VerificationPolicy` in `skills/verifier.py` |

---

## 3. Detailed Call-Site Inventory

1. **`agent_orchestrator/agents/base.py`**:
   - Lines 58-63:
     ```python
     if active_skills:
         skill_instructions = self.skill_registry.load_many(active_skills)
         if skill_instructions:
             prompt_parts.append(f"\n{skill_instructions}")
     ```
     This violated the non-negotiable contract by dumping hundreds of lines of passive markdown into the model's context window.
2. **`agent_orchestrator/runtime/dag_scheduler.py`**:
   - Lines 196-201: Queries `skill_registry` and passes list of strings to `agent.execute(active_skills=...)`, triggering passive prompt injection.
3. **`skills/runtime.py` & `skills/resolver.py`**:
   - Implemented executable policies, but disconnected from `BaseAgent`'s main prompt builder.

---

## 4. Consolidation & Migration Decision

1. **Decommission Markdown Prompt Injection**: Completely remove `self.skill_registry.load_many(active_skills)` from `BaseAgent.build_system_prompt()`. `SKILL.md` files remain passive documentation and are never dumped into model prompts.
2. **Authoritative Skill Pipeline**:
   $$\text{Task} \longrightarrow \text{SkillResolver} \longrightarrow \text{SkillRuntime} \longrightarrow \text{ToolPolicy / ContextPolicy / VerificationPolicy}$$
3. **Integration with AgentExecutionLoop**: `AgentExecutionLoop` resolves skills via `SkillResolver` and configures `ToolPolicy` and `CapabilityRouter`, strictly controlling which tools are permitted.
