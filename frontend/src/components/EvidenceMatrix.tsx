import React from 'react';
import { VerificationEvidence } from '../types/orchestrator';
import { StatusBadge } from './StatusBadge';
import {
  ShieldAlert,
  ShieldCheck,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Code2,
  Terminal,
  FileCheck2,
  Flame,
  Percent,
  Info
} from 'lucide-react';

interface EvidenceMatrixProps {
  evidence: VerificationEvidence | null;
  className?: string;
}

export const EvidenceMatrix: React.FC<EvidenceMatrixProps> = ({ evidence, className = '' }) => {
  if (!evidence) {
    return (
      <div className={`p-8 bg-bg-panel border border-border-subtle rounded-xl text-center ${className}`}>
        <ShieldCheck className="w-10 h-10 text-content-muted mx-auto mb-2" />
        <h4 className="text-sm font-semibold text-content-primary">No Verification Data Yet</h4>
        <p className="text-xs text-content-secondary mt-1">
          Verification evidence will be captured and validated against ground-truth gates after task execution.
        </p>
      </div>
    );
  }

  const allTestsPassed = evidence.tests_passed === evidence.tests_total && evidence.tests_total > 0;

  return (
    <div className={`space-y-6 ${className}`}>
      {/* Adversarial Override Banner */}
      {evidence.adversarial_override && (
        <div className="bg-amber-950/40 border-2 border-amber-500/80 rounded-xl p-4.5 flex items-start gap-3.5 shadow-lg shadow-amber-950/20 animate-pulse-slow">
          <ShieldAlert className="w-6 h-6 text-amber-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <h4 className="text-sm font-bold text-amber-300 uppercase tracking-wide flex items-center gap-2">
              Adversarial Ground-Truth Veto Enforced
            </h4>
            <p className="text-xs text-amber-200/90 leading-relaxed">
              Reviewer agent proposed <span className="font-mono font-bold">PASS</span>, but the ground-truth verification engine discovered critical discrepancies and overrode the verdict.
            </p>
            {evidence.veto_reason && (
              <div className="mt-2 bg-black/40 px-3 py-2 rounded-lg border border-amber-500/30 text-xs font-mono text-amber-300">
                <span className="text-amber-400 font-bold">VETO REASON: </span>
                {evidence.veto_reason}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 5-Gate Matrix Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Gate 1: Build */}
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-4 flex flex-col justify-between shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-mono font-bold text-content-muted uppercase">1. Build Status</span>
            <Code2 className="w-4 h-4 text-accent-info" />
          </div>
          <div className="flex items-center gap-2.5 my-1">
            {evidence.build_pass ? (
              <div className="flex items-center gap-2 text-accent-success font-semibold text-sm">
                <CheckCircle2 className="w-5 h-5" />
                <span>BUILD PASSED</span>
              </div>
            ) : (
              <div className="flex items-center gap-2 text-accent-danger font-semibold text-sm">
                <XCircle className="w-5 h-5" />
                <span>BUILD FAILED</span>
              </div>
            )}
          </div>
          <span className="text-[11px] font-mono text-content-muted mt-2">
            Exit Code: {evidence.test_exit_code === 0 ? '0 (Success)' : evidence.test_exit_code}
          </span>
        </div>

        {/* Gate 2: Tests */}
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-4 flex flex-col justify-between shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-mono font-bold text-content-muted uppercase">2. Test Suite</span>
            <Terminal className="w-4 h-4 text-accent-primary" />
          </div>
          <div className="flex items-baseline gap-2 my-1">
            <span className={`text-2xl font-bold font-mono ${allTestsPassed ? 'text-accent-success' : 'text-accent-danger'}`}>
              {evidence.tests_passed}
            </span>
            <span className="text-sm font-mono text-content-muted">/ {evidence.tests_total} passed</span>
          </div>
          <div className="w-full bg-bg-base h-1.5 rounded-full overflow-hidden mt-2">
            <div
              className={`h-full ${allTestsPassed ? 'bg-accent-success' : 'bg-accent-danger'}`}
              style={{
                width: `${evidence.tests_total > 0 ? (evidence.tests_passed / evidence.tests_total) * 100 : 0}%`,
              }}
            />
          </div>
        </div>

        {/* Gate 3: Lint & Static Analysis */}
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-4 flex flex-col justify-between shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-mono font-bold text-content-muted uppercase">3. Static Analysis</span>
            <AlertTriangle className="w-4 h-4 text-accent-warning" />
          </div>
          <div className="flex items-center gap-3 my-1">
            <div>
              <span className={`text-xl font-mono font-bold ${evidence.lint_errors > 0 ? 'text-accent-danger' : 'text-accent-success'}`}>
                {evidence.lint_errors}
              </span>
              <span className="text-[11px] font-mono text-content-muted ml-1">errors</span>
            </div>
            <div className="border-l border-border-subtle pl-3">
              <span className="text-xl font-mono font-bold text-accent-warning">
                {evidence.lint_warnings}
              </span>
              <span className="text-[11px] font-mono text-content-muted ml-1">warnings</span>
            </div>
          </div>
          <span className="text-[11px] font-mono text-content-muted mt-2">
            {evidence.lint_errors === 0 ? 'Clean AST / Types' : 'Lint violations present'}
          </span>
        </div>

        {/* Gate 4: Diff Coverage */}
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-4 flex flex-col justify-between shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-mono font-bold text-content-muted uppercase">4. Diff Coverage</span>
            <Percent className="w-4 h-4 text-accent-purple" />
          </div>
          <div className="flex items-baseline gap-1 my-1">
            <span className={`text-2xl font-mono font-bold ${evidence.diff_coverage_pct >= 80 ? 'text-accent-success' : 'text-accent-warning'}`}>
              {evidence.diff_coverage_pct}%
            </span>
          </div>
          <div className="w-full bg-bg-base h-1.5 rounded-full overflow-hidden mt-2">
            <div
              className={`h-full ${evidence.diff_coverage_pct >= 80 ? 'bg-accent-success' : 'bg-accent-warning'}`}
              style={{ width: `${Math.min(100, evidence.diff_coverage_pct)}%` }}
            />
          </div>
        </div>
      </div>

      {/* Tautological Assertions Watchdog */}
      {evidence.tautological_assertions > 0 && (
        <div className="bg-rose-950/30 border border-rose-500/40 rounded-xl p-4 flex items-start gap-3">
          <Flame className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
          <div>
            <h5 className="text-xs font-mono font-bold text-rose-300 uppercase">
              Tautological / Vacuous Assertions Flagged ({evidence.tautological_assertions})
            </h5>
            <p className="text-xs text-rose-200/80 mt-0.5">
              The verification scanner detected assertions that pass vacuously (e.g. `assert True`, dummy mocks, or skipped invariants).
            </p>
            {evidence.tautological_details && (
              <ul className="mt-2 space-y-1 text-xs font-mono text-rose-300/90 list-disc list-inside">
                {evidence.tautological_details.map((item, idx) => (
                  <li key={idx}>{item}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {/* Acceptance Criteria Checklist */}
      {evidence.acceptance_criteria && evidence.acceptance_criteria.length > 0 && (
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-5 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <FileCheck2 className="w-4 h-4 text-accent-primary" />
              <h4 className="text-xs font-mono font-bold text-content-primary uppercase tracking-wider">
                Acceptance Criteria Verification ({evidence.acceptance_criteria.filter(a => a.verified).length}/{evidence.acceptance_criteria.length})
              </h4>
            </div>
            <span className="text-xs font-mono text-content-muted">Ground-Truth Validated</span>
          </div>

          <div className="space-y-2.5">
            {evidence.acceptance_criteria.map((item, idx) => (
              <div
                key={idx}
                className={`p-3 rounded-lg border flex flex-col gap-1.5 transition-colors ${
                  item.verified
                    ? 'bg-bg-base/80 border-emerald-500/30 text-content-primary'
                    : 'bg-rose-950/20 border-rose-500/30 text-rose-200'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    {item.verified ? (
                      <CheckCircle2 className="w-4 h-4 text-accent-success shrink-0" />
                    ) : (
                      <XCircle className="w-4 h-4 text-accent-danger shrink-0" />
                    )}
                    <span className="text-xs font-medium">{item.criterion}</span>
                  </div>
                  <span
                    className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded ${
                      item.verified
                        ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/30'
                        : 'bg-rose-950 text-rose-400 border border-rose-500/30'
                    }`}
                  >
                    {item.verified ? 'VERIFIED' : 'UNVERIFIED'}
                  </span>
                </div>

                {item.evidence_snippet && (
                  <div className="mt-1 pl-6 text-[11px] font-mono text-content-secondary bg-bg-panel/70 p-2 rounded border border-border-subtle overflow-x-auto">
                    {item.evidence_snippet}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
