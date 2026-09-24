import React, { useState, useEffect } from 'react';
import { gatewayApi } from '../api/gatewayApi';
import { GatewayProviderStatus } from '../types/gateway';
import {
  Server,
  RefreshCw,
  Search,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Clock,
  ExternalLink,
  ShieldCheck,
  Zap,
} from 'lucide-react';

interface GatewayProvidersPageProps {
  navigate: (route: string) => void;
}

export const GatewayProvidersPage: React.FC<GatewayProvidersPageProps> = ({ navigate }) => {
  const [providers, setProviders] = useState<GatewayProviderStatus[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('ALL');

  const fetchProviders = async () => {
    try {
      const data = await gatewayApi.getProviders();
      setProviders(data);
    } catch (err: any) {
      console.error('Failed to fetch providers:', err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchProviders();
    const interval = setInterval(fetchProviders, 8000);
    return () => clearInterval(interval);
  }, []);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await gatewayApi.refreshProviders();
      await fetchProviders();
    } catch (err: any) {
      alert(`Provider refresh failed: ${err.message}`);
    } finally {
      setIsRefreshing(false);
    }
  };

  const filteredProviders = providers.filter((p) => {
    const matchesSearch =
      p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.base_url.toLowerCase().includes(searchQuery.toLowerCase());

    if (!matchesSearch) return false;
    if (statusFilter === 'CONNECTED') return p.status === 'connected';
    if (statusFilter === 'AUTH_FAILED') return p.status === 'auth_failed';
    if (statusFilter === 'NO_KEY') return !p.has_key && p.auth_type !== 'none';
    return true;
  });

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-5 bg-slate-900 border border-slate-800 rounded-2xl shadow-xl">
        <div className="flex items-center gap-3.5">
          <div className="w-11 h-11 rounded-xl bg-gradient-to-tr from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
            <Server className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold font-mono text-slate-100">
              Provider Ecosystem & Discovery
            </h1>
            <p className="text-xs text-slate-400">
              34+ Integrated Providers • Dynamic Accessibility Verification • Quota & Reset Timers
            </p>
          </div>
        </div>

        <button
          onClick={handleRefresh}
          disabled={isRefreshing}
          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-xs font-mono font-semibold text-white shadow-lg shadow-cyan-600/20 transition-all cursor-pointer disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
          {isRefreshing ? 'Syncing Ecosystem...' : 'Trigger Discovery Sync'}
        </button>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 p-4 bg-slate-900 border border-slate-800 rounded-xl">
        <div className="relative w-full sm:w-80">
          <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search provider by name, ID, or endpoint..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs font-mono text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
          />
        </div>

        <div className="flex items-center gap-2 text-xs font-mono">
          <span className="text-slate-500">FILTER:</span>
          {['ALL', 'CONNECTED', 'AUTH_FAILED', 'NO_KEY'].map((f) => (
            <button
              key={f}
              onClick={() => setStatusFilter(f)}
              className={`px-3 py-1 rounded-lg border transition-all cursor-pointer ${
                statusFilter === f
                  ? 'bg-cyan-950/40 text-cyan-300 border-cyan-500/40 font-semibold'
                  : 'bg-slate-950 text-slate-400 border-slate-800 hover:border-slate-700'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {/* Providers Grid */}
      {isLoading ? (
        <div className="p-12 text-center text-xs font-mono text-slate-400">Loading provider statuses...</div>
      ) : filteredProviders.length === 0 ? (
        <div className="p-12 text-center text-xs font-mono text-slate-500 bg-slate-900 rounded-xl border border-slate-800">
          No providers found matching search query.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredProviders.map((p) => {
            const isConnected = p.status === 'connected';
            const isAuthError = p.status === 'auth_failed';

            return (
              <div
                key={p.id}
                className="p-4 rounded-xl bg-slate-900 border border-slate-800 hover:border-slate-700 transition-all space-y-3 shadow-sm"
              >
                {/* Header */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold font-mono text-slate-100">{p.name}</span>
                    <span className="text-[10px] font-mono text-slate-500">({p.id})</span>
                  </div>

                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold flex items-center gap-1 border ${
                      isConnected
                        ? 'bg-emerald-950/30 text-emerald-400 border-emerald-800/40'
                        : isAuthError
                        ? 'bg-red-950/30 text-red-400 border-red-800/40'
                        : 'bg-amber-950/30 text-amber-400 border-amber-800/40'
                    }`}
                  >
                    {isConnected ? (
                      <CheckCircle2 className="w-3 h-3" />
                    ) : isAuthError ? (
                      <XCircle className="w-3 h-3" />
                    ) : (
                      <AlertTriangle className="w-3 h-3" />
                    )}
                    {p.status.toUpperCase()}
                  </span>
                </div>

                {/* Base URL */}
                <div className="text-[11px] font-mono text-slate-400 truncate bg-slate-950 px-2.5 py-1 rounded border border-slate-850">
                  {p.base_url}
                </div>

                {/* Stats */}
                <div className="grid grid-cols-3 gap-2 pt-1 text-center font-mono">
                  <div className="bg-slate-950 p-2 rounded-lg border border-slate-800">
                    <div className="text-[10px] text-slate-500 uppercase">Models</div>
                    <div className="text-sm font-bold text-slate-200">{p.model_count}</div>
                  </div>
                  <div className="bg-slate-950 p-2 rounded-lg border border-slate-800">
                    <div className="text-[10px] text-slate-500 uppercase">Free Models</div>
                    <div className="text-sm font-bold text-emerald-400">{p.free_count}</div>
                  </div>
                  <div className="bg-slate-950 p-2 rounded-lg border border-slate-800">
                    <div className="text-[10px] text-slate-500 uppercase">Latency</div>
                    <div className="text-sm font-bold text-amber-400">{p.latency_ms} ms</div>
                  </div>
                </div>

                {/* Error or Sync Info */}
                {p.error ? (
                  <div className="text-[10px] font-mono text-red-400 bg-red-950/30 p-2 rounded border border-red-900/40 truncate">
                    {p.error}
                  </div>
                ) : (
                  <div className="flex items-center justify-between text-[10px] font-mono text-slate-500 pt-1">
                    <span className="flex items-center gap-1">
                      <Clock className="w-3 h-3 text-slate-600" />
                      Sync: {p.last_sync ? new Date(p.last_sync).toLocaleTimeString() : 'Never'}
                    </span>
                    <button
                      onClick={() => navigate(`/gateway/models?provider=${p.id}`)}
                      className="text-cyan-400 hover:underline flex items-center gap-0.5 cursor-pointer"
                    >
                      <span>View Models</span>
                      <ExternalLink className="w-2.5 h-2.5" />
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
