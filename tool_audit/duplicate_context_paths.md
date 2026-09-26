# Duplicate Context Paths Audit & Consolidation

## 1. Findings

Prior to this consolidation pass, prompt context was being constructed across multiple discordant locations:
1. `context/compiler.py` (`ContextCompiler`): The authoritative 7-stream context compiler (Task, RepositoryBrain, WorkingMemory, ActiveDiff, Symbols, Errors, VerificationState, TokenBudget).
2. `agent_orchestrator/context/budget_allocator.py` (`ContextAssembler` & `ContextBudget`): Legacy budget allocator concatenating string slices.
3. `agent_orchestrator/context/relevance_ranker.py` (`RelevanceRanker`): Legacy ranker producing formatted strings.
4. `agent_orchestrator/agents/coder.py` and `tester.py`: Performing manual string joins of `focal_content`, `interface_content`, and `arch_slice_content`.

## 2. Consolidation Action

- **Single Authority**: `ContextCompiler.compile()` is the sole entry point for constructing model context.
- **Persona Role**: Persona modules (`CoderAgent`, `TesterAgent`, `ReviewerAgent`) are restricted to contributing:
  - Task objective
  - Role definition / style
  - Specific domain constraints
- **Delegation**: `ContextAssembler` and legacy agents delegate their prompt building to `ContextCompiler.compile()`.
- **Output**: Exactly one final context package is produced: `CompiledContextPackage`.
