"""
Agent Capability Model: Declarative Capabilities, Dynamic Tool Scoping,
Skill Resolution, Model Constraints, and Permission Envelopes.
"""
from dataclasses import dataclass, field
from enum import Enum
import fnmatch
from typing import Any, Dict, List, Optional, Set, Tuple, Union


class ModelTier(str, Enum):
    FAST = "FAST"            # Low latency / cost (e.g. gpt-4o-mini, haiku, flash)
    BALANCED = "BALANCED"    # Standard coding tier (e.g. gpt-4o, claude-3-5-sonnet)
    FRONTIER = "FRONTIER"    # High reasoning / frontier tier (e.g. o1, o3, claude-3-opus)

    @property
    def rank(self) -> int:
        ranks = {ModelTier.FAST: 1, ModelTier.BALANCED: 2, ModelTier.FRONTIER: 3}
        return ranks.get(self, 1)


@dataclass
class ModelConstraint:
    """
    Specifies constraints and preferences on the LLM powering an agent.
    """
    min_context_window: int = 8000
    required_features: List[str] = field(default_factory=list)  # e.g. ["tools", "json_mode", "structured_outputs"]
    preferred_tier: ModelTier = ModelTier.BALANCED
    supported_models: List[str] = field(default_factory=list)   # Allowed glob patterns or exact names
    forbidden_models: List[str] = field(default_factory=list)   # Blocked models
    min_reasoning_effort: Optional[str] = None                  # e.g. "low", "medium", "high"

    def validate_model(
        self,
        model_name: str,
        context_window: Optional[int] = None,
        features: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """
        Validates whether a candidate model satisfies this constraint.
        Returns (is_valid, failure_reason_if_any).
        """
        norm_name = (model_name or "").lower().strip()

        # 1. Forbidden models check
        for pattern in self.forbidden_models:
            pat_norm = pattern.lower().strip()
            if fnmatch.fnmatch(norm_name, pat_norm) or pat_norm in norm_name:
                return False, f"Model '{model_name}' matches forbidden pattern '{pattern}'"

        # 2. Supported models check (if list is non-empty)
        if self.supported_models:
            matched = False
            for pattern in self.supported_models:
                pat_norm = pattern.lower().strip()
                if fnmatch.fnmatch(norm_name, pat_norm) or pat_norm in norm_name:
                    matched = True
                    break
            if not matched:
                return False, f"Model '{model_name}' is not in supported models list {self.supported_models}"

        # 3. Context window check
        if context_window is not None and context_window < self.min_context_window:
            return False, f"Model context window ({context_window}) is below minimum required ({self.min_context_window})"

        # 4. Required features check
        if features is not None and self.required_features:
            norm_features = {f.lower().strip() for f in features}
            for req in self.required_features:
                if req.lower().strip() not in norm_features:
                    return False, f"Model lacks required feature '{req}'"

        return True, ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_context_window": self.min_context_window,
            "required_features": self.required_features,
            "preferred_tier": self.preferred_tier.value,
            "supported_models": self.supported_models,
            "forbidden_models": self.forbidden_models,
            "min_reasoning_effort": self.min_reasoning_effort,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelConstraint":
        tier_str = str(data.get("preferred_tier", "BALANCED")).upper()
        try:
            tier = ModelTier(tier_str)
        except ValueError:
            tier = ModelTier.BALANCED
        return cls(
            min_context_window=int(data.get("min_context_window", 8000)),
            required_features=list(data.get("required_features", [])),
            preferred_tier=tier,
            supported_models=list(data.get("supported_models", [])),
            forbidden_models=list(data.get("forbidden_models", [])),
            min_reasoning_effort=data.get("min_reasoning_effort"),
        )


TOOL_GROUPS: Dict[str, List[str]] = {
    "filesystem": [
        "read_file",
        "write_file",
        "replace_file_content",
        "insert_lines",
        "delete_lines",
        "apply_diff_blocks",
        "list_directory",
    ],
    "filesystem_readonly": [
        "read_file",
        "list_directory",
    ],
    "terminal": [
        "terminal_execute",
        "ast_syntax_check",
    ],
    "code_search": [
        "regex_grep",
        "find_symbol",
        "find_references",
        "get_dependencies",
        "get_codebase_map",
        "semantic_code_search",
        "get_call_graph",
        "get_impact_radius",
        "get_architecture_summary",
        "query_codebase_graph",
        "get_symbol_neighbors",
        "get_architecture_slice",
        "request_more_evidence",
    ],
    "git": [
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        "git_blame",
        "git_branch",
        "git_checkout",
        "git_commit",
        "git_restore",
        "git_patch",
        "git_init",
    ],
    "git_readonly": [
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        "git_blame",
    ],
    "verification": [
        "static_code_check",
        "inspect_existing_tests",
        "run_build_pipeline",
        "run_static_analysis",
        "verify_ground_truth",
    ],
    "skills": [
        "search_skills",
        "load_skill",
        "read_skill_reference",
        "execute_skill_script",
        "install_skill",
    ],
    "skills_readonly": [
        "search_skills",
        "load_skill",
        "read_skill_reference",
    ],
    "communication": [
        "send_agent_message",
        "query_agent",
        "publish_finding",
        "read_inbox",
    ],
    "swarm": [
        "spawn_subagent",
        "delegate_subtask",
        "handoff_to_agent",
        "discover_swarm_agents",
        "post_to_blackboard",
        "read_from_blackboard",
        "request_consensus",
        "terminate_subagent",
    ],
    "memory": [
        "search_project_memory",
        "record_project_memory",
        "update_scratchpad",
    ],
    "checkpoint": [
        "rollback_to_checkpoint",
        "get_workspace_changes",
    ],
}


def expand_tool_names(tools: List[str]) -> List[str]:
    """
    Expands tool group aliases (e.g. 'filesystem', 'code-search', 'terminal')
    into concrete tool identifiers, preserving specific tool names.
    Deduplicates while preserving order.
    """
    expanded: List[str] = []
    seen: Set[str] = set()

    for item in tools:
        norm = item.lower().replace("-", "_").strip()
        if norm in TOOL_GROUPS:
            for sub_tool in TOOL_GROUPS[norm]:
                if sub_tool not in seen:
                    seen.add(sub_tool)
                    expanded.append(sub_tool)
        else:
            if item not in seen:
                seen.add(item)
                expanded.append(item)
    return expanded


@dataclass
class CapabilityDefinition:
    """
    Formal definition of an agent capability, binding required tools,
    skills, model constraints, and permission boundaries.
    """
    capability_id: str
    name: str
    description: str = ""
    required_tools: List[str] = field(default_factory=list)
    required_skills: List[str] = field(default_factory=list)
    model_constraint: Optional[ModelConstraint] = None
    permission_policy: Optional[Any] = None
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "description": self.description,
            "required_tools": self.required_tools,
            "required_skills": self.required_skills,
            "model_constraint": self.model_constraint.to_dict() if self.model_constraint else None,
            "permission_policy": self.permission_policy.to_dict() if self.permission_policy and hasattr(self.permission_policy, "to_dict") else None,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CapabilityDefinition":
        mc_data = data.get("model_constraint")
        mc = ModelConstraint.from_dict(mc_data) if mc_data else None

        policy_data = data.get("permission_policy")
        policy = None
        if policy_data:
            from ..runtime.permission_policy import PermissionPolicy, ToolOperationType
            ops = {ToolOperationType(op) for op in policy_data.get("allowed_operations", [])}
            policy = PermissionPolicy(
                allowed_operations=ops,
                allowed_write_patterns=policy_data.get("allowed_write_patterns", []),
                forbidden_write_patterns=policy_data.get("forbidden_write_patterns", []),
                allowed_command_prefixes=policy_data.get("allowed_command_prefixes", []),
                forbidden_command_patterns=policy_data.get("forbidden_command_patterns", []),
                vcs_commit_requires_approval=policy_data.get("vcs_commit_requires_approval", True),
            )

        return cls(
            capability_id=data["capability_id"],
            name=data.get("name", data["capability_id"]),
            description=data.get("description", ""),
            required_tools=list(data.get("required_tools", [])),
            required_skills=list(data.get("required_skills", [])),
            model_constraint=mc,
            permission_policy=policy,
            tags=list(data.get("tags", [])),
        )


class CapabilityRegistry:
    """
    Registry for discoverable capabilities and resolution engine for tools,
    skills, model constraints, and permission policies.
    """

    def __init__(self):
        self._capabilities: Dict[str, CapabilityDefinition] = {}
        self._aliases: Dict[str, str] = {}
        self._init_standard_capabilities()

    def register(self, cap: CapabilityDefinition, aliases: Optional[List[str]] = None):
        """Registers a CapabilityDefinition and optional aliases."""
        key = cap.capability_id.lower().strip()
        self._capabilities[key] = cap
        if aliases:
            for alias in aliases:
                self._aliases[alias.lower().strip()] = key

    def register_alias(self, alias: str, capability_id: str):
        """Maps an alternative string key to a registered capability ID."""
        self._aliases[alias.lower().strip()] = capability_id.lower().strip()

    def get(self, cap_id: str) -> Optional[CapabilityDefinition]:
        """Looks up a capability by ID or alias."""
        key = cap_id.lower().strip()
        if key in self._aliases:
            key = self._aliases[key]
        return self._capabilities.get(key)

    def list_capabilities(self, tag: Optional[str] = None) -> List[CapabilityDefinition]:
        """Lists all registered capabilities, optionally filtered by tag."""
        if not tag:
            return list(self._capabilities.values())
        norm_tag = tag.lower().strip()
        return [c for c in self._capabilities.values() if norm_tag in [t.lower() for t in c.tags]]

    def resolve_tools(self, capability_ids: List[str]) -> List[str]:
        """
        Resolves the union of all tools required by the specified capabilities,
        expanding tool groups and deduplicating.
        """
        all_tools: List[str] = []
        for cap_id in capability_ids:
            cap = self.get(cap_id)
            if cap:
                all_tools.extend(cap.required_tools)
        return expand_tool_names(all_tools)

    def resolve_skills(self, capability_ids: List[str]) -> List[str]:
        """
        Resolves the union of all skills required by the specified capabilities.
        """
        all_skills: List[str] = []
        seen: Set[str] = set()
        for cap_id in capability_ids:
            cap = self.get(cap_id)
            if cap:
                for skill in cap.required_skills:
                    if skill not in seen:
                        seen.add(skill)
                        all_skills.append(skill)
        return all_skills

    def resolve_model_constraints(self, capability_ids: List[str]) -> Optional[ModelConstraint]:
        """
        Aggregates model constraints across capabilities:
        - Takes the maximum min_context_window
        - Unions required_features
        - Chooses the highest tier rank
        - Combines supported/forbidden models
        """
        constraints: List[ModelConstraint] = []
        for cap_id in capability_ids:
            cap = self.get(cap_id)
            if cap and cap.model_constraint:
                constraints.append(cap.model_constraint)

        if not constraints:
            return None

        max_ctx = max(c.min_context_window for c in constraints)
        req_features: Set[str] = set()
        for c in constraints:
            req_features.update(c.required_features)

        highest_tier = max((c.preferred_tier for c in constraints), key=lambda t: t.rank)
        all_forbidden: Set[str] = set()
        for c in constraints:
            all_forbidden.update(c.forbidden_models)

        # Supported models: if any constraint specifies supported models, intersect them or union them
        all_supported: List[str] = []
        for c in constraints:
            if c.supported_models:
                all_supported.extend(c.supported_models)

        return ModelConstraint(
            min_context_window=max_ctx,
            required_features=list(req_features),
            preferred_tier=highest_tier,
            supported_models=list(set(all_supported)),
            forbidden_models=list(all_forbidden),
        )

    def resolve_permissions(self, capability_ids: List[str]) -> Any:
        """
        Combines permission policies from all specified capabilities.
        If no capability defines a policy, returns a default permissive policy.
        """
        from ..runtime.permission_policy import PermissionPolicy, ToolOperationType

        policies = []
        for cap_id in capability_ids:
            cap = self.get(cap_id)
            if cap and cap.permission_policy:
                policies.append(cap.permission_policy)

        if not policies:
            return PermissionPolicy(
                allowed_operations={ToolOperationType.READ, ToolOperationType.CONTROL},
                allowed_write_patterns=[],
                allowed_command_prefixes=[],
            )

        merged_ops: Set[ToolOperationType] = set()
        merged_allowed_writes: Set[str] = set()
        merged_forbidden_writes: Set[str] = set()
        merged_allowed_cmds: Set[str] = set()
        merged_forbidden_cmds: Set[str] = set()
        requires_approval = True

        for p in policies:
            merged_ops.update(p.allowed_operations)
            merged_allowed_writes.update(p.allowed_write_patterns)
            merged_forbidden_writes.update(p.forbidden_write_patterns)
            merged_allowed_cmds.update(p.allowed_command_prefixes)
            merged_forbidden_cmds.update(p.forbidden_command_patterns)
            if not p.vcs_commit_requires_approval:
                requires_approval = False

        return PermissionPolicy(
            allowed_operations=merged_ops,
            allowed_write_patterns=list(merged_allowed_writes),
            forbidden_write_patterns=list(merged_forbidden_writes),
            allowed_command_prefixes=list(merged_allowed_cmds),
            forbidden_command_patterns=list(merged_forbidden_cmds),
            vcs_commit_requires_approval=requires_approval,
        )

    def _init_standard_capabilities(self):
        """Initializes standard engineering and autonomous agent capabilities."""
        from ..runtime.permission_policy import DEFAULT_ROLE_POLICIES

        # 1. Planning & Decomposition
        self.register(
            CapabilityDefinition(
                capability_id="planning",
                name="Planning & Task Decomposition",
                description="Creates structured execution roadmaps, milestone decompositions, and risk mitigations.",
                required_tools=["filesystem_readonly", "code_search", "verification", "checkpoint", "skills_readonly", "swarm"],
                required_skills=["planning-and-task-breakdown", "idea-refine"],
                model_constraint=ModelConstraint(
                    min_context_window=16000,
                    required_features=["tools", "structured_outputs"],
                    preferred_tier=ModelTier.BALANCED,
                ),
                permission_policy=DEFAULT_ROLE_POLICIES.get("PLANNER"),
                tags=["core", "planning"],
            ),
            aliases=["roadmapping", "task-decomposition", "risk-analysis"],
        )

        # 2. Specification & Contracts
        self.register(
            CapabilityDefinition(
                capability_id="specification",
                name="Specification & API Contracts",
                description="Formulates requirements, API contracts, acceptance criteria, and schema boundaries.",
                required_tools=["filesystem", "code_search", "skills_readonly", "swarm"],
                required_skills=["spec-driven-development", "api-and-interface-design"],
                model_constraint=ModelConstraint(
                    min_context_window=16000,
                    required_features=["tools"],
                    preferred_tier=ModelTier.BALANCED,
                ),
                permission_policy=DEFAULT_ROLE_POLICIES.get("SPECIFICATION"),
                tags=["core", "spec"],
            ),
            aliases=["spec-writing", "api-contracts", "requirements-analysis", "acceptance-criteria"],
        )

        # 3. Architecture & Modular Topology
        self.register(
            CapabilityDefinition(
                capability_id="architecture",
                name="Architecture & Modular Topology",
                description="Designs modular system topology, component layouts, interfaces, and seam boundaries.",
                required_tools=["filesystem", "code_search", "skills_readonly", "swarm"],
                required_skills=["codebase-design", "domain-modeling"],
                model_constraint=ModelConstraint(
                    min_context_window=32000,
                    required_features=["tools"],
                    preferred_tier=ModelTier.FRONTIER,
                ),
                permission_policy=DEFAULT_ROLE_POLICIES.get("ARCHITECTURE"),
                tags=["core", "architecture"],
            ),
            aliases=["software-architecture", "system-design", "component-hierarchy", "seam-design"],
        )

        # 4. Code Generation & Refactoring
        self.register(
            CapabilityDefinition(
                capability_id="code_generation",
                name="Code Generation & Refactoring",
                description="Generates, inspects, and refactors working production code and modules.",
                required_tools=["filesystem", "terminal", "code_search", "verification", "checkpoint", "git", "skills", "swarm"],
                required_skills=["code-simplification", "incremental-implementation"],
                model_constraint=ModelConstraint(
                    min_context_window=16000,
                    required_features=["tools"],
                    preferred_tier=ModelTier.BALANCED,
                ),
                permission_policy=DEFAULT_ROLE_POLICIES.get("CODER"),
                tags=["core", "coding"],
            ),
            aliases=["refactoring", "frontend", "backend", "general-coding", "coding"],
        )

        # 5. Testing & Verification
        self.register(
            CapabilityDefinition(
                capability_id="testing_execution",
                name="Testing & Verification Execution",
                description="Generates unit tests, executes test suites in terminal/sandbox, and verifies boundaries.",
                required_tools=["filesystem", "terminal", "code_search", "verification", "skills", "swarm"],
                required_skills=["test-driven-development", "tdd"],
                model_constraint=ModelConstraint(
                    min_context_window=16000,
                    required_features=["tools"],
                    preferred_tier=ModelTier.BALANCED,
                ),
                permission_policy=DEFAULT_ROLE_POLICIES.get("TESTER"),
                tags=["core", "testing"],
            ),
            aliases=["testing", "tdd", "unit-tests", "integration-tests", "terminal-execution"],
        )

        # 6. Code Review & Quality Audit
        self.register(
            CapabilityDefinition(
                capability_id="code_review",
                name="Code Review & Quality Audit",
                description="Conducts deep multi-axis quality review, security auditing, and PASS/FAIL evaluation.",
                required_tools=["filesystem_readonly", "code_search", "verification", "git_readonly", "skills_readonly", "swarm"],
                required_skills=["code-review-and-quality", "doubt-driven-development"],
                model_constraint=ModelConstraint(
                    min_context_window=32000,
                    required_features=["tools"],
                    preferred_tier=ModelTier.FRONTIER,
                ),
                permission_policy=DEFAULT_ROLE_POLICIES.get("REVIEWER"),
                tags=["core", "review"],
            ),
            aliases=["quality-audit", "standards-compliance", "rubric-scoring", "review"],
        )

        # 7. Debugging & Traceback Analysis
        self.register(
            CapabilityDefinition(
                capability_id="debugging",
                name="Debugging & Defect Repair",
                description="Expert in traceback diagnosis, AST inspection, unit test execution, and targeted defect repair.",
                required_tools=["filesystem", "terminal", "code_search", "verification", "skills", "swarm"],
                required_skills=["debugging-and-error-recovery", "diagnosing-bugs", "test-driven-development"],
                model_constraint=ModelConstraint(
                    min_context_window=16000,
                    required_features=["tools"],
                    preferred_tier=ModelTier.BALANCED,
                ),
                permission_policy=DEFAULT_ROLE_POLICIES.get("CODER"),
                tags=["debugging", "coding"],
            ),
            aliases=["traceback-analysis", "defect-repair", "pytest", "unit-test-analysis", "python"],
        )

        # 8. Security Auditing
        self.register(
            CapabilityDefinition(
                capability_id="security_audit",
                name="Security Auditing & Hardening",
                description="Audits input handlers, authentication, secrets, and OWASP vulnerabilities.",
                required_tools=["filesystem_readonly", "code_search", "verification", "skills_readonly", "swarm"],
                required_skills=["security-and-hardening"],
                model_constraint=ModelConstraint(
                    min_context_window=32000,
                    required_features=["tools"],
                    preferred_tier=ModelTier.FRONTIER,
                ),
                permission_policy=DEFAULT_ROLE_POLICIES.get("REVIEWER"),
                tags=["security", "audit"],
            ),
            aliases=["security", "hardening", "owasp-audit", "vulnerability-scan"],
        )

        # 9. Swarm Coordination
        self.register(
            CapabilityDefinition(
                capability_id="swarm_coordination",
                name="Swarm Coordination & Orchestration",
                description="Manages agent lifecycle, task assignment, consensus voting, and blackboard sharing.",
                required_tools=["swarm", "communication", "skills_readonly", "checkpoint"],
                required_skills=["persona-project-manager"],
                model_constraint=ModelConstraint(
                    min_context_window=16000,
                    required_features=["tools"],
                    preferred_tier=ModelTier.BALANCED,
                ),
                permission_policy=DEFAULT_ROLE_POLICIES.get("TASKORCHESTRATOR"),
                tags=["coordination", "swarm"],
            ),
            aliases=["orchestration", "swarm-management"],
        )


# Global default instance
default_capability_registry = CapabilityRegistry()
