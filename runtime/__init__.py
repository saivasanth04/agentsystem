"""
Runtime Engine & Execution Loop.
Provides capability-driven tool routing, strict observation normalization,
and the Claude-style 7-phase execution loop:
Reason -> Select Tool -> Execute -> Observation -> State Update -> Context Rebuild -> Reason Again
"""
from .tool_policy import (
    ToolPolicy,
    DEFAULT_TOOL_ALIASES,
    CAPABILITY_TO_TOOLS,
)
from .capability_router import (
    CapabilityRouter,
    BrowserMCPAdapter,
)
from .permission_engine import (
    PermissionEngine,
    PermissionEvaluationResult,
)
from .observation_engine import (
    Observation,
    ObservationEngine,
)
from .execution_state import (
    ExecutionState,
    LoopStatus,
)
from .event_stream import (
    EventStream,
    LoopEvent,
    LoopEventType,
)
from .agent_loop import (
    AgentExecutionLoop,
)

__all__ = [
    "ToolPolicy",
    "DEFAULT_TOOL_ALIASES",
    "CAPABILITY_TO_TOOLS",
    "CapabilityRouter",
    "BrowserMCPAdapter",
    "PermissionEngine",
    "PermissionEvaluationResult",
    "Observation",
    "ObservationEngine",
    "ExecutionState",
    "LoopStatus",
    "EventStream",
    "LoopEvent",
    "LoopEventType",
    "AgentExecutionLoop",
]
