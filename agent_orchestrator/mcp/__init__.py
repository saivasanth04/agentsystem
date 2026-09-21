"""
Model Context Protocol (MCP) subsystem for agent_orchestrator.
"""
from .protocol import (
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCNotification,
    JSONRPCError,
    MCPError,
    MCPServerState,
    MCPServerCapabilities,
    MCPClientCapabilities,
    ServerHealthReport,
    ToolDefinition,
    ToolCallResult,
)
from .transport import BaseTransport, StdioTransport, InMemoryTransport
from .circuit_breaker import CircuitState, MCPCircuitBreaker
from .client import MCPClientSession
from .manager import MCPManager

__all__ = [
    "JSONRPCRequest",
    "JSONRPCResponse",
    "JSONRPCNotification",
    "JSONRPCError",
    "MCPError",
    "MCPServerState",
    "MCPServerCapabilities",
    "MCPClientCapabilities",
    "ServerHealthReport",
    "ToolDefinition",
    "ToolCallResult",
    "BaseTransport",
    "StdioTransport",
    "InMemoryTransport",
    "MCPClientSession",
    "MCPManager",
    "CircuitState",
    "MCPCircuitBreaker",
]
