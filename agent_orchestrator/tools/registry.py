"""
Core Tool Registry Engine: Centralized Registration, Discovery, Schema Generation,
Unified Authorization, Pipeline Execution, and Active Health Checking.
"""
from dataclasses import dataclass, field
from enum import Enum
import inspect
import json
import math
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from ..runtime.permission_policy import (
    PolicyEvaluationResult,
    ToolOperationType,
    ToolPermissionPolicyEngine,
)


class ToolHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


@dataclass
class HealthReport:
    """Diagnostic health report for a tool."""
    status: ToolHealthStatus
    latency_ms: float = 0.0
    details: str = ""
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 2),
            "details": self.details,
            "checked_at": self.checked_at,
        }


@dataclass
class ToolExecutionResult:
    """Standardized result envelope for tool executions."""
    success: bool
    output: Any = None
    data: Any = None
    duration_ms: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    provenance: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "data": self.data,
            "duration_ms": round(self.duration_ms, 2),
            "error": self.error,
            "metadata": self.metadata,
            "provenance": str(self.provenance.value if hasattr(self.provenance, "value") else self.provenance) if self.provenance else None,
        }


@dataclass
class ToolEntry:
    """
    Metadata and callable envelope for a tool registered in the registry.
    """
    name: str
    description: str = ""
    tool_instance: Any = None
    args_schema: Optional[Any] = None
    operation_type: ToolOperationType = ToolOperationType.READ
    tags: List[str] = field(default_factory=list)
    category: str = "general"
    health_check_fn: Optional[Callable[[], Union[bool, Tuple[bool, str], HealthReport]]] = None
    timeout_seconds: float = 30.0
    provenance: Optional[Any] = None

    def get_schema(self) -> Dict[str, Any]:
        """Generates OpenAI function calling schema for this tool."""
        properties: Dict[str, Any] = {}
        required: List[str] = []

        if self.args_schema is not None:
            if hasattr(self.args_schema, "model_json_schema"):
                s = self.args_schema.model_json_schema()
                properties = s.get("properties", {})
                required = s.get("required", [])
            elif hasattr(self.args_schema, "schema"):
                s = self.args_schema.schema()
                properties = s.get("properties", {})
                required = s.get("required", [])
            elif isinstance(self.args_schema, dict):
                properties = self.args_schema.get("properties", {})
                required = self.args_schema.get("required", [])

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


