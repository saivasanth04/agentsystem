import React, { useState, useEffect } from 'react';
import { BudgetStats } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import { TokenMeter } from '../components/TokenMeter';
import {
  CircleDollarSign,
  Cpu,
  AlertTriangle,
  Flame,
  CheckCircle2,
  RefreshCw,
  Clock
} from 'lucide-react';

interface BudgetsPageProps {
  navigate: (route: string) => void;
}

export const BudgetsPage: React.FC<BudgetsPageProps> = ({ navigate }) => {
  const [stats, setStats] = useState<BudgetStats | null>(null);
  const [loading, setLoading] = useState(true);

  const loadBudgets = async () => {
    setLoading(true);
    try {
      const data = await orchestratorApi.getBudgets();
      setStats(data);
    } catch (err) {
      console.error('Failed to load budgets:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadBudgets();
  }, []);

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold font-sans text-content-primary tracking-tight flex items-center gap-2.5">
            <CircleDollarSign className="w-6 h-6 text-accent-success" />
            Budget & Resource Ledger
          </h1>
          <p className="text-xs font-mono text-content-secondary mt-1">
            Task-level token accounting, model tier cost breakdown, and circuit breaker policies
          </p>
        </div>

        <button
          onClick={loadBudgets}
          className="p-2 rounded-xl bg-bg-panel hover:bg-bg-elevated border border-border-subtle text-content-secondary hover:text-content-primary transition-colors"
          title="Refresh Budgets"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Top Budget Specifications Bar */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-md">
          <span className="text-[10px] font-mono text-content-muted block mb-1">CUMULATIVE EXPENDITURE</span>
          <span className="text-2xl font-bold font-mono text-accent-success">
            ${stats?.total_cost_usd?.toFixed(4) || '0.0000'}
          </span>
          <span className="text-[10px] font-mono text-content-muted block mt-1">
            Max limit: ${stats?.max_session_cost_usd?.toFixed(2) || '5.00'}
          </span>
        </div>

        <div className="bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-md">
          <span className="text-[10px] font-mono text-content-muted block mb-1">TOTAL CONSUMED TOKENS</span>
          <span className="text-2xl font-bold font-mono text-accent-info">
            {Math.round((stats?.total_tokens || 0) / 1000)}k
          </span>
          <span className="text-[10px] font-mono text-content-muted block mt-1">
            Max budget: {Math.round((stats?.max_session_tokens || 500000) / 1000)}k
          </span>
        </div>

        <div className="bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-md">
          <span className="text-[10px] font-mono text-content-muted block mb-1">PER-TASK CEILING</span>
          <span className="text-2xl font-bold font-mono text-accent-warning">
            ${stats?.per_task_cost_limit_usd?.toFixed(2) || '1.00'}
          </span>
          <span className="text-[10px] font-mono text-content-muted block mt-1">
            Hard cap per subtask execution
          </span>
        </div>
      </div>

      {/* Budget Meter Bar */}
      <TokenMeter
        currentCostUsd={stats?.total_cost_usd || 0}
        maxCostUsd={stats?.max_session_cost_usd || 5.0}
        currentTokens={stats?.total_tokens || 0}
        maxTokens={stats?.max_session_tokens || 500000}
      />

      {/* Exceeded Events & Cost Ledger Table */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Cost Ledger Table */}
        <div className="lg:col-span-2 bg-bg-panel border border-border-subtle rounded-xl p-5 shadow-lg space-y-4">
          <h3 className="text-sm font-bold font-mono text-content-primary uppercase">
            Task-Level Cost & Token Ledger
          </h3>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead className="bg-bg-elevated/70 text-content-muted uppercase border-b border-border-subtle">
                <tr>
                  <th className="py-2.5 px-3">Session</th>
                  <th className="py-2.5 px-3">Task ID</th>
                  <th className="py-2.5 px-3">Model</th>
                  <th className="py-2.5 px-3">Prompt</th>
                  <th className="py-2.5 px-3">Completion</th>
                  <th className="py-2.5 px-3">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle/30">
                {(!stats?.history || stats.history.length === 0) ? (
                  <tr>
                    <td colSpan={6} className="py-8 text-center text-content-muted">
                      No ledger transactions recorded yet.
                    </td>
                  </tr>
                ) : (
                  stats.history.map((entry) => (
                    <tr key={entry.id} className="hover:bg-bg-elevated/50 transition-colors">
                      <td className="py-2.5 px-3 font-bold text-accent-primary">{entry.session_id.slice(0, 8)}...</td>
                      <td className="py-2.5 px-3 font-bold">{entry.task_id}</td>
                      <td className="py-2.5 px-3 text-content-secondary">{entry.model}</td>
                      <td className="py-2.5 px-3">{entry.prompt_tokens}</td>
                      <td className="py-2.5 px-3">{entry.completion_tokens}</td>
                      <td className="py-2.5 px-3 text-accent-success font-semibold">${entry.cost_usd.toFixed(4)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Budget Exceeded / Halts Feed */}
        <div className="lg:col-span-1 bg-bg-panel border border-border-subtle rounded-xl p-5 shadow-lg space-y-3">
          <h3 className="text-sm font-bold font-mono text-content-primary uppercase flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-400" />
            Budget Policy Actions
          </h3>

          <div className="space-y-2.5">
            {(!stats?.exceeded_events || stats.exceeded_events.length === 0) ? (
              <div className="text-center py-10 text-xs font-mono text-content-muted">
                Zero budget violations. All sessions remained within configured policy bounds.
              </div>
            ) : (
              stats.exceeded_events.map((ev, i) => (
                <div key={i} className="bg-bg-base p-3 rounded-lg border border-border-subtle text-xs space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-rose-950 text-rose-400 border border-rose-500/30 font-bold">
                      {ev.action_taken}
                    </span>
                    <span className="text-[10px] font-mono text-content-muted">{new Date(ev.timestamp).toLocaleTimeString()}</span>
                  </div>
                  <p className="text-content-secondary font-mono text-[11px]">{ev.reason}</p>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
