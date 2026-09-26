"""
Runtime modules for ReAct execution and root-cause failure diagnosis.
"""
from .react_loop import ReActAgentLoop
from .react_step import ReActStep, ReActTrajectory
from .evidence_stopping import EvidenceType, EvidenceRecord, EvidenceLedger
from .diagnostics import FailureDiagnostician, DiagnosticReport
from .task_graph import TaskDAG, ExecutableTask, TaskState, TaskPermissions, RetryPolicy, ArtifactRecord, generate_task_id, spawn_child_subtasks
from .verification import TaskVerificationGate, VerificationResult
VerificationGate = TaskVerificationGate
from .dag_scheduler import ConcurrentDAGScheduler
from .analysis import ParallelDomainAnalyzer, DomainAnalysisMatrix
from .messaging import MessageBus, MessageType, StructuredMessage
from .replan_engine import EpistemicReplanner, ReplanResult
from .fault_localization import (
    TracebackFrame,
    ParsedFailure,
    FaultLocus,
    EmpiricalFaultLocalizer,
    AdversarialAttributionArbiter,
)

from .approval_gate import (
    ApprovalGate,
    AutoApprovalGate,
    InteractiveApprovalGate,
    PolicyBasedApprovalGate,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalDecision,
    DestructiveActionType,
    DestructiveActionClassifier,
)
from .event_bus import (
    EventBus,
    EventType,
    ExecutionEvent,
)
from .idempotency import (
    OperationRecord,
    OperationLedger,
    ConcurrencyConflictError,
    compute_operation_id,
    global_operation_ledger,
)
from .semantic_plan_validator import (
    SemanticPlanValidator,
    SemanticAlignmentReport,
)
from .agent_contract import (
    InterAgentContract,
    ContractEnforcementReport,
    AgentContractEnforcer,
)
from .traceability import (
    TraceStatus,
    TestCaseEvidence,
    RequirementTraceNode,
    TraceabilityMatrix,
    TraceabilityEngine,
)
from .test_evidence_parser import TestExecutionParser
from .regression_detector import (
    DependencyImpact,
    RegressionReport,
    DependencyRegressionDetector,
)
from .project_discovery import (
    ProjectProfile,
    ProjectDiscoveryEngine,
)
from .repo_bootstrap import (
    GitInfo,
    ProjectInfo,
    StackInfo,
    PackageManagerInfo,
    BuildSystemInfo,
    TestRunnerInfo,
    BootstrapReport,
    detect_git,
    detect_project,
    detect_stack,
    detect_package_manager,
    detect_build_system,
    detect_test_runner,
    RepositoryBootstrapper,
)
from runtime.tool_policy import ToolPolicy, DEFAULT_TOOL_ALIASES, CAPABILITY_TO_TOOLS
from runtime.capability_router import CapabilityRouter, BrowserMCPAdapter
from runtime.permission_engine import PermissionEngine, PermissionEvaluationResult
from runtime.observation_engine import Observation, ObservationEngine
from runtime.execution_state import ExecutionState, LoopStatus
from runtime.event_stream import EventStream, LoopEvent, LoopEventType
from runtime.agent_loop import AgentExecutionLoop

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
    "ReActAgentLoop",
    "ReActStep",
    "ReActTrajectory",
    "EvidenceType",
    "EvidenceRecord",
    "EvidenceLedger",
    "FailureDiagnostician",
    "DiagnosticReport",
    "TaskDAG",
    "ExecutableTask",
    "TaskState",
    "TaskPermissions",
    "RetryPolicy",
    "ArtifactRecord",
    "generate_task_id",
    "spawn_child_subtasks",
    "TaskVerificationGate",
    "VerificationGate",
    "VerificationResult",
    "ProductionIDE",
    "IDEVerificationPipeline",
    "IDERepairPipeline",
    "ConcurrentDAGScheduler",
    "ParallelDomainAnalyzer",
    "DomainAnalysisMatrix",
    "MessageBus",
    "MessageType",
    "StructuredMessage",
    "EpistemicReplanner",
    "ReplanResult",
    "TracebackFrame",
    "ParsedFailure",
    "FaultLocus",
    "EmpiricalFaultLocalizer",
    "AdversarialAttributionArbiter",
    "ApprovalGate",
    "AutoApprovalGate",
    "InteractiveApprovalGate",
    "PolicyBasedApprovalGate",
    "ApprovalPolicy",
    "ApprovalRequest",
    "ApprovalDecision",
    "DestructiveActionType",
    "DestructiveActionClassifier",
    "EventBus",
    "EventType",
    "ExecutionEvent",
    "OperationRecord",
    "OperationLedger",
    "ConcurrencyConflictError",
    "compute_operation_id",
    "global_operation_ledger",
    "SemanticPlanValidator",
    "SemanticAlignmentReport",
    "InterAgentContract",
    "ContractEnforcementReport",
    "AgentContractEnforcer",
    "TraceStatus",
    "TestCaseEvidence",
    "RequirementTraceNode",
    "TraceabilityMatrix",
    "TraceabilityEngine",
    "TestExecutionParser",
    "DependencyImpact",
    "RegressionReport",
    "DependencyRegressionDetector",
    "ProjectProfile",
    "ProjectDiscoveryEngine",
    "GitInfo",
    "ProjectInfo",
    "StackInfo",
    "PackageManagerInfo",
    "BuildSystemInfo",
    "TestRunnerInfo",
    "BootstrapReport",
    "detect_git",
    "detect_project",
    "detect_stack",
    "detect_package_manager",
    "detect_build_system",
    "detect_test_runner",
    "RepositoryBootstrapper",
]


def __getattr__(name: str):
    if name in ("ProductionIDE", "IDEVerificationPipeline", "IDERepairPipeline"):
        import ide
        return getattr(ide, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
