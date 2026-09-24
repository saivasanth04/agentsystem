export type TaskStatus = 
  | 'PENDING'
  | 'READY'
  | 'RUNNING'
  | 'VERIFYING'
  | 'COMPLETED'
  | 'IN_PROGRESS'
  | 'NEED_VERIFICATION'
  | 'INCOMPLETE'
  | 'FAILED'
  | 'STOPPED'
  | 'NEED_MORE_EVIDENCE'
  | 'BLOCKED'
  | 'SKIPPED';

export type ReviewVerdict = 'PASS' | 'FAIL' | 'UNDECIDED';

export type ModelTier = 'auto' | 'fast' | 'smart' | 'coder' | 'FAST' | 'BALANCED' | 'FRONTIER' | 'CODING' | 'REASONING' | 'FALLBACK';

export interface WorkspaceInfo {
  path: string;
  project_name: string;
  is_git: boolean;
  git_branch: string;
  git_commit?: string;
  git_dirty: boolean;
  dirty_count: number;
  is_accessible: boolean;
  file_count?: number;
}

export interface WorkspaceValidation {
  valid: boolean;
  path: string;
  resolved_path: string;
  project_name: string;
  is_git: boolean;
  git_branch: string;
  git_commit?: string;
  git_dirty: boolean;
  dirty_count: number;
  file_count: number;
  accessible: boolean;
  error: string | null;
}

export interface RecentWorkspace {
  path: string;
  project_name: string;
  last_opened: string;
  is_git: boolean;
  git_branch: string;
}

export interface FileTreeNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size?: number;
  extension?: string;
  modified_at?: string | null;
  children?: FileTreeNode[];
  count?: number;
}

export interface WorkspaceFile {
  filepath: string;
  content: string;
  lines: number;
  size: number;
  modified_at: string | null;
  is_binary: boolean;
}

export interface PreflightCheck {
  id: string;
  name: string;
  status: 'PASS' | 'WARN' | 'FAIL';
  message: string;
}

export interface PreflightResult {
  ready: boolean;
  workspace_path: string;
  checks: PreflightCheck[];
  diagnostics: string[];
}

export interface TerminalExecutionResult {
  command: string;
  stdout: string;
  stderr: string;
  exit_code: number;
  duration_ms: number;
  workspace_path: string;
}

export interface McpTool {
  name: string;
  description: string;
  parameters: Record<string, any>;
}

export interface McpServerInfo {
  name: string;
  status: 'CONNECTED' | 'DISCONNECTED' | 'ERROR';
  tool_count: number;
  tools: McpTool[];
  latency_ms: number;
  capabilities: string[];
}

export interface ContextPreview {
  workspace_path: string;
  tech_stack: string[];
  focal_files: Array<{
    filepath: string;
    relevance_score: number;
    provenance: string;
  }>;
  context_budget: {
    total_tokens_allocated: number;
    focal_files_tokens: number;
    repo_map_tokens: number;
    skills_tokens: number;
    conversation_history_tokens: number;
  };
  retrieved_skills: string[];
}

export interface ExecutableTask {
  id: string;
  name?: string;
  description: string;
  objective?: string;
  capabilities?: string[];
  tools?: string[];
  skills?: string[];
  inputs?: string[];
  outputs?: string[];
  acceptance_tests?: string[];
  status: TaskStatus;
  dependencies: string[];
  wave?: number;
  assigned_agent?: string;
  assigned_model?: string;
  attempts?: number;
  max_attempts?: number;
  result?: string | null;
  error?: string | null;
  tool_history?: Array<{
    tool: string;
    input: any;
    output: any;
    status: string;
    timestamp: string;
  }>;
  observations?: string[];
  started_at?: string | null;
  completed_at?: string | null;
}

export interface DAGEdge {
  from: string;
  to: string;
}

export interface DAGSnapshot {
  session_id: string;
  nodes: ExecutableTask[];
  edges: DAGEdge[];
  waves: string[][];
  active_wave: number;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  running_tasks: number;
}

export interface SessionSummary {
  session_id: string;
  user_request: string;
  status: TaskStatus;
  verdict: ReviewVerdict;
  score: number;
  iteration: number;
  max_iterations: number;
  total_cost_usd: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  created_at: string;
  updated_at: string;
  duration_seconds: number;
  workspace_path?: string;
  git_branch?: string;
  git_commit?: string;
  manifest_hash?: string;
  snapshot_id?: string;
  git_dirty?: boolean;
}

