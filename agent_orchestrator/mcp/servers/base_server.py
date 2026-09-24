"""
Base MCP Server Framework.
Allows rapid creation of fully-compliant Model Context Protocol servers.
"""
import inspect
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from ..protocol import (
    JSONRPCError,
    JSONRPCResponse,
    MCPErrorCode,
    ToolCallResult,
    ToolDefinition,
)

logger = logging.getLogger("mcp.server")


class BaseMCPServer:
    """
    Standard base class for MCP servers.
    Handles message dispatch, tool registrations, initialization protocol, and error formatting.
    """

    def __init__(self, name: str, version: str = "1.0.0", description: str = ""):
        self.name = name
        self.version = version
        self.description = description
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._resources: Dict[str, Dict[str, Any]] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: Callable[..., Any],
    ) -> None:
        """Register a tool with its JSON schema and execution handler."""
        self._tools[name] = {
            "definition": ToolDefinition(
                name=name,
                description=description,
                inputSchema=input_schema,
                server_name=self.name,
            ),
            "handler": handler,
        }

    def register_resource(
        self,
        uri: str,
        name: str,
        description: str,
        mime_type: str = "text/plain",
        reader: Optional[Callable[[], str]] = None,
    ) -> None:
        """Register an inspectable resource."""
        self._resources[uri] = {
            "uri": uri,
            "name": name,
            "description": description,
            "mimeType": mime_type,
            "reader": reader,
        }

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Convenience method to execute a registered tool handler directly."""
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found in server '{self.name}'.")
        handler = self._tools[name]["handler"]
        arguments = arguments or {}
        sig = inspect.signature(handler)
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if has_varkw:
            return handler(**arguments)
        filtered = {k: v for k, v in arguments.items() if k in sig.parameters}
        return handler(**filtered)


    def handle_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process incoming JSON-RPC messages and return a response if applicable."""
        if not isinstance(message, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": MCPErrorCode.INVALID_REQUEST, "message": "Message must be an object"},
            }

        req_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {}) or {}

        # Handle notifications (no id)
        if req_id is None:
            if method == "notifications/initialized":
                logger.debug(f"[{self.name}] Client initialized notification received.")
            elif method == "notifications/cancelled":
                logger.info(f"[{self.name}] Received request cancellation: {params.get('requestId')}")
            return None

        # Handle requests
        try:
            if method == "initialize":
                return self._handle_initialize(req_id, params)
            elif method == "ping":
                return {"jsonrpc": "2.0", "id": req_id, "result": {}}
            elif method == "tools/list":
                return self._handle_list_tools(req_id, params)
            elif method == "tools/call":
                return self._handle_call_tool(req_id, params)
            elif method == "resources/list":
                return self._handle_list_resources(req_id)
            elif method == "resources/read":
                return self._handle_read_resource(req_id, params)
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": MCPErrorCode.METHOD_NOT_FOUND, "message": f"Method '{method}' not found"},
                }
        except Exception as e:
            logger.exception(f"Error handling MCP method '{method}': {e}")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": MCPErrorCode.INTERNAL_ERROR, "message": str(e)},
            }

    def _handle_initialize(self, req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {"subscribe": False, "listChanged": True},
                },
                "serverInfo": {
                    "name": self.name,
                    "version": self.version,
                    "description": self.description,
                },
            },
        }

    def _handle_list_tools(self, req_id: Any, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        tools_list = []
        for t_info in self._tools.values():
            defn: ToolDefinition = t_info["definition"]
            tools_list.append({
                "name": defn.name,
                "description": defn.description,
                "inputSchema": defn.inputSchema,
            })
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": tools_list},
        }

    def _handle_call_tool(self, req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name not in self._tools:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Tool '{tool_name}' not found."}],
                    "isError": True,
                },
            }

        tool = self._tools[tool_name]
        handler = tool["handler"]

        try:
            call_args = dict(arguments) if isinstance(arguments, dict) else {}
            # Alias normalizations
            if "filepath" in call_args and "path" not in call_args:
                call_args["path"] = call_args["filepath"]
            elif "file_path" in call_args and "path" not in call_args:
                call_args["path"] = call_args["file_path"]
            elif "path" in call_args and "filepath" not in call_args:
                call_args["filepath"] = call_args["path"]

            if "text" in call_args and "content" not in call_args:
                call_args["content"] = call_args["text"]
            elif "content" in call_args and "text" not in call_args:
                call_args["text"] = call_args["content"]

            if "cmd" in call_args and "command" not in call_args:
                call_args["command"] = call_args["cmd"]

            if "symbol" in call_args and "symbol_name" not in call_args:
                call_args["symbol_name"] = call_args["symbol"]
            elif "query" in call_args and "symbol_name" not in call_args:
                call_args["symbol_name"] = call_args["query"]

            sig = inspect.signature(handler)
            param_names = list(sig.parameters.keys())
            if len(param_names) == 1 and (param_names[0] in ("args", "arguments", "payload") or next(iter(sig.parameters.values())).kind == inspect.Parameter.VAR_KEYWORD):
                res = handler(call_args)
            else:
                has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                if has_varkw:
                    res = handler(**call_args)
                else:
                    filtered_args = {k: v for k, v in call_args.items() if k in sig.parameters}
                    res = handler(**filtered_args)

            if isinstance(res, ToolCallResult):
                return {"jsonrpc": "2.0", "id": req_id, "result": res.to_dict()}
            elif isinstance(res, dict):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(res, indent=2)}],
                        "isError": False,
                        "data": res,
                    },
                }
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": str(res)}],
                        "isError": False,
                    },
                }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error executing tool '{tool_name}': {str(e)}"}],
                    "isError": True,
                },
            }

    def _handle_list_resources(self, req_id: Any) -> Dict[str, Any]:
        resources_list = []
        for r in self._resources.values():
            resources_list.append({
                "uri": r["uri"],
                "name": r["name"],
                "description": r["description"],
                "mimeType": r["mimeType"],
            })
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"resources": resources_list},
        }

    def _handle_read_resource(self, req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        uri = params.get("uri")
        if uri not in self._resources:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": MCPErrorCode.INVALID_PARAMS, "message": f"Resource '{uri}' not found."},
            }
        res_info = self._resources[uri]
        reader = res_info.get("reader")
        content_text = reader() if reader else ""
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": res_info["mimeType"],
                        "text": content_text,
                    }
                ]
            },
        }
