"""
Runtime modules for ReAct execution and root-cause failure diagnosis.
"""
from .react_loop import ReActAgentLoop
from .react_step import ReActStep, ReActTrajectory
from .evidence_stopping import EvidenceType, EvidenceRecord, EvidenceLedger
from .diagnostics import FailureDiagnostician, DiagnosticReport
from .task_graph import TaskDAG, ExecutableTask, TaskState, TaskPermissions, RetryPolicy, ArtifactRecord, generate_task_id, spawn_child_subtasks
from .verification import TaskVerificationGate, VerificationResult
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

__all__ = [
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
    "VerificationResult",
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



