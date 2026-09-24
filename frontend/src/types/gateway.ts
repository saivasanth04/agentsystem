export interface GatewayOverview {
  connected_providers: number;
  total_providers: number;
  active_models: number;
  accessible_free_models: number;
  healthy_deployments: number;
  average_latency_ms: number;
  requests_per_minute: number;
  success_rate_pct: number;
  retry_rate_pct: number;
  fallback_rate_pct: number;
  cache_hit_ratio_pct: number;
  token_consumption: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
  active_agent_sessions: number;
  last_sync_time: string | null;
}

export interface GatewayProviderStatus {
  id: string;
  name: string;
  base_url: string;
  has_key: boolean;
  auth_type: string;
  status: 'connected' | 'auth_failed' | 'unreachable' | 'timeout' | 'pending' | 'http_error';
  model_count: number;
  free_count: number;
  last_sync: string;
  error: string | null;
  latency_ms: number;
}

export interface GatewayModel {
  provider: string;
  model: string;
  accessible: boolean;
  free: boolean;
  latency: number;
  health: number;
  rpmRemaining: number | null;
  tpmRemaining: number | null;
  quotaRemaining: number | null;
  quotaLimit: number | null;
  resetAt: string | null;
  capabilities: string[];
  contextWindow: number | null;
  lastSuccess: string;
  lastFailure: string | null;
  totalRequests?: number;
  totalSuccesses?: number;
  totalTokens?: number;
}

export interface GatewayRoutingTrace {
  trace_id: string;
  timestamp: string;
  logical_mode: 'auto' | 'fast' | 'smart' | 'coder' | string;
  candidates_count: number;
  candidates_sample: string[];
  selected_provider: string;
  selected_model: string;
  status: 'SUCCESS' | 'RETRY' | 'FALLBACK' | 'ERROR';
  latency_ms: number;
  tokens_used: number;
  retries: number;
  fallbacks_taken: string[];
  error: string | null;
}

export interface GatewayAnalytics {
  provider_distribution: Record<string, number>;
  mode_distribution: Record<string, number>;
  latency_percentiles: {
    p50_ms: number;
    p90_ms: number;
    p99_ms: number;
  };
  total_requests: number;
  total_tokens: number;
}

export interface GatewaySettings {
  unified_api_key: string;
  discovery_interval_seconds: number;
  request_timeout: number;
  max_retries: number;
  routing_strategy: string;
  semantic_cache_enabled: boolean;
  budget_limit_usd: number;
}

export type LogicalExecutionMode = 'auto' | 'fast' | 'smart' | 'coder';
