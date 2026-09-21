"""
Swarm Primitives & Multi-Agent Coordination Module.
Exposes agent lifecycle, shared blackboard, delegation, stateful handoff,
consensus voting, conflict resolution, supervisor watchdogs, and swarm tools.
"""
from .lifecycle import (
    AgentLifecycleState,
    ResourceLimits,
    SwarmAgentInstance,
    SwarmPool,
)
from .blackboard import (
    BlackboardEntry,
    ResourceReservation,
    SwarmBlackboard,
)
from .delegation import (
    DelegationRequest,
    DelegationResult,
    HandoffRecord,
    DelegationManager,
)
from .consensus import (
    VotingMechanism,
    Vote,
    Proposal,
    ConflictRecord,
    SwarmConsensusEngine,
)
from .watchdog import (
    HealthStatus,
    WatchdogPolicy,
    AgentHealthReport,
    SupervisorWatchdog,
)
from .coordinator import SwarmCoordinator
from .tools import SwarmToolRegistry

__all__ = [
    "AgentLifecycleState",
    "ResourceLimits",
    "SwarmAgentInstance",
    "SwarmPool",
    "BlackboardEntry",
    "ResourceReservation",
    "SwarmBlackboard",
    "DelegationRequest",
    "DelegationResult",
    "HandoffRecord",
    "DelegationManager",
    "VotingMechanism",
    "Vote",
    "Proposal",
    "ConflictRecord",
    "SwarmConsensusEngine",
    "HealthStatus",
    "WatchdogPolicy",
    "AgentHealthReport",
    "SupervisorWatchdog",
    "SwarmCoordinator",
    "SwarmToolRegistry",
]
