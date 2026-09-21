"""
Contracts and structured data schemas for multi-agent deliverables and interface contracts.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RequirementContract(BaseModel):
    id: str
    description: str
    input_contract: str = ""
    output_contract: str = ""
    acceptance_criteria: List[str] = Field(default_factory=list)


class NonFunctionalRequirementContract(BaseModel):
    id: str
    category: str
    target: str


class EdgeCaseContract(BaseModel):
    scenario: str
    expected_behavior: str


class SpecificationContract(BaseModel):
    feature_name: str
    overview: str = ""
    functional_requirements: List[RequirementContract] = Field(default_factory=list)
    non_functional_requirements: List[NonFunctionalRequirementContract] = Field(default_factory=list)
    edge_cases: List[EdgeCaseContract] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)


class ComponentBlueprintContract(BaseModel):
    module_name: str
    purpose: str
    classes_or_functions: List[Dict[str, Any]] = Field(default_factory=list)


class ArchitectureContract(BaseModel):
    system_title: str
    design_patterns: List[str] = Field(default_factory=list)
    component_structure: List[ComponentBlueprintContract] = Field(default_factory=list)
    file_layout: List[Dict[str, str]] = Field(default_factory=list)
    data_flow_description: str = ""


class PlanPhaseContract(BaseModel):
    phase_number: int
    name: str
    description: str = ""
    deliverables: List[str] = Field(default_factory=list)
    agent_assigned: str = "CODER"


class ExecutionPlanContract(BaseModel):
    project_title: str
    goal_summary: str = ""
    phases: List[PlanPhaseContract] = Field(default_factory=list)
    milestones: List[str] = Field(default_factory=list)
    risk_mitigations: List[Dict[str, str]] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)


class CodeDeliverableContract(BaseModel):
    summary: str
    written_files: List[str] = Field(default_factory=list)
    deliverables: Dict[str, Any] = Field(default_factory=dict)
    turns_taken: int = 1


class TestResultContract(BaseModel):
    test_strategy: str
    test_files: List[str] = Field(default_factory=list)
    execution_success: bool = True
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


class BuildEvidenceContract(BaseModel):
    success: bool = True
    failed_stage: Optional[str] = None
    stages_run: List[str] = Field(default_factory=list)
    details: str = "Build succeeded cleanly."


class TestEvidenceContract(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    exit_code: int = 0
    details: str = ""


class LintEvidenceContract(BaseModel):
    errors: int = 0
    warnings: int = 0
    tools_run: List[str] = Field(default_factory=list)
    details: str = ""


class DiffCoverageEvidenceContract(BaseModel):
    percentage: float = 100.0
    lines_changed: int = 0
    lines_covered: int = 0
    uncovered_files: List[str] = Field(default_factory=list)
    skipped: bool = False
    details: str = ""


class VerificationEvidenceContract(BaseModel):
    build: BuildEvidenceContract = Field(default_factory=BuildEvidenceContract)
    tests: TestEvidenceContract = Field(default_factory=TestEvidenceContract)
    lint: LintEvidenceContract = Field(default_factory=LintEvidenceContract)
    diff_coverage: DiffCoverageEvidenceContract = Field(default_factory=DiffCoverageEvidenceContract)
    acceptance_criteria: Dict[str, str] = Field(default_factory=dict)
    traceability_matrix: Optional[Dict[str, Any]] = None
    regression_report: Optional[Dict[str, Any]] = None
    tautological_assertions: int = 0
    passed: bool = True


class ReviewAuditContract(BaseModel):
    verdict: str  # PASS | FAIL | UNDECIDED
    score_out_of_100: int = 100
    summary: str
    evidence: Optional[VerificationEvidenceContract] = None
    strengths: List[str] = Field(default_factory=list)
    issues: List[Dict[str, str]] = Field(default_factory=list)
    target_agent_for_fix: str = "CODER"
    remediation_plan: List[str] = Field(default_factory=list)


class ResourceBudgetContract(BaseModel):
    max_processes: int = 0
    max_memory_mb: float = 0.0
    max_cpu_percent: float = 0.0
    max_cpu_cores: float = 0.0
    max_disk_write_mb: float = 0.0
    max_single_file_mb: float = 0.0
    max_network_requests: int = 0
    max_network_mb: float = 0.0
    max_tool_calls_total: int = 0
    max_tool_calls_per_turn: int = 0
    max_tokens: int = 0
    max_runtime_seconds: float = 0.0
    max_command_timeout_seconds: float = 0.0
    action: str = "HALT"  # HALT | WARN


class TaskPermissionsContract(BaseModel):
    allowed_read_paths: List[str] = Field(default_factory=lambda: ["*"])
    allowed_write_paths: List[str] = Field(default_factory=lambda: ["*"])
    allowed_commands: List[str] = Field(default_factory=list)
    network_allowed: bool = False
    allowed_domains: List[str] = Field(default_factory=list)
    blocked_domains: List[str] = Field(default_factory=list)
    allowed_paths: List[str] = Field(default_factory=lambda: ["*"])
    blocked_paths: List[str] = Field(default_factory=list)
    read_only_paths: List[str] = Field(default_factory=list)
    sensitive_paths: List[str] = Field(default_factory=list)
    resource_budget: ResourceBudgetContract = Field(default_factory=ResourceBudgetContract)


class RetryPolicyContract(BaseModel):
    max_retries: int = 2
    retry_delay_seconds: float = 1.0
    exponential_backoff: bool = True


class ExecutableTaskContract(BaseModel):
    task_id: str
    objective: str
    dependencies: List[str] = Field(default_factory=list)
    state: str = "PENDING"  # PENDING | READY | RUNNING | VERIFYING | COMPLETED | FAILED | BLOCKED | SKIPPED | NEED_VERIFICATION | INCOMPLETE | STOPPED
    required_capabilities: List[str] = Field(default_factory=list)
    required_tools: List[str] = Field(default_factory=list)
    preferred_skills: List[str] = Field(default_factory=list)
    inputs: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    acceptance_tests: List[str] = Field(default_factory=list)
    permissions: TaskPermissionsContract = Field(default_factory=TaskPermissionsContract)
    resource_budget: ResourceBudgetContract = Field(default_factory=ResourceBudgetContract)
    timeout_seconds: int = 180
    max_turns: int = 15
    retry_policy: RetryPolicyContract = Field(default_factory=RetryPolicyContract)
    owner_agent: Optional[str] = None
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    result_data: Optional[Any] = None
    error_message: Optional[str] = None


class TaskDAGContract(BaseModel):
    title: str = "Execution DAG"
    tasks: List[ExecutableTaskContract] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FileEditActionType(str):
    CREATE_FILE = "CREATE_FILE"
    MODIFY_SYMBOL = "MODIFY_SYMBOL"
    REPLACE_BLOCK = "REPLACE_BLOCK"
    INSERT_BLOCK = "INSERT_BLOCK"
    DELETE_FILE = "DELETE_FILE"
    RENAME_FILE = "RENAME_FILE"
    APPLY_PATCH = "APPLY_PATCH"


class FileEditStepStatus(str):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class FileEditAction(BaseModel):
    step_id: str = Field(description="Unique identifier for this edit step, e.g. 'step-1'")
    filepath: str = Field(description="Target relative filepath within workspace")
    action_type: str = Field(default="MODIFY_SYMBOL", description="Type of edit: CREATE_FILE, MODIFY_SYMBOL, REPLACE_BLOCK, INSERT_BLOCK, DELETE_FILE, RENAME_FILE, APPLY_PATCH")
    target_symbols: List[str] = Field(default_factory=list, description="Target AST symbols (classes/functions/methods) being created or modified")
    description: str = Field(default="", description="Human-readable description of the exact change")
    rationale: str = Field(default="", description="Architectural or logical rationale for this edit")
    dependencies: List[str] = Field(default_factory=list, description="List of prior step_ids that must complete before this edit")
    expected_version: Optional[str] = Field(default=None, description="Expected OCC version tag or hash if modifying an existing file")
    status: str = Field(default="PENDING", description="Status: PENDING, IN_PROGRESS, COMPLETED, SKIPPED, FAILED")
    verification_check: Optional[str] = Field(default=None, description="Check command or AST check to verify this edit step")
    error: Optional[str] = Field(default=None, description="Error message if step failed")


class StructuredEditPlanContract(BaseModel):
    plan_id: str = Field(default_factory=lambda: "plan-default")
    task_id: Optional[str] = Field(default=None, description="Parent task ID, e.g. 'T-01'")
    summary: str = Field(default="", description="High-level summary of the mutation plan")
    edit_sequence: List[FileEditAction] = Field(default_factory=list, description="Ordered sequence of atomic file edit actions")
    invariants: List[str] = Field(default_factory=list, description="Key system invariants that must not be broken")
    verification_commands: List[str] = Field(default_factory=list, description="Verification commands to execute after all edits")
    estimated_risk: str = Field(default="LOW", description="Risk classification: LOW, MEDIUM, HIGH, CRITICAL")
    created_at: Optional[str] = None


class ReasoningMode(str):
    ONE_SHOT = "ONE_SHOT"
    DELIBERATIVE = "DELIBERATIVE"
    REFLECTIVE = "REFLECTIVE"


class ReasoningConfigContract(BaseModel):
    mode: str = Field(default="ONE_SHOT", description="Reasoning mode: ONE_SHOT, DELIBERATIVE, REFLECTIVE")
    min_exploration_turns: int = Field(default=0, description="Minimum tool exploration turns required before outputting deliverables")
    enable_self_reflection: bool = Field(default=False, description="Whether to require a dedicated self-critique/reflection turn before finalization")
    reflection_prompt: Optional[str] = Field(default=None, description="Optional custom self-critique prompt")



