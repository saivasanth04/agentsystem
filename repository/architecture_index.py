"""
Architecture Index.
Decomposes the repository into modular architectural components, assigns architectural layers,
and evaluates layer boundary violations with persistent SQLite storage.
"""
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("repository.architecture_index")

# Layer hierarchy (Higher layers may depend on lower layers, but lower layers must not import higher layers)
LAYER_ORDER: Dict[str, int] = {
    "Protocol": 10,
    "Persistence": 20,
    "Execution": 30,
    "Orchestration": 40,
    "Gateway": 50,
    "Presentation": 60,
}


@dataclass
class ArchitectureComponent:
    """Represents a discrete modular subsystem within the codebase."""
    name: str
    root_path: str
    layer: str
    responsibilities: List[str] = field(default_factory=list)
    exported_interfaces: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "root_path": self.root_path,
            "layer": self.layer,
            "layer_rank": LAYER_ORDER.get(self.layer, 0),
            "responsibilities": self.responsibilities,
            "exported_interfaces": self.exported_interfaces,
            "dependencies": self.dependencies,
        }


class ArchitectureIndex:
    """
    Architectural boundary indexing and seam analysis engine.
    Detects subsystems, maps dependencies, and flags architectural inversion anti-patterns.
    """

    KNOWN_COMPONENTS = [
        {
            "name": "UnifiedGateway",
            "root_path": "unified_gateway",
            "layer": "Gateway",
            "responsibilities": ["Multi-provider LLM request routing", "Token rate-limiting", "Provider failover"],
            "exported_interfaces": ["UnifiedGatewayServer", "GatewayRouter"],
        },
        {
            "name": "TaskOrchestrator",
            "root_path": "agent_orchestrator",
            "layer": "Orchestration",
            "responsibilities": ["Multi-agent state graph coordination", "Dynamic subtask scheduling", "Verification gate"],
            "exported_interfaces": ["TaskOrchestrator", "OrchestratorState"],
        },
        {
            "name": "SkillRuntime",
            "root_path": "skills",
            "layer": "Execution",
            "responsibilities": ["Dynamic procedural skill compilation", "Runtime policy enforcement", "Tool scoping"],
            "exported_interfaces": ["SkillRuntime", "SkillResolver", "SkillCompiler", "SkillVerifier"],
        },
        {
            "name": "UnifiedToolDispatcher",
            "root_path": "agent_orchestrator/tools",
            "layer": "Execution",
            "responsibilities": ["Unified native & MCP tool dispatching", "Circuit breaker monitoring", "Adaptive fallback"],
            "exported_interfaces": ["UnifiedToolDispatcher", "BuiltinToolRegistry"],
        },
        {
            "name": "MCPProtocol",
            "root_path": "agent_orchestrator/mcp",
            "layer": "Protocol",
            "responsibilities": ["Model Context Protocol servers", "JSON-RPC stdio transports", "Circuit breakers"],
            "exported_interfaces": ["MCPManager", "InMemoryTransport"],
        },
        {
            "name": "WorkspaceManager",
            "root_path": "agent_orchestrator/tools/workspace.py",
            "layer": "Persistence",
            "responsibilities": ["Workspace sandboxing", "Path traversal protection", "File mutation locking"],
            "exported_interfaces": ["WorkspaceManager", "safe_resolve_path"],
        },
        {
            "name": "RepositoryIntelligence",
            "root_path": "repository",
            "layer": "Execution",
            "responsibilities": ["AST & tree-sitter symbol extraction", "networkx import/call graphs", "Persistent repository brain"],
            "exported_interfaces": ["RepositoryBrain", "SymbolIndex", "ImportGraph", "CallGraph"],
        },
        {
            "name": "FrontendUI",
            "root_path": "frontend",
            "layer": "Presentation",
            "responsibilities": ["Real-time execution visualization", "Agent state inspector", "Task management UI"],
            "exported_interfaces": ["ReactSPA"],
        },
    ]

    def __init__(self, root_dir: Union[str, Path], db_conn: Optional[sqlite3.Connection] = None):
        self.root_dir = Path(root_dir).resolve()
        self.db = db_conn
        self.components: Dict[str, ArchitectureComponent] = {}
        self._init_components()

    def _init_components(self):
        """Discovers and initializes architectural components existing in workspace."""
        for c in self.KNOWN_COMPONENTS:
            p = self.root_dir / c["root_path"]
            if p.exists():
                comp = ArchitectureComponent(
                    name=c["name"],
                    root_path=c["root_path"],
                    layer=c["layer"],
                    responsibilities=c["responsibilities"],
                    exported_interfaces=c["exported_interfaces"],
                )
                self.components[comp.name] = comp
                if self.db:
                    self._persist_component(comp)

        # Fallback to dynamic component discovery if no standard known components exist
        if not self.components:
            dirs = [d for d in self.root_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
            if dirs:
                for d in dirs:
                    comp = ArchitectureComponent(
                        name=d.name.capitalize(),
                        root_path=d.name,
                        layer="Execution",
                        responsibilities=[f"Subsystem {d.name}"],
                        exported_interfaces=[],
                    )
                    self.components[comp.name] = comp
                    if self.db:
                        self._persist_component(comp)
            else:
                comp = ArchitectureComponent(
                    name="CoreApplication",
                    root_path=".",
                    layer="Execution",
                    responsibilities=["Core workspace logic and modules"],
                    exported_interfaces=[],
                )
                self.components[comp.name] = comp
                if self.db:
                    self._persist_component(comp)

    def _persist_component(self, c: ArchitectureComponent):
        """Persists component metadata to SQLite."""
        if not self.db:
            return
        self.db.execute(
            """
            INSERT OR REPLACE INTO architecture_components
            (name, root_path, layer, responsibilities_json, exported_interfaces_json, dependencies_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                c.name,
                c.root_path,
                c.layer,
                json.dumps(c.responsibilities),
                json.dumps(c.exported_interfaces),
                json.dumps(c.dependencies),
            ),
        )
        try:
            self.db.commit()
        except Exception:
            pass

    def get_components(self) -> List[ArchitectureComponent]:
        """Returns all registered architectural subsystems."""
        return list(self.components.values())

    def get_component_for_file(self, file_path: str) -> Optional[ArchitectureComponent]:
        """Maps a file path to its owning architectural component."""
        posix_p = Path(file_path).as_posix()
        for comp in self.components.values():
            if posix_p.startswith(comp.root_path):
                return comp
        return None

    def check_layer_violations(self, import_edges: List[Any]) -> List[Dict[str, Any]]:
        """
        Detects architectural boundary violations where a lower layer
        inappropriately imports from a higher layer.
        """
        violations = []
        for edge in import_edges:
            src = getattr(edge, "source_file", None)
            tgt = getattr(edge, "target_file", None)
            if not src or not tgt:
                continue

            src_comp = self.get_component_for_file(src)
            tgt_comp = self.get_component_for_file(tgt)

            if src_comp and tgt_comp and src_comp.name != tgt_comp.name:
                src_rank = LAYER_ORDER.get(src_comp.layer, 0)
                tgt_rank = LAYER_ORDER.get(tgt_comp.layer, 0)

                # Lower layer importing higher layer is an architectural violation
                if src_rank < tgt_rank:
                    violations.append({
                        "source_file": src,
                        "source_component": src_comp.name,
                        "source_layer": src_comp.layer,
                        "target_file": tgt,
                        "target_component": tgt_comp.name,
                        "target_layer": tgt_comp.layer,
                        "violation": f"Layer inversion: {src_comp.layer} (rank {src_rank}) depends on {tgt_comp.layer} (rank {tgt_rank})",
                    })
        return violations
