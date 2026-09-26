"""
Skill Compiler.
Transforms passive markdown skill definitions and manifests into
structured, executable operational procedures and runtime policies.
"""
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set

from agent_orchestrator.registry.skill_registry import SkillManifest
from .policy import RuntimePolicy, ToolPolicy

logger = logging.getLogger("skills.compiler")


@dataclass
class ExecutionStep:
    """
    An individual operational step within an executable skill procedure.
    """
    step_number: int
    name: str
    objective: str
    suggested_tools: List[str] = field(default_factory=list)
    validation_gate: Optional[str] = None
    required_inputs: List[str] = field(default_factory=list)
    expected_outputs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_number": self.step_number,
            "name": self.name,
            "objective": self.objective,
            "suggested_tools": self.suggested_tools,
            "validation_gate": self.validation_gate,
            "required_inputs": self.required_inputs,
            "expected_outputs": self.expected_outputs,
        }


@dataclass
class ExecutionProcedure:
    """
    Ordered operational procedure derived from skill instructions.
    """
    skill_name: str
    steps: List[ExecutionStep] = field(default_factory=list)
    preconditions: List[str] = field(default_factory=list)
    postconditions: List[str] = field(default_factory=list)
    verification_checklist: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "steps": [s.to_dict() for s in self.steps],
            "preconditions": self.preconditions,
            "postconditions": self.postconditions,
            "verification_checklist": self.verification_checklist,
        }


@dataclass
class CompiledSkill:
    """
    Compiled, executable representation of a skill.
    Binds the static manifest with executable operational procedures,
    runtime constraints, and tool authorization policies.
    """
    name: str
    version: str
    category: str
    manifest: SkillManifest
    procedure: ExecutionProcedure
    runtime_policy: RuntimePolicy
    tool_policy: ToolPolicy
    instructions: str
    scripts: Dict[str, str] = field(default_factory=dict)
    references: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "category": self.category,
            "procedure": self.procedure.to_dict(),
            "runtime_policy": self.runtime_policy.to_dict(),
            "tool_policy": self.tool_policy.to_dict(),
            "scripts": list(self.scripts.keys()),
            "references": list(self.references.keys()),
            "instructions_length": len(self.instructions),
        }