export interface SessionDetail extends SessionSummary {
  workspace_path?: string;
  reproducibility: {
    snapshot_id: string;
    manifest_hash: string;
    git_dirty: boolean;
    seed: number;
    python_version: string;
    orchestrator_version: string;
    workspace_path?: string;
    git_branch?: string;
    git_commit?: string;
  };
  execution_summary: {
    total_tasks: number;
    completed_tasks: number;
    failed_tasks: number;
    running_tasks: number;
    pending_tasks: number;
    total_attempts: number;
    checkpoints_count: number;
    observations_count: number;
    tools_used: string[];
    skills_used: string[];
  };
  final_report?: {
    reviewer_summary: string;
    strengths: string[];
    issues: Array<{
      severity: 'CRITICAL' | 'MAJOR' | 'MINOR' | 'SUGGESTION';
      title: string;
      description: string;
      file_location?: string;
    }>;
    remediation_plan?: string[];
    score: number;
    verdict: ReviewVerdict;
  };
}

export interface AgentMessage {
  id: string;
  session_id: string;
  agent: string;
  role: string;
  stage: string;
  content: string;
  structured_data?: Record<string, any>;
  timestamp: string;
  message_type?: 'system' | 'agent' | 'tool' | 'verdict' | 'error' | 'replan';
}

export interface VerificationEvidence {
  session_id: string;
  build_pass: boolean;
  build_output?: string;
  tests_passed: number;
  tests_total: number;
  test_exit_code: number;
  test_details?: Array<{ name: string; status: 'PASS' | 'FAIL' | 'SKIPPED'; duration: number; error?: string }>;
  lint_errors: number;
  lint_warnings: number;
  lint_details?: Array<{ file: string; line: number; rule: string; message: string }>;
  diff_coverage_pct: number;
  acceptance_criteria: Array<{
    criterion: string;
    verified: boolean;
    evidence_snippet?: string;
  }>;
  tautological_assertions: number;
  tautological_details?: string[];
  reviewer_verdict: ReviewVerdict;
  adversarial_override: boolean;
  veto_reason?: string;
  ground_truth_score: number;
}

export interface TraceSpan {
  id: string;
  session_id: string;
  name: string;
  parent_id?: string | null;
  start_time: string;
  end_time?: string;
  duration_ms?: number;
  status: 'OK' | 'ERROR' | 'UNSET';
  attributes: Record<string, any>;
  children?: TraceSpan[];
}

export interface ArtifactMeta {
  artifact_id: string;
  session_id: string;
  task_id?: string;
  name: string;
  category: 'plans' | 'specifications' | 'patches' | 'test_results' | 'reports' | 'logs';
  path: string;
  size_bytes: number;
  created_at: string;
  metadata?: Record<string, any>;
}

export interface AgentManifest {
  id: string;
  name: string;
  role: string;
  description: string;
  capabilities: string[];
  tools: string[];
  skills: string[];
  model_tier: ModelTier;
  status: 'ACTIVE' | 'IDLE' | 'BUSY' | 'PAUSED' | 'TERMINATED';
  turn_count: number;
  parent_id?: string | null;
}

export interface BudgetLedgerEntry {
  id: string;
  session_id: string;
  task_id: string;
  agent: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  timestamp: string;
}

export interface BudgetStats {
  total_cost_usd: number;
  max_session_cost_usd: number;
  total_tokens: number;
  max_session_tokens: number;
  per_task_cost_limit_usd: number;
  token_budget_used_pct: number;
  cost_budget_used_pct: number;
  tier_breakdown: Record<string, { cost: number; tokens: number }>;
  history: BudgetLedgerEntry[];
  exceeded_events: Array<{
    timestamp: string;
    session_id: string;
    reason: string;
    action_taken: 'HALT' | 'WARN';
  }>;
}

export interface MemoryItem {
  id: string;
  scope: 'working' | 'task' | 'episodic' | 'conventions' | 'semantic';
  key: string;
  value: any;
  tags: string[];
  updated_at: string;
}

export interface ReplanRecord {
  iteration: number;
  trigger_reason: string;
  feedback_summary: string;
  injected_task_ids: string[];
  pruned_task_ids: string[];
  rollback_files_count?: number;
  timestamp: string;
}

export interface FileDiffItem {
  path: string;
  status: 'MODIFIED' | 'ADDED' | 'DELETED';
  before_content?: string;
  after_content?: string;
  diff_text?: string;
  author_agent?: string;
  model_used?: string;
  prompt_hash?: string;
}

export interface DashboardKPIs {
  total_sessions: number;
  pass_rate_pct: number;
  avg_tokens_per_session: number;
  total_cost_usd: number;
  active_runs_count: number;
  verdict_distribution: {
    pass: number;
    fail: number;
    undecided: number;
  };
  cost_timeline: Array<{
    date: string;
    fast_cost: number;
    balanced_cost: number;
    frontier_cost: number;
    total_tokens: number;
  }>;
  agent_activity_heatmap: Array<{
    agent: string;
    hour: number;
    actions: number;
  }>;
  alerts: Array<{
    id: string;
    type: 'BUDGET' | 'CIRCUIT_BREAKER' | 'ROLLBACK' | 'ERROR';
    message: string;
    timestamp: string;
    session_id?: string;
  }>;
}

export interface EventMessage {
  event_type: string;
  session_id?: string;
  timestamp: string;
  data?: any;
  payload?: any;
  level?: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';
}
