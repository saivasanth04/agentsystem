import React, { useState, useEffect } from 'react';
import {
  SessionDetail,
  DAGSnapshot,
  AgentMessage,
  VerificationEvidence,
  FileDiffItem,
  ReplanRecord,
  TraceSpan
} from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import { StatusBadge } from '../components/StatusBadge';
import { AgentAvatar } from '../components/AgentAvatar';
import { DAGCanvas } from '../components/DAGCanvas';
import { EvidenceMatrix } from '../components/EvidenceMatrix';
import { DiffViewer } from '../components/DiffViewer';
import { TraceWaterfall } from '../components/TraceWaterfall';
import { ReplanTimeline } from '../components/ReplanTimeline';
import { JsonViewer } from '../components/JsonViewer';
import {
  FolderKanban,
  GitBranch,
  MessageSquare,
  ShieldCheck,
  FileCode,
  RotateCcw,
  Sparkles,
  CheckCircle2,
  AlertTriangle,
  Flame,
  Clock,
  DollarSign,
  Cpu,
  Layers,
  ArrowLeft,
  Copy,
  Check,
  Terminal,
  Play,
  RotateCcw as RollbackIcon,
  RefreshCw
} from 'lucide-react';

interface SessionDetailPageProps {
  sessionId: string;
  navigate: (route: string) => void;
}