class ToolRegistry:
    """
    Comprehensive Tool Registry managing the entire lifecycle:
    - register() / unregister()
    - discover()
    - get_schema() / get_schemas()
    - authorize()
    - execute()
    - health_check()
    """

    def __init__(self, workspace: Optional[Any] = None, **kwargs: Any):
        self.workspace = workspace
        self._tools: Dict[str, ToolEntry] = {}
        self._aliases: Dict[str, str] = {}
        self._idf: Dict[str, float] = {}
        self._vector_index: Dict[str, Dict[str, float]] = {}

    def register(
        self,
        tool: Any,
        name: Optional[str] = None,
        description: Optional[str] = None,
        args_schema: Optional[Any] = None,
        operation_type: Optional[ToolOperationType] = None,
        tags: Optional[List[str]] = None,
        category: str = "general",
        health_check_fn: Optional[Callable[[], Union[bool, Tuple[bool, str], HealthReport]]] = None,
        timeout_seconds: float = 30.0,
        aliases: Optional[List[str]] = None,
        provenance: Optional[Any] = None,
    ) -> ToolEntry:
        """
        Registers a tool into the registry. Supports LangChain StructuredTool,
        callable functions, or explicit ToolEntry objects.
        """
        if isinstance(tool, ToolEntry):
            entry = tool
            if provenance is not None and entry.provenance is None:
                entry.provenance = provenance
            t_name = entry.name
        else:
            t_name = name or getattr(tool, "name", None)
            if not t_name:
                if callable(tool):
                    t_name = tool.__name__
                else:
                    raise ValueError("Tool name must be provided or accessible via tool.name")

            t_desc = description or getattr(tool, "description", None) or (tool.__doc__ or "").strip()
            t_schema = args_schema or getattr(tool, "args_schema", None)
            t_op = operation_type or ToolPermissionPolicyEngine.get_tool_operation_type(t_name)
            t_tags = list(tags or [])

            entry = ToolEntry(
                name=t_name,
                description=t_desc,
                tool_instance=tool,
                args_schema=t_schema,
                operation_type=t_op,
                tags=t_tags,
                category=category,
                health_check_fn=health_check_fn,
                timeout_seconds=timeout_seconds,
                provenance=provenance,
            )

        norm_key = entry.name.lower().strip()
        self._tools[norm_key] = entry

        if aliases:
            for alias in aliases:
                self._aliases[alias.lower().strip()] = norm_key

        self._rebuild_search_index()
        return entry

    def unregister(self, name: str) -> bool:
        """
        Deregisters a tool from the registry.
        """
        norm_key = name.lower().strip()
        if norm_key in self._aliases:
            norm_key = self._aliases.pop(norm_key)

        if norm_key in self._tools:
            del self._tools[norm_key]
            # Clean any dangling aliases pointing to this key
            self._aliases = {k: v for k, v in self._aliases.items() if v != norm_key}
            self._rebuild_search_index()
            return True
        return False

    def get(self, name: str) -> Optional[ToolEntry]:
        """Looks up a tool by exact name, registered alias, or stripped prefix."""
        if not name:
            return None
        norm_key = name.lower().strip()
        if norm_key in self._aliases:
            norm_key = self._aliases[norm_key]
        if norm_key in self._tools:
            return self._tools[norm_key]

        # Strip prefixes (e.g. mcp_, builtin__, native_, <server>__)
        bare = norm_key
        if "__" in bare:
            bare = bare.split("__")[-1]
        prefixes = ("mcp_", "builtin_", "native_", "mcp-server-filesystem_", "mcp-server-git_", "mcp-server-terminal_", "mcp-server-memory_", "filesystem_", "git_", "terminal_", "memory_")
        changed = True
        while changed:
            changed = False
            if bare in self._tools or bare in self._aliases:
                break
            for pfx in prefixes:
                if bare.startswith(pfx) and len(bare) > len(pfx) and bare not in self._tools and bare not in self._aliases:
                    bare = bare[len(pfx):]
                    changed = True
                    break

        if bare in self._aliases:
            bare = self._aliases[bare]
        return self._tools.get(bare)

    def list_tools(self, category: Optional[str] = None) -> List[ToolEntry]:
        """Returns all registered tool entries, optionally filtered by category."""
        if not category:
            return list(self._tools.values())
        norm_cat = category.lower().strip()
        return [t for t in self._tools.values() if t.category.lower() == norm_cat]

    def _tokenize(self, text: str) -> List[str]:
        return [tok for tok in re.findall(r"[a-zA-Z0-9_\-\.]+", text.lower()) if len(tok) > 1]

    def _rebuild_search_index(self):
        """Rebuilds TF-IDF vector index for semantic tool discovery."""
        num_docs = len(self._tools)
        if num_docs == 0:
            self._idf.clear()
            self._vector_index.clear()
            return

        doc_freqs: Dict[str, int] = {}
        doc_tokens_map: Dict[str, List[str]] = {}

        for key, entry in self._tools.items():
            corpus_parts = [
                entry.name,
                entry.description,
                entry.category,
                " ".join(entry.tags),
            ]
            tokens = self._tokenize(" ".join(corpus_parts))
            doc_tokens_map[key] = tokens
            for tok in set(tokens):
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
        tags: Optional[List[str]] = None,
        category: Optional[str] = None,
        agent_role: Optional[str] = None,
        top_k: int = 5,
        threshold: float = 0.0,
    ) -> List[Tuple[ToolEntry, float]]:
        """
        Discovers relevant tools using TF-IDF similarity, tag bonuses, and category filters.
        """
        if not self._tools:
            return []

        q_tokens = self._tokenize(query) if query else []
        q_norm_vec: Dict[str, float] = {}
        if q_tokens:
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

        req_tags = {t.lower().strip() for t in (tags or [])}
        results: List[Tuple[ToolEntry, float]] = []

        for key, entry in self._tools.items():
            if category and entry.category.lower() != category.lower().strip():
                continue

            sim_score = 0.0
            if q_norm_vec:
                doc_vec = self._vector_index.get(key, {})
                sim_score = sum(q_norm_vec[tok] * doc_vec.get(tok, 0.0) for tok in q_norm_vec)

            tag_bonus = 0.0
            if req_tags:
                entry_tags = {t.lower().strip() for t in entry.tags}
                matched_tags = req_tags.intersection(entry_tags)
                tag_bonus = (len(matched_tags) / len(req_tags)) * 2.0

            name_bonus = 0.0
            if any(tok in entry.name.lower() for tok in q_tokens):
                name_bonus += 0.5

            total_score = sim_score + tag_bonus + name_bonus
            if total_score >= threshold or not query:
                results.append((entry, total_score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def get_schema(self, name: str) -> Optional[Dict[str, Any]]:
        """Returns the OpenAI function calling schema for a specific tool."""
        entry = self.get(name)
        if not entry:
            return None
        return entry.get_schema()

    def get_schemas(
        self,
        names: Optional[List[str]] = None,
        agent_role: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns schemas for specified tools, or all registered tools.
        """
        target_entries: List[ToolEntry] = []
        if names:
            seen = set()
            for n in names:
                entry = self.get(n)
                if entry and entry.name not in seen:
                    seen.add(entry.name)
                    target_entries.append(entry)
        else:
            seen = set()
            for entry in self._tools.values():
                if entry.name not in seen:
                    seen.add(entry.name)
                    target_entries.append(entry)

        return [e.get_schema() for e in target_entries]

    def authorize(
        self,
        name: str,
        args: Dict[str, Any],
        agent_role: Optional[Any] = None,
        task_permissions: Optional[Any] = None,
        workspace: Optional[Any] = None,
    ) -> PolicyEvaluationResult:
        """
        Validates whether an agent with `agent_role` is permitted to execute tool `name` with `args`.
        """
        entry = self.get(name)
        tool_name = entry.name if entry else name
        op_type = entry.operation_type if entry else None
        return ToolPermissionPolicyEngine.evaluate_tool_invocation(
            agent_role=agent_role,
            tool_name=tool_name,
            args=args,
            task_permissions=task_permissions,
            workspace=workspace,
            operation_type=op_type,
        )

    @staticmethod
    def normalize_arguments(tool_name: str, args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Normalizes parameter aliases across all registered tools to ensure robust LLM tool execution.
        """
        if not isinstance(args, dict):
            return {}
        call_args = dict(args)
        clean = (tool_name or "").lower().strip()
        bare = clean.split("__")[-1]
        if bare.startswith("mcp_"):
            bare = bare[4:]

        # Filepath aliasing
        if "filepath" not in call_args:
            for alt in ("file_path", "path", "rel_path", "filename", "file"):
                if alt in call_args:
                    call_args["filepath"] = call_args.pop(alt)
                    break

        # Content aliasing
        if "content" not in call_args:
            for alt in ("text", "data", "body", "new_content"):
                if alt in call_args:
                    call_args["content"] = call_args.pop(alt)
                    break

        # Tool-specific normalizations
        if bare in ("replace_file_content", "edit_file"):
            if "target_content" not in call_args:
                for alt in ("target", "old_content", "find", "search"):
                    if alt in call_args:
                        call_args["target_content"] = call_args.pop(alt)
                        break
            if "replacement_content" not in call_args:
                for alt in ("replacement", "new_content", "replace"):
                    if alt in call_args:
                        call_args["replacement_content"] = call_args.pop(alt)
                        break

        elif bare in ("apply_diff_blocks", "diff_blocks"):
            if "diff_blocks" not in call_args:
                for alt in ("diff", "patch", "blocks"):
                    if alt in call_args:
                        call_args["diff_blocks"] = call_args.pop(alt)
                        break

        elif bare in ("rename_file",):
            if "old_filepath" not in call_args:
                for alt in ("old_path", "source", "src", "from_path"):
                    if alt in call_args:
                        call_args["old_filepath"] = call_args.pop(alt)
                        break
            if "new_filepath" not in call_args:
                for alt in ("new_path", "target", "dest", "to_path"):
                    if alt in call_args:
                        call_args["new_filepath"] = call_args.pop(alt)
                        break

        elif bare in ("move_file",):
            if "source_filepath" not in call_args:
                for alt in ("source_path", "source", "src", "filepath"):
                    if alt in call_args:
                        call_args["source_filepath"] = call_args.pop(alt)
                        break
            if "target_dir" not in call_args:
                for alt in ("target_directory", "destination", "dest", "dir", "directory"):
                    if alt in call_args:
                        call_args["target_dir"] = call_args.pop(alt)
                        break

        elif bare in ("apply_patch",):
            if "patch_content" not in call_args:
                for alt in ("patch", "diff"):
                    if alt in call_args:
                        call_args["patch_content"] = call_args.pop(alt)
                        break

        elif bare in ("list_directory", "list_files"):
            if "path" not in call_args:
                for alt in ("filepath", "file_path", "dir", "directory", "folder", "rel_path"):
                    if alt in call_args:
                        call_args["path"] = call_args.pop(alt)
                        break

        elif bare in ("terminal_execute", "run_command", "bash"):
            if "command" not in call_args:
                for alt in ("cmd", "exec", "script"):
                    if alt in call_args:
                        call_args["command"] = call_args.pop(alt)
                        break

        elif bare in ("find_symbol", "find_references", "get_symbol_neighbors"):
            if "symbol_name" not in call_args:
                for alt in ("symbol", "query", "name"):
                    if alt in call_args:
                        call_args["symbol_name"] = call_args.pop(alt)
                        break

        elif bare in ("get_dependencies", "get_call_graph", "get_impact_radius"):
            if "target" not in call_args:
                for alt in ("symbol_name", "symbol", "filepath", "path", "query"):
                    if alt in call_args:
                        call_args["target"] = call_args.pop(alt)
                        break

        elif bare in ("get_architecture_slice",):
            if "target_file" not in call_args:
                for alt in ("filepath", "file_path", "path", "file"):
                    if alt in call_args:
                        call_args["target_file"] = call_args.pop(alt)
                        break

        elif bare in ("regex_grep", "grep_search"):
            if "pattern" not in call_args:
                for alt in ("query", "regex", "search"):
                    if alt in call_args:
                        call_args["pattern"] = call_args.pop(alt)
                        break

        elif bare in ("search_skills", "query_specification", "query_architecture", "query_codebase_graph"):
            if "query" not in call_args:
                for alt in ("q", "search", "keyword", "prompt"):
                    if alt in call_args:
                        call_args["query"] = call_args.pop(alt)
                        break

        elif bare in ("load_skill", "read_skill_reference", "execute_skill_script"):
            if "skill_name" not in call_args:
                for alt in ("name", "skill"):
                    if alt in call_args:
                        call_args["skill_name"] = call_args.pop(alt)
                        break

        elif bare in ("complete_task",):
            if "summary" not in call_args:
                for alt in ("result", "message", "output", "deliverables_summary"):
                    if alt in call_args:
                        call_args["summary"] = str(call_args.pop(alt))
                        break

        return call_args

    @staticmethod
    def _prune_and_coerce_args(entry: ToolEntry, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prunes extraneous LLM reasoning keys and coerces arguments based on schema / signature.
        """
        pruned = ToolRegistry.normalize_arguments(entry.name, args)
        known_fields: Optional[Set[str]] = None

        if entry.args_schema is not None:
            if hasattr(entry.args_schema, "model_fields"):
                known_fields = set(entry.args_schema.model_fields.keys())
            elif hasattr(entry.args_schema, "__fields__"):
                known_fields = set(entry.args_schema.__fields__.keys())
            elif isinstance(entry.args_schema, dict):
                known_fields = set(entry.args_schema.get("properties", {}).keys())

        tool_inst = entry.tool_instance
        fn = getattr(tool_inst, "func", None) if tool_inst else None
        if not fn and callable(tool_inst):
            fn = tool_inst

        if fn:
            try:
                sig = inspect.signature(fn)
                has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                if not has_var_keyword:
                    fn_params = set(sig.parameters.keys())
                    if known_fields is None:
                        known_fields = fn_params
                    else:
                        known_fields = known_fields.union(fn_params)
            except Exception:
                pass

        common_extraneous = {
            "thought", "thoughts", "reasoning", "_reasoning", "rationale",
            "_rationale", "explanation", "comment", "comments"
        }
        for k in common_extraneous:
            if known_fields is not None and k not in known_fields and k in pruned:
                pruned.pop(k, None)

        if known_fields is not None:
            pruned = {k: v for k, v in pruned.items() if k in known_fields}

        return pruned

    def execute(
        self,
        name: str,
        args: Dict[str, Any],
        caller_role: Optional[Any] = None,
        task_permissions: Optional[Any] = None,
        workspace: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolExecutionResult:
        """
        Executes a registered tool through the standardized pipeline:
        Authorization -> Normalization -> Idempotency -> Invocation -> Secret Redaction.
        """
        t_start = time.time()
        entry = self.get(name)
        if not entry:
            return ToolExecutionResult(
                success=False,
                error=f"Tool '{name}' not found.",
                duration_ms=0.0,
            )

        # 1. Normalize arguments
        call_args = self.normalize_arguments(entry.name, args)

        # 2. Authorization
        auth_res = self.authorize(
            name=entry.name,
            args=call_args,
            agent_role=caller_role,
            task_permissions=task_permissions,
            workspace=workspace,
        )
        if not auth_res.allowed:
            return ToolExecutionResult(
                success=False,
                error=auth_res.reason,
                metadata={"suggested_action": auth_res.suggested_action},
                duration_ms=round((time.time() - t_start) * 1000, 2),
            )

        # 3. Idempotency Check
        op_id = call_args.get("operation_id")
        if op_id:
            try:
                from ..runtime.idempotency import global_operation_ledger
                if global_operation_ledger.has_executed(op_id):
                    cached_record = global_operation_ledger.get(op_id)
                    if cached_record and cached_record.result is not None:
                        res_dict = dict(cached_record.result)
                        res_dict["cached"] = True
                        res_dict["already_applied"] = True
                        return ToolExecutionResult(
                            success=True,
                            output=res_dict.get("output", res_dict),
                            data=res_dict,
                            duration_ms=round((time.time() - t_start) * 1000, 2),
                            metadata={"cached": True},
                        )
            except Exception:
                pass

        # 4. Invocation
        tool_inst = entry.tool_instance
        raw_res = None
        try:
            if hasattr(tool_inst, "invoke"):
                try:
                    raw_res = tool_inst.invoke(call_args)
                except Exception as inner_ex:
                    pruned_args = self._prune_and_coerce_args(entry, call_args)
                    if pruned_args != call_args:
                        raw_res = tool_inst.invoke(pruned_args)
                    else:
                        raise inner_ex
            elif callable(tool_inst):
                try:
                    raw_res = tool_inst(**call_args)
                except TypeError as inner_ex:
                    pruned_args = self._prune_and_coerce_args(entry, call_args)
                    if pruned_args != call_args:
                        raw_res = tool_inst(**pruned_args)
                    else:
                        raise inner_ex
            else:
                return ToolExecutionResult(
                    success=False,
                    error=f"Tool '{entry.name}' instance is neither callable nor an object with .invoke()",
                    duration_ms=round((time.time() - t_start) * 1000, 2),
                )

            # Standardize output
            output_val = raw_res
            data_val = None
            is_success = True

            if isinstance(raw_res, dict):
                is_success = raw_res.get("success", True)
                err = raw_res.get("error")
                output_val = raw_res.get("output") or raw_res.get("content") or raw_res
                data_val = raw_res
            else:
                err = None

            # Secret redaction
            try:
                from ..security.secrets import secret_manager
                output_val = secret_manager.redact_structure(output_val)
                if data_val:
                    data_val = secret_manager.redact_structure(data_val)
            except Exception:
                pass

            # Untrusted tool payload sanitization
            try:
                from ..security.trust_boundaries import UntrustedToolPayload, ToolProvenance
                prov = entry.provenance or ToolProvenance.WORKSPACE_DATA
                if isinstance(data_val, dict):
                    data_val = UntrustedToolPayload.sanitize(entry.name, data_val, provenance=prov)
                if isinstance(output_val, dict):
                    output_val = UntrustedToolPayload.sanitize(entry.name, output_val, provenance=prov)
            except Exception:
                pass

            duration = (time.time() - t_start) * 1000.0
            return ToolExecutionResult(
                success=bool(is_success and not err),
                output=output_val,
                data=data_val,
                duration_ms=round(duration, 2),
                error=str(err) if err else None,
                provenance=entry.provenance,
            )
        except Exception as ex:
            duration = (time.time() - t_start) * 1000.0
            return ToolExecutionResult(
                success=False,
                output=None,
                duration_ms=round(duration, 2),
                error=str(ex),
                provenance=entry.provenance if 'entry' in locals() and entry else None,
            )

    def health_check(self, name: Optional[str] = None) -> Dict[str, HealthReport]:
        """
        Executes active liveness probes on registered tools.
        If `name` is given, inspects only that tool; otherwise checks all tools.
        """
        reports: Dict[str, HealthReport] = {}
        targets = [self.get(name)] if name else list(self._tools.values())

        for entry in targets:
            if not entry:
                if name:
                    reports[name] = HealthReport(
                        status=ToolHealthStatus.UNKNOWN,
                        details=f"Tool '{name}' is not registered",
                    )
                continue

            t_start = time.time()
            if entry.health_check_fn:
                try:
                    res = entry.health_check_fn()
                    lat = (time.time() - t_start) * 1000.0
                    if isinstance(res, HealthReport):
                        reports[entry.name] = res
                    elif isinstance(res, tuple) and len(res) == 2:
                        is_ok, details = res
                        reports[entry.name] = HealthReport(
                            status=ToolHealthStatus.HEALTHY if is_ok else ToolHealthStatus.UNHEALTHY,
                            latency_ms=lat,
                            details=str(details),
                        )
                    elif isinstance(res, bool):
                        reports[entry.name] = HealthReport(
                            status=ToolHealthStatus.HEALTHY if res else ToolHealthStatus.UNHEALTHY,
                            latency_ms=lat,
                            details="Health check passed" if res else "Health check failed",
                        )
                    else:
                        reports[entry.name] = HealthReport(
                            status=ToolHealthStatus.HEALTHY,
                            latency_ms=lat,
                            details=str(res),
                        )
                except Exception as ex:
                    lat = (time.time() - t_start) * 1000.0
                    reports[entry.name] = HealthReport(
                        status=ToolHealthStatus.UNHEALTHY,
                        latency_ms=lat,
                        details=f"Health probe exception: {str(ex)}",
                    )
            else:
                # Default sanity check: verify tool instance is present and responsive
                lat = (time.time() - t_start) * 1000.0
                if entry.tool_instance is not None:
                    reports[entry.name] = HealthReport(
                        status=ToolHealthStatus.HEALTHY,
                        latency_ms=lat,
                        details="Tool instance verified present and registered",
                    )
                else:
                    reports[entry.name] = HealthReport(
                        status=ToolHealthStatus.UNHEALTHY,
                        latency_ms=lat,
                        details="Tool instance is None",
                    )

        return reports
