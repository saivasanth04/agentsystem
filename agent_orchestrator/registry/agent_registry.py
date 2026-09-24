"""
Agent Registry: Dynamic Manifest Registration, Capability Resolution, and Factory Instantiation.
"""
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple, Type, Union

if TYPE_CHECKING:
    from ..agents.base import BaseAgent


from ..capabilities.model import (
    CapabilityDefinition,
    CapabilityRegistry,
    ModelConstraint,
    ModelTier,
    default_capability_registry,
    expand_tool_names,
)
from ..runtime.permission_policy import DEFAULT_ROLE_POLICIES, PermissionPolicy, ToolOperationType


@dataclass
class AgentManifest:
    """
    Declarative specification for an autonomous agent persona.
    """
    name: str
    role_description: str
    capabilities: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    model_requirements: Dict[str, Any] = field(default_factory=dict)
    system_prompt_template: Optional[str] = None
    agent_class: Optional[Type[Any]] = None
    category: str = "general"
    model: Optional[str] = None
    temperature: float = 0.2
    default_skills: List[str] = field(default_factory=list)
    capability_specs: List[CapabilityDefinition] = field(default_factory=list)
    model_constraint: Optional[ModelConstraint] = None
    permission_policy: Optional[Any] = None

    def get_effective_tools(self, registry: Optional[CapabilityRegistry] = None) -> List[str]:
        """
        Resolves the comprehensive set of tools available to this agent:
        Combines explicitly declared tools (expanding groups) with tools
        derived from declared capabilities.
        """
        reg = registry or default_capability_registry
        explicit_tools = expand_tool_names(self.tools)
        cap_tools = reg.resolve_tools(self.capabilities)
        combined: List[str] = []
        seen: Set[str] = set()
        for t in explicit_tools + cap_tools:
            if t not in seen:
                seen.add(t)
                combined.append(t)
        return combined

    def get_effective_skills(
        self,
        registry: Optional[CapabilityRegistry] = None,
        skill_registry: Optional[Any] = None,
    ) -> List[str]:
        """
        Resolves all skills for this agent, combining direct declarations
        with skills implied by capabilities, and optionally resolving transitive dependencies
        if a skill_registry is provided.
        """
        reg = registry or default_capability_registry
        direct_skills = list(self.skills) + list(self.default_skills)
        cap_skills = reg.resolve_skills(self.capabilities)
        combined: List[str] = []
        seen: Set[str] = set()
        for s in direct_skills + cap_skills:
            if s not in seen:
                seen.add(s)
                combined.append(s)

        if skill_registry and hasattr(skill_registry, "resolve_dependencies"):
            resolved_all: List[str] = []
            resolved_seen: Set[str] = set()
            for s in combined:
                try:
                    deps = skill_registry.resolve_dependencies(s)
                    for dep in deps:
                        if dep not in resolved_seen:
                            resolved_seen.add(dep)
                            resolved_all.append(dep)
                except Exception:
                    if s not in resolved_seen:
                        resolved_seen.add(s)
                        resolved_all.append(s)
            return resolved_all

        return combined

    def get_effective_model_constraint(self, registry: Optional[CapabilityRegistry] = None) -> ModelConstraint:
        """
        Resolves the effective ModelConstraint for this agent.
        Precedence:
        1. Explicit self.model_constraint
        2. Inferred from self.model_requirements
        3. Resolved from self.capabilities
        4. Sane default
        """
        if self.model_constraint:
            return self.model_constraint

        if self.model_requirements and any(k in self.model_requirements for k in ("min_context_window", "required_features", "preferred_tier", "supported_models")):
            try:
                return ModelConstraint.from_dict(self.model_requirements)
            except Exception:
                pass

        reg = registry or default_capability_registry
        cap_constraint = reg.resolve_model_constraints(self.capabilities)
        if cap_constraint:
            return cap_constraint

        return ModelConstraint()

    def get_effective_permissions(self, registry: Optional[CapabilityRegistry] = None) -> Any:
        """
        Resolves the PermissionPolicy for this agent.
        Precedence:
        1. Explicit self.permission_policy
        2. Resolved from self.capabilities
        3. Default role policy from DEFAULT_ROLE_POLICIES
        """
        if self.permission_policy:
            return self.permission_policy

        reg = registry or default_capability_registry
        cap_policy = reg.resolve_permissions(self.capabilities)
        if cap_policy and cap_policy.allowed_operations:
            return cap_policy

        role_clean = self.name.upper().strip()
        if role_clean in DEFAULT_ROLE_POLICIES:
            return DEFAULT_ROLE_POLICIES[role_clean]

        return cap_policy

    def validate_model_compatibility(
        self,
        model_name: str,
        context_window: Optional[int] = None,
        features: Optional[List[str]] = None,
        registry: Optional[CapabilityRegistry] = None,
    ) -> Tuple[bool, str]:
        """
        Validates if a candidate model satisfies this agent's model constraints.
        """
        constraint = self.get_effective_model_constraint(registry)
        return constraint.validate_model(model_name, context_window, features)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "role_description": self.role_description,
            "capabilities": self.capabilities,
            "tools": self.tools,
            "skills": self.skills,
            "model_requirements": self.model_requirements,
            "category": self.category,
            "model": self.model,
            "temperature": self.temperature,
            "model_constraint": self.model_constraint.to_dict() if self.model_constraint else None,
            "permission_policy": self.permission_policy.to_dict() if self.permission_policy and hasattr(self.permission_policy, "to_dict") else None,
        }


