import React from 'react';
import { ModelTier } from '../types/orchestrator';
import { Sparkles, Zap, Brain, Code2, ShieldCheck, CheckCircle2 } from 'lucide-react';

interface ModelSelectorProps {
  selectedTier: ModelTier;
  onChangeTier: (tier: ModelTier) => void;
  roleOverrides?: Record<string, string>;
  onRoleOverrideChange?: (role: string, mode: string) => void;
  className?: string;
}

export const ModelSelector: React.FC<ModelSelectorProps> = ({
  selectedTier,
  onChangeTier,
  roleOverrides = {},
  onRoleOverrideChange,
  className = '',
}) => {
  // Normalize tier to logical mode
  const currentMode = String(selectedTier).toLowerCase();
  const normalizedTier =
    currentMode === 'fast'
      ? 'fast'
      : currentMode === 'smart' || currentMode === 'frontier' || currentMode === 'reasoning'
      ? 'smart'
      : currentMode === 'coder' || currentMode === 'coding'
      ? 'coder'
      : 'auto';

  const modes: Array<{
    id: ModelTier;
    key: string;
    label: string;
    strategy: string;
    desc: string;
    icon: any;
    accentBorder: string;
    accentBg: string;
    badgeColor: string;
  }> = [
    {
      id: 'auto',
      key: 'auto',
      label: 'Auto',
      strategy: 'Intelligent Router',
      desc: 'Automatic dynamic routing via LiteLLM Auto Router, balancing latency, health, and free tiers.',
      icon: Sparkles,
      accentBorder: 'border-cyan-500/50 hover:border-cyan-400',
      accentBg: 'bg-cyan-950/20 text-cyan-400',
      badgeColor: 'bg-cyan-500/10 text-cyan-300 border-cyan-500/30',
    },
    {
      id: 'fast',
      key: 'fast',
      label: 'Fast',
      strategy: 'Lowest Latency',
      desc: 'Sub-second responsive execution optimized for simple tasks, smoke tests, and low-latency edits.',
      icon: Zap,
      accentBorder: 'border-amber-500/50 hover:border-amber-400',
      accentBg: 'bg-amber-950/20 text-amber-400',
      badgeColor: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
    },
    {
      id: 'smart',
      key: 'smart',
      label: 'Smart',
      strategy: 'Highest Reasoning',
      desc: 'Maximum cognitive depth for architecture, complex refactoring, and root-cause analysis.',
      icon: Brain,
      accentBorder: 'border-purple-500/50 hover:border-purple-400',
      accentBg: 'bg-purple-950/20 text-purple-400',
      badgeColor: 'bg-purple-500/10 text-purple-300 border-purple-500/30',
    },
    {
      id: 'coder',
      key: 'coder',
      label: 'Coder',
      strategy: 'Code Specialized',
      desc: 'Targets code-specialized models for syntax accuracy, modularity, and automated unit test generation.',
      icon: Code2,
      accentBorder: 'border-emerald-500/50 hover:border-emerald-400',
      accentBg: 'bg-emerald-950/20 text-emerald-400',
      badgeColor: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
    },
  ];

  const roles = [
    { role: 'planner', name: 'Planner Agent', defaultMode: 'smart' },
    { role: 'specification', name: 'Specification Agent', defaultMode: 'smart' },
    { role: 'architecture', name: 'Architecture Agent', defaultMode: 'smart' },
    { role: 'coder', name: 'Coder Agent', defaultMode: 'coder' },
    { role: 'tester', name: 'Tester Agent', defaultMode: 'coder' },
    { role: 'reviewer', name: 'Reviewer Agent', defaultMode: 'smart' },
  ];

  return (
    <div className={`space-y-4 ${className}`}>
      {/* 4 Logical Execution Modes Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {modes.map((m) => {
          const Icon = m.icon;
          const isSelected = normalizedTier === m.key;

          return (
            <div
              key={m.key}
              onClick={() => onChangeTier(m.id)}
              className={`relative p-4 rounded-xl border cursor-pointer transition-all duration-200 select-none ${
                isSelected
                  ? 'border-cyan-500 bg-slate-900 shadow-lg shadow-cyan-950/40 ring-1 ring-cyan-500'
                  : 'border-slate-800 bg-slate-950/80 hover:bg-slate-900 ' + m.accentBorder
              }`}
            >
              {isSelected && (
                <div className="absolute top-3 right-3 text-cyan-400">
                  <CheckCircle2 className="w-4 h-4 fill-cyan-500/20" />
                </div>
              )}

              <div className="flex items-center gap-2.5 mb-2">
                <div className={`p-2 rounded-lg border border-slate-700/60 ${m.accentBg}`}>
                  <Icon className="w-4 h-4" />
                </div>
                <div>
                  <div className="text-sm font-bold font-mono text-slate-100">{m.label}</div>
                  <span
                    className={`inline-block text-[10px] font-mono px-1.5 py-0.5 rounded border uppercase font-medium ${m.badgeColor}`}
                  >
                    {m.strategy}
                  </span>
                </div>
              </div>

              <p className="text-[11px] text-slate-400 leading-relaxed mt-2">{m.desc}</p>
            </div>
          );
        })}
      </div>

      {/* Per-Role Swarm Logical Mode Allocation */}
      {onRoleOverrideChange && (
        <div className="bg-slate-950 p-4 rounded-xl border border-slate-800 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono font-bold text-slate-400 uppercase flex items-center gap-2">
              <ShieldCheck className="w-3.5 h-3.5 text-cyan-400" />
              Per-Role Agent Logical Mode Allocation
            </span>
            <span className="text-[11px] font-mono text-slate-500">
              Gateway routes automatically via LiteLLM
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
            {roles.map((r) => {
              const currentVal = (roleOverrides[r.role] || r.defaultMode).toLowerCase();
              return (
                <div
                  key={r.role}
                  className="flex items-center justify-between gap-2 bg-slate-900/90 p-2.5 rounded-lg border border-slate-800"
                >
                  <span className="text-xs font-medium text-slate-200 truncate">{r.name}</span>
                  <select
                    value={currentVal}
                    onChange={(e) => onRoleOverrideChange(r.role, e.target.value)}
                    className="bg-slate-950 border border-slate-700 rounded px-2.5 py-1 text-xs font-mono font-semibold text-cyan-300 focus:outline-none focus:border-cyan-500 cursor-pointer"
                  >
                    <option value="auto">auto</option>
                    <option value="fast">fast</option>
                    <option value="smart">smart</option>
                    <option value="coder">coder</option>
                  </select>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
