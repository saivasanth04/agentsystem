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
  PlayCircle,
  PlusCircle,
  AlertTriangle,
  FolderKanban,
  ArrowUpRight,
  RotateCcw,
  Trash2,
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
      setRecentSessions(sessionsData);
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
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold font-sans text-content-primary tracking-tight">
            Mission Control Dashboard
          </h1>
          <p className="text-xs font-mono text-content-secondary mt-1">
            Real-time multi-agent execution telemetry, verdict rates, and system health
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={loadData}
            className="p-2 rounded-xl bg-bg-panel hover:bg-bg-elevated border border-border-subtle text-content-secondary hover:text-content-primary transition-colors"
            title="Refresh Metrics"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={() => navigate('/tasks/new')}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-accent-primary hover:bg-indigo-600 text-white font-semibold text-xs shadow-lg shadow-accent-primary/20 transition-all font-mono"
          >
            <PlusCircle className="w-4 h-4" />
            Launch New Task
          </button>
        </div>
      </div>

      {/* KPI Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        {/* Total Sessions */}
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-md flex flex-col justify-between">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-mono text-content-muted uppercase">Total Runs</span>
            <FolderKanban className="w-4 h-4 text-accent-primary" />
          </div>
          <span className="text-2xl font-bold font-mono text-content-primary">
            {kpis?.total_sessions ?? 0}
          </span>
          <span className="text-[10px] font-mono text-content-secondary mt-1">Orchestrated workflows</span>
        </div>

        {/* PASS Rate */}
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-md flex flex-col justify-between">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-mono text-content-muted uppercase">Pass Rate</span>
            <CheckCircle2 className="w-4 h-4 text-accent-success" />
          </div>
          <span className="text-2xl font-bold font-mono text-accent-success">
            {kpis?.pass_rate_pct ?? 0}%
          </span>
          <span className="text-[10px] font-mono text-content-secondary mt-1">Ground-truth verified</span>
        </div>

        {/* Avg Tokens */}
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-md flex flex-col justify-between">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-mono text-content-muted uppercase">Avg Tokens/Run</span>
            <Cpu className="w-4 h-4 text-accent-info" />
          </div>
          <span className="text-2xl font-bold font-mono text-content-primary">
            {((kpis?.avg_tokens_per_session ?? 0) / 1000).toFixed(1)}k
          </span>
          <span className="text-[10px] font-mono text-content-secondary mt-1">Prompt + Completion</span>
        </div>

        {/* Total Cost */}
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-md flex flex-col justify-between">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-mono text-content-muted uppercase">Cumulative Cost</span>
            <DollarSign className="w-4 h-4 text-accent-warning" />
          </div>
          <span className="text-2xl font-bold font-mono text-accent-warning">
            ${(kpis?.total_cost_usd ?? 0).toFixed(4)}
          </span>
          <span className="text-[10px] font-mono text-content-secondary mt-1">USD across all tiers</span>
        </div>

        {/* Active Runs */}
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-md flex flex-col justify-between">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-mono text-content-muted uppercase">Active Runs</span>
            <Activity className="w-4 h-4 text-accent-info animate-pulse" />
          </div>
          <span className="text-2xl font-bold font-mono text-accent-info">
            {kpis?.active_runs_count ?? 0}
          </span>
          <span className="text-[10px] font-mono text-content-secondary mt-1">Live parallel DAGs</span>
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
        <div className="lg:col-span-2 bg-bg-panel border border-border-subtle rounded-xl p-5 shadow-lg space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-border-subtle">
            <h3 className="text-sm font-bold font-mono text-content-primary uppercase">
              Recent Orchestration Runs
            </h3>
            <button
              onClick={() => navigate('/sessions')}
              className="text-xs font-mono text-accent-primary hover:underline flex items-center gap-1"
            >
              View All <ArrowUpRight className="w-3.5 h-3.5" />
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="text-content-muted border-b border-border-subtle/50 pb-2">
                  <th className="py-2">Session ID</th>
                  <th className="py-2">Task Request</th>
                  <th className="py-2">Status</th>
                  <th className="py-2">Verdict</th>
                  <th className="py-2">Cost</th>
                  <th className="py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle/30">
                {recentSessions.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-8 text-center text-content-muted">
                      No sessions recorded yet. Launch your first task above!
                    </td>
                  </tr>
                ) : (
                  recentSessions.map((s) => (
                    <tr
                      key={s.session_id}
                      onClick={() => navigate(`/sessions/${s.session_id}`)}
                      className="hover:bg-bg-elevated/60 cursor-pointer transition-colors group"
                    >
                      <td className="py-3 font-bold text-accent-primary">
                        {s.session_id.slice(0, 10)}...
                      </td>
                      <td className="py-3 max-w-xs truncate text-content-primary font-sans font-medium">
                        {s.user_request}
                      </td>
                      <td className="py-3">
                        <StatusBadge status={s.status} size="sm" />
                      </td>
                      <td className="py-3">
                        <StatusBadge status={s.verdict} size="sm" />
                      </td>
                      <td className="py-3 text-content-secondary">
                        ${s.total_cost_usd.toFixed(4)}
                      </td>
                      <td className="py-3 text-right">
                        {s.status === 'STOPPED' || s.status === 'FAILED' ? (
                          <button
                            onClick={(e) => handleResume(s.session_id, e)}
                            className="px-2 py-1 rounded bg-accent-primary/20 hover:bg-accent-primary/40 text-accent-primary text-[10px] font-mono border border-accent-primary/40 flex items-center gap-1 ml-auto"
                            title="Resume Crashed Session"
                          >
                            <RotateCcw className="w-3 h-3" />
                            Resume
                          </button>
                        ) : (
                          <span className="text-content-muted group-hover:text-accent-primary text-[11px]">
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
        <div className="lg:col-span-1 bg-bg-panel border border-border-subtle rounded-xl p-5 shadow-lg space-y-3">
          <div className="flex items-center justify-between pb-3 border-b border-border-subtle">
            <h3 className="text-sm font-bold font-mono text-content-primary uppercase flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-amber-400" />
              System Alerts Feed
            </h3>
          </div>

          <div className="space-y-2.5 max-h-80 overflow-y-auto">
            {(!kpis?.alerts || kpis.alerts.length === 0) ? (
              <div className="text-center py-10 text-xs font-mono text-content-muted">
                All systems nominal. No circuit breakers or budget warnings triggered.
              </div>
            ) : (
              kpis.alerts.map((alert) => (
                <div
                  key={alert.id}
                  className="p-3 rounded-lg bg-bg-base border border-border-subtle text-xs space-y-1"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-500/30">
                      {alert.type}
                    </span>
                    <span className="text-[10px] font-mono text-content-muted">
                      {new Date(alert.timestamp).toLocaleTimeString()}
                    </span>
                  </div>
                  <p className="text-content-secondary font-mono text-[11px] leading-relaxed">
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