# Backwards compatibility alias
AgentDefinition = AgentManifest


class AgentRegistry:
    """
    Dynamic Registry for agent discovery, capability resolution, tool sandboxing,
    and runtime instantiation.
    """

    def __init__(self):
        self._agents: Dict[str, AgentManifest] = {}
        self._idf: Dict[str, float] = {}
        self._vector_index: Dict[str, Dict[str, float]] = {}

    def register(self, manifest: AgentManifest):
        """Registers or updates an AgentManifest in the registry."""
        key = manifest.name.upper().strip()
        self._agents[key] = manifest
        self._rebuild_search_index()

    def register_from_dict(self, data: Dict[str, Any]) -> AgentManifest:
        """Parses and registers an agent manifest from dictionary data."""
        name = data.get("name") or data.get("agent") or "UNNAMED_AGENT"

        mc_data = data.get("model_constraint")
        mc = None
        if mc_data and isinstance(mc_data, dict):
            mc = ModelConstraint.from_dict(mc_data)
        elif data.get("model_requirements") and any(k in data["model_requirements"] for k in ("min_context_window", "required_features", "preferred_tier", "supported_models")):
            mc = ModelConstraint.from_dict(data["model_requirements"])

        policy_data = data.get("permission_policy")
        policy = None
        if policy_data and isinstance(policy_data, dict):
            ops = {ToolOperationType(op) for op in policy_data.get("allowed_operations", [])}
            policy = PermissionPolicy(
                allowed_operations=ops,
                allowed_write_patterns=policy_data.get("allowed_write_patterns", []),
                forbidden_write_patterns=policy_data.get("forbidden_write_patterns", []),
                allowed_command_prefixes=policy_data.get("allowed_command_prefixes", []),
                forbidden_command_patterns=policy_data.get("forbidden_command_patterns", []),
                vcs_commit_requires_approval=policy_data.get("vcs_commit_requires_approval", True),
            )

        manifest = AgentManifest(
            name=name,
            role_description=data.get("role_description") or data.get("description") or "",
            capabilities=data.get("capabilities") or [],
            tools=data.get("tools") or [],
            skills=data.get("skills") or [],
            model_requirements=data.get("model_requirements") or data.get("model_reqs") or {},
            system_prompt_template=data.get("system_prompt_template") or data.get("prompt_template"),
            category=data.get("category", "general"),
            model=data.get("model"),
            temperature=float(data.get("temperature", 0.2)),
            model_constraint=mc,
            permission_policy=policy,
        )
        self.register(manifest)
        return manifest

    def load_from_directory(self, dir_path: Union[str, Path]) -> List[AgentManifest]:
        """Loads and registers all .json and .yaml/.yml agent manifests from a directory."""
        path = Path(dir_path)
        loaded = []
        if not path.exists() or not path.is_dir():
            return loaded

        for file_path in path.glob("*.*"):
            if file_path.suffix.lower() == ".json":
                try:
                    data = json.loads(file_path.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                loaded.append(self.register_from_dict(item))
                    elif isinstance(data, dict):
                        loaded.append(self.register_from_dict(data))
                except Exception:
                    pass
            elif file_path.suffix.lower() in (".yaml", ".yml"):
                try:
                    import yaml
                    data = yaml.safe_load(file_path.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                loaded.append(self.register_from_dict(item))
                    elif isinstance(data, dict):
                        loaded.append(self.register_from_dict(data))
                except Exception:
                    pass
        return loaded

    def get(self, name: str) -> Optional[AgentManifest]:
        """Lookup an agent manifest by name (case-insensitive with alias and variant resolution)."""
        if not name or not isinstance(name, str):
            return None
        key = name.upper().strip()
        if key in self._agents:
            return self._agents[key]
        clean_key = re.sub(r"[-_]AGENT$", "", key)
        if clean_key in self._agents:
            return self._agents[clean_key]
        aliases = {
            "SPEC": "SPECIFICATION",
            "ARCH": "ARCHITECTURE",
            "PLAN": "PLANNER",
            "TEST": "TESTER",
            "CODE": "CODER",
            "REVIEW": "REVIEWER",
            "DOC": "DOCUMENTER",
            "DOCS": "DOCUMENTER",
            "SEC": "SECURITY_AUDITOR",
            "SECURITY": "SECURITY_AUDITOR",
        }
        if clean_key in aliases and aliases[clean_key] in self._agents:
            return self._agents[aliases[clean_key]]
        for k, v in self._agents.items():
            if k.lower() == name.lower().strip() or getattr(v, "name", "").lower() == name.lower().strip():
                return v
        return None

    def list_agents(self, category: Optional[str] = None) -> List[AgentManifest]:
        """Lists all registered agents, optionally filtered by category."""
        if not category:
            return list(self._agents.values())
        cat_lower = category.lower().strip()
        return [a for a in self._agents.values() if a.category.lower() == cat_lower]

    def _tokenize(self, text: str) -> List[str]:
        return [tok for tok in re.findall(r"[a-zA-Z0-9_\-\.\+]+", text.lower()) if len(tok) > 1]

    def _rebuild_search_index(self):
        """Rebuilds TF-IDF vector index for agent discovery."""
        num_docs = len(self._agents)
        if num_docs == 0:
            self._idf.clear()
            self._vector_index.clear()
            return

        doc_freqs: Dict[str, int] = {}
        doc_tokens_map: Dict[str, List[str]] = {}

        for key, manifest in self._agents.items():
            corpus_parts = [
                manifest.name,
                manifest.role_description,
                " ".join(manifest.capabilities),
                " ".join(manifest.tools),
                " ".join(manifest.skills),
                manifest.category,
            ]
            tokens = self._tokenize(" ".join(corpus_parts))
            doc_tokens_map[key] = tokens
            unique_toks = set(tokens)
            for tok in unique_toks:
                doc_freqs[tok] = doc_freqs.get(tok, 0) + 1

        self._idf = {tok: math.log((num_docs + 1) / (freq + 0.5)) + 1.0 for tok, freq in doc_freqs.items()}

        self._vector_index.clear()
        for key, tokens in doc_tokens_map.items():
            tf: Dict[str, float] = {}
            for tok in tokens:
                tf[tok] = tf.get(tok, 0.0) + 1.0
            total_tokens = max(len(tokens), 1)
            vec: Dict[str, float] = {}
            norm_sq = 0.0
            for tok, count in tf.items():
                tfidf = (count / total_tokens) * self._idf.get(tok, 1.0)
                vec[tok] = tfidf
                norm_sq += tfidf * tfidf
            norm = math.sqrt(norm_sq) or 1.0
            self._vector_index[key] = {tok: val / norm for tok, val in vec.items()}

    def discover(
        self,
        query: str = "",
        capabilities: Optional[List[str]] = None,
        tools: Optional[List[str]] = None,
        category: Optional[str] = None,
        top_k: int = 3,
        threshold: float = 0.0,
    ) -> List[Tuple[AgentManifest, float]]:
        """
        Dynamically discovers the most relevant agents based on:
        1. Semantic TF-IDF similarity on query & description.
        2. Exact and fuzzy match bonus for requested capabilities.
        3. Tool coverage bonus for requested tools.
        4. Category filtering.
        """
        if not self._agents:
            return []

        # Tokenize query
        q_tokens = self._tokenize(query)
        q_tf: Dict[str, float] = {}
        for tok in q_tokens:
            q_tf[tok] = q_tf.get(tok, 0.0) + 1.0
        q_norm_sq = 0.0
        q_vec: Dict[str, float] = {}
        for tok, count in q_tf.items():
            tfidf = (count / max(len(q_tokens), 1)) * self._idf.get(tok, 1.0)
            q_vec[tok] = tfidf
            q_norm_sq += tfidf * tfidf
        q_norm = math.sqrt(q_norm_sq) or 1.0
        q_norm_vec = {tok: val / q_norm for tok, val in q_vec.items()}

        req_caps: Set[str] = {c.lower().strip() for c in (capabilities or [])}
        req_tools: Set[str] = {t.lower().strip() for t in (tools or [])}

        results: List[Tuple[AgentManifest, float]] = []

        for key, manifest in self._agents.items():
            if category and manifest.category.lower() != category.lower().strip():
                continue

            # 1. Cosine similarity
            doc_vec = self._vector_index.get(key, {})
            sim_score = sum(q_norm_vec[tok] * doc_vec.get(tok, 0.0) for tok in q_norm_vec)

            # 2. Capability overlap score
            agent_caps = {c.lower().strip() for c in manifest.capabilities}
            cap_bonus = 0.0
            if req_caps:
                matched_caps = req_caps.intersection(agent_caps)
                cap_bonus = (len(matched_caps) / len(req_caps)) * 2.0

            # 3. Tool coverage score
            agent_tools = {t.lower().strip() for t in manifest.tools}
            tool_bonus = 0.0
            if req_tools:
                matched_tools = req_tools.intersection(agent_tools)
                tool_bonus = (len(matched_tools) / len(req_tools)) * 1.5

            # 4. Name keyword match
            name_bonus = 0.0
            if any(tok in manifest.name.lower() for tok in q_tokens):
                name_bonus += 0.5

            total_score = sim_score + cap_bonus + tool_bonus + name_bonus
            if total_score >= threshold:
                results.append((manifest, total_score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def create_agent_instance(
        self,
        name_or_manifest: Union[str, AgentManifest],
        llm=None,
        workspace=None,
        tool_registry=None,
        skill_registry=None,
        mcp_client=None,
        message_bus=None,
        approval_gate=None,
        **kwargs,
    ) -> "BaseAgent":
        """
        Dynamically instantiates an agent instance:
        - If manifest has a custom agent_class, instantiates that class using signature introspection.
        - Otherwise, instantiates a DynamicAgent configured with the manifest.
        - Uses inspect.signature to prevent unexpected keyword argument errors.
        """
        import inspect

        if isinstance(name_or_manifest, AgentManifest):
            manifest = name_or_manifest
        else:
            manifest = self.get(name_or_manifest)
            if not manifest:
                raise ValueError(f"Agent '{name_or_manifest}' is not registered in AgentRegistry.")

        from ..agents.dynamic_agent import DynamicAgent
        target_cls = manifest.agent_class or DynamicAgent

        candidate_kwargs: Dict[str, Any] = {
            "model": manifest.model,
            "llm": llm,
            "workspace": workspace,
            "tool_registry": tool_registry,
            "skill_registry": skill_registry,
            "mcp_client": mcp_client,
            "message_bus": message_bus,
            "approval_gate": approval_gate,
            **kwargs,
        }

        if issubclass(target_cls, DynamicAgent):
            candidate_kwargs.update({
                "name": manifest.name,
                "role_description": manifest.role_description,
                "capabilities": manifest.capabilities,
                "tools": manifest.tools,
                "skills": manifest.skills,
                "model_requirements": manifest.model_requirements,
                "system_prompt_template": manifest.system_prompt_template,
            })

        # Signature introspection to reconcile caller and constructor contracts
        try:
            sig = inspect.signature(target_cls.__init__)
            has_var_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            if has_var_kwargs:
                filtered_kwargs = candidate_kwargs
            else:
                accepted_params = set(sig.parameters.keys())
                filtered_kwargs = {k: v for k, v in candidate_kwargs.items() if k in accepted_params}
            return target_cls(**filtered_kwargs)
        except Exception as e:
            # Fallback direct construction
            if issubclass(target_cls, DynamicAgent):
                return DynamicAgent(
                    name=manifest.name,
                    role_description=manifest.role_description,
                    capabilities=manifest.capabilities,
                    tools=manifest.tools,
                    skills=manifest.skills,
                    model_requirements=manifest.model_requirements,
                    system_prompt_template=manifest.system_prompt_template,
                    model=manifest.model,
                    llm=llm,
                    workspace=workspace,
                    tool_registry=tool_registry,
                    skill_registry=skill_registry,
                    mcp_client=mcp_client,
                    message_bus=message_bus,
                    approval_gate=approval_gate,
                )
            raise e
