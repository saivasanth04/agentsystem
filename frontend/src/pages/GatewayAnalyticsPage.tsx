import React, { useState, useEffect } from 'react';
import { gatewayApi } from '../api/gatewayApi';
import { GatewayAnalytics } from '../types/gateway';
import {
  TrendingUp,
  BarChart3,
  PieChart,
  Clock,
  Layers,
  Sparkles,
  Zap,
  Brain,
  Code2,
  Database,
  ShieldCheck,
  RefreshCw,
} from 'lucide-react';

interface GatewayAnalyticsPageProps {
  navigate: (route: string) => void;
}

export const GatewayAnalyticsPage: React.FC<GatewayAnalyticsPageProps> = () => {
  const [analytics, setAnalytics] = useState<GatewayAnalytics | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchAnalytics = async () => {
    try {
      const data = await gatewayApi.getAnalytics();
      setAnalytics(data);
    } catch (err: any) {
      console.error('Failed to load analytics:', err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchAnalytics();
    const interval = setInterval(fetchAnalytics, 6000);
    return () => clearInterval(interval);
  }, []);

  const modeIcons: Record<string, any> = {
    auto: Sparkles,
    fast: Zap,
    smart: Brain,
    coder: Code2,
  };

  const totalModeReqs = analytics ? Object.values(analytics.mode_distribution).reduce((a, b) => a + b, 0) || 1 : 1;
  const totalProvReqs = analytics ? Object.values(analytics.provider_distribution).reduce((a, b) => a + b, 0) || 1 : 1;

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-5 bg-slate-900 border border-slate-800 rounded-2xl shadow-xl">
        <div className="flex items-center gap-3.5">
          <div className="w-11 h-11 rounded-xl bg-gradient-to-tr from-pink-500 via-rose-500 to-amber-500 flex items-center justify-center shadow-lg shadow-rose-500/20">
            <TrendingUp className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold font-mono text-slate-100">
              Gateway Analytics & Telemetry
            </h1>
            <p className="text-xs text-slate-400">
              Provider Traffic Distribution • Latency Percentiles • Token Efficiency & Free Tier Savings
            </p>
          </div>
        </div>

        <button
          onClick={fetchAnalytics}
          className="flex items-center gap-2 px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-mono font-medium text-slate-200 border border-slate-700 cursor-pointer"
        >
          <RefreshCw className="w-3.5 h-3.5 text-cyan-400" />
          <span>Refresh Analytics</span>
        </button>
      </div>

      {/* Latency Percentiles Banner */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="p-4 bg-slate-900 border border-slate-800 rounded-xl space-y-1">
          <div className="flex items-center justify-between text-slate-400 font-mono text-xs">
            <span className="uppercase">Median Latency (p50)</span>
            <Clock className="w-3.5 h-3.5 text-emerald-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-emerald-400">
            {analytics ? `${analytics.latency_percentiles.p50_ms} ms` : '--'}
          </div>
          <p className="text-[10px] text-slate-500 font-mono">50% of requests complete faster than this</p>
        </div>

        <div className="p-4 bg-slate-900 border border-slate-800 rounded-xl space-y-1">
          <div className="flex items-center justify-between text-slate-400 font-mono text-xs">
            <span className="uppercase">90th Percentile (p90)</span>
            <Clock className="w-3.5 h-3.5 text-amber-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-amber-400">
            {analytics ? `${analytics.latency_percentiles.p90_ms} ms` : '--'}
          </div>
          <p className="text-[10px] text-slate-500 font-mono">90% of requests complete within this budget</p>
        </div>

        <div className="p-4 bg-slate-900 border border-slate-800 rounded-xl space-y-1">
          <div className="flex items-center justify-between text-slate-400 font-mono text-xs">
            <span className="uppercase">99th Percentile (p99)</span>
            <Clock className="w-3.5 h-3.5 text-rose-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-rose-400">
            {analytics ? `${analytics.latency_percentiles.p99_ms} ms` : '--'}
          </div>
          <p className="text-[10px] text-slate-500 font-mono">Tail latency edge threshold</p>
        </div>
      </div>

      {/* Charts Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Logical Execution Mode Breakdown */}
        <div className="p-5 bg-slate-900 border border-slate-800 rounded-2xl space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-mono font-bold text-slate-200 uppercase flex items-center gap-2">
              <PieChart className="w-4 h-4 text-cyan-400" />
              Logical Mode Distribution
            </h3>
            <span className="text-[11px] font-mono text-slate-500">Agent request breakdown</span>
          </div>

          <div className="space-y-3 pt-2">
            {analytics &&
              Object.entries(analytics.mode_distribution).map(([mode, count]) => {
                const Icon = modeIcons[mode] || Sparkles;
                const pct = Math.round((count / totalModeReqs) * 100);
                return (
                  <div key={mode} className="space-y-1">
                    <div className="flex items-center justify-between text-xs font-mono">
                      <span className="flex items-center gap-2 text-slate-300 uppercase font-bold">
                        <Icon className="w-3.5 h-3.5 text-cyan-400" />
                        {mode}
                      </span>
                      <span className="text-slate-400">
                        {count} reqs ({pct}%)
                      </span>
                    </div>
                    <div className="w-full h-2 rounded-full bg-slate-950 overflow-hidden">
                      <div
                        className={`h-full ${
                          mode === 'fast'
                            ? 'bg-amber-400'
                            : mode === 'smart'
                            ? 'bg-purple-400'
                            : mode === 'coder'
                            ? 'bg-emerald-400'
                            : 'bg-cyan-400'
                        }`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
          </div>
        </div>

        {/* Provider Traffic Distribution */}
        <div className="p-5 bg-slate-900 border border-slate-800 rounded-2xl space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-mono font-bold text-slate-200 uppercase flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-indigo-400" />
              Provider Request Distribution
            </h3>
            <span className="text-[11px] font-mono text-slate-500">34+ providers load-balanced</span>
          </div>

          <div className="space-y-3 pt-2 max-h-[300px] overflow-y-auto pr-1">
            {analytics &&
              Object.entries(analytics.provider_distribution)
                .sort((a, b) => b[1] - a[1])
                .map(([provider, count]) => {
                  const pct = Math.round((count / totalProvReqs) * 100);
                  return (
                    <div key={provider} className="space-y-1">
                      <div className="flex items-center justify-between text-xs font-mono">
                        <span className="text-slate-300 font-semibold">{provider}</span>
                        <span className="text-slate-400">
                          {count} reqs ({pct}%)
                        </span>
                      </div>
                      <div className="w-full h-2 rounded-full bg-slate-950 overflow-hidden">
                        <div className="h-full bg-indigo-500" style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  );
                })}
          </div>
        </div>
      </div>
    </div>
  );
};
