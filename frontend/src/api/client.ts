import {
  DashboardKPIs,
  SessionSummary,
  SessionDetail,
  DAGSnapshot,
  AgentMessage,
  VerificationEvidence,
  ArtifactMeta,
  AgentManifest,
  BudgetStats,
  MemoryItem,
  ReplanRecord,
  FileDiffItem,
  WorkspaceInfo,
  WorkspaceValidation,
  FileTreeNode,
  WorkspaceFile,
  PreflightResult,
  TerminalExecutionResult,
  McpServerInfo,
  ContextPreview,
} from '../types/orchestrator';

const API_BASE = '/api';

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    let errorDetail = response.statusText;
    try {
      const errorJson = await response.json();
      errorDetail = errorJson.detail || errorJson.message || JSON.stringify(errorJson);
    } catch {
      // ignore json parse error
    }
    throw new Error(`API Error [${response.status}]: ${errorDetail}`);
  }

  return response.json();
}

export const orchestratorApi = {
  // -------------------------------------------------------------------------
  // 1. Workspace & Filesystem Operations
  // -------------------------------------------------------------------------
  validateWorkspace: (path: string) =>
    request<WorkspaceValidation>('/workspace/validate', {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),

  getWorkspaceInfo: (path?: string) => {
    const qs = path ? `?path=${encodeURIComponent(path)}` : '';
    return request<WorkspaceInfo>(`/workspace/info${qs}`);
  },

  browseDirectories: (currentPath?: string) => {
    const qs = currentPath ? `?current_path=${encodeURIComponent(currentPath)}` : '';
    return request<{ current_path: string; parent_path: string | null; directories: Array<{ name: string; path: string; is_git: boolean }> }>(
      `/workspace/browse${qs}`
    );
  },

  getWorkspaceFiles: (path?: string, maxDepth: number = 4) => {
    const params = new URLSearchParams();
    if (path) params.set('path', path);
    if (maxDepth) params.set('max_depth', maxDepth.toString());
    const qs = params.toString() ? `?${params.toString()}` : '';
    return request<{ workspace_path: string; project_name: string; tree: FileTreeNode[]; total_top_level: number }>(
      `/workspace/files${qs}`
    );
  },

  readWorkspaceFile: (filepath: string, workspacePath?: string) => {
    const params = new URLSearchParams({ filepath });
    if (workspacePath) params.set('workspace_path', workspacePath);
    return request<WorkspaceFile>(`/workspace/file?${params.toString()}`);
  },

  saveWorkspaceFile: (filepath: string, content: string, workspacePath?: string) =>
    request<{ success: boolean; filepath: string; lines: number; size: number; modified_at: string }>(
      '/workspace/file',
      {
        method: 'POST',
        body: JSON.stringify({ filepath, content, workspace_path: workspacePath }),
      }
    ),

  createWorkspaceFile: (path: string, isDirectory: boolean = false, content: string = '', workspacePath?: string) =>
    request<{ success: boolean; path: string; is_directory: boolean }>('/workspace/file/create', {
      method: 'POST',
      body: JSON.stringify({ path, is_directory: isDirectory, content, workspace_path: workspacePath }),
    }),

  renameWorkspaceFile: (oldPath: string, newPath: string, workspacePath?: string) =>
    request<{ success: boolean; old_path: string; new_path: string }>('/workspace/file/rename', {
      method: 'POST',
      body: JSON.stringify({ old_path: oldPath, new_path: newPath, workspace_path: workspacePath }),
    }),

  deleteWorkspaceFile: (filepath: string, workspacePath?: string) => {
    const params = new URLSearchParams({ filepath });
    if (workspacePath) params.set('workspace_path', workspacePath);
    return request<{ success: boolean; filepath: string }>(`/workspace/file?${params.toString()}`, {
      method: 'DELETE',
    });
  },

  runWorkspacePreflight: (workspacePath?: string, model?: string) =>
    request<PreflightResult>('/workspace/preflight', {
      method: 'POST',
      body: JSON.stringify({ workspace_path: workspacePath, model }),
    }),

  runPreflight: (workspacePath?: string, model?: string) =>
    request<PreflightResult>('/workspace/preflight', {
      method: 'POST',
      body: JSON.stringify({ workspace_path: workspacePath, model }),
    }),

  // -------------------------------------------------------------------------
  // 2. Terminal & MCP Tool Operations
  // -------------------------------------------------------------------------
  runTerminalCommand: (command: string, workspacePath?: string) =>
    request<TerminalExecutionResult>('/terminal/run', {
      method: 'POST',
      body: JSON.stringify({ command, workspace_path: workspacePath }),
    }),

  getMcpServers: (workspacePath?: string) => {
    const qs = workspacePath ? `?workspace_path=${encodeURIComponent(workspacePath)}` : '';
    return request<{ servers: McpServerInfo[]; total_servers: number }>(`/mcp/servers${qs}`);
  },

  callMcpTool: (serverName: string, toolName: string, args: Record<string, any> = {}) =>
    request<{ success: boolean; server: string; tool: string; result?: any; error?: string }>('/mcp/call', {
      method: 'POST',
      body: JSON.stringify({ server_name: serverName, tool_name: toolName, arguments: args }),
    }),

  previewContext: (userRequest: string, workspacePath?: string) =>
    request<ContextPreview>('/context/preview', {
      method: 'POST',
      body: JSON.stringify({ workspace_path: workspacePath || '', user_request: userRequest }),
    }),

  // -------------------------------------------------------------------------
  // 3. Dashboard
  // -------------------------------------------------------------------------
  getDashboardKPIs: () => request<DashboardKPIs>('/dashboard/stats'),

  // -------------------------------------------------------------------------
  // 4. Tasks & Launch
  // -------------------------------------------------------------------------
  dryRunTask: (data: {
    user_request: string;
    workspace_path?: string;
    model?: string;
    config?: Record<string, any>;
  }) =>
    request<{ success: boolean; dag: DAGSnapshot; estimated_cost_usd: number; estimated_tokens: number }>('/tasks/dry-run', {
      method: 'POST',
      body: JSON.stringify({
        user_request: data.user_request,
        workspace_path: data.workspace_path || (data.config && data.config.workspace_dir),
        model: data.model,
      }),
    }),

  launchTask: (data: {
    user_request: string;
    workspace_path?: string;
    model?: string;
    max_iterations?: number;
    max_session_cost?: number;
    max_tokens?: number;
    role_models?: Record<string, string>;
    config?: Record<string, any>;
  }) =>
    request<{
      success: boolean;
      session_id: string;
      status: string;
      prompt: string;
      workspace_path?: string;
      project_name?: string;
      git_branch?: string;
      started_at: string;
    }>('/tasks/launch', {
      method: 'POST',
      body: JSON.stringify({
        user_request: data.user_request,
        workspace_path: data.workspace_path || (data.config && data.config.workspace_dir),
        model: data.model,
        max_iterations: data.max_iterations || (data.config && data.config.max_replan_iterations),
        max_session_cost: data.max_session_cost || (data.config && data.config.max_cost_usd),
        max_tokens: data.max_tokens || (data.config && data.config.max_tokens),
        role_models: data.role_models || (data.config && data.config.role_overrides),
      }),
    }),

  // -------------------------------------------------------------------------
  // 5. Sessions
  // -------------------------------------------------------------------------
  getSessions: (params?: { status?: string; query?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.status) q.set('status', params.status);
    if (params?.query) q.set('query', params.query);
    if (params?.limit) q.set('limit', params.limit.toString());
    const qs = q.toString() ? `?${q.toString()}` : '';
    return request<{ total: number; sessions: SessionSummary[] }>(`/sessions${qs}`);
  },

  listSessions: (params?: { status?: string; query?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.status) q.set('status', params.status);
    if (params?.query) q.set('query', params.query);
    if (params?.limit) q.set('limit', params.limit.toString());
    const qs = q.toString() ? `?${q.toString()}` : '';
    return request<{ total: number; sessions: SessionSummary[] }>(`/sessions${qs}`);
  },

  getSessionDetail: (sessionId: string) => request<SessionDetail>(`/sessions/${sessionId}`),

  cancelSession: (sessionId: string, reason?: string) =>
    request<{ success: boolean; message: string }>(`/sessions/${sessionId}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason || 'User requested cancellation' }),
    }),

  resumeSession: (sessionId: string) =>
    request<{ success: boolean; session_id: string; status: string; remaining_tasks: number }>(`/sessions/${sessionId}/resume`, {
      method: 'POST',
    }),

  rollbackSession: (sessionId: string, checkpointId: string) =>
    request<{ success: boolean; rollback_result: any; restored_files?: number }>(`/sessions/${sessionId}/rollback`, {
      method: 'POST',
      body: JSON.stringify({ checkpoint_id: checkpointId }),
    }),

  deleteSession: (sessionId: string) =>
    request<{ success: boolean; message: string }>(`/sessions/${sessionId}`, {
      method: 'DELETE',
    }),

  // -------------------------------------------------------------------------
  // 6. Session-Specific Sub-resources
  // -------------------------------------------------------------------------
  getSessionDAG: (sessionId: string) => request<DAGSnapshot>(`/sessions/${sessionId}/dag`),

  getSessionMessages: (sessionId: string) => request<{ messages: AgentMessage[] }>(`/sessions/${sessionId}/messages`),

  getVerificationEvidence: (sessionId: string) =>
    request<VerificationEvidence>(`/sessions/${sessionId}/verification`),

  getSessionDiff: (sessionId: string) =>
    request<{ files_changed: number; diff_blocks: Array<FileDiffItem> }>(
      `/sessions/${sessionId}/diff`
    ),

  getReplanHistory: (sessionId: string) => request<{ replan_history: ReplanRecord[] }>(`/sessions/${sessionId}/replan`),

  // -------------------------------------------------------------------------
  // 7. Artifacts, Agents, Swarm, Budgets, Memory, Settings
  // -------------------------------------------------------------------------
  getArtifacts: (params?: { category?: string; session_id?: string }) => {
    const q = new URLSearchParams();
    if (params?.category) q.set('category', params.category);
    if (params?.session_id) q.set('session_id', params.session_id);
    const qs = q.toString() ? `?${q.toString()}` : '';
    return request<{ artifacts: ArtifactMeta[]; total: number }>(`/artifacts${qs}`);
  },

  getArtifact: (artifactId: string) =>
    request<{ artifact: ArtifactMeta; content: string }>(`/artifacts/${artifactId}`),

  getAgents: () =>
    request<AgentManifest[]>('/agents'),

  getSwarmStatus: () =>
    request<{
      active_nodes: number;
      blackboard_posts: Array<{ author: string; topic: string; content: string; timestamp: string }>;
      consensus_votes: Array<{ issue: string; yay: number; nay: number; status: string }>;
    }>('/swarm/status'),

  getBudgets: () =>
    request<{
      ledger: any[];
      limits: { max_session_cost_usd: number; max_total_tokens: number };
      current_session_cost: number;
    }>('/budgets'),

  getMemory: () =>
    request<{
      working_memory: { facts: string[]; pitfalls: string[]; scratchpad: string };
      episodic_experiences: any[];
      project_conventions: Record<string, string>;
      semantic_knowledge_nodes: number;
    }>('/memory'),

  getSettings: () => request<Record<string, any>>('/settings'),

  saveSettings: (settings: Record<string, any>) =>
    request<{ success: boolean; settings: Record<string, any> }>('/settings', {
      method: 'POST',
      body: JSON.stringify(settings),
    }),
};
