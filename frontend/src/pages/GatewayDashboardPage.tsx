import React, { useState, useEffect } from 'react';
import { gatewayApi } from '../api/gatewayApi';
import { GatewayOverview, GatewayRoutingTrace } from '../types/gateway';
import {
  Activity,
  Server,
  Cpu,
  Zap,
  Brain,
  Code2,
  Sparkles,
  RefreshCw,
  Clock,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  TrendingUp,
  Database,
  ShieldCheck,
  Layers,
  Repeat,
  Share2,
} from 'lucide-react';

interface GatewayDashboardPageProps {
  navigate: (route: string) => void;
}

export const GatewayDashboardPage: React.FC<GatewayDashboardPageProps> = ({ navigate }) => {
  const [overview, setOverview] = useState<GatewayOverview | null>(null);
  const [recentTraces, setRecentTraces] = useState<GatewayRoutingTrace[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchDashboardData = async () => {
    try {
      setError(null);
      const [ovData, tracesData] = await Promise.all([
        gatewayApi.getOverview(),
        gatewayApi.getInspectorTraces(8),
      ]);
      setOverview(ovData);
      setRecentTraces(tracesData);
    } catch (err: any) {
      setError(err.message || 'Failed to load gateway overview');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchDashboardData();
    const interval = setInterval(fetchDashboardData, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    try {
      await gatewayApi.refreshProviders();
      await fetchDashboardData();
    } catch (err: any) {
      alert(`Discovery refresh failed: ${err.message}`);
    } finally {
      setIsRefreshing(false);
    }
  };

  const widgets = [
    {
      title: 'Connected Providers',
      value: overview ? `${overview.connected_providers} / ${overview.total_providers || 34}` : '--',
      subtitle: 'Live authenticated endpoints',
      icon: Server,
      color: 'text-cyan-400 bg-cyan-950/20 border-cyan-500/30',
      route: '/gateway/providers',
    },
    {
      title: 'Active Models',
      value: overview?.active_models ?? '--',
      subtitle: 'Accessible in LiteLLM registry',
      icon: Cpu,
      color: 'text-indigo-400 bg-indigo-950/20 border-indigo-500/30',
      route: '/gateway/models',
    },
    {
      title: 'Accessible Free Models',
      value: overview?.accessible_free_models ?? '--',
      subtitle: 'Zero-cost routing candidates',
      icon: CheckCircle2,
      color: 'text-emerald-400 bg-emerald-950/20 border-emerald-500/30',
      route: '/gateway/models?free_only=true',
    },
    {
      title: 'Healthy Deployments',
      value: overview?.healthy_deployments ?? '--',
      subtitle: 'Health score ≥ 0.70',
      icon: ShieldCheck,
      color: 'text-teal-400 bg-teal-950/20 border-teal-500/30',
      route: '/gateway/models?healthy_only=true',
    },
    {
      title: 'Average Latency',
      value: overview ? `${overview.average_latency_ms} ms` : '--',
      subtitle: 'Real-time response time',
      icon: Clock,
      color: 'text-amber-400 bg-amber-950/20 border-amber-500/30',
      route: '/gateway/analytics',
    },
    {
      title: 'Requests / Minute',
      value: overview?.requests_per_minute ?? '--',
      subtitle: 'Live throughput across agents',
      icon: Activity,
      color: 'text-blue-400 bg-blue-950/20 border-blue-500/30',
      route: '/gateway/analytics',
    },
    {
      title: 'Success Rate',
      value: overview ? `${overview.success_rate_pct}%` : '--',
      subtitle: 'Production completions',
      icon: TrendingUp,
      color: 'text-green-400 bg-green-950/20 border-green-500/30',
      route: '/gateway/analytics',
    },
    {
      title: 'Retry Rate',
      value: overview ? `${overview.retry_rate_pct}%` : '0.0%',
      subtitle: 'Native LiteLLM backoff',
      icon: Repeat,
      color: 'text-yellow-400 bg-yellow-950/20 border-yellow-500/30',
      route: '/gateway/inspector',
    },
    {
      title: 'Fallback Rate',
      value: overview ? `${overview.fallback_rate_pct}%` : '0.0%',
      subtitle: 'Seamless failover triggers',
      icon: Share2,
      color: 'text-purple-400 bg-purple-950/20 border-purple-500/30',
      route: '/gateway/inspector',
    },
    {
      title: 'Cache Hit Ratio',
      value: overview ? `${overview.cache_hit_ratio_pct}%` : '--',
      subtitle: 'Semantic & exact prompt hits',
      icon: Database,
      color: 'text-sky-400 bg-sky-950/20 border-sky-500/30',
      route: '/gateway/settings',
    },
    {
      title: 'Token Consumption',
      value: overview ? `${(overview.token_consumption.total_tokens / 1000).toFixed(1)}k` : '--',
      subtitle: 'Cumulative tracked tokens',
      icon: Layers,
      color: 'text-pink-400 bg-pink-950/20 border-pink-500/30',
      route: '/gateway/analytics',
    },
    {
      title: 'Active Agent Sessions',
      value: overview?.active_agent_sessions ?? '--',
      subtitle: 'Parallel swarm workflows',
      icon: Brain,
      color: 'text-violet-400 bg-violet-950/20 border-violet-500/30',
      route: '/sessions',
    },
  ];

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-5 bg-slate-900 border border-slate-800 rounded-2xl shadow-xl">
        <div className="flex items-center gap-3.5">
          <div className="w-11 h-11 rounded-xl bg-gradient-to-tr from-cyan-500 via-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
            <Server className="w-6 h-6 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-bold font-mono text-slate-100">
                Enterprise LiteLLM Gateway
              </h1>
              <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 font-semibold">
                NATIVE LITELLM ROUTER
              </span>
            </div>
            <p className="text-xs text-slate-400">
              Single OpenAI Endpoint • One Unified Virtual Key • 34+ Auto-Discovered Providers • Latency Routing
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleManualRefresh}
            disabled={isRefreshing}
            className="flex items-center gap-2 px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs font-mono font-medium text-slate-200 transition-all cursor-pointer disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 text-cyan-400 ${isRefreshing ? 'animate-spin' : ''}`} />
            {isRefreshing ? 'Syncing...' : 'Discover Models'}
          </button>

          <button
            onClick={() => navigate('/gateway/inspector')}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-xs font-mono font-semibold text-white shadow-lg shadow-cyan-600/20 transition-all cursor-pointer"
          >
            <span>Routing Inspector</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-red-950/40 border border-red-800 text-red-300 text-xs font-mono flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
          <span>Gateway Connection Notice: {error}. Check if Gateway server is active on port 8080.</span>
        </div>
      )}

      {/* 12 Operational Metric Cards with Drill-down */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs font-mono text-slate-400">
          <span className="uppercase font-bold tracking-wider">Operational Health & Telemetry Metrics</span>
          <span className="text-[11px] text-slate-500">Click any metric for instant drill-down</span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3.5">
          {widgets.map((w, idx) => {
            const Icon = w.icon;
            return (
              <div
                key={idx}
                onClick={() => navigate(w.route)}
                className="p-4 rounded-xl bg-slate-900 border border-slate-800 hover:border-cyan-500/40 hover:bg-slate-850 cursor-pointer transition-all duration-200 group relative overflow-hidden shadow-sm"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[11px] font-mono text-slate-400 uppercase font-medium">
                    {w.title}
                  </span>
                  <div className={`p-1.5 rounded-lg border ${w.color}`}>
                    <Icon className="w-3.5 h-3.5" />
                  </div>
                </div>

                <div className="text-xl font-bold font-mono text-slate-100 group-hover:text-cyan-300 transition-colors">
                  {w.value}
                </div>

                <div className="text-[10px] text-slate-500 mt-1 truncate">{w.subtitle}</div>

                <div className="absolute bottom-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity text-cyan-400">
                  <ArrowRight className="w-3.5 h-3.5" />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 4 Logical Execution Modes Overview */}
      <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-cyan-400" />
            <h2 className="text-sm font-mono font-bold text-slate-200 uppercase">
              Intelligent Routing Modes (LiteLLM Deployment Pools)
            </h2>
          </div>
          <span className="text-xs font-mono text-slate-500">Zero physical model names exposed</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-3.5">
          {/* AUTO */}
          <div
            onClick={() => navigate('/gateway/models')}
            className="p-4 rounded-xl bg-slate-950 border border-slate-800 hover:border-cyan-500/50 cursor-pointer transition-all space-y-2"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold text-cyan-400 flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5" />
                auto
              </span>
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-300 border border-cyan-500/30">
                ACTIVE
              </span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Auto Router balancing health, observed latency, and free tiers across all 34+ providers.
            </p>
          </div>

          {/* FAST */}
          <div
            onClick={() => navigate('/gateway/models?capability=fast')}
            className="p-4 rounded-xl bg-slate-950 border border-slate-800 hover:border-amber-500/50 cursor-pointer transition-all space-y-2"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold text-amber-400 flex items-center gap-1.5">
                <Zap className="w-3.5 h-3.5" />
                fast
              </span>
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/30">
                &lt; 500ms
              </span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Lowest latency lightweight deployments for smoke tests, simple edits, and instant responses.
            </p>
          </div>

          {/* SMART */}
          <div
            onClick={() => navigate('/gateway/models?capability=reasoning')}
            className="p-4 rounded-xl bg-slate-950 border border-slate-800 hover:border-purple-500/50 cursor-pointer transition-all space-y-2"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold text-purple-400 flex items-center gap-1.5">
                <Brain className="w-3.5 h-3.5" />
                smart
              </span>
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-300 border border-purple-500/30">
                REASONING
              </span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              High-cognition models for complex architecture decomposition, planning, and debugging.
            </p>
          </div>

          {/* CODER */}
          <div
            onClick={() => navigate('/gateway/models?capability=coding')}
            className="p-4 rounded-xl bg-slate-950 border border-slate-800 hover:border-emerald-500/50 cursor-pointer transition-all space-y-2"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold text-emerald-400 flex items-center gap-1.5">
                <Code2 className="w-3.5 h-3.5" />
                coder
              </span>
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/30">
                SPECIALIZED
              </span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Code-specialized deployments for production implementations without placeholders.
            </p>
          </div>
        </div>
      </div>

      {/* Live Routing Trace Preview */}
      <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-cyan-400" />
            <h2 className="text-sm font-mono font-bold text-slate-200 uppercase">
              Live Routing Traces (LiteLLM Execution Stream)
            </h2>
          </div>
          <button
            onClick={() => navigate('/gateway/inspector')}
            className="text-xs font-mono text-cyan-400 hover:underline flex items-center gap-1"
          >
            <span>Open Routing Inspector</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>

        {recentTraces.length === 0 ? (
          <div className="p-8 text-center text-xs font-mono text-slate-500 bg-slate-950/60 rounded-xl border border-slate-800">
            No routing traces recorded yet. Requests dispatched to /v1/chat/completions will stream here live.
          </div>
        ) : (
          <div className="space-y-2">
            {recentTraces.map((trace) => (
              <div
                key={trace.trace_id}
                onClick={() => navigate('/gateway/inspector')}
                className="flex items-center justify-between p-3 rounded-xl bg-slate-950 border border-slate-800 hover:border-slate-700 cursor-pointer transition-colors text-xs font-mono"
              >
                <div className="flex items-center gap-3">
                  <span className="px-2 py-0.5 rounded bg-cyan-950/40 text-cyan-400 border border-cyan-800/40 font-bold uppercase">
                    {trace.logical_mode}
                  </span>
                  <span className="text-slate-300">{trace.selected_provider}</span>
                  <span className="text-slate-500">/</span>
                  <span className="text-slate-400 truncate max-w-xs">{trace.selected_model}</span>
                </div>

                <div className="flex items-center gap-4 text-slate-400">
                  <span>{trace.latency_ms} ms</span>
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                      trace.status === 'SUCCESS'
                        ? 'bg-emerald-950/30 text-emerald-400 border border-emerald-800/30'
                        : trace.status === 'RETRY'
                        ? 'bg-amber-950/30 text-amber-400 border border-amber-800/30'
                        : 'bg-red-950/30 text-red-400 border border-red-800/30'
                    }`}
                  >
                    {trace.status}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
