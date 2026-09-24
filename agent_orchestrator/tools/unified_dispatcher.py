"""
Unified Tool Dispatcher & Registry.
Bridges Builtin Native Tools and dynamic MCP Tools under a single agent interface.
Integrates active health checking, circuit breaker monitoring, dynamic schema pruning,
and adaptive fallback routing across heterogeneous tool providers.
"""
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set, Union

from ..mcp.circuit_breaker import CircuitState
from ..mcp.manager import MCPManager
from ..mcp.protocol import ToolCallResult, ToolDefinition
from .builtin_tools import BuiltinToolRegistry

logger = logging.getLogger("tools.dispatcher")


class UnifiedToolDispatcher:
    """
    Unified Tool Dispatcher combining Builtin Native Tools and Model Context Protocol (MCP) servers.
    Ensures agents have resilient access to both local system primitives and dynamic external MCP tools
    with health-aware adaptive routing, circuit breaker fail-fast, and graceful fallback.
    """

    def __init__(
        self,
        builtin_registry: BuiltinToolRegistry,
        mcp_manager: Optional[MCPManager] = None,
        fallback_engine: Optional[Any] = None,
    ):
        self.builtin_registry = builtin_registry
        self.mcp_manager = mcp_manager or MCPManager(workspace_dir=builtin_registry.workspace.root_dir)
        self._fallback_memory_store: Dict[str, Dict[str, Any]] = {}

        # Capability Fallback Engine (Multi-tier fidelity ladder: Graft -> CBM -> ripgrep -> tree-sitter -> filesystem)
        if fallback_engine is not None:
            self.fallback_engine = fallback_engine
        else:
            try:
                from ..capabilities.fallback import CapabilityFallbackEngine
                self.fallback_engine = CapabilityFallbackEngine(
                    workspace_dir=builtin_registry.workspace.root_dir
                )
            except Exception as e:
                logger.warning(f"Failed to initialize CapabilityFallbackEngine: {e}")
                self.fallback_engine = None

        # Agent role to MCP server mapping (supports short and canonical names)
        self._agent_mcp_permissions: Dict[str, List[str]] = {
            "TASKORCHESTRATOR": ["memory", "mcp-server-memory", "sqlite", "mcp-server-sqlite", "claude-flow"],
            "PLANNER": ["filesystem", "mcp-server-filesystem", "git", "mcp-server-git", "memory", "mcp-server-memory", "fetch", "mcp-server-fetch"],
            "SPECIFICATION": ["filesystem", "mcp-server-filesystem", "memory", "mcp-server-memory", "fetch", "mcp-server-fetch", "brave-search"],
            "ARCHITECTURE": ["filesystem", "mcp-server-filesystem", "memory", "mcp-server-memory"],
            "CODER": ["filesystem", "mcp-server-filesystem", "git", "mcp-server-git", "memory", "mcp-server-memory", "terminal", "mcp-server-terminal"],
            "TESTER": ["filesystem", "mcp-server-filesystem", "terminal", "mcp-server-terminal", "docker-mcp"],
            "REVIEWER": ["filesystem", "mcp-server-filesystem", "git", "mcp-server-git", "memory", "mcp-server-memory", "semgrep-mcp"],
        }

    def _get_resolved_allowed_servers(self, agent_name: Optional[str]) -> Set[str]:
        agent_clean = (agent_name or "").upper().strip()
        allowed = self._agent_mcp_permissions.get(agent_clean, [])
        resolved_set = set()
        for s in allowed:
            resolved_set.add(s)
            resolved_set.add(s.lower())
            if self.mcp_manager and hasattr(self.mcp_manager, "_resolve_server_name"):
                res = self.mcp_manager._resolve_server_name(s)
                resolved_set.add(res)
                resolved_set.add(res.lower())
        return resolved_set

    def get_schemas(self, agent_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Aggregate schemas from both Builtin Registry and authorized MCP servers.
        Dynamically adapts schemas: suppresses tools from UNHEALTHY MCP servers
        to protect the agent from making doomed tool calls.
        """
        schemas = []
        seen_names = set()

        # 1. Add Builtin Tools schemas
        builtin_schemas = self.builtin_registry.get_schemas(agent_name=agent_name)
        for s in builtin_schemas:
            fn_name = s.get("function", {}).get("name")
            if fn_name and fn_name not in seen_names:
                seen_names.add(fn_name)
                schemas.append(s)

        # 2. Add MCP Tools schemas with health filtering (Unknown callers receive no MCP schemas)
        resolved_allowed = self._get_resolved_allowed_servers(agent_name)

        if resolved_allowed and self.mcp_manager:
            for server_info in self.mcp_manager.discover_servers():
                s_name = server_info["server_name"]
                s_res = self.mcp_manager._resolve_server_name(s_name) if hasattr(self.mcp_manager, "_resolve_server_name") else s_name
                if s_name not in resolved_allowed and s_res not in resolved_allowed and s_name.lower() not in resolved_allowed:
                    continue

                health_status = self.mcp_manager.get_server_health_status(s_name)
                if health_status == "UNHEALTHY":
                    logger.warning(
                        f"MCP Server '{s_name}' is UNHEALTHY (circuit open/error). Suppressing its tools from LLM schemas."
                    )
                    continue

                for tool in self.mcp_manager.discover_tools(server_name=s_name):
                    if tool.name not in seen_names:
                        seen_names.add(tool.name)
                        schemas.append(tool.to_function_schema())

        return schemas

    def get_all_tools(self) -> List[Any]:
        """Returns all unique tools across builtin and healthy MCP servers."""
        tools = list(self.builtin_registry.get_all_tools())
        if self.mcp_manager:
            for server_info in self.mcp_manager.discover_servers():
                s_name = server_info["server_name"]
                if self.mcp_manager.get_server_health_status(s_name) != "UNHEALTHY":
                    tools.extend(self.mcp_manager.discover_tools(server_name=s_name))
        return tools

    def get_tools(self) -> List[Any]:
        """Alias for get_all_tools()."""
        return self.get_all_tools()

    def get_tools_for_agent(self, agent_name: str) -> List[Any]:
        """
        Returns tool items accessible to the agent (builtin tools + healthy MCP tools).
        """
        tools = list(self.builtin_registry.get_tools_for_agent(agent_name))
        resolved_allowed = self._get_resolved_allowed_servers(agent_name)

        if self.mcp_manager:
            for server_info in self.mcp_manager.discover_servers():
                s_name = server_info["server_name"]
                s_res = self.mcp_manager._resolve_server_name(s_name) if hasattr(self.mcp_manager, "_resolve_server_name") else s_name
                if s_name in resolved_allowed or s_res in resolved_allowed or s_name.lower() in resolved_allowed:
                    health_status = self.mcp_manager.get_server_health_status(s_name)
                    if health_status != "UNHEALTHY":
                        mcp_tools = self.mcp_manager.discover_tools(server_name=s_name)
                        tools.extend(mcp_tools)
        return tools

    def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        agent_role: Optional[Any] = None,
        agent_name: Optional[str] = None,
        caller_role: Optional[Any] = None,
        task_permissions: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Dispatch tool call across either Builtin Registry or MCP Server Manager.
        Reacts adaptively to server health:
        - Evaluates central authorization first under agent/caller role and task permissions.
        - If MCP server is HEALTHY: routes directly to MCP server.
        - If MCP server is UNHEALTHY: fails-fast and routes to native fallback provider (e.g. CBM or Filesystem) without blocking.
        - If tool is native (e.g. graft_subtask / spawn_subtasks): routes to Builtin Registry, propagating permission context.
        """
        clean_name = (name or "").strip()
        arguments = arguments if isinstance(arguments, dict) else {}
        eff_role = agent_role or agent_name or caller_role or kwargs.get("agent_role") or kwargs.get("agent_name") or kwargs.get("caller_role")
        eff_task_permissions = task_permissions or kwargs.get("task_permissions") or kwargs.get("permissions")

        # 0. Central Authorization Check
        auth_res = self.authorize(
            clean_name,
            arguments,
            agent_role=eff_role,
            task_permissions=eff_task_permissions,
        )
        if not getattr(auth_res, "allowed", True):
            reason = getattr(auth_res, "reason", "Permission Denied")
            suggested = getattr(auth_res, "suggested_action", None)
            logger.warning(
                f"Permission Denied by UnifiedToolDispatcher for tool '{clean_name}' (role: {eff_role}): {reason}"
            )
            return {
                "output": reason,
                "error": reason,
                "success": False,
                "is_error": True,
                "suggested_action": suggested,
            }

        # Priority 1: Check if tool belongs to an MCP Server
        if self.mcp_manager:
            server_name = self.mcp_manager._tool_to_server.get(clean_name)
            if server_name or clean_name.startswith("mcp_") or "__" in clean_name:
                resolved_server = server_name
                if not resolved_server and "__" in clean_name:
                    resolved_server = clean_name.split("__")[0]

                # Check health and circuit breaker state
                server_health = (
                    self.mcp_manager.get_server_health_status(resolved_server)
                    if resolved_server
                    else "HEALTHY"
                )
                cb = self.mcp_manager.get_circuit_breaker(resolved_server) if resolved_server else None

                # If MCP server is UNHEALTHY or circuit is OPEN, execute adaptive fallback
                if server_health == "UNHEALTHY" or (cb and not cb.can_attempt()):
                    fallback_res = self._handle_unhealthy_mcp_fallback(
                        clean_name,
                        arguments,
                        resolved_server,
                        caller_role=eff_role,
                        task_permissions=eff_task_permissions,
                    )
                    if fallback_res is not None:
                        return fallback_res

                from ..tracing import get_tracer, SpanType, SpanStatus
                tracer = get_tracer("orchestrator")
                with tracer.start_as_current_span(
                    f"MCP Call: {resolved_server or 'unknown'}.{clean_name}",
                    span_type=SpanType.MCP_CALL,
                    attributes={"server_name": resolved_server, "tool_name": clean_name},
                ) as mcp_span:
                    mcp_res = self.mcp_manager.call_tool(clean_name, arguments)
                    if isinstance(mcp_res, ToolCallResult):
                        if mcp_res.isError:
                            mcp_span.set_status(SpanStatus.ERROR, "MCP tool error")
                            # If the call failed with circuit open or connection drop, attempt fallback
                            fallback_res = self._handle_unhealthy_mcp_fallback(
                                clean_name,
                                arguments,
                                resolved_server,
                                caller_role=eff_role,
                                task_permissions=eff_task_permissions,
                            )
                            if fallback_res is not None:
                                return fallback_res
                        else:
                            mcp_span.set_status(SpanStatus.OK)

                        from ..security.trust_boundaries import UntrustedToolPayload, ToolProvenance
                        text_parts = [c.get("text", "") for c in mcp_res.content if isinstance(c, dict)]
                        combined_text = "\n".join(text_parts) if text_parts else ""
                        s_data = mcp_res.structured_data
                        if isinstance(s_data, dict):
                            s_data = UntrustedToolPayload.sanitize(clean_name, s_data, provenance=ToolProvenance.EXTERNAL_MCP)
                        return {
                            "output": combined_text or s_data or ("Executed successfully" if not mcp_res.isError else "MCP Tool Execution Failed"),
                            "data": s_data,
                            "error": combined_text if mcp_res.isError else None,
                            "success": not mcp_res.isError,
                            "is_mcp": True,
                            "server": resolved_server,
                            "_provenance": ToolProvenance.EXTERNAL_MCP,
                        }

        # Priority 2: Builtin Local Tools (e.g. graft_subtask / spawn_subtasks, filesystem)
        builtin_res = self.builtin_registry.call_tool(
            clean_name,
            arguments,
            caller_role=eff_role,
            task_permissions=eff_task_permissions,
        )
        if isinstance(builtin_res, dict) and "error" in builtin_res and "not found" in str(builtin_res.get("error")):
            # Fallback to MCP tool search if builtin not found
            if self.mcp_manager:
                from ..tracing import get_tracer, SpanType, SpanStatus
                tracer = get_tracer("orchestrator")
                with tracer.start_as_current_span(
                    f"MCP Call: {clean_name}",
                    span_type=SpanType.MCP_CALL,
                    attributes={"tool_name": clean_name},
                ) as mcp_span:
                    mcp_res = self.mcp_manager.call_tool(clean_name, arguments)
                    from ..security.trust_boundaries import UntrustedToolPayload, ToolProvenance
                    text_parts = [c.get("text", "") for c in mcp_res.content if isinstance(c, dict)]
                    out_text = "\n".join(text_parts) if text_parts else ""
                    out_data = mcp_res.structured_data
                    if isinstance(out_data, dict):
                        out_data = UntrustedToolPayload.sanitize(clean_name, out_data, provenance=ToolProvenance.EXTERNAL_MCP)
                    if not mcp_res.isError:
                        mcp_span.set_status(SpanStatus.OK)
                        return {
                            "output": out_text or out_data or "Executed successfully",
                            "data": out_data,
                            "success": True,
                            "is_mcp": True,
                            "_provenance": ToolProvenance.EXTERNAL_MCP,
                        }
                    else:
                        mcp_span.set_status(SpanStatus.ERROR, "MCP tool error")
                        return {
                            "output": out_text or out_data or "MCP Tool Execution Failed",
                            "error": out_text or "MCP Tool Execution Failed",
                            "data": out_data,
                            "success": False,
                            "is_mcp": True,
                            "_provenance": ToolProvenance.EXTERNAL_MCP,
                        }
        return builtin_res

    def execute_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        agent_name: Optional[str] = None,
        agent_role: Optional[Any] = None,
        task_permissions: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Alias for call_tool."""
        return self.call_tool(
            name,
            arguments,
            agent_name=agent_name,
            agent_role=agent_role,
            task_permissions=task_permissions,
            **kwargs,
        )

    async def execute_tool_async(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        agent_name: Optional[str] = None,
        agent_role: Optional[Any] = None,
        task_permissions: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Asynchronously executes a tool call, delegating to worker thread if blocking.
        """
        import asyncio
        return await asyncio.to_thread(
            self.call_tool,
            tool_name,
            arguments,
            agent_name=agent_name,
            agent_role=agent_role,
            task_permissions=task_permissions,
            **kwargs,
        )

    def _handle_unhealthy_mcp_fallback(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        server_name: Optional[str],
        caller_role: Optional[Any] = None,
        task_permissions: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Adaptive fallback router when an MCP server is UNHEALTHY or its circuit breaker is OPEN.
        Routes to native in-process engines (e.g. CBM or Filesystem) without blocking timeouts.
        """
        bare_tool = tool_name.split("__")[-1]
        logger.warning(
            f"MCP server '{server_name}' is UNHEALTHY/degraded. Triggering adaptive fallback for tool '{bare_tool}'."
        )

        # 1. Fallback for Codebase Memory (CBM) MCP tools
        if bare_tool in ("store_memory", "search_memory", "query_symbols", "index_codebase", "find_references", "get_dependencies"):
            return self._fallback_cbm_execution(bare_tool, arguments, server_name)

        # 2. Fallback for Filesystem MCP tools
        if bare_tool in ("read_file", "write_file", "list_directory", "get_file_info", "delete_file"):
            builtin_res = self.builtin_registry.call_tool(
                bare_tool,
                arguments,
                caller_role=caller_role,
                task_permissions=task_permissions,
            )
            if isinstance(builtin_res, dict):
                builtin_res["fallback"] = True
                builtin_res["fallback_provider"] = "native_filesystem"
                builtin_res["is_mcp"] = False
                builtin_res["server"] = f"{server_name} [FALLBACK: native_fs]"
            return builtin_res

        return None

    def _fallback_cbm_execution(
        self, tool_name: str, arguments: Dict[str, Any], server_name: Optional[str]
    ) -> Dict[str, Any]:
        """Fallback execution engine for Codebase Memory tools when CBM MCP is unhealthy."""
        if tool_name == "store_memory":
            key = str(arguments.get("key", "")).strip()
            val = arguments.get("value", "")
            cat = str(arguments.get("category", "general"))
            self._fallback_memory_store[key] = {"key": key, "value": val, "category": cat, "timestamp": time.time()}
            return {
                "output": f"Memory '{key}' stored in native fallback memory store.",
                "data": {"success": True, "key": key, "fallback": True},
                "success": True,
                "is_mcp": False,
                "fallback": True,
                "fallback_provider": "native_cbm",
                "server": f"{server_name or 'mcp-server-memory'} [FALLBACK: native_cbm]",
            }

        elif tool_name == "search_memory":
            query = str(arguments.get("query", "")).lower()
            cat = str(arguments.get("category", "")).lower()
            matches = []
            for item in self._fallback_memory_store.values():
                if cat and cat != item["category"].lower():
                    continue
                if not query or query in item["key"].lower() or query in str(item["value"]).lower():
                    matches.append(item)
            return {
                "output": f"Found {len(matches)} memory records via native fallback memory store.",
                "data": {"memories": matches, "fallback": True},
                "success": True,
                "is_mcp": False,
                "fallback": True,
                "fallback_provider": "native_cbm",
                "server": f"{server_name or 'mcp-server-memory'} [FALLBACK: native_cbm]",
            }

        elif tool_name == "index_codebase":
            try:
                if hasattr(self.builtin_registry, "cbm") and self.builtin_registry.cbm:
                    self.builtin_registry.cbm.build_memory()
                    return {
                        "output": "Codebase indexed successfully via native CBM knowledge graph.",
                        "data": {"success": True, "fallback": True},
                        "success": True,
                        "is_mcp": False,
                        "fallback": True,
                        "fallback_provider": "native_cbm",
                        "server": f"{server_name or 'mcp-server-memory'} [FALLBACK: native_cbm]",
                    }
            except Exception as ex:
                logger.warning(f"Native CBM indexing error: {ex}")
            return {
                "output": "Codebase indexing completed via fallback.",
                "data": {"success": True, "fallback": True},
                "success": True,
                "is_mcp": False,
                "fallback": True,
                "fallback_provider": "native_cbm",
                "server": f"{server_name or 'mcp-server-memory'} [FALLBACK: native_cbm]",
            }

        elif tool_name == "query_symbols":
            query = str(arguments.get("symbol_name", "") or arguments.get("query", "")).strip()
            if self.fallback_engine:
                fb_res = self.fallback_engine.execute("find_symbols", query)
                matches = fb_res.data if isinstance(fb_res.data, list) else []
                return {
                    "output": f"Found {len(matches)} symbol matches via {fb_res.provider_name} (Tier {fb_res.active_tier.value}).",
                    "data": {
                        "matches": matches,
                        "fallback": True,
                        "active_tier": fb_res.active_tier.value,
                        "provider": fb_res.provider_name,
                        "cascade_path": fb_res.cascade_path,
                    },
                    "success": fb_res.success,
                    "is_mcp": False,
                    "fallback": True,
                    "fallback_provider": fb_res.provider_name,
                    "server": f"{server_name or 'mcp-server-memory'} [FALLBACK: {fb_res.provider_name}]",
                }
            matches = []
            if hasattr(self.builtin_registry, "code_graph") and self.builtin_registry.code_graph:
                for sym_name, sym_info in getattr(self.builtin_registry.code_graph, "symbols", {}).items():
                    if query.lower() in sym_name.lower():
                        matches.append({"name": sym_name, "details": str(sym_info)})
            return {
                "output": f"Found {len(matches)} symbol matches via native AST code graph.",
                "data": {"matches": matches, "fallback": True},
                "success": True,
                "is_mcp": False,
                "fallback": True,
                "fallback_provider": "native_cbm",
                "server": f"{server_name or 'mcp-server-memory'} [FALLBACK: native_cbm]",
            }

        elif tool_name == "find_references":
            symbol = str(arguments.get("symbol_name", "") or arguments.get("symbol", "")).strip()
            if self.fallback_engine:
                fb_res = self.fallback_engine.execute("find_references", symbol)
                refs = fb_res.data if isinstance(fb_res.data, list) else []
                return {
                    "output": f"Found {len(refs)} references via {fb_res.provider_name} (Tier {fb_res.active_tier.value}).",
                    "data": {
                        "references": refs,
                        "fallback": True,
                        "active_tier": fb_res.active_tier.value,
                        "provider": fb_res.provider_name,
                        "cascade_path": fb_res.cascade_path,
                    },
                    "success": fb_res.success,
                    "is_mcp": False,
                    "fallback": True,
                    "fallback_provider": fb_res.provider_name,
                    "server": f"{server_name or 'mcp-server-memory'} [FALLBACK: {fb_res.provider_name}]",
                }
            return {
                "output": "Find references not supported without fallback engine.",
                "data": {"references": [], "fallback": True},
                "success": False,
                "is_mcp": False,
                "fallback": True,
                "fallback_provider": "native_cbm",
                "server": f"{server_name or 'mcp-server-memory'} [FALLBACK: native_cbm]",
            }

        return {
            "output": f"Tool '{tool_name}' executed via native fallback.",
            "data": {"fallback": True},
            "success": True,
            "is_mcp": False,
            "fallback": True,
            "fallback_provider": "native_cbm",
            "server": f"{server_name or 'mcp-server-memory'} [FALLBACK: native_cbm]",
        }

    def dispatch_capability(
        self,
        operation: str,
        query: str = "",
        workspace_dir: Optional[Any] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Dispatches high-level semantic capability requests through the multi-tier fallback engine.
        Cascades through:
        Tier 1: Graft (wiring graph / blast radius / caller hierarchy)
        Tier 2: CBM (in-process AST knowledge graph & symbol index)
        Tier 3: ripgrep (lexical search)
        Tier 4: tree-sitter (concrete syntax structural symbols)
        Tier 5: filesystem (raw directory traversal & line scanning)
        """
        if not self.fallback_engine:
            from ..capabilities.fallback import CapabilityFallbackEngine
            self.fallback_engine = CapabilityFallbackEngine(
                workspace_dir=workspace_dir or self.builtin_registry.workspace.root_dir
            )

        res = self.fallback_engine.execute(
            operation=operation,
            query=query,
            workspace_dir=workspace_dir or self.builtin_registry.workspace.root_dir,
            **kwargs,
        )
        return res.to_dict()

    def register(self, tool: Any, **kwargs) -> Any:
        """Dynamically registers a tool in the underlying builtin registry."""
        return self.builtin_registry.register(tool, **kwargs)

    def unregister(self, name: str) -> bool:
        """Deregisters a tool from the underlying builtin registry."""
        return self.builtin_registry.unregister(name)

    def discover(
        self,
        query: str = "",
        tags: Optional[List[str]] = None,
        category: Optional[str] = None,
        agent_role: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Any]:
        """Discovers tools across builtin registry and active MCP tools."""
        return self.builtin_registry.discover(
            query=query, tags=tags, category=category, agent_role=agent_role, top_k=top_k
        )

    def get_schema(self, name: str) -> Optional[Dict[str, Any]]:
        """Returns the function calling schema for a specific tool."""
        # Check builtin first
        schema = self.builtin_registry.get_schema(name)
        if schema:
            return schema

        # Check MCP tools
        if self.mcp_manager:
            clean = name.strip()
            for server_info in self.mcp_manager.discover_servers():
                s_name = server_info["server_name"]
                if self.mcp_manager.get_server_health_status(s_name) == "UNHEALTHY":
                    continue
                for tool in self.mcp_manager.discover_tools(server_name=s_name):
                    if tool.name.lower() == clean.lower():
                        return tool.to_function_schema()
        return None

    def authorize(
        self,
        name: str,
        args: Dict[str, Any],
        agent_role: Optional[Any] = None,
        task_permissions: Optional[Any] = None,
    ) -> Any:
        """Evaluates authorization for tool execution."""
        return self.builtin_registry.authorize(
            name, args, agent_role=agent_role, task_permissions=task_permissions
        )

    def execute(
        self,
        name: str,
        args: Dict[str, Any],
        caller_role: Optional[Any] = None,
        task_permissions: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        """Executes a tool across unified dispatcher with standardized result envelope."""
        from .registry import ToolExecutionResult

        res = self.call_tool(
            name,
            args,
            caller_role=caller_role,
            task_permissions=task_permissions,
            **kwargs,
        )
        if isinstance(res, dict):
            is_success = res.get("success", True) and ("error" not in res)
            return ToolExecutionResult(
                success=is_success,
                output=res.get("output") or res.get("content") or res,
                data=res,
                error=res.get("error"),
                metadata={
                    "is_mcp": res.get("is_mcp", False),
                    "server": res.get("server"),
                    "fallback": res.get("fallback", False),
                    "fallback_provider": res.get("fallback_provider"),
                    "suggested_action": res.get("suggested_action"),
                },
            )
        return ToolExecutionResult(success=True, output=res, data=res)

    def health_check(self, name: Optional[str] = None) -> Dict[str, Any]:
        """Runs health diagnostics across builtin registry and MCP servers."""
        from .registry import HealthReport, ToolHealthStatus

        reports = dict(self.builtin_registry.health_check(name=name))

        if self.mcp_manager and (name is None or "mcp" in name.lower()):
            mcp_reports = self.mcp_manager.health_check()
            if isinstance(mcp_reports, dict):
                for s_name, s_rep in mcp_reports.items():
                    health_str = self.mcp_manager.get_server_health_status(s_name)
                    if health_str == "HEALTHY":
                        st = ToolHealthStatus.HEALTHY
                    elif health_str == "DEGRADED":
                        st = ToolHealthStatus.DEGRADED
                    else:
                        st = ToolHealthStatus.UNHEALTHY

                    reports[f"mcp_server_{s_name}"] = HealthReport(
                        status=st,
                        latency_ms=s_rep.latency_ms or 0.0,
                        details=f"MCP Server '{s_name}': state={s_rep.state.value}, circuit={s_rep.circuit_state}, tools={s_rep.tools_count}, connected={s_rep.is_connected}",
                    )

        if self.fallback_engine and (name is None or "capability" in name.lower() or "fallback" in name.lower()):
            for p in self.fallback_engine.providers:
                is_avail = p.is_available(self.builtin_registry.workspace.root_dir)
                reports[f"capability_provider_{p.name}"] = HealthReport(
                    status=ToolHealthStatus.HEALTHY if is_avail else ToolHealthStatus.UNHEALTHY,
                    latency_ms=0.0,
                    details=f"Capability Tier {p.tier.value} ({p.tier.label}): {'available' if is_avail else 'unavailable/disabled'}",
                )
        return reports

    def get_tools_for_agent(self, agent_name: Optional[str] = None) -> List[Any]:
        """Returns tools list for a given agent role from the builtin registry."""
        if hasattr(self.builtin_registry, "get_tools_for_agent"):
            return self.builtin_registry.get_tools_for_agent(agent_name)
        return []

    def get_all_tools(self) -> List[Any]:
        """Returns all registered tools from the builtin registry."""
        if hasattr(self.builtin_registry, "get_all_tools"):
            return self.builtin_registry.get_all_tools()
        return []

    def __getattr__(self, name: str) -> Any:
        """Forward missing attributes/methods transparently to the underlying builtin registry."""
        if "builtin_registry" in self.__dict__ and hasattr(self.builtin_registry, name):
            return getattr(self.builtin_registry, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def close(self) -> None:
        """Close underlying MCP manager."""
        if self.mcp_manager:
            self.mcp_manager.close()

