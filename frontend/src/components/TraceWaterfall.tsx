import React, { useState } from 'react';
import { TraceSpan } from '../types/orchestrator';
import { ChevronRight, ChevronDown, Clock, Activity, CheckCircle, AlertCircle } from 'lucide-react';

interface TraceWaterfallProps {
  spans: TraceSpan[];
  className?: string;
}

export const TraceWaterfall: React.FC<TraceWaterfallProps> = ({ spans, className = '' }) => {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  if (!spans || spans.length === 0) {
    return (
      <div className={`p-8 bg-bg-panel border border-border-subtle rounded-xl text-center ${className}`}>
        <Activity className="w-10 h-10 text-content-muted mx-auto mb-2" />
        <h4 className="text-sm font-semibold text-content-primary">No Traces Recorded</h4>
        <p className="text-xs text-content-secondary mt-1">
          Execution spans and subagent lifecycle waterfall will appear here when telemetry is active.
        </p>
      </div>
    );
  }

  const toggleCollapse = (id: string) => {
    setCollapsed((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  // Find max duration for relative width scaling
  const maxDuration = Math.max(...spans.map((s) => s.duration_ms || 100), 100);

  const renderSpan = (span: TraceSpan, depth = 0) => {
    const isCollapsed = !!collapsed[span.id];
    const duration = span.duration_ms || 50;
    const widthPct = Math.max(5, (duration / maxDuration) * 100);

    return (
      <div key={span.id} className="space-y-1">
        <div
          className="flex items-center gap-3 p-2 rounded-lg hover:bg-bg-elevated/60 transition-colors group text-xs font-mono"
          style={{ paddingLeft: `${depth * 20 + 8}px` }}
        >
          {span.children && span.children.length > 0 ? (
            <button
              onClick={() => toggleCollapse(span.id)}
              className="p-0.5 text-content-muted hover:text-content-primary"
            >
              {isCollapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </button>
          ) : (
            <span className="w-3.5 h-3.5" />
          )}

          <span className="font-semibold text-content-primary truncate min-w-[180px] max-w-[240px]">
            {span.name}
          </span>

          {/* Span Bar */}
          <div className="flex-1 bg-bg-base h-5 rounded-md overflow-hidden relative border border-border-subtle/50 flex items-center">
            <div
              className={`h-full rounded-md transition-all duration-300 flex items-center px-2 ${
                span.status === 'ERROR'
                  ? 'bg-rose-600/80 text-white'
                  : depth === 0
                  ? 'bg-accent-primary/80 text-white'
                  : 'bg-indigo-500/60 text-indigo-100'
              }`}
              style={{ width: `${widthPct}%` }}
            >
              <span className="text-[10px] font-mono whitespace-nowrap">
                {duration}ms
              </span>
            </div>
          </div>

          <span className="text-[10px] text-content-muted min-w-[60px] text-right">
            {span.status}
          </span>
        </div>

        {!isCollapsed && span.children && span.children.map((child) => renderSpan(child, depth + 1))}
      </div>
    );
  };

  return (
    <div className={`bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-lg ${className}`}>
      <div className="flex items-center justify-between pb-3 mb-3 border-b border-border-subtle">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-accent-primary" />
          <span className="text-xs font-mono font-bold text-content-primary uppercase">
            Span Execution Waterfall
          </span>
        </div>
        <span className="text-xs font-mono text-content-muted">Total Spans: {spans.length}</span>
      </div>

      <div className="space-y-1 overflow-x-auto max-h-[480px]">
        {spans.map((span) => renderSpan(span, 0))}
      </div>
    </div>
  );
};
