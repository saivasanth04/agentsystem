import React, { useState } from 'react';
import { Shield, FileText, Database, Code, ChevronRight, ChevronDown } from 'lucide-react';

interface ContextInspectorProps {
  contexts?: Array<{
    id: string;
    trust_level: 'USER_INSTRUCTION' | 'CONTROL_SYSTEM' | 'UNTRUSTED_REPOSITORY' | 'TOOL_OUTPUT';
    source: string;
    content: string;
    tokens_estimate?: number;
  }>;
  className?: string;
}

export const ContextInspector: React.FC<ContextInspectorProps> = ({ contexts = [], className = '' }) => {
  const [expandedId, setExpandedId] = useState<string | null>(contexts[0]?.id || null);

  const getTrustBadge = (trust: string) => {
    switch (trust) {
      case 'CONTROL_SYSTEM':
        return 'bg-purple-950/70 text-purple-300 border-purple-500/40';
      case 'USER_INSTRUCTION':
        return 'bg-blue-950/70 text-blue-300 border-blue-500/40';
      case 'UNTRUSTED_REPOSITORY':
        return 'bg-amber-950/70 text-amber-300 border-amber-500/40';
      case 'TOOL_OUTPUT':
      default:
        return 'bg-emerald-950/70 text-emerald-300 border-emerald-500/40';
    }
  };

  if (!contexts || contexts.length === 0) {
    return (
      <div className={`p-6 bg-bg-panel border border-border-subtle rounded-xl text-center ${className}`}>
        <Shield className="w-8 h-8 text-content-muted mx-auto mb-2" />
        <h5 className="text-xs font-semibold text-content-primary">No Injected Context</h5>
      </div>
    );
  }

  return (
    <div className={`space-y-2 bg-bg-panel border border-border-subtle rounded-xl p-4 ${className}`}>
      <div className="flex items-center justify-between pb-2 mb-2 border-b border-border-subtle">
        <span className="text-xs font-mono font-bold text-content-muted uppercase">
          Injected Prompt Context ({contexts.length} Slices)
        </span>
      </div>

      <div className="space-y-2">
        {contexts.map((c) => {
          const isExpanded = expandedId === c.id;

          return (
            <div key={c.id} className="border border-border-subtle rounded-lg overflow-hidden bg-bg-base">
              <button
                onClick={() => setExpandedId(isExpanded ? null : c.id)}
                className="w-full flex items-center justify-between p-2.5 text-left hover:bg-bg-elevated/60 transition-colors"
              >
                <div className="flex items-center gap-2">
                  {isExpanded ? <ChevronDown className="w-3.5 h-3.5 text-content-muted" /> : <ChevronRight className="w-3.5 h-3.5 text-content-muted" />}
                  <span className="text-xs font-mono font-semibold text-content-primary truncate">{c.source}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded border uppercase ${getTrustBadge(c.trust_level)}`}>
                    {c.trust_level}
                  </span>
                  {c.tokens_estimate && (
                    <span className="text-[10px] font-mono text-content-muted">~{c.tokens_estimate} tok</span>
                  )}
                </div>
              </button>

              {isExpanded && (
                <div className="p-3 border-t border-border-subtle/50 bg-black/40 max-h-60 overflow-y-auto">
                  <pre className="text-xs font-mono text-content-secondary whitespace-pre-wrap leading-relaxed">
                    {c.content}
                  </pre>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
