import React from 'react';
import { ModelTier } from '../types/orchestrator';
import { Cpu, Zap, Brain, Sparkles, ChevronDown } from 'lucide-react';

interface ModelSelectorProps {
  selectedTier: ModelTier;
  onChangeTier: (tier: ModelTier) => void;
  roleOverrides?: Record<string, string>;
  onRoleOverrideChange?: (role: string, model: string) => void;
  className?: string;
}

export const ModelSelector: React.FC<ModelSelectorProps> = ({
  selectedTier,
  onChangeTier,
  roleOverrides = {},
  onRoleOverrideChange,
  className = '',
}) => {
  const tiers: Array<{ id: ModelTier; label: string; desc: string; icon: any; color: string }> = [
    {
      id: 'FAST',
      label: 'Fast Tier',
      desc: 'Sub-second lightweight execution (e.g. Gemini Flash / Haiku)',
      icon: Zap,
      color: 'text-amber-400 border-amber-500/40 bg-amber-950/20',
    },
    {
      id: 'BALANCED',
      label: 'Balanced Tier',
      desc: 'Balanced reasoning & throughput (e.g. Sonnet 3.7 / GPT-4o)',
      icon: Brain,
      color: 'text-indigo-400 border-indigo-500/40 bg-indigo-950/20',
    },
    {
      id: 'FRONTIER',
      label: 'Frontier Tier',
      desc: 'Deep reasoning & high-stakes coding (e.g. Claude 3.7 Thinking / O3)',
      icon: Sparkles,
      color: 'text-purple-400 border-purple-500/40 bg-purple-950/20',
    },
  ];

  const roles = [
    { role: 'planner', name: 'Planner Agent', defaultModel: 'claude-3-7-sonnet' },
    { role: 'coder', name: 'Coder Agent', defaultModel: 'claude-3-7-sonnet' },
    { role: 'tester', name: 'Tester Agent', defaultModel: 'claude-3-5-haiku' },
    { role: 'reviewer', name: 'Reviewer Agent', defaultModel: 'claude-3-7-sonnet' },
  ];

  return (
    <div className={`space-y-4 ${className}`}>
      {/* Tier Selection Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {tiers.map((t) => {
          const Icon = t.icon;
          const isSelected = selectedTier === t.id;

          return (
            <div
              key={t.id}
              onClick={() => onChangeTier(t.id)}
              className={`p-3.5 rounded-xl border cursor-pointer transition-all ${
                isSelected
                  ? 'border-accent-primary bg-bg-elevated shadow-lg ring-1 ring-accent-primary'
                  : 'border-border-subtle bg-bg-panel hover:bg-bg-elevated'
              }`}
            >
              <div className="flex items-center gap-2 mb-1.5">
                <div className={`p-1.5 rounded-lg border ${t.color}`}>
                  <Icon className="w-4 h-4" />
                </div>
                <span className="text-xs font-mono font-bold text-content-primary">
                  {t.label}
                </span>
              </div>
              <p className="text-[11px] text-content-secondary leading-relaxed">
                {t.desc}
              </p>
            </div>
          );
        })}
      </div>

      {/* Role Overrides Grid */}
      {onRoleOverrideChange && (
        <div className="bg-bg-panel p-4 rounded-xl border border-border-subtle space-y-3">
          <span className="text-xs font-mono font-bold text-content-muted uppercase block">
            Per-Role Model Routing Overrides
          </span>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {roles.map((r) => (
              <div key={r.role} className="flex items-center justify-between gap-2 bg-bg-base p-2.5 rounded-lg border border-border-subtle">
                <span className="text-xs font-medium text-content-primary truncate">{r.name}</span>
                <select
                  value={roleOverrides[r.role] || r.defaultModel}
                  onChange={(e) => onRoleOverrideChange(r.role, e.target.value)}
                  className="bg-bg-elevated border border-border-subtle rounded px-2 py-1 text-xs font-mono text-content-primary focus:outline-none focus:border-accent-primary"
                >
                  <option value="claude-3-7-sonnet">claude-3-7-sonnet</option>
                  <option value="claude-3-5-haiku">claude-3-5-haiku</option>
                  <option value="gpt-4o">gpt-4o</option>
                  <option value="gemini-2.0-flash">gemini-2.0-flash</option>
                  <option value="o3-mini">o3-mini</option>
                </select>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
