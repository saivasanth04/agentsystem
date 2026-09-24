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
import { useWorkspace } from '../context/WorkspaceContext';
import { StatusBadge } from '../components/StatusBadge';
import { AgentAvatar } from '../components/AgentAvatar';
import { DAGCanvas } from '../components/DAGCanvas';
import { EvidenceMatrix } from '../components/EvidenceMatrix';
import { DiffViewer } from '../components/DiffViewer';
import { TraceWaterfall } from '../components/TraceWaterfall';
import { ReplanTimeline } from '../components/ReplanTimeline';
import { JsonViewer } from '../components/JsonViewer';
import { FileExplorer } from '../components/FileExplorer';
import { CodeEditor } from '../components/CodeEditor';
import { ProjectRuntimeCard } from '../components/ProjectRuntimeCard';
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
  ArrowLeft,
  Copy,
  Check,
  Play,
  RefreshCw,
  FolderOpen,
  Folder,
  Globe,
  Trash2,
  XCircle
} from 'lucide-react';


interface SessionDetailPageProps {
  sessionId: string;
  navigate: (route: string) => void;
}

export const SessionDetailPage: React.FC<SessionDetailPageProps> = ({ sessionId, navigate }) => {
  const { activeWorkspace, openWorkspace } = useWorkspace();
  const [activeTab, setActiveTab] = useState<'overview' | 'runtime' | 'workspace' | 'dag' | 'messages' | 'verification' | 'diff' | 'replan'>('overview');
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [dag, setDag] = useState<DAGSnapshot | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [evidence, setEvidence] = useState<VerificationEvidence | null>(null);
  const [diffs, setDiffs] = useState<FileDiffItem[]>([]);
  const [replans, setReplans] = useState<ReplanRecord[]>([]);
  const [spans, setSpans] = useState<TraceSpan[]>([]);
  const [loading, setLoading] = useState(true);
  const [copiedHash, setCopiedHash] = useState(false);

  // Selected file for IDE workspace tab
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  // Message filters
  const [agentFilter, setAgentFilter] = useState('ALL');
  const [messageSearch, setMessageSearch] = useState('');

  const loadAllSessionData = async () => {
    setLoading(true);
    try {
      const [det, dagData, msgsData, evData, dfData, repData] = await Promise.all([
        orchestratorApi.getSessionDetail(sessionId),
        orchestratorApi.getSessionDAG(sessionId).catch(() => null),
        orchestratorApi.getSessionMessages(sessionId).catch(() => ({ messages: [] })),
        orchestratorApi.getVerificationEvidence(sessionId).catch(() => null),
        orchestratorApi.getSessionDiff(sessionId).catch(() => ({ files_changed: 0, diff_blocks: [] })),
        orchestratorApi.getReplanHistory(sessionId).catch(() => ({ replan_history: [] })),
      ]);

      setDetail(det);
      setDag(dagData);
      setMessages(msgsData.messages || []);
      setEvidence(evData);
      setDiffs(dfData.diff_blocks || []);
      setReplans(repData.replan_history || []);
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

  const handleCancel = async () => {
    if (!confirm(`Are you sure you want to cancel the active execution of session "${sessionId}"?`)) {
      return;
    }
    try {
      await orchestratorApi.cancelSession(sessionId, 'Operator cancelled from session detail');
      loadAllSessionData();
    } catch (err: any) {
      alert(`Cancel failed: ${err.message || err}`);
    }
  };

  const handleDelete = async () => {
    if (!confirm(`Permanently delete session "${sessionId}" and all associated artifacts and database records? This action cannot be undone.`)) {
      return;
    }
    try {
      await orchestratorApi.deleteSession(sessionId);
      navigate('/sessions');
    } catch (err: any) {
      alert(`Delete failed: ${err.message || err}`);
    }
  };

  // Determine session target workspace path
  const sessionWorkspacePath = detail?.workspace_path || detail?.reproducibility?.workspace_path || activeWorkspace?.path;

  const filteredMessages = messages.filter((m) => {
    const matchesAgent = agentFilter === 'ALL' || m.agent === agentFilter || m.role === agentFilter;
    const matchesSearch = messageSearch === '' || `${m.content} ${JSON.stringify(m.structured_data || '')}`.toLowerCase().includes(messageSearch.toLowerCase());
    return matchesAgent && matchesSearch;
  });

  const uniqueAgents = Array.from(new Set(messages.map((m) => m.agent || m.role).filter(Boolean)));

  if (loading && !detail) {
    return (
      <div className="flex items-center justify-center h-96 font-mono text-sm text-slate-400">
        <RefreshCw className="w-5 h-5 animate-spin mr-2 text-cyan-400" />
        Loading session snapshot, artifacts, and execution telemetry...
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="text-center py-20 bg-slate-900 border border-slate-800 rounded-2xl max-w-lg mx-auto space-y-4 shadow-xl">
        <AlertTriangle className="w-12 h-12 text-amber-400 mx-auto" />
        <h3 className="text-base font-bold text-slate-100">Session Not Found</h3>
        <p className="text-xs font-mono text-slate-400">
          No session record with ID <span className="text-cyan-400 font-bold">{sessionId}</span> exists.
        </p>
        <button
          onClick={() => navigate('/sessions')}
          className="px-4 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-mono text-xs font-semibold shadow-md transition-colors"
        >
          Back to Sessions
        </button>
      </div>
    );
  }

  const isRunning = detail.status === 'IN_PROGRESS' || detail.status === 'RUNNING' || detail.status === 'PENDING';

  return (
    <div className="max-w-7xl mx-auto space-y-5 pb-12">
      {/* Top Breadcrumb & Controls */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/sessions')}
            className="p-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-slate-200 transition-colors"
            title="Back to Sessions"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-bold font-mono text-slate-100">
                {detail.session_id}
              </h1>
              <StatusBadge status={detail.status} size="sm" />
              <StatusBadge status={detail.verdict} size="sm" />
            </div>
            <div className="flex flex-wrap items-center gap-2 mt-1">
              <span className="text-[11px] font-mono text-cyan-400 bg-cyan-950/60 border border-cyan-500/30 px-2 py-0.5 rounded-lg flex items-center gap-1.5" title={sessionWorkspacePath}>
                <Folder className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
                <span className="font-semibold text-slate-400">Workspace:</span>
                <span className="text-slate-200 truncate max-w-xs">{sessionWorkspacePath}</span>
              </span>
              {detail.git_branch && (
                <span className="text-[11px] font-mono text-slate-400 bg-slate-900 border border-slate-800 px-2 py-0.5 rounded-lg flex items-center gap-1">
                  <GitBranch className="w-3 h-3 text-slate-400" />
                  <span>{detail.git_branch}</span>
                </span>
              )}
            </div>
            <p className="text-xs text-slate-400 line-clamp-1 mt-1">
              {detail.user_request}
            </p>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-3">
          {sessionWorkspacePath && activeWorkspace?.path !== sessionWorkspacePath && (
            <button
              onClick={() => openWorkspace(sessionWorkspacePath)}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-cyan-500/40 text-cyan-300 text-xs font-mono font-medium transition-colors"
              title={`Switch active workspace to ${sessionWorkspacePath}`}
            >
              <FolderOpen className="w-3.5 h-3.5 text-cyan-400" />
              <span>Switch to Session Workspace</span>
            </button>
          )}

          <button
            onClick={() => navigate(`/sessions/${sessionId}/live`)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-cyan-950/60 hover:bg-cyan-900/60 border border-cyan-500/40 text-cyan-300 text-xs font-mono font-semibold transition-colors"
          >
            <Play className="w-3.5 h-3.5 fill-current" />
            Live DAG Theater
          </button>

          {isRunning && (
            <button
              onClick={handleCancel}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-amber-950/60 hover:bg-amber-900/60 border border-amber-500/40 text-amber-300 text-xs font-mono font-semibold transition-colors"
              title="Cancel running orchestration"
            >
              <XCircle className="w-3.5 h-3.5 text-amber-400" />
              <span>Cancel Run</span>
            </button>
          )}

          {(detail.status === 'STOPPED' || detail.status === 'FAILED') && (
            <button
              onClick={handleResume}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-mono font-bold transition-all shadow-md"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Resume Session
            </button>
          )}

          <button
            onClick={handleDelete}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-rose-950/40 hover:bg-rose-900/60 border border-rose-500/30 text-rose-300 text-xs font-mono font-semibold transition-colors"
            title="Delete session permanently"
          >
            <Trash2 className="w-3.5 h-3.5 text-rose-400" />
            <span>Delete</span>
          </button>
        </div>
      </div>


      {/* Navigation Tabs */}
      <div className="flex items-center gap-1.5 border-b border-slate-800 overflow-x-auto pb-1">
        {[
          { id: 'overview', label: 'Overview', icon: FolderKanban },
          { id: 'runtime', label: 'Runtime & Preview', icon: Globe },
          { id: 'workspace', label: 'IDE & Filesystem', icon: FileCode },
          { id: 'dag', label: 'DAG Execution', icon: GitBranch },
          { id: 'messages', label: 'Messages & Trace', icon: MessageSquare },
          { id: 'verification', label: 'Verification Evidence', icon: ShieldCheck },
          { id: 'diff', label: 'Git Diff View', icon: GitBranch },
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
                  ? 'bg-slate-900 text-cyan-400 border-t border-x border-slate-800 font-bold shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/50'
              }`}
            >
              <Icon className="w-4 h-4" />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* TAB 1: OVERVIEW */}
      {activeTab === 'overview' && (
        <div className="space-y-6">
          {/* Header Stats Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <div className="bg-slate-900 border border-slate-800 p-3.5 rounded-2xl shadow-sm">
              <span className="text-[10px] font-mono text-slate-400 block mb-1">SCORE</span>
              <span className={`text-xl font-bold font-mono ${detail.score >= 80 ? 'text-emerald-400' : 'text-amber-400'}`}>
                {detail.score} / 100
              </span>
            </div>
            <div className="bg-slate-900 border border-slate-800 p-3.5 rounded-2xl shadow-sm">
              <span className="text-[10px] font-mono text-slate-400 block mb-1">REPLANS</span>
              <span className="text-xl font-bold font-mono text-slate-100">
                {detail.iteration} / {detail.max_iterations}
              </span>
            </div>
            <div className="bg-slate-900 border border-slate-800 p-3.5 rounded-2xl shadow-sm">
              <span className="text-[10px] font-mono text-slate-400 block mb-1">DURATION</span>
              <span className="text-xl font-bold font-mono text-slate-100">
                {Math.round(detail.duration_seconds || 0)}s
              </span>
            </div>
            <div className="bg-slate-900 border border-slate-800 p-3.5 rounded-2xl shadow-sm">
              <span className="text-[10px] font-mono text-slate-400 block mb-1">TOTAL COST</span>
              <span className="text-xl font-bold font-mono text-emerald-400">
                ${(detail.total_cost_usd || 0).toFixed(4)}
              </span>
            </div>
            <div className="bg-slate-900 border border-slate-800 p-3.5 rounded-2xl shadow-sm">
              <span className="text-[10px] font-mono text-slate-400 block mb-1">TOKENS</span>
              <span className="text-xl font-bold font-mono text-cyan-400">
                {Math.round((detail.total_tokens || 0) / 1000)}k
              </span>
            </div>
            <div className="bg-slate-900 border border-slate-800 p-3.5 rounded-2xl shadow-sm">
              <span className="text-[10px] font-mono text-slate-400 block mb-1">COMPLETED TASKS</span>
              <span className="text-xl font-bold font-mono text-emerald-400">
                {detail.execution_summary?.completed_tasks || 0} / {detail.execution_summary?.total_tasks || 0}
              </span>
            </div>
          </div>

          {/* Project Runtime & Preview Subsystem Card */}
          <ProjectRuntimeCard sessionId={sessionId} workspacePath={sessionWorkspacePath} />

          {/* Reproducibility Card */}
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg space-y-3">
            <div className="flex items-center justify-between pb-2 border-b border-slate-800">
              <h3 className="text-xs font-mono font-bold text-slate-200 uppercase flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-cyan-400" />
                Deterministic Reproducibility Manifest
              </h3>
              <span className="text-[11px] font-mono text-slate-400">Seed: {detail.reproducibility?.seed || 42}</span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 text-xs font-mono">
              <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                <span className="text-[10px] text-slate-400 block mb-1">WORKSPACE DIRECTORY</span>
                <span className="text-slate-200 font-bold truncate block" title={sessionWorkspacePath}>
                  {sessionWorkspacePath || 'workspace-local'}
                </span>
              </div>
              <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                <span className="text-[10px] text-slate-400 block mb-1">MANIFEST HASH (12-CHAR)</span>
                <div className="flex items-center justify-between">
                  <span className="text-cyan-400 font-bold">
                    {(detail.reproducibility?.manifest_hash || 'a1b2c3d4e5f6').slice(0, 12)}
                  </span>
                  <button
                    onClick={() => handleCopy(detail.reproducibility?.manifest_hash || '')}
                    className="p-0.5 hover:text-white text-slate-400"
                  >
                    {copiedHash ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                </div>
              </div>
              <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                <span className="text-[10px] text-slate-400 block mb-1">GIT WORKING TREE</span>
                <span className={`font-bold ${detail.reproducibility?.git_dirty ? 'text-amber-400' : 'text-emerald-400'}`}>
                  {detail.reproducibility?.git_dirty ? 'DIRTY (Uncommitted)' : 'CLEAN'}
                </span>
              </div>
              <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                <span className="text-[10px] text-slate-400 block mb-1">ORCHESTRATOR VERSION</span>
                <span className="text-slate-200 font-bold">
                  v{detail.reproducibility?.orchestrator_version || '2.0'}
                </span>
              </div>
            </div>
          </div>

          {/* Final Reviewer Report Panel */}
          {detail.final_report && (
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
              <div className="flex items-center justify-between pb-3 border-b border-slate-800">
                <h3 className="text-sm font-bold font-mono text-slate-200 uppercase flex items-center gap-2">
                  <ShieldCheck className="w-5 h-5 text-emerald-400" />
                  Final Reviewer Quality Report & Verdict
                </h3>
                <StatusBadge status={detail.final_report.verdict} size="md" />
              </div>

              {/* Summary */}
              <div className="bg-slate-950 p-4 rounded-xl border border-slate-800">
                <h4 className="text-xs font-mono font-bold text-slate-400 uppercase mb-1.5">Executive Summary</h4>
                <p className="text-sm text-slate-200 font-sans leading-relaxed">
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
                      <li key={i} className="text-xs text-slate-300 bg-slate-950 p-2.5 rounded-xl border border-emerald-500/20 flex items-start gap-2">
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
                      <div key={i} className="bg-slate-950 p-3 rounded-xl border border-rose-500/30 text-xs space-y-1">
                        <div className="flex items-center justify-between">
                          <span className="font-bold text-rose-300 font-mono">{iss.title}</span>
                          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-rose-950 text-rose-400 border border-rose-500/30 uppercase">
                            {iss.severity}
                          </span>
                        </div>
                        <p className="text-slate-300">{iss.description}</p>
                        {iss.file_location && (
                          <span className="text-[11px] font-mono text-slate-400 block">
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

      {/* TAB: RUNTIME & PREVIEW */}
      {activeTab === 'runtime' && (
        <div className="space-y-4">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm">
            <h3 className="text-xs font-mono font-bold text-slate-300 uppercase mb-1">
              Isolated Project Application Subsystem
            </h3>
            <p className="text-xs text-slate-400">
              Run and preview user applications within the session's workspace (<code className="text-cyan-400">{sessionWorkspacePath}</code>).
              Process execution, ports, and dev servers are strictly decoupled from Agent System infrastructure.
            </p>
          </div>
          <ProjectRuntimeCard sessionId={sessionId} workspacePath={sessionWorkspacePath} />
        </div>
      )}

      {/* TAB 2: IDE & FILESYSTEM */}
      {activeTab === 'workspace' && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 h-[650px]">
            {/* Left: File Explorer */}
            <div className="lg:col-span-4 h-full">
              <FileExplorer
                selectedFile={selectedFile}
                onSelectFile={setSelectedFile}
                workspacePath={sessionWorkspacePath}
              />
            </div>

            {/* Right: Code Editor & Viewer */}
            <div className="lg:col-span-8 h-full">
              {selectedFile ? (
                <CodeEditor
                  filepath={selectedFile}
                  workspacePath={sessionWorkspacePath}
                  onClose={() => setSelectedFile(null)}
                />
              ) : (
                <div className="h-full bg-slate-900 border border-slate-800 rounded-2xl flex flex-col items-center justify-center text-slate-500 space-y-2">
                  <FileCode className="w-10 h-10 stroke-1 text-slate-600" />
                  <p className="text-xs font-mono">Select a file from the explorer to open and edit.</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* TAB 3: DAG EXECUTION */}
      {activeTab === 'dag' && <DAGCanvas dag={dag} />}

      {/* TAB 4: MESSAGES / TRACE */}
      {activeTab === 'messages' && (
        <div className="space-y-4">
          {/* Filter Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3 p-3 bg-slate-900 border border-slate-800 rounded-2xl">
            <div className="flex items-center gap-2 flex-1 max-w-sm">
              <input
                type="text"
                value={messageSearch}
                onChange={(e) => setMessageSearch(e.target.value)}
                placeholder="Search conversation trace..."
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-1.5 text-xs font-mono text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
              />
            </div>

            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-slate-400">AGENT:</span>
              <select
                value={agentFilter}
                onChange={(e) => setAgentFilter(e.target.value)}
                className="bg-slate-950 border border-slate-800 rounded-xl px-2.5 py-1.5 text-xs font-mono text-slate-200 focus:outline-none"
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
              <div className="p-8 text-center text-xs font-mono text-slate-500 bg-slate-900 border border-slate-800 rounded-2xl">
                No conversation messages match the current filters.
              </div>
            ) : (
              filteredMessages.map((msg) => (
                <div
                  key={msg.id}
                  className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm space-y-2.5"
                >
                  <div className="flex items-center justify-between pb-2 border-b border-slate-800/80">
                    <div className="flex items-center gap-2.5">
                      <AgentAvatar agentName={msg.agent} role={msg.role} size="sm" showRoleLabel={true} />
                      {msg.stage && (
                        <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-950 border border-slate-800 text-cyan-400 uppercase">
                          {msg.stage}
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] font-mono text-slate-400">
                      {new Date(msg.timestamp).toLocaleTimeString()}
                    </span>
                  </div>

                  <p className="text-xs text-slate-200 font-sans leading-relaxed whitespace-pre-wrap">
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

      {/* TAB 5: VERIFICATION EVIDENCE */}
      {activeTab === 'verification' && (
        <div className="space-y-6">
          <EvidenceMatrix evidence={evidence} />
          {spans.length > 0 && <TraceWaterfall spans={spans} />}
        </div>
      )}

      {/* TAB 6: FILES & DIFF */}
      {activeTab === 'diff' && <DiffViewer diffs={diffs} />}

      {/* TAB 7: RE-PLAN HISTORY */}
      {activeTab === 'replan' && <ReplanTimeline records={replans} />}
    </div>
  );
};
