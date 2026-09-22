import React from 'react';
import { ReplanRecord } from '../types/orchestrator';
import { RotateCcw, AlertTriangle, PlusCircle, MinusCircle, History } from 'lucide-react';

interface ReplanTimelineProps {
  records: ReplanRecord[];
  className?: string;
}

export const ReplanTimeline: React.FC<ReplanTimelineProps> = ({ records, className = '' }) => {
  if (!records || records.length === 0) {
    return (
      <div className={`p-8 bg-bg-panel border border-border-subtle rounded-xl text-center ${className}`}>
        <History className="w-10 h-10 text-content-muted mx-auto mb-2" />
        <h4 className="text-sm font-semibold text-content-primary">Zero Replanning Cycles</h4>
        <p className="text-xs text-content-secondary mt-1">
          The initial execution plan succeeded without requiring iterative replan or transactional rollbacks.
        </p>
      </div>
    );
  }

  return (
    <div className={`space-y-4 bg-bg-panel border border-border-subtle rounded-xl p-6 shadow-md ${className}`}>
      <div className="flex items-center justify-between pb-3 border-b border-border-subtle">
        <div className="flex items-center gap-2">
          <RotateCcw className="w-4 h-4 text-accent-warning" />
          <h4 className="text-xs font-mono font-bold text-content-primary uppercase">
            Replanning & Rollback Timeline ({records.length} Iterations)
          </h4>
        </div>
      </div>

      <div className="relative pl-6 space-y-6 before:absolute before:left-2 before:top-2 before:bottom-2 before:w-0.5 before:bg-border-subtle">
        {records.map((rec, idx) => (
          <div key={idx} className="relative space-y-2">
            {/* Timeline Dot */}
            <div className="absolute -left-[27px] top-1 w-3.5 h-3.5 rounded-full bg-accent-warning border-2 border-bg-base" />

            <div className="bg-bg-base p-4 rounded-xl border border-border-subtle space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-bold text-amber-400">
                  ITERATION #{rec.iteration}
                </span>
                <span className="text-[10px] font-mono text-content-muted">
                  {new Date(rec.timestamp).toLocaleTimeString()}
                </span>
              </div>

              {/* Trigger Reason */}
              <div className="flex items-start gap-2 text-xs text-content-primary">
                <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <span className="font-semibold">{rec.trigger_reason}</span>
              </div>

              {/* Feedback Summary */}
              {rec.feedback_summary && (
                <p className="text-xs text-content-secondary bg-bg-panel p-2.5 rounded-lg border border-border-subtle/50 font-sans leading-relaxed">
                  {rec.feedback_summary}
                </p>
              )}

              {/* Injected & Pruned Tasks */}
              <div className="flex flex-wrap gap-4 pt-2 border-t border-border-subtle/50 text-xs font-mono">
                {rec.injected_task_ids && rec.injected_task_ids.length > 0 && (
                  <div className="flex items-center gap-1.5 text-emerald-400">
                    <PlusCircle className="w-3.5 h-3.5" />
                    <span>Injected: {rec.injected_task_ids.join(', ')}</span>
                  </div>
                )}
                {rec.pruned_task_ids && rec.pruned_task_ids.length > 0 && (
                  <div className="flex items-center gap-1.5 text-rose-400">
                    <MinusCircle className="w-3.5 h-3.5" />
                    <span>Pruned: {rec.pruned_task_ids.join(', ')}</span>
                  </div>
                )}
                {rec.rollback_files_count !== undefined && rec.rollback_files_count > 0 && (
                  <div className="flex items-center gap-1.5 text-amber-400">
                    <RotateCcw className="w-3.5 h-3.5" />
                    <span>Rollback: {rec.rollback_files_count} files reverted</span>
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