export const SessionDetailPage: React.FC<SessionDetailPageProps> = ({ sessionId, navigate }) => {
  const [activeTab, setActiveTab] = useState<'overview' | 'dag' | 'messages' | 'verification' | 'diff' | 'replan'>('overview');
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [dag, setDag] = useState<DAGSnapshot | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [evidence, setEvidence] = useState<VerificationEvidence | null>(null);
  const [diffs, setDiffs] = useState<FileDiffItem[]>([]);
  const [replans, setReplans] = useState<ReplanRecord[]>([]);
  const [spans, setSpans] = useState<TraceSpan[]>([]);
  const [loading, setLoading] = useState(true);
  const [copiedHash, setCopiedHash] = useState(false);

  // Message filters
  const [agentFilter, setAgentFilter] = useState('ALL');
  const [messageSearch, setMessageSearch] = useState('');

  const loadAllSessionData = async () => {
    setLoading(true);
    try {
      const [det, dagData, msgs, ev, df, rep] = await Promise.all([
        orchestratorApi.getSessionDetail(sessionId),
        orchestratorApi.getSessionDAG(sessionId).catch(() => null),
        orchestratorApi.getSessionMessages(sessionId).catch(() => []),
        orchestratorApi.getVerificationEvidence(sessionId).catch(() => null),
        orchestratorApi.getSessionDiff(sessionId).catch(() => []),
        orchestratorApi.getReplanHistory(sessionId).catch(() => []),
      ]);

      setDetail(det);
      setDag(dagData);
      setMessages(msgs);
      setEvidence(ev);
      setDiffs(df);
      setReplans(rep);
    } catch (err) {
      console.error('Failed to load session details:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAllSessionData();
  }, [sessionId]);

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedHash(true);
    setTimeout(() => setCopiedHash(false), 2000);
  };

  const handleResume = async () => {
    try {
      await orchestratorApi.resumeSession(sessionId);
      navigate(`/sessions/${sessionId}/live`);
    } catch (err: any) {
      alert(`Resume failed: ${err.message}`);
    }
  };

  const filteredMessages = messages.filter((m) => {
    const matchesAgent = agentFilter === 'ALL' || m.agent === agentFilter || m.role === agentFilter;
    const matchesSearch = messageSearch === '' || `${m.content} ${JSON.stringify(m.structured_data || '')}`.toLowerCase().includes(messageSearch.toLowerCase());
    return matchesAgent && matchesSearch;
  });

  const uniqueAgents = Array.from(new Set(messages.map((m) => m.agent || m.role).filter(Boolean)));

  if (loading && !detail) {
    return (
      <div className="flex items-center justify-center h-96 font-mono text-sm text-content-muted">
        <RefreshCw className="w-5 h-5 animate-spin mr-2" />
        Loading session snapshot and execution trace...
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="text-center py-20 bg-bg-panel border border-border-subtle rounded-xl max-w-lg mx-auto space-y-4">
        <AlertTriangle className="w-12 h-12 text-accent-warning mx-auto" />
        <h3 className="text-base font-bold text-content-primary">Session Not Found</h3>
        <p className="text-xs font-mono text-content-secondary">
          No session record with ID <span className="text-accent-primary font-bold">{sessionId}</span> exists.
        </p>
        <button
          onClick={() => navigate('/sessions')}
          className="px-4 py-2 rounded-lg bg-accent-primary text-white font-mono text-xs font-semibold"
        >
          Back to Sessions
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Top Breadcrumb & Controls */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/sessions')}
            className="p-2 rounded-xl bg-bg-panel hover:bg-bg-elevated border border-border-subtle text-content-secondary hover:text-content-primary transition-colors"
            title="Back to Sessions"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-bold font-mono text-content-primary">
                {detail.session_id}
              </h1>
              <StatusBadge status={detail.status} size="sm" />
              <StatusBadge status={detail.verdict} size="sm" />
            </div>
            <p className="text-xs text-content-secondary line-clamp-1 mt-0.5">
              {detail.user_request}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate(`/sessions/${sessionId}/live`)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-indigo-950/60 hover:bg-indigo-900/60 border border-indigo-500/40 text-indigo-300 text-xs font-mono font-semibold transition-colors"
          >
            <Play className="w-3.5 h-3.5 fill-indigo-300" />
            Live DAG Theater
          </button>

          {(detail.status === 'STOPPED' || detail.status === 'FAILED') && (
            <button
              onClick={handleResume}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-accent-primary hover:bg-indigo-600 text-white text-xs font-mono font-bold transition-all shadow"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Resume Session
            </button>
          )}
        </div>
      </div>

      {/* Navigation Tabs */}
      <div className="flex items-center gap-2 border-b border-border-subtle overflow-x-auto pb-1">
        {[
          { id: 'overview', label: 'Overview', icon: FolderKanban },
          { id: 'dag', label: 'DAG Execution', icon: GitBranch },
          { id: 'messages', label: 'Messages & Trace', icon: MessageSquare },
          { id: 'verification', label: 'Verification Evidence', icon: ShieldCheck },
          { id: 'diff', label: 'Files & Diff', icon: FileCode },
          { id: 'replan', label: 'Re-plan History', icon: RotateCcw },
        ].map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;

          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={`flex items-center gap-2 px-4 py-2.5 rounded-t-xl text-xs font-mono font-medium transition-all ${
                isActive
                  ? 'bg-bg-panel text-accent-primary border-t border-x border-border-subtle font-bold'
                  : 'text-content-secondary hover:text-content-primary hover:bg-bg-elevated/40'
              }`}
            >
              <Icon className="w-4 h-4" />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* TAB A: OVERVIEW */}
      {activeTab === 'overview' && (
        <div className="space-y-6">
          {/* Header Stats Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <div className="bg-bg-panel border border-border-subtle p-3.5 rounded-xl">
              <span className="text-[10px] font-mono text-content-muted block mb-1">SCORE</span>
              <span className={`text-xl font-bold font-mono ${detail.score >= 80 ? 'text-accent-success' : 'text-accent-warning'}`}>
                {detail.score} / 100
              </span>
            </div>
            <div className="bg-bg-panel border border-border-subtle p-3.5 rounded-xl">
              <span className="text-[10px] font-mono text-content-muted block mb-1">REPLANS</span>
              <span className="text-xl font-bold font-mono text-content-primary">
                {detail.iteration} / {detail.max_iterations}
              </span>
            </div>
            <div className="bg-bg-panel border border-border-subtle p-3.5 rounded-xl">
              <span className="text-[10px] font-mono text-content-muted block mb-1">DURATION</span>
              <span className="text-xl font-bold font-mono text-content-primary">
                {Math.round(detail.duration_seconds)}s
              </span>
            </div>
            <div className="bg-bg-panel border border-border-subtle p-3.5 rounded-xl">
              <span className="text-[10px] font-mono text-content-muted block mb-1">TOTAL COST</span>
              <span className="text-xl font-bold font-mono text-accent-warning">
                ${detail.total_cost_usd.toFixed(4)}
              </span>
            </div>
            <div className="bg-bg-panel border border-border-subtle p-3.5 rounded-xl">
              <span className="text-[10px] font-mono text-content-muted block mb-1">TOKENS</span>
              <span className="text-xl font-bold font-mono text-accent-info">
                {Math.round(detail.total_tokens / 1000)}k
              </span>
            </div>
            <div className="bg-bg-panel border border-border-subtle p-3.5 rounded-xl">
              <span className="text-[10px] font-mono text-content-muted block mb-1">COMPLETED TASKS</span>
              <span className="text-xl font-bold font-mono text-accent-success">
                {detail.execution_summary.completed_tasks} / {detail.execution_summary.total_tasks}
              </span>
            </div>
          </div>

          {/* Reproducibility Card */}
          <div className="bg-bg-panel border border-border-subtle rounded-xl p-5 shadow-lg space-y-3">
            <div className="flex items-center justify-between pb-2 border-b border-border-subtle">
              <h3 className="text-xs font-mono font-bold text-content-primary uppercase flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-accent-primary" />
                Deterministic Reproducibility Manifest
              </h3>
              <span className="text-[11px] font-mono text-content-muted">Seed: {detail.reproducibility.seed}</span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 text-xs font-mono">
              <div className="bg-bg-base p-3 rounded-lg border border-border-subtle">
                <span className="text-[10px] text-content-muted block mb-1">SNAPSHOT ID</span>
                <span className="text-content-primary font-bold truncate block">
                  {detail.reproducibility.snapshot_id}
                </span>
              </div>
              <div className="bg-bg-base p-3 rounded-lg border border-border-subtle">
                <span className="text-[10px] text-content-muted block mb-1">MANIFEST HASH (12-CHAR)</span>
                <div className="flex items-center justify-between">
                  <span className="text-accent-primary font-bold">
                    {detail.reproducibility.manifest_hash.slice(0, 12)}
                  </span>
                  <button
                    onClick={() => handleCopy(detail.reproducibility.manifest_hash)}
                    className="p-0.5 hover:text-white text-content-muted"
                  >
                    {copiedHash ? <Check className="w-3.5 h-3.5 text-accent-success" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                </div>
              </div>
              <div className="bg-bg-base p-3 rounded-lg border border-border-subtle">
                <span className="text-[10px] text-content-muted block mb-1">GIT WORKING TREE</span>
                <span className={`font-bold ${detail.reproducibility.git_dirty ? 'text-amber-400' : 'text-emerald-400'}`}>
                  {detail.reproducibility.git_dirty ? 'DIRTY (Uncommitted)' : 'CLEAN'}
                </span>
              </div>
              <div className="bg-bg-base p-3 rounded-lg border border-border-subtle">
                <span className="text-[10px] text-content-muted block mb-1">ORCHESTRATOR VERSION</span>
                <span className="text-content-primary font-bold">
                  v{detail.reproducibility.orchestrator_version}
                </span>
              </div>
            </div>
          </div>

          {/* Final Reviewer Report Panel */}
          {detail.final_report && (
            <div className="bg-bg-panel border border-border-subtle rounded-xl p-6 shadow-xl space-y-4">
              <div className="flex items-center justify-between pb-3 border-b border-border-subtle">
                <h3 className="text-sm font-bold font-mono text-content-primary uppercase flex items-center gap-2">
                  <ShieldCheck className="w-5 h-5 text-accent-success" />
                  Final Reviewer Quality Report & Verdict
                </h3>
                <StatusBadge status={detail.final_report.verdict} size="md" />
              </div>

              {/* Summary */}
              <div className="bg-bg-base p-4 rounded-xl border border-border-subtle">
                <h4 className="text-xs font-mono font-bold text-content-muted uppercase mb-1.5">Executive Summary</h4>
                <p className="text-sm text-content-primary font-sans leading-relaxed">
                  {detail.final_report.reviewer_summary}
                </p>
              </div>

              {/* Strengths */}
              {detail.final_report.strengths && detail.final_report.strengths.length > 0 && (
                <div>
                  <h4 className="text-xs font-mono font-bold text-emerald-400 uppercase mb-2 flex items-center gap-1.5">
                    <CheckCircle2 className="w-4 h-4" />
                    Verified Strengths ({detail.final_report.strengths.length})
                  </h4>
                  <ul className="space-y-1.5">
                    {detail.final_report.strengths.map((str, i) => (
                      <li key={i} className="text-xs text-content-secondary bg-bg-base p-2.5 rounded-lg border border-emerald-500/20 flex items-start gap-2">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 mt-1.5 shrink-0" />
                        <span>{str}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Issues List */}
              {detail.final_report.issues && detail.final_report.issues.length > 0 && (
                <div>
                  <h4 className="text-xs font-mono font-bold text-rose-400 uppercase mb-2 flex items-center gap-1.5">
                    <AlertTriangle className="w-4 h-4" />
                    Flagged Discrepancies & Deficiencies ({detail.final_report.issues.length})
                  </h4>
                  <div className="space-y-2">
                    {detail.final_report.issues.map((iss, i) => (
                      <div key={i} className="bg-bg-base p-3 rounded-lg border border-rose-500/30 text-xs space-y-1">
                        <div className="flex items-center justify-between">
                          <span className="font-bold text-rose-300 font-mono">{iss.title}</span>
                          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-rose-950 text-rose-400 border border-rose-500/30 uppercase">
                            {iss.severity}
                          </span>
                        </div>
                        <p className="text-content-secondary">{iss.description}</p>
                        {iss.file_location && (
                          <span className="text-[11px] font-mono text-content-muted block">
                            File: {iss.file_location}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* TAB B: DAG EXECUTION */}
      {activeTab === 'dag' && <DAGCanvas dag={dag} />}

      {/* TAB C: MESSAGES / TRACE */}
      {activeTab === 'messages' && (
        <div className="space-y-4">
          {/* Filter Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3 p-3 bg-bg-panel border border-border-subtle rounded-xl">
            <div className="flex items-center gap-2 flex-1 max-w-sm">
              <input
                type="text"
                value={messageSearch}
                onChange={(e) => setMessageSearch(e.target.value)}
                placeholder="Search conversation trace..."
                className="w-full bg-bg-base border border-border-subtle rounded-lg px-3 py-1.5 text-xs font-mono text-content-primary placeholder-content-muted focus:outline-none focus:border-accent-primary"
              />
            </div>

            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-content-muted">AGENT:</span>
              <select
                value={agentFilter}
                onChange={(e) => setAgentFilter(e.target.value)}
                className="bg-bg-base border border-border-subtle rounded-lg px-2.5 py-1.5 text-xs font-mono text-content-primary focus:outline-none"
              >
                <option value="ALL">All Agents</option>
                {uniqueAgents.map((ag) => (
                  <option key={ag} value={ag}>{ag}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Messages Timeline */}
          <div className="space-y-3">
            {filteredMessages.length === 0 ? (
              <div className="p-8 text-center text-xs font-mono text-content-muted bg-bg-panel border border-border-subtle rounded-xl">
                No conversation messages match the current filters.
              </div>
            ) : (
              filteredMessages.map((msg) => (
                <div
                  key={msg.id}
                  className="bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-sm space-y-2.5"
                >
                  <div className="flex items-center justify-between pb-2 border-b border-border-subtle/50">
                    <div className="flex items-center gap-2.5">
                      <AgentAvatar agentName={msg.agent} role={msg.role} size="sm" showRoleLabel={true} />
                      {msg.stage && (
                        <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-bg-base border border-border-subtle text-accent-info uppercase">
                          {msg.stage}
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] font-mono text-content-muted">
                      {new Date(msg.timestamp).toLocaleTimeString()}
                    </span>
                  </div>

                  <p className="text-xs text-content-primary font-sans leading-relaxed whitespace-pre-wrap">
                    {msg.content}
                  </p>

                  {msg.structured_data && Object.keys(msg.structured_data).length > 0 && (
                    <JsonViewer data={msg.structured_data} title="Agent Payload JSON" initialExpanded={false} />
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* TAB D: VERIFICATION EVIDENCE */}
      {activeTab === 'verification' && (
        <div className="space-y-6">
          <EvidenceMatrix evidence={evidence} />
          {spans.length > 0 && <TraceWaterfall spans={spans} />}
        </div>
      )}

      {/* TAB E: FILES & DIFF */}
      {activeTab === 'diff' && <DiffViewer diffs={diffs} />}

      {/* TAB F: RE-PLAN HISTORY */}
      {activeTab === 'replan' && <ReplanTimeline records={replans} />}
    </div>
  );
};
