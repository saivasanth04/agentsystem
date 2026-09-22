import React, { useEffect, useState } from 'react';
import { DashboardKPIs, SessionSummary } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import { StatusBadge } from '../components/StatusBadge';
import { VerdictDonut, CostTimeline, AgentHeatmap } from '../components/MetricsCharts';
import {
  Activity,
  CheckCircle2,
  Cpu,
  DollarSign,
  PlusCircle,
  AlertTriangle,
  FolderKanban,
  ArrowUpRight,
  RotateCcw,
  RefreshCw
} from 'lucide-react';

interface DashboardPageProps {
  navigate: (route: string) => void;
}

export const DashboardPage: React.FC<DashboardPageProps> = ({ navigate }) => {
  const [kpis, setKpis] = useState<DashboardKPIs | null>(null);
  const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);

  const loadData = async () => {
    setLoading(true);
    try {
      const [kpiData, sessionsData] = await Promise.all([
        orchestratorApi.getDashboardKPIs(),
        orchestratorApi.getSessions({ limit: 8 }),
      ]);
      setKpis(kpiData);
      setRecentSessions(sessionsData.sessions || []);
    } catch (err) {
      console.error('Failed to load dashboard metrics:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    const timer = setInterval(loadData, 8000);
    return () => clearInterval(timer);
  }, []);

  const handleResume = async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await orchestratorApi.resumeSession(sessionId);
      navigate(`/sessions/${sessionId}/live`);
    } catch (err: any) {
      alert(`Resume failed: ${err.message}`);
    }
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold font-sans text-slate-100 tracking-tight">
            Mission Control Dashboard
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-1">
            Real-time multi-agent execution telemetry, verdict rates, and workspace system health
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={loadData}
            className="p-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-slate-100 transition-colors"
            title="Refresh Metrics"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-cyan-400' : ''}`} />
          </button>
          <button
            onClick={() => navigate('/tasks/new')}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold text-xs shadow-lg shadow-cyan-500/20 transition-all font-mono"
          >
            <PlusCircle className="w-4 h-4" />
            Launch New Task
          </button>
        </div>
      </div>

      {/* KPI Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        {/* Total Sessions */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-md flex flex-col justify-between">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-mono text-slate-400 uppercase">Total Runs</span>
            <FolderKanban className="w-4 h-4 text-cyan-400" />
          </div>
          <span className="text-2xl font-bold font-mono text-slate-100">
            {kpis?.total_sessions ?? 0}
          </span>
          <span className="text-[10px] font-mono text-slate-500 mt-1">Orchestrated workflows</span>
        </div>

        {/* PASS Rate */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-md flex flex-col justify-between">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-mono text-slate-400 uppercase">Pass Rate</span>
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          </div>
          <span className="text-2xl font-bold font-mono text-emerald-400">
            {kpis?.pass_rate_pct ?? 0}%
          </span>
          <span className="text-[10px] font-mono text-slate-500 mt-1">Ground-truth verified</span>
        </div>

        {/* Avg Tokens */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-md flex flex-col justify-between">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-mono text-slate-400 uppercase">Avg Tokens/Run</span>
            <Cpu className="w-4 h-4 text-cyan-400" />
          </div>
          <span className="text-2xl font-bold font-mono text-slate-100">
            {((kpis?.avg_tokens_per_session ?? 0) / 1000).toFixed(1)}k
          </span>
          <span className="text-[10px] font-mono text-slate-500 mt-1">Prompt + Completion</span>
        </div>

        {/* Total Cost */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-md flex flex-col justify-between">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-mono text-slate-400 uppercase">Cumulative Cost</span>
            <DollarSign className="w-4 h-4 text-amber-400" />
          </div>
          <span className="text-2xl font-bold font-mono text-amber-400">
            ${(kpis?.total_cost_usd ?? 0).toFixed(4)}
          </span>
          <span className="text-[10px] font-mono text-slate-500 mt-1">USD across all tiers</span>
        </div>

        {/* Active Runs */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-md flex flex-col justify-between">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-mono text-slate-400 uppercase">Active Runs</span>
            <Activity className="w-4 h-4 text-cyan-400 animate-pulse" />
          </div>
          <span className="text-2xl font-bold font-mono text-cyan-400">
            {kpis?.active_runs_count ?? 0}
          </span>
          <span className="text-[10px] font-mono text-slate-500 mt-1">Live parallel DAGs</span>
        </div>
      </div>

      {/* Visual Analytics Charts Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Verdict Donut */}
        <div className="lg:col-span-1">
          <VerdictDonut verdicts={kpis?.verdict_distribution || { pass: 0, fail: 0, undecided: 0 }} />
        </div>

        {/* Cost & Token Timeline */}
        <div className="lg:col-span-2">
          <CostTimeline data={kpis?.cost_timeline || []} />
        </div>
      </div>

      {/* Agent Activity Heatmap */}
      <AgentHeatmap data={kpis?.agent_activity_heatmap || []} />

      {/* Recent Sessions Table & Alerts Feed */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recent Sessions */}
        <div className="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-slate-800">
            <h3 className="text-sm font-bold font-mono text-slate-100 uppercase">
              Recent Orchestration Runs
            </h3>
            <button
              onClick={() => navigate('/sessions')}
              className="text-xs font-mono text-cyan-400 hover:underline flex items-center gap-1"
            >
              View All <ArrowUpRight className="w-3.5 h-3.5" />
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="text-slate-400 border-b border-slate-800 pb-2">
                  <th className="py-2">Session ID</th>
                  <th className="py-2">Task Request</th>
                  <th className="py-2">Status</th>
                  <th className="py-2">Verdict</th>
                  <th className="py-2">Cost</th>
                  <th className="py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/40">
                {recentSessions.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-8 text-center text-slate-500">
                      No sessions recorded yet. Launch your first task above!
                    </td>
                  </tr>
                ) : (
                  recentSessions.map((s) => (
                    <tr
                      key={s.session_id}
                      onClick={() => navigate(`/sessions/${s.session_id}`)}
                      className="hover:bg-slate-800/40 cursor-pointer transition-colors group"
                    >
                      <td className="py-3 font-bold text-cyan-400">
                        {s.session_id.slice(0, 10)}...
                      </td>
                      <td className="py-3 max-w-xs truncate text-slate-200 font-sans font-medium">
                        {s.user_request}
                      </td>
                      <td className="py-3">
                        <StatusBadge status={s.status} size="sm" />
                      </td>
                      <td className="py-3">
                        <StatusBadge status={s.verdict} size="sm" />
                      </td>
                      <td className="py-3 text-slate-400">
                        ${s.total_cost_usd.toFixed(4)}
                      </td>
                      <td className="py-3 text-right">
                        {s.status === 'STOPPED' || s.status === 'FAILED' ? (
                          <button
                            onClick={(e) => handleResume(s.session_id, e)}
                            className="px-2 py-1 rounded bg-cyan-500/20 hover:bg-cyan-500/40 text-cyan-300 text-[10px] font-mono border border-cyan-500/40 flex items-center gap-1 ml-auto"
                            title="Resume Crashed Session"
                          >
                            <RotateCcw className="w-3 h-3" />
                            Resume
                          </button>
                        ) : (
                          <span className="text-slate-500 group-hover:text-cyan-400 text-[11px]">
                            Inspect →
                          </span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Alerts & Circuit Breakers Feed */}
        <div className="lg:col-span-1 bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg space-y-3">
          <div className="flex items-center justify-between pb-3 border-b border-slate-800">
            <h3 className="text-sm font-bold font-mono text-slate-100 uppercase flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-amber-400" />
              System Alerts Feed
            </h3>
          </div>

          <div className="space-y-2.5 max-h-80 overflow-y-auto">
            {(!kpis?.alerts || kpis.alerts.length === 0) ? (
              <div className="text-center py-10 text-xs font-mono text-slate-500">
                All systems nominal. No circuit breakers or budget warnings triggered.
              </div>
            ) : (
              kpis.alerts.map((alert) => (
                <div
                  key={alert.id}
                  className="p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs space-y-1"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-500/30">
                      {alert.type}
                    </span>
                    <span className="text-[10px] font-mono text-slate-500">
                      {new Date(alert.timestamp).toLocaleTimeString()}
                    </span>
                  </div>
                  <p className="text-slate-300 font-mono text-[11px] leading-relaxed">
                    {alert.message}
                  </p>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