class SkillCompiler:
    """
    Compiles SkillManifests and raw markdown into CompiledSkills.
    Replaces static Prompt strings with structured procedural steps.
    """

    # Category to primary agent role mapping
    ROLE_AFFINITY: Dict[str, str] = {
        "python": "CODER",
        "react": "CODER",
        "frontend": "CODER",
        "database": "CODER",
        "git": "CODER",
        "testing": "TESTER",
        "security": "TESTER",
        "code-review": "REVIEWER",
        "architecture": "ARCHITECTURE",
        "planning": "PLANNER",
        "deployment": "CODER",
        "general": "CODER",
    }

    # Category default model tiers
    MODEL_TIERS: Dict[str, str] = {
        "architecture": "reasoning",
        "security": "reasoning",
        "code-review": "reasoning",
        "testing": "default",
        "python": "default",
        "react": "default",
        "git": "fast",
        "general": "default",
    }

    def __init__(self):
        pass

    def compile(self, manifest: SkillManifest, raw_markdown: Optional[str] = None) -> CompiledSkill:
        """
        Compiles a skill manifest and its instructions into an executable CompiledSkill.
        """
        markdown_text = raw_markdown or manifest.system_instructions or ""

        # 1. Parse instructions for procedural steps
        procedure = self._extract_procedure(manifest.name, markdown_text, manifest)

        # 2. Derive ToolPolicy
        tool_policy = self._derive_tool_policy(manifest, procedure)

        # 3. Derive RuntimePolicy
        runtime_policy = self._derive_runtime_policy(manifest)

        return CompiledSkill(
            name=manifest.name,
            version=manifest.version,
            category=manifest.category,
            manifest=manifest,
            procedure=procedure,
            runtime_policy=runtime_policy,
            tool_policy=tool_policy,
            instructions=markdown_text,
            scripts=dict(manifest.scripts),
            references=dict(manifest.references),
        )

    def _extract_procedure(self, skill_name: str, markdown: str, manifest: SkillManifest) -> ExecutionProcedure:
        """
        Extracts step-by-step execution procedures from markdown instructions.
        """
        steps: List[ExecutionStep] = []
        preconditions: List[str] = []
        postconditions: List[str] = []
        checklist: List[str] = []

        # Look for explicit numbered steps in markdown: e.g. "1. Step name", "Step 1:", "## 1."
        step_pattern = re.compile(
            r"^(?:###?\s*(?:Step\s*)?(\d+)[:.]\s*(.+)|(\d+)\.\s+(.+))$",
            re.MULTILINE
        )

        matches = list(step_pattern.finditer(markdown))
        if matches:
            for idx, match in enumerate(matches, 1):
                num_str = match.group(1) or match.group(3)
                title = (match.group(2) or match.group(4) or "").strip()
                step_num = int(num_str) if num_str and num_str.isdigit() else idx

                # Extract content until next step or header
                start_pos = match.end()
                next_start = matches[idx].start() if idx < len(matches) else len(markdown)
                step_body = markdown[start_pos:next_start].strip()

                # Infer tools mentioned in step body
                suggested_tools = self._detect_tools_in_text(step_body, manifest.required_tools)

                steps.append(ExecutionStep(
                    step_number=step_num,
                    name=title,
                    objective=step_body[:200] if step_body else title,
                    suggested_tools=suggested_tools,
                    validation_gate=f"Verify completion of {title}",
                ))
        else:
            # Synthesize standard 3-phase procedural execution plan if markdown has no explicit numbered steps
            req_tools = manifest.required_tools or ["read_file", "write_file"]
            steps = [
                ExecutionStep(
                    step_number=1,
                    name="Phase 1: Investigation & Context Gathering",
                    objective=f"Inspect workspace and gather requirements for skill '{skill_name}'.",
                    suggested_tools=[t for t in req_tools if "read" in t or "list" in t or "find" in t] or ["read_file", "list_directory"],
                    validation_gate="Ground truth files and interfaces inspected",
                ),
                ExecutionStep(
                    step_number=2,
                    name="Phase 2: Targeted Implementation & Mutation",
                    objective=f"Apply changes according to '{skill_name}' guidelines using delta modification tools.",
                    suggested_tools=[t for t in req_tools if "write" in t or "replace" in t or "apply" in t or "commit" in t] or ["replace_file_content", "write_file"],
                    validation_gate="File mutations applied without syntax errors",
                ),
                ExecutionStep(
                    step_number=3,
                    name="Phase 3: Deterministic Verification & Validation",
                    objective=f"Verify that outputs meet the acceptance criteria of '{skill_name}'.",
                    suggested_tools=[t for t in req_tools if "execute" in t or "status" in t or "check" in t] or ["terminal_execute", "ast_syntax_check"],
                    validation_gate="All tests and verification checks pass",
                ),
            ]

        # Extract checklist items (e.g. "- [ ] ..." or "- ...")
        checklist_matches = re.findall(r"^[ \t]*[-*]\s+(?:\[[ xX]\]\s+)?(.+)$", markdown, re.MULTILINE)
        checklist = [item.strip() for item in checklist_matches[:10] if len(item.strip()) > 5]

        # Preconditions from permissions / required tools
        if manifest.permissions:
            preconditions.extend([f"Requires permission: {p}" for p in manifest.permissions])
        if manifest.required_tools:
            preconditions.append(f"Requires tools: {', '.join(manifest.required_tools)}")

        postconditions.append(f"Deliverables verified according to {skill_name} standards.")

        return ExecutionProcedure(
            skill_name=skill_name,
            steps=steps,
            preconditions=preconditions,
            postconditions=postconditions,
            verification_checklist=checklist,
        )

    def _detect_tools_in_text(self, text: str, known_tools: List[str]) -> List[str]:
        """Identifies known tool references within a block of text."""
        detected = []
        text_lower = text.lower()
        for tool in known_tools:
            if tool.lower() in text_lower:
                detected.append(tool)
        return detected

    def _derive_tool_policy(self, manifest: SkillManifest, procedure: ExecutionProcedure) -> ToolPolicy:
        """Constructs a ToolPolicy tailored to the skill."""
        req_set = set(manifest.required_tools)
        # Add tools from procedure steps
        for step in procedure.steps:
            for t in step.suggested_tools:
                req_set.add(t)

        allowed_set = set(req_set)
        # Always allow fundamental read/inspect operations
        allowed_set.update({"read_file", "list_directory", "find_symbol", "regex_grep", "ast_syntax_check"})

        perm_set = set(manifest.permissions)
        if "workspace:write" in perm_set or any("write" in t or "replace" in t for t in req_set):
            perm_set.add("workspace:write")
        if "terminal:execute" in perm_set or any("terminal" in t or "exec" in t for t in req_set):
            perm_set.add("terminal:execute")

        return ToolPolicy(
            allowed_tools=allowed_set,
            required_tools=req_set,
            permissions=perm_set,
        )

    def _derive_runtime_policy(self, manifest: SkillManifest) -> RuntimePolicy:
        """Derives execution timeouts, turns, approval gates, and model tiers."""
        cat = manifest.category.lower()
        affinity = self.ROLE_AFFINITY.get(cat, "CODER")
        tier = self.MODEL_TIERS.get(cat, "default")

        # Security, destructive, or migration categories mandate approval and higher turns
        approval_required = cat in ("security", "deployment", "database") or "security" in manifest.tags
        timeout = 600 if cat in ("testing", "deployment", "database") else 300
        max_turns = 30 if cat in ("testing", "debugging", "python", "react") else 20

        return RuntimePolicy(
            timeout_seconds=timeout,
            max_turns=max_turns,
            max_retries=3,
            approval_required=approval_required,
            agent_affinity=affinity,
            model_tier=tier,
            sandboxed=True,
        )
