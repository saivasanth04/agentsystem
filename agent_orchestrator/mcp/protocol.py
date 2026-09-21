"""
Model Context Protocol (MCP) standard protocol specifications and schemas.
Adheres to JSON-RPC 2.0 and Model Context Protocol schema definitions.
"""
from dataclasses import dataclass, field
from enum import Enum, IntEnum
import time
from typing import Any, Dict, List, Optional, Union


class MCPServerState(str, Enum):
    """Lifecycle state machine for MCP servers."""
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class MCPErrorCode(IntEnum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    SERVER_NOT_INITIALIZED = -32002
    UNKNOWN_ERROR = -32001
    TOOL_EXECUTION_ERROR = -32000
    TIMEOUT = -32008


class MCPError(Exception):
    """Base exception for all MCP related failures."""
    def __init__(self, message: str, code: int = MCPErrorCode.INTERNAL_ERROR, data: Optional[Any] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {"code": int(self.code), "message": self.message}
        if self.data is not None:
            res["data"] = self.data
        return res


class MCPConnectionError(MCPError):
    """Raised when transport connection fails or is severed."""
    def __init__(self, message: str):
        super().__init__(message, code=MCPErrorCode.INTERNAL_ERROR)


class MCPTimeoutError(MCPError):
    """Raised when a request exceeds timeout limits."""
    def __init__(self, message: str = "Request timed out"):
        super().__init__(message, code=MCPErrorCode.TIMEOUT)


class MCPToolExecutionError(MCPError):
    """Raised when an MCP tool fails during execution."""
    def __init__(self, message: str, data: Optional[Any] = None):
        super().__init__(message, code=MCPErrorCode.TOOL_EXECUTION_ERROR, data=data)


@dataclass
class JSONRPCError:
    code: int = MCPErrorCode.INTERNAL_ERROR
    message: str = "Internal error"
    data: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"code": int(self.code), "message": self.message}
        if self.data is not None:
            d["data"] = self.data
        return d


@dataclass
class JSONRPCRequest:
    jsonrpc: str = "2.0"
    id: Optional[Union[str, int]] = None
    method: str = ""
    params: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id, "method": self.method}
        if self.params is not None:
            d["params"] = self.params
        return d


@dataclass
class JSONRPCResponse:
    jsonrpc: str = "2.0"
    id: Optional[Union[str, int]] = None
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            d["error"] = self.error
        else:
            d["result"] = self.result
        return d


@dataclass
class JSONRPCNotification:
    jsonrpc: str = "2.0"
    method: str = ""
    params: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params is not None:
            d["params"] = self.params
        return d


@dataclass
class ToolInputSchema:
    type: str = "object"
    properties: Dict[str, Any] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)


@dataclass
class ToolDefinition:
    name: str
    description: str
    inputSchema: Dict[str, Any]
    server_name: Optional[str] = None

    def to_function_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": f"[{self.server_name}] {self.description}" if self.server_name else self.description,
                "parameters": self.inputSchema,
            },
        }


@dataclass
class ToolCallResult:
    content: List[Dict[str, Any]] = field(default_factory=list)
    isError: bool = False
    structured_data: Optional[Any] = None

    @classmethod
    def success(cls, text: str, data: Optional[Any] = None) -> "ToolCallResult":
        return cls(
            content=[{"type": "text", "text": text}],
            isError=False,
            structured_data=data,
        )

    @classmethod
    def failure(cls, error_msg: str, data: Optional[Any] = None) -> "ToolCallResult":
        return cls(
            content=[{"type": "text", "text": error_msg}],
            isError=True,
            structured_data=data,
        )

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "content": self.content,
            "isError": self.isError,
        }
        if self.structured_data is not None:
            res["data"] = self.structured_data
        return res


@dataclass
class MCPClientCapabilities:
    """Declared client capabilities according to MCP spec."""
    roots: Dict[str, Any] = field(default_factory=lambda: {"listChanged": True})
    sampling: Dict[str, Any] = field(default_factory=dict)
    experimental: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "roots": self.roots,
            "sampling": self.sampling,
            "experimental": self.experimental,
        }


@dataclass
class MCPServerCapabilities:
    """Declared server capabilities discovered during handshake."""
    tools: Optional[Dict[str, Any]] = None
    resources: Optional[Dict[str, Any]] = None
    prompts: Optional[Dict[str, Any]] = None
    logging: Optional[Dict[str, Any]] = None
    experimental: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MCPServerCapabilities":
        return cls(
            tools=data.get("tools"),
            resources=data.get("resources"),
            prompts=data.get("prompts"),
            logging=data.get("logging"),
            experimental=data.get("experimental"),
        )

    def supports_tools(self) -> bool:
        return self.tools is not None

    def supports_resources(self) -> bool:
        return self.resources is not None

    def supports_prompts(self) -> bool:
        return self.prompts is not None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        if self.tools is not None:
            d["tools"] = self.tools
        if self.resources is not None:
            d["resources"] = self.resources
        if self.prompts is not None:
            d["prompts"] = self.prompts
        if self.logging is not None:
            d["logging"] = self.logging
        if self.experimental is not None:
            d["experimental"] = self.experimental
        return d


@dataclass
class ServerHealthReport:
    """Health diagnostic report for a registered MCP server."""
    server_name: str
    state: MCPServerState
    is_connected: bool
    latency_ms: Optional[float] = None
    tools_count: int = 0
    error: Optional[str] = None
    server_info: Dict[str, Any] = field(default_factory=dict)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    circuit_state: str = "CLOSED"
    timestamp: float = field(default_factory=time.time)

    @property
    def is_alive(self) -> bool:
        return self.is_connected

    def to_dict(self) -> Dict[str, Any]:
        return {
            "server_name": self.server_name,
            "state": str(self.state.value if hasattr(self.state, "value") else self.state),
            "is_connected": self.is_connected,
            "latency_ms": self.latency_ms,
            "tools_count": self.tools_count,
            "error": self.error,
            "server_info": self.server_info,
            "capabilities": self.capabilities,
            "circuit_state": self.circuit_state,
            "timestamp": self.timestamp,
        }

