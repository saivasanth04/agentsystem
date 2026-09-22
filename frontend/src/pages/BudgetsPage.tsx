import React, { useState, useEffect } from 'react';
import { BudgetStats } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import { TokenMeter } from '../components/TokenMeter';
import {
  CircleDollarSign,
  AlertTriangle,
  RefreshCw,
} from 'lucide-react';

interface BudgetsPageProps {
  navigate: (route: string) => void;
}

export const BudgetsPage: React.FC<BudgetsPageProps> = () => {
  const [stats, setStats] = useState<BudgetStats | null>(null);
  const [loading, setLoading] = useState(true);

  const loadBudgets = async () => {
    setLoading(true);
    try {
      const data = await orchestratorApi.getBudgets();
      const mappedStats: BudgetStats = {
        total_cost_usd: data.current_session_cost || 0,
        max_session_cost_usd: data.limits?.max_session_cost_usd || 5.0,
        total_tokens: 0,
        max_session_tokens: data.limits?.max_total_tokens || 500000,
        per_task_cost_limit_usd: 1.0,
        token_budget_used_pct: 0,
        cost_budget_used_pct: 0,
        tier_breakdown: {},
        history: data.ledger || [],
        exceeded_events: [],
      };
      setStats(mappedStats);
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
          <h1 className="text-2xl font-bold font-sans text-slate-100 tracking-tight flex items-center gap-2.5">
            <CircleDollarSign className="w-6 h-6 text-emerald-400" />
            Budget & Resource Ledger
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-1">
            Task-level token accounting, model tier cost breakdown, and circuit breaker policies
          </p>
        </div>

        <button
          onClick={loadBudgets}
          className="p-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-slate-100 transition-colors"
          title="Refresh Budgets"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-cyan-400' : ''}`} />
        </button>
      </div>

      {/* Top Budget Specifications Bar */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-md">
          <span className="text-[10px] font-mono text-slate-400 block mb-1">CUMULATIVE EXPENDITURE</span>
          <span className="text-2xl font-bold font-mono text-emerald-400">
            ${stats?.total_cost_usd?.toFixed(4) || '0.0000'}
          </span>
          <span className="text-[10px] font-mono text-slate-500 block mt-1">
            Max limit: ${stats?.max_session_cost_usd?.toFixed(2) || '5.00'}
          </span>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-md">
          <span className="text-[10px] font-mono text-slate-400 block mb-1">TOTAL CONSUMED TOKENS</span>
          <span className="text-2xl font-bold font-mono text-cyan-400">
            {Math.round((stats?.total_tokens || 0) / 1000)}k
          </span>
          <span className="text-[10px] font-mono text-slate-500 block mt-1">
            Max budget: {Math.round((stats?.max_session_tokens || 500000) / 1000)}k
          </span>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-md">
          <span className="text-[10px] font-mono text-slate-400 block mb-1">PER-TASK CEILING</span>
          <span className="text-2xl font-bold font-mono text-amber-400">
            ${stats?.per_task_cost_limit_usd?.toFixed(2) || '1.00'}
          </span>
          <span className="text-[10px] font-mono text-slate-500 block mt-1">
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
        <div className="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg space-y-4">
          <h3 className="text-sm font-bold font-mono text-slate-100 uppercase">
            Task-Level Cost & Token Ledger
          </h3>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead className="bg-slate-950 text-slate-400 uppercase border-b border-slate-800">
                <tr>
                  <th className="py-2.5 px-3">Session</th>
                  <th className="py-2.5 px-3">Task ID</th>
                  <th className="py-2.5 px-3">Model</th>
                  <th className="py-2.5 px-3">Prompt</th>
                  <th className="py-2.5 px-3">Completion</th>
                  <th className="py-2.5 px-3">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/40">
                {(!stats?.history || stats.history.length === 0) ? (
                  <tr>
                    <td colSpan={6} className="py-8 text-center text-slate-500">
                      No ledger transactions recorded yet.
                    </td>
                  </tr>
                ) : (
                  stats.history.map((entry) => (
                    <tr key={entry.id} className="hover:bg-slate-800/40 transition-colors">
                      <td className="py-2.5 px-3 font-bold text-cyan-400">{entry.session_id ? entry.session_id.slice(0, 8) : 'sys'}...</td>
                      <td className="py-2.5 px-3 font-bold">{entry.task_id || 'T-00'}</td>
                      <td className="py-2.5 px-3 text-slate-300">{entry.model}</td>
                      <td className="py-2.5 px-3">{entry.prompt_tokens}</td>
                      <td className="py-2.5 px-3">{entry.completion_tokens}</td>
                      <td className="py-2.5 px-3 text-emerald-400 font-semibold">${entry.cost_usd.toFixed(4)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Budget Exceeded / Halts Feed */}
        <div className="lg:col-span-1 bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg space-y-3">
          <h3 className="text-sm font-bold font-mono text-slate-100 uppercase flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-400" />
            Budget Policy Actions
          </h3>

          <div className="space-y-2.5">
            {(!stats?.exceeded_events || stats.exceeded_events.length === 0) ? (
              <div className="text-center py-10 text-xs font-mono text-slate-500">
                Zero budget violations. All sessions remained within configured policy bounds.
              </div>
            ) : (
              stats.exceeded_events.map((ev, i) => (
                <div key={i} className="bg-slate-950 p-3 rounded-xl border border-slate-800 text-xs space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-rose-950 text-rose-400 border border-rose-500/30 font-bold">
                      {ev.action_taken}
                    </span>
                    <span className="text-[10px] font-mono text-slate-500">{new Date(ev.timestamp).toLocaleTimeString()}</span>
                  </div>
                  <p className="text-slate-300 font-mono text-[11px]">{ev.reason}</p>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
