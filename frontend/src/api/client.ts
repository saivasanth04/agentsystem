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
  // Dashboard
  getDashboardKPIs: () => request<DashboardKPIs>('/dashboard/stats'),

  // Tasks & Launch
  dryRunTask: (data: { user_request: string; config?: Record<string, any> }) =>
    request<{ dag: DAGSnapshot; estimated_cost_usd: number; estimated_tokens: number }>('/tasks/dry-run', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  launchTask: (data: { user_request: string; config?: Record<string, any> }) =>
    request<{ session_id: string; message: string; dag: DAGSnapshot }>('/tasks/launch', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Sessions
  getSessions: (params?: { status?: string; search?: string; limit?: number }) => {
    const query = new URLSearchParams();
    if (params?.status) query.set('status', params.status);
    if (params?.search) query.set('search', params.search);
    if (params?.limit) query.set('limit', params.limit.toString());
    const qs = query.toString() ? `?${query.toString()}` : '';
    return request<SessionSummary[]>(`/sessions${qs}`);
  },

  getSessionDetail: (sessionId: string) => request<SessionDetail>(`/sessions/${sessionId}`),

  cancelSession: (sessionId: string, reason?: string) =>
    request<{ success: boolean; message: string }>(`/sessions/${sessionId}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason || 'User requested cancellation' }),
    }),

  resumeSession: (sessionId: string) =>
    request<{ success: boolean; session_id: string }>(`/sessions/${sessionId}/resume`, {
      method: 'POST',
    }),

  rollbackSession: (sessionId: string, snapshotId: string) =>
    request<{ success: boolean; restored_files: number }>(`/sessions/${sessionId}/rollback`, {
      method: 'POST',
      body: JSON.stringify({ snapshot_id: snapshotId }),
    }),

  // Session Specific Details
  getSessionDAG: (sessionId: string) => request<DAGSnapshot>(`/sessions/${sessionId}/dag`),

  getSessionMessages: (sessionId: string) => request<AgentMessage[]>(`/sessions/${sessionId}/messages`),

  getVerificationEvidence: (sessionId: string) =>
    request<VerificationEvidence>(`/sessions/${sessionId}/verification`),

  getSessionDiff: (sessionId: string) => request<FileDiffItem[]>(`/sessions/${sessionId}/diff`),

  getReplanHistory: (sessionId: string) => request<ReplanRecord[]>(`/sessions/${sessionId}/replan`),

  // Artifacts
  getArtifacts: (params?: { category?: string; session_id?: string }) => {
    const query = new URLSearchParams();
    if (params?.category) query.set('category', params.category);
    if (params?.session_id) query.set('session_id', params.session_id);
    const qs = query.toString() ? `?${query.toString()}` : '';
    return request<ArtifactMeta[]>(`/artifacts${qs}`);
  },

  getArtifactContent: (artifactId: string) => request<{ content: string; meta: ArtifactMeta }>(`/artifacts/${artifactId}`),

  // Agents & Swarm
  getAgents: () => request<AgentManifest[]>('/agents'),

  getSwarmStatus: () => request<{
    active_nodes: number;
    blackboard_posts: Array<{ author: string; topic: string; content: string; timestamp: string }>;
    consensus_votes: Array<{ issue: string; yay: number; nay: number; status: string }>;
  }>('/swarm/status'),

  // Budgets
  getBudgets: () => request<BudgetStats>('/budgets'),

  // Memory
  getMemory: (scope?: string, search?: string) => {
    const query = new URLSearchParams();
    if (scope) query.set('scope', scope);
    if (search) query.set('search', search);
    const qs = query.toString() ? `?${query.toString()}` : '';
    return request<MemoryItem[]>(`/memory${qs}`);
  },

  // Settings
  getSettings: () => request<Record<string, any>>('/settings'),

  saveSettings: (settings: Record<string, any>) =>
    request<{ success: boolean; message: string }>('/settings', {
      method: 'POST',
      body: JSON.stringify(settings),
    }),
};
