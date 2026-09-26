"""
Repository Brain.
Central persistent codebase intelligence engine backed by SQLite in .orchestrator/repository/.
Coordinates scanner, symbol indexing, import graph, call graph, runtime detector, and architecture index.
Fully integrated with SkillRegistry, AgentRegistry, MCPManager, UnifiedToolDispatcher,
WorkspaceManager, LangGraph StateGraph, and LiteLLM Gateway.
"""
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.registry.skill_registry import SkillRegistry
from agent_orchestrator.registry.agent_registry import AgentRegistry
from agent_orchestrator.mcp.manager import MCPManager
from agent_orchestrator.llm import LLMClient

from .scanner import RepositoryScanner, ScannedFile, GitMetadata
from .symbol_index import SymbolIndex, CodeSymbol
from .import_graph import ImportGraph, ImportEdge
from .call_graph import CallGraph, CallEdge
from .runtime_detector import RuntimeDetector, RuntimeProfile
from .architecture_index import ArchitectureIndex, ArchitectureComponent

logger = logging.getLogger("repository.brain")


class RepositoryBrain:
    """
    Unified Persistent Repository Intelligence Engine.
    Stores and queries verified code reality from .orchestrator/repository/repository_brain.db.
    """

    def __init__(
        self,
        workspace_manager: Optional[WorkspaceManager] = None,
        db_path: Optional[Union[str, Path]] = None,
        skill_registry: Optional[SkillRegistry] = None,
        agent_registry: Optional[AgentRegistry] = None,
        mcp_manager: Optional[MCPManager] = None,
        tool_dispatcher: Optional[UnifiedToolDispatcher] = None,
        llm_client: Optional[LLMClient] = None,
        **kwargs: Any,
    ):
        ws = workspace_manager
        if ws is None and "workspace_root" in kwargs and kwargs["workspace_root"]:
            try:
                from agent_orchestrator.tools.workspace import WorkspaceManager
                ws = WorkspaceManager(root_dir=kwargs["workspace_root"])
            except Exception:
                ws = None
        self.workspace = ws or WorkspaceManager()
        self.root_dir = Path(self.workspace.root_dir).resolve()

        # Persistent database setup in .orchestrator/repository/
        default_db_dir = self.root_dir / ".orchestrator" / "repository"
        default_db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path else default_db_dir / "repository_brain.db"
        self.db_conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._init_database_schema()

        # Specialized Intelligence Engines
        self.scanner = RepositoryScanner(self.root_dir, db_conn=self.db_conn)
        self.symbols = SymbolIndex(db_conn=self.db_conn)
        self.imports = ImportGraph(self.root_dir, db_conn=self.db_conn)
        self.calls = CallGraph(db_conn=self.db_conn)
        self.runtime = RuntimeDetector(self.root_dir, db_conn=self.db_conn)
        self.architecture = ArchitectureIndex(self.root_dir, db_conn=self.db_conn)

        # Core Systems Integration
        self.skill_registry = skill_registry or SkillRegistry()
        self.agent_registry = agent_registry or AgentRegistry()
        self.mcp_manager = mcp_manager
        self.dispatcher = tool_dispatcher
        self.llm = llm_client or LLMClient()

        if self.dispatcher:
            self.register_tools_with_dispatcher(self.dispatcher)

    def _init_database_schema(self):
        """Initializes persistent SQLite database schema."""
        cursor = self.db_conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                language TEXT,
                sha256 TEXT,
                size_bytes INTEGER,
                last_modified REAL,
                is_git_tracked INTEGER,
                is_git_modified INTEGER
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS symbols (
                name TEXT,
                kind TEXT,
                file_path TEXT,
                line_number INTEGER,
                end_line INTEGER,
                parent_symbol TEXT,
                signature TEXT,
                docstring TEXT,
                PRIMARY KEY (file_path, name, line_number)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols (name)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS imports (
                source_file TEXT,
                imported_module TEXT,
                imported_symbol TEXT,
                alias TEXT,
                line_number INTEGER,
                is_relative INTEGER,
                target_file TEXT,
                PRIMARY KEY (source_file, imported_module, imported_symbol, line_number)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                caller_file TEXT,
                caller_symbol TEXT,
                callee_name TEXT,
                line_number INTEGER,
                args_count INTEGER,
                PRIMARY KEY (caller_file, caller_symbol, callee_name, line_number)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls (callee_name)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS runtime_profiles (
                id INTEGER PRIMARY KEY,
                primary_language TEXT,
                framework TEXT,
                package_manager TEXT,
                test_framework TEXT,
                is_monorepo INTEGER,
                docker_present INTEGER,
                entrypoints_json TEXT,
                dependencies_json TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS architecture_components (
                name TEXT PRIMARY KEY,
                root_path TEXT,
                layer TEXT,
                responsibilities_json TEXT,
                exported_interfaces_json TEXT,
                dependencies_json TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS brain_metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at REAL
            )
        """)
        self.db_conn.commit()

    def build_full_index(self, force: bool = False) -> Dict[str, Any]:
        """
        Executes complete, end-to-end repository indexing:
        1. Filesystem & Git status scan (GitPython)
        2. AST and tree-sitter symbol extraction
        3. AST & networkx dependency import graph construction
        4. AST & networkx call graph construction
        5. Tech stack & environment profile detection
        6. Architectural component and layer mapping
        Persists all data to SQLite and records metadata.
        """
        start_time = time.time()
        logger.info(f"Building repository brain index for {self.root_dir}...")

        # 1. Scan files
        scanned_files = self.scanner.scan(force_all=force)
        git_meta = self.scanner.get_git_metadata()

        total_symbols = 0
        total_imports = 0
        total_calls = 0

        # 2-4. Process each file for symbols, imports, and calls
        for sf in scanned_files:
            abs_p = Path(sf.absolute_path)
            if not abs_p.exists():
                continue

            try:
                content = abs_p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # Symbols
            if sf.language in ("python", "javascript", "typescript") or sf.path.endswith((".py", ".js", ".ts", ".tsx")):
                syms = self.symbols.index_file(sf.path, content, sf.language)
                total_symbols += len(syms)

            # Python AST specific processing
            if sf.language == "python" or sf.path.endswith(".py"):
                imps = self.imports.index_file_imports(sf.path, content)
                total_imports += len(imps)

                calls = self.calls.index_file_calls(sf.path, content)
                total_calls += len(calls)

        # 5. Detect Runtime Stack
        runtime_profile = self.runtime.detect()

        # 6. Index Architecture
        components = self.architecture.get_components()

        # Record metadata
        dur = round(time.time() - start_time, 4)
        summary = {
            "root_dir": str(self.root_dir),
            "database_path": str(self.db_path),
            "scanned_files_count": len(scanned_files),
            "indexed_symbols_count": total_symbols,
            "indexed_imports_count": total_imports,
            "indexed_calls_count": total_calls,
            "runtime_profile": runtime_profile.to_dict(),
            "architecture_components_count": len(components),
            "git_metadata": git_meta.to_dict(),
            "duration_seconds": dur,
            "indexed_at": time.time(),
        }

        self.db_conn.execute(
            "INSERT OR REPLACE INTO brain_metadata (key, value, updated_at) VALUES (?, ?, ?)",
            ("latest_index_summary", json.dumps(summary), time.time()),
        )
        self.db_conn.commit()

        logger.info(f"Repository brain index built successfully in {dur}s.")
        return summary

    def index_repository(self, force: bool = False) -> Dict[str, Any]:
        """Convenience alias for build_full_index."""
        return self.build_full_index(force=force)

    # =========================================================================
    # REPOSITORY INTELLIGENCE QUERIES
    # =========================================================================

    def find_symbol(self, name: str) -> List[Dict[str, Any]]:
        """Finds code symbol definitions across the repository."""
        syms = self.symbols.find_symbol(name)
        return [s.to_dict() for s in syms]

    def get_symbol(self, name: str) -> Optional[Dict[str, Any]]:
        """Returns the first symbol matching name, or None."""
        syms = self.find_symbol(name)
        return syms[0] if syms else None

    def get_file_symbols(self, file_path: str) -> List[Dict[str, Any]]:
        """Returns all symbols declared in a specific file."""
        syms = self.symbols.get_symbols_in_file(file_path)
        if not syms:
            # Try fuzzy matching by basename or suffix if absolute/relative differences exist
            cursor = self.db_conn.cursor()
            cursor.execute(
                """
                SELECT name, kind, file_path, line_number, end_line, parent_symbol, signature, docstring
                FROM symbols WHERE file_path = ? OR file_path LIKE ? ORDER BY line_number ASC
                """,
                (file_path, f"%{file_path}"),
            )
            return [
                {
                    "name": r[0], "kind": r[1], "file_path": r[2], "line_number": r[3],
                    "end_line": r[4], "parent_symbol": r[5], "signature": r[6], "docstring": r[7],
                }
                for r in cursor.fetchall()
            ]
        return [s.to_dict() for s in syms]

    def get_all_symbols(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Returns all indexed symbols up to limit."""
        cursor = self.db_conn.cursor()
        cursor.execute(
            """
            SELECT name, kind, file_path, line_number, end_line, parent_symbol, signature, docstring
            FROM symbols LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "name": r[0], "kind": r[1], "file_path": r[2], "line_number": r[3],
                "end_line": r[4], "parent_symbol": r[5], "signature": r[6], "docstring": r[7],
            }
            for r in cursor.fetchall()
        ]

    def get_callers(self, symbol_name: str) -> List[Dict[str, Any]]:
        """Returns callers for a given function or method symbol."""
        return self.calls.get_callers(symbol_name)

    def get_callees(self, symbol_name: str) -> List[Dict[str, Any]]:
        """Returns callees for a given function or method symbol."""
        return self.calls.get_callees(symbol_name)

    def get_call_graph(self, symbol_name: str) -> Dict[str, Any]:
        """Returns callers and callees for a given function or method symbol."""
        callers = self.calls.get_callers(symbol_name)
        callees = self.calls.get_callees(symbol_name)
        return {
            "symbol": symbol_name,
            "callers": callers,
            "callees": callees,
            "total_callers": len(callers),
            "total_callees": len(callees),
        }

    def get_impact_radius(self, symbol_name: str) -> Dict[str, Any]:
        """Calculates upstream blast radius for a symbol mutation."""
        return self.calls.get_impact_radius(symbol_name)

    def get_dependencies(self, file_path: str, recursive: bool = False) -> List[str]:
        """Returns direct or transitive dependencies for a file."""
        return self.imports.get_dependencies(file_path, recursive=recursive)

    def get_dependents(self, file_path: str, recursive: bool = False) -> List[str]:
        """Returns direct or transitive dependents for a file."""
        return self.imports.get_dependents(file_path, recursive=recursive)

    def get_architecture_summary(self) -> Dict[str, Any]:
        """Returns architectural components, layers, and boundary health."""
        comps = [c.to_dict() for c in self.architecture.get_components()]
        return {
            "components": comps,
            "total_components": len(comps),
            "layers": sorted(list({c["layer"] for c in comps})),
        }

    def get_runtime_profile(self) -> Dict[str, Any]:
        """Returns detected tech stack and runtime environment profile."""
        return self.runtime.detect().to_dict()

    # =========================================================================
    # CORE SYSTEM INTEGRATIONS
    # =========================================================================

    def register_tools_with_dispatcher(self, dispatcher: UnifiedToolDispatcher):
        """
        Registers repository intelligence tools directly into UnifiedToolDispatcher,
        resolving the capability deficits identified in Phase 1 (e.g. get_call_graph, get_impact_radius).
        """
        tools_to_register = [
            ("repo_find_symbol", lambda name, **kw: self.find_symbol(name)),
            ("repo_get_call_graph", lambda symbol_name, **kw: self.get_call_graph(symbol_name)),
            ("repo_get_impact_radius", lambda symbol_name, **kw: self.get_impact_radius(symbol_name)),
            ("repo_get_dependencies", lambda file_path, recursive=False, **kw: self.get_dependencies(file_path, recursive)),
            ("repo_architecture_summary", lambda **kw: self.get_architecture_summary()),
            ("get_call_graph", lambda symbol_name, **kw: self.get_call_graph(symbol_name)),
            ("get_impact_radius", lambda symbol_name, **kw: self.get_impact_radius(symbol_name)),
            ("get_architecture_summary", lambda **kw: self.get_architecture_summary()),
        ]

        if hasattr(dispatcher, "builtin_registry") and dispatcher.builtin_registry:
            br = dispatcher.builtin_registry
            for tool_name, tool_fn in tools_to_register:
                if hasattr(br, "register_tool"):
                    try:
                        br.register_tool(name=tool_name, func=tool_fn)
                    except Exception:
                        pass
                elif hasattr(br, "_tools") and isinstance(br._tools, dict):
                    br._tools[tool_name] = tool_fn

    def create_langgraph_node(self) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        """
        Creates a LangGraph-compatible node function for inclusion in StateGraph.
        Injects verified repository intelligence into orchestrator graph state.
        """
        def repository_intelligence_node(state: Dict[str, Any]) -> Dict[str, Any]:
            # Ensure fresh index
            summary = self.build_full_index()
            new_state = dict(state)
            new_state["repository_summary"] = summary
            new_state["codebase_runtime_profile"] = summary.get("runtime_profile")
            new_state["codebase_architecture"] = self.get_architecture_summary()
            return new_state

        return repository_intelligence_node

    def close(self):
        """Closes the persistent SQLite database connection safely."""
        if self.db_conn:
            try:
                self.db_conn.commit()
                self.db_conn.close()
            except Exception:
                pass
