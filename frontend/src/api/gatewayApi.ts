import {
  GatewayOverview,
  GatewayProviderStatus,
  GatewayModel,
  GatewayRoutingTrace,
  GatewayAnalytics,
  GatewaySettings,
} from '../types/gateway';

const GATEWAY_BASE = '/api/gateway';

async function gatewayRequest<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${GATEWAY_BASE}${endpoint}`, {
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
    throw new Error(`Gateway API Error [${response.status}]: ${errorDetail}`);
  }

  return response.json();
}

export const gatewayApi = {
  getOverview: () => gatewayRequest<GatewayOverview>('/overview'),
  
  getProviders: () => gatewayRequest<GatewayProviderStatus[]>('/providers'),
  
  refreshProviders: () =>
    gatewayRequest<{ status: string; message: string; result: any }>('/providers/refresh', {
      method: 'POST',
    }),

  getModels: (params?: {
    capability?: string;
    free_only?: boolean;
    accessible_only?: boolean;
    healthy_only?: boolean;
    provider?: string;
    search?: string;
  }) => {
    const qs = new URLSearchParams();
    if (params?.capability) qs.set('capability', params.capability);
    if (params?.free_only !== undefined) qs.set('free_only', String(params.free_only));
    if (params?.accessible_only !== undefined) qs.set('accessible_only', String(params.accessible_only));
    if (params?.healthy_only !== undefined) qs.set('healthy_only', String(params.healthy_only));
    if (params?.provider) qs.set('provider', params.provider);
    if (params?.search) qs.set('search', params.search);
    const query = qs.toString() ? `?${qs.toString()}` : '';
    return gatewayRequest<GatewayModel[]>(`/models${query}`);
  },

  getInspectorTraces: (limit: number = 50) =>
    gatewayRequest<GatewayRoutingTrace[]>(`/inspector?limit=${limit}`),

  getAnalytics: () => gatewayRequest<GatewayAnalytics>('/analytics'),

  getSettings: () => gatewayRequest<GatewaySettings>('/settings'),

  updateSettings: (data: Partial<GatewaySettings>) =>
    gatewayRequest<{ status: string; message: string }>('/settings', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
};
