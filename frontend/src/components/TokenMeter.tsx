import React from 'react';
import { DollarSign, Cpu, AlertTriangle } from 'lucide-react';

interface TokenMeterProps {
  currentTokens?: number;
  maxTokens?: number;
  currentCostUsd?: number;
  maxCostUsd?: number;
  className?: string;
  showLabels?: boolean;
}

export const TokenMeter: React.FC<TokenMeterProps> = ({
  currentTokens = 0,
  maxTokens = 500000,
  currentCostUsd = 0.0,
  maxCostUsd = 5.0,
  className = '',
  showLabels = true,
}) => {
  const safeCost = typeof currentCostUsd === 'number' && !isNaN(currentCostUsd) ? currentCostUsd : 0;
  const safeTokens = typeof currentTokens === 'number' && !isNaN(currentTokens) ? currentTokens : 0;
  const safeMaxCost = typeof maxCostUsd === 'number' && maxCostUsd > 0 ? maxCostUsd : 5.0;
  const safeMaxTokens = typeof maxTokens === 'number' && maxTokens > 0 ? maxTokens : 500000;

  const tokenPct = Math.min(100, Math.round((safeTokens / safeMaxTokens) * 100));
  const costPct = Math.min(100, Math.round((safeCost / safeMaxCost) * 100));

  const getBarColor = (pct: number) => {
    if (pct >= 100) return 'bg-rose-500 shadow-rose-950/40';
    if (pct >= 80) return 'bg-amber-500 shadow-amber-950/40';
    return 'bg-accent-primary shadow-indigo-950/40';
  };

  const formatTokens = (n: number) => {
    if (n >= 1000000) return `${(n / 1000000).toFixed(2)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return (n || 0).toString();
  };

  return (
    <div className={`space-y-3 bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-md ${className}`}>
      {/* Cost Progress */}
      <div className="space-y-1.5">
        {showLabels && (
          <div className="flex items-center justify-between text-xs font-mono">
            <span className="flex items-center gap-1 text-content-secondary font-medium">
              <DollarSign className="w-3.5 h-3.5 text-accent-success" />
              SESSION COST
            </span>
            <span className="font-bold text-content-primary">
              ${safeCost.toFixed(4)} <span className="text-content-muted">/ ${safeMaxCost.toFixed(2)}</span>
            </span>
          </div>
        )}
        <div className="w-full bg-bg-base h-2 rounded-full overflow-hidden border border-border-subtle/50">
          <div
            className={`h-full rounded-full transition-all duration-500 ${getBarColor(costPct)}`}
            style={{ width: `${costPct}%` }}
          />
        </div>
      </div>

      {/* Token Progress */}
      <div className="space-y-1.5">
        {showLabels && (
          <div className="flex items-center justify-between text-xs font-mono">
            <span className="flex items-center gap-1 text-content-secondary font-medium">
              <Cpu className="w-3.5 h-3.5 text-accent-info" />
              TOTAL TOKENS
            </span>
            <span className="font-bold text-content-primary">
              {formatTokens(safeTokens)} <span className="text-content-muted">/ {formatTokens(safeMaxTokens)}</span>
            </span>
          </div>
        )}
        <div className="w-full bg-bg-base h-2 rounded-full overflow-hidden border border-border-subtle/50">
          <div
            className={`h-full rounded-full transition-all duration-500 ${getBarColor(tokenPct)}`}
            style={{ width: `${tokenPct}%` }}
          />
        </div>
      </div>

      {/* Warning Alert if > 80% */}
      {(costPct >= 80 || tokenPct >= 80) && (
        <div className="flex items-center gap-2 text-amber-400 bg-amber-950/40 border border-amber-500/40 px-2.5 py-1.5 rounded-lg text-xs font-mono">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span>Approaching budget limit ({Math.max(costPct, tokenPct)}%)</span>
        </div>
      )}
    </div>
  );
};
