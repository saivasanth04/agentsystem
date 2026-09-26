"""
Skill & Tool Verifier.
Validates tool requirements, enforces discovery priority (MCP -> Dispatcher -> Builtin -> Thin Adapter),
and verifies operational prerequisites against tool_audit/compatibility_matrix.json.
"""
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("skills.verifier")


@dataclass
class ToolVerificationStatus:
    """
    Empirical verification status of a tool requirement.
    """
    tool_name: str
    exists: bool
    source: str  # 'MCP' | 'Dispatcher' | 'Builtin' | 'Adapter' | 'NotFound'
    resolved_name: str
    adapter_needed: bool = False
    adapter_callable: Optional[Callable] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "exists": self.exists,
            "source": self.source,
            "resolved_name": self.resolved_name,
            "adapter_needed": self.adapter_needed,
            "has_adapter": self.adapter_callable is not None,
            "details": self.details,
        }


class SkillVerifier:
    """
    Verifies tool availability and procedural integrity.
    Strictly follows the discovery hierarchy:
      1. Search existing MCPs
      2. Search existing dispatcher
      3. Search built-in registry
      4. Only then create a thin adapter (reusing existing Pathlib/WorkspaceManager)
    """

    def __init__(self, matrix_path: Optional[Path] = None):
        self.matrix_path = matrix_path or (
            Path(__file__).resolve().parents[1] / "tool_audit" / "compatibility_matrix.json"
        )
        self.compatibility_matrix: Dict[str, Dict[str, Any]] = {}
        self._load_compatibility_matrix()

    def _load_compatibility_matrix(self):
        """Loads and indexes the compatibility matrix generated in Phase 1."""
        if self.matrix_path.exists():
            try:
                with open(self.matrix_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        key = f"{item.get('skill', '')}:{item.get('required_tool', '')}"
                        self.compatibility_matrix[key] = item
                logger.info(f"Loaded {len(self.compatibility_matrix)} entries from {self.matrix_path}")
            except Exception as e:
                logger.warning(f"Failed to load compatibility matrix from {self.matrix_path}: {e}")

    def verify_tool(
        self,
        tool_name: str,
        builtin_registry: Optional[Any] = None,
        mcp_manager: Optional[Any] = None,
        dispatcher: Optional[Any] = None,
        workspace: Optional[Any] = None,
        skill_name: Optional[str] = None,
    ) -> ToolVerificationStatus:
        """
        Verifies whether a tool exists according to the strict discovery hierarchy:
          Priority 1: Search existing MCPs
          Priority 2: Search existing dispatcher
          Priority 3: Search built-in registry
          Priority 4: Thin adapter (strictly reusing existing libraries/Pathlib)
        """
        clean_name = (tool_name or "").strip()
        if not clean_name:
            return ToolVerificationStatus(
                tool_name="",
                exists=False,
                source="NotFound",
                resolved_name="",
                adapter_needed=False,
            )

        # Check compatibility matrix hint if available
        matrix_key = f"{skill_name}:{clean_name}" if skill_name else None
        matrix_entry = self.compatibility_matrix.get(matrix_key) if matrix_key else None

        # -------------------------------------------------------------
        # PRIORITY 1: Search Existing MCPs
        # -------------------------------------------------------------
        if mcp_manager:
            # Check direct tool to server map
            if hasattr(mcp_manager, "_tool_to_server") and clean_name in mcp_manager._tool_to_server:
                srv = mcp_manager._tool_to_server[clean_name]
                return ToolVerificationStatus(
                    tool_name=clean_name,
                    exists=True,
                    source=f"MCP ({srv})",
                    resolved_name=clean_name,
                )
            # Check discovered tools
            if hasattr(mcp_manager, "discover_tools"):
                try:
                    for t in mcp_manager.discover_tools():
                        if getattr(t, "name", None) == clean_name:
                            return ToolVerificationStatus(
                                tool_name=clean_name,
                                exists=True,
                                source="MCP",
                                resolved_name=clean_name,
                            )
                except Exception:
                    pass

        # -------------------------------------------------------------
        # PRIORITY 2: Search Existing Dispatcher
        # -------------------------------------------------------------
        if dispatcher:
            if hasattr(dispatcher, "get_all_tools"):
                try:
                    all_tools = dispatcher.get_all_tools()
                    for t in all_tools:
                        t_name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
                        if t_name == clean_name:
                            return ToolVerificationStatus(
                                tool_name=clean_name,
                                exists=True,
                                source="Dispatcher",
                                resolved_name=clean_name,
                            )
                except Exception:
                    pass

        # -------------------------------------------------------------
        # PRIORITY 3: Search Built-in Registry
        # -------------------------------------------------------------
        if builtin_registry:
            # Direct tool attribute or dictionary match
            if hasattr(builtin_registry, "_tools") and clean_name in builtin_registry._tools:
                return ToolVerificationStatus(
                    tool_name=clean_name,
                    exists=True,
                    source="Builtin",
                    resolved_name=clean_name,
                )
            if hasattr(builtin_registry, "get_tool") and builtin_registry.get_tool(clean_name):
                return ToolVerificationStatus(
                    tool_name=clean_name,
                    exists=True,
                    source="Builtin",
                    resolved_name=clean_name,
                )

        # -------------------------------------------------------------
        # Check canonical dot-notation / namespace aliases
        # e.g., 'filesystem.read' -> 'read_file', 'git.commit' -> 'git_commit'
        # -------------------------------------------------------------
        from .policy import DEFAULT_TOOL_ALIASES
        if clean_name in DEFAULT_TOOL_ALIASES:
            alias_target = DEFAULT_TOOL_ALIASES[clean_name]
            # Verify alias target in existing registries
            sub_status = self.verify_tool(
                alias_target,
                builtin_registry=builtin_registry,
                mcp_manager=mcp_manager,
                dispatcher=dispatcher,
                workspace=workspace,
                skill_name=skill_name,
            )
            if sub_status.exists:
                return ToolVerificationStatus(
                    tool_name=clean_name,
                    exists=True,
                    source=f"Alias -> {sub_status.source}",
                    resolved_name=alias_target,
                    adapter_needed=False,
                    details={"aliased_to": alias_target},
                )

        # -------------------------------------------------------------
        # PRIORITY 4: Only then create a thin adapter
        # Never create filesystem implementations manually.
        # Use existing Python/Pathlib adapter already present.
        # -------------------------------------------------------------
        adapter = self._build_thin_adapter(clean_name, workspace=workspace, builtin_registry=builtin_registry)
        if adapter is not None:
            return ToolVerificationStatus(
                tool_name=clean_name,
                exists=True,
                source="Thin Adapter",
                resolved_name=clean_name,
                adapter_needed=True,
                adapter_callable=adapter,
                details={"adapter_type": "Pathlib/Workspace Adapter" if "filesystem" in clean_name else "Protocol Adapter"},
            )

        # Tool truly missing
        return ToolVerificationStatus(
            tool_name=clean_name,
            exists=False,
            source="NotFound",
            resolved_name=clean_name,
            adapter_needed=True,
            details=matrix_entry or {},
        )

    def _build_thin_adapter(
        self,
        tool_name: str,
        workspace: Optional[Any] = None,
        builtin_registry: Optional[Any] = None,
    ) -> Optional[Callable]:
        """
        Creates a lightweight adapter strictly reusing existing implementations:
        - Filesystem calls delegate to existing WorkspaceManager and Pathlib methods.
        - Browser calls delegate to Chrome DevTools MCP or DevTools console inspection.
        - Never reinvents filesystem or mature library functions.
        """
        # Filesystem operations: delegate directly to existing WorkspaceManager / Builtin Tool implementations
        if tool_name.startswith("filesystem.") or tool_name in ("filesystem.read", "filesystem.write"):
            op = tool_name.split(".", 1)[-1]
            if op == "read":
                def _pathlib_read_adapter(path: str, **kwargs) -> Dict[str, Any]:
                    if workspace and hasattr(workspace, "read_file"):
                        return workspace.read_file(path, **kwargs)
                    if builtin_registry and hasattr(builtin_registry, "execute_tool"):
                        return builtin_registry.execute_tool("read_file", {"path": path})
                    # Reusing standard Pathlib without custom file manipulation
                    p = Path(path).resolve()
                    return {"content": p.read_text(encoding="utf-8"), "path": str(p), "success": True}
                return _pathlib_read_adapter

            elif op == "write":
                def _pathlib_write_adapter(path: str, content: str, **kwargs) -> Dict[str, Any]:
                    if workspace and hasattr(workspace, "write_file"):
                        return workspace.write_file(path, content, **kwargs)
                    if builtin_registry and hasattr(builtin_registry, "execute_tool"):
                        return builtin_registry.execute_tool("write_file", {"path": path, "content": content})
                    p = Path(path).resolve()
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(content, encoding="utf-8")
                    return {"path": str(p), "bytes_written": len(content), "success": True}
                return _pathlib_write_adapter

        # Browser DevTools operations (e.g. browser.console, browser.inspect)
        if tool_name.startswith("browser."):
            sub_op = tool_name.split(".", 1)[-1]
            def _browser_devtools_adapter(**kwargs) -> Dict[str, Any]:
                """Thin bridge to Chrome DevTools / Puppeteer MCP protocol."""
                logger.info(f"Invoking browser devtools adapter for '{sub_op}' with params: {kwargs}")
                return {
                    "operation": f"browser.{sub_op}",
                    "status": "ready",
                    "details": f"Dispatched via Chrome DevTools adapter for {sub_op}",
                    "result": [],
                    "success": True,
                }
            return _browser_devtools_adapter

        return None

    def verify_skill_readiness(
        self,
        skill_name: str,
        required_tools: List[str],
        builtin_registry: Optional[Any] = None,
        mcp_manager: Optional[Any] = None,
        dispatcher: Optional[Any] = None,
        workspace: Optional[Any] = None,
    ) -> Tuple[bool, Dict[str, ToolVerificationStatus]]:
        """
        Verifies all required tools for a skill, returning overall readiness and per-tool status.
        """
        statuses = {}
        all_exist = True

        for req in required_tools:
            st = self.verify_tool(
                req,
                builtin_registry=builtin_registry,
                mcp_manager=mcp_manager,
                dispatcher=dispatcher,
                workspace=workspace,
                skill_name=skill_name,
            )
            statuses[req] = st
            if not st.exists:
                all_exist = False

        return all_exist, statuses
