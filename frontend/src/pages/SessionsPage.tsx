import React, { useState, useEffect } from 'react';
import { SessionSummary } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import { StatusBadge } from '../components/StatusBadge';
import {
  FolderKanban,
  Search,
  Download,
  ArrowRight,
  RefreshCw,
  Trash2
} from 'lucide-react';

interface SessionsPageProps {
  navigate: (route: string) => void;
}

export const SessionsPage: React.FC<SessionsPageProps> = ({ navigate }) => {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [verdictFilter, setVerdictFilter] = useState('ALL');

  const loadSessions = async () => {
    setLoading(true);
    try {
      const data = await orchestratorApi.getSessions();
      setSessions(data.sessions || []);
    } catch (err) {
      console.error('Failed to load sessions:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteSession = async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Are you sure you want to delete session "${sessionId}" and all associated artifacts? This action cannot be undone.`)) {
      return;
    }
    setDeletingId(sessionId);
    try {
      await orchestratorApi.deleteSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
    } catch (err: any) {
      alert(`Failed to delete session: ${err.message || err}`);
    } finally {
      setDeletingId(null);
    }
  };


  useEffect(() => {
    loadSessions();
  }, []);

  const filteredSessions = sessions.filter((s) => {
    const matchesSearch =
      searchQuery === '' ||
      `${s.session_id} ${s.user_request}`.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = statusFilter === 'ALL' || s.status === statusFilter;
    const matchesVerdict = verdictFilter === 'ALL' || s.verdict === verdictFilter;
    return matchesSearch && matchesStatus && matchesVerdict;
  });

  const handleExportJson = () => {
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(filteredSessions, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', dataStr);
    downloadAnchor.setAttribute('download', `orchestrator_sessions_${Date.now()}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6 pb-12">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold font-sans text-slate-100 tracking-tight flex items-center gap-2.5">
            <FolderKanban className="w-6 h-6 text-cyan-400" />
            Orchestration Sessions
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-1">
            Historical ledger of all multi-agent workflows, verdicts, and workspace resource consumption
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={loadSessions}
            className="p-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-slate-100 transition-colors"
            title="Refresh Sessions"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-cyan-400' : ''}`} />
          </button>
          <button
            onClick={handleExportJson}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-200 text-xs font-mono transition-colors"
          >
            <Download className="w-4 h-4" />
            Export JSON
          </button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 p-4 bg-slate-900 border border-slate-800 rounded-2xl shadow-md">
        <div className="flex items-center gap-3 flex-1 min-w-[280px]">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Filter by session ID or request query..."
              className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-9 pr-4 py-2 text-xs font-mono text-slate-100 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
            />
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Status Filter */}
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono text-slate-400">STATUS:</span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="bg-slate-950 border border-slate-800 rounded-xl px-2.5 py-1.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
            >
              <option value="ALL">All Statuses</option>
              <option value="COMPLETED">Completed</option>
              <option value="IN_PROGRESS">In Progress</option>
              <option value="FAILED">Failed</option>
              <option value="STOPPED">Stopped</option>
              <option value="NEED_VERIFICATION">Need Verification</option>
            </select>
          </div>

          {/* Verdict Filter */}
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono text-slate-400">VERDICT:</span>
            <select
              value={verdictFilter}
              onChange={(e) => setVerdictFilter(e.target.value)}
              className="bg-slate-950 border border-slate-800 rounded-xl px-2.5 py-1.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
            >
              <option value="ALL">All Verdicts</option>
              <option value="PASS">PASS</option>
              <option value="FAIL">FAIL</option>
              <option value="UNDECIDED">UNDECIDED</option>
            </select>
          </div>
        </div>
      </div>

      {/* Sessions Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-slate-950 text-slate-400 uppercase border-b border-slate-800">
              <tr>
                <th className="py-3 px-4">Session ID</th>
                <th className="py-3 px-4">User Request</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4">Verdict</th>
                <th className="py-3 px-4">Iterations</th>
                <th className="py-3 px-4">Tokens</th>
                <th className="py-3 px-4">Cost</th>
                <th className="py-3 px-4">Date</th>
                <th className="py-3 px-4 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/40">
              {filteredSessions.length === 0 ? (
                <tr>
                  <td colSpan={9} className="py-12 text-center text-slate-500">
                    No matching sessions found.
                  </td>
                </tr>
              ) : (
                filteredSessions.map((s) => (
                  <tr
                    key={s.session_id}
                    onClick={() => navigate(`/sessions/${s.session_id}`)}
                    className="hover:bg-slate-800/40 cursor-pointer transition-colors group"
                  >
                    <td className="py-3 px-4 font-bold text-cyan-400">
                      {s.session_id.slice(0, 12)}...
                    </td>
                    <td className="py-3 px-4 max-w-sm truncate text-slate-200 font-sans font-medium">
                      {s.user_request}
                    </td>
                    <td className="py-3 px-4">
                      <StatusBadge status={s.status} size="sm" />
                    </td>
                    <td className="py-3 px-4">
                      <StatusBadge status={s.verdict} size="sm" />
                    </td>
                    <td className="py-3 px-4 text-slate-400">
                      {s.iteration}/{s.max_iterations}
                    </td>
                    <td className="py-3 px-4 text-slate-400">
                      {Math.round(s.total_tokens / 1000)}k
                    </td>
                    <td className="py-3 px-4 font-semibold text-emerald-400">
                      ${s.total_cost_usd.toFixed(4)}
                    </td>
                    <td className="py-3 px-4 text-slate-500">
                      {new Date(s.created_at).toLocaleDateString()}
                    </td>
                    <td className="py-3 px-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={(e) => handleDeleteSession(s.session_id, e)}
                          disabled={deletingId === s.session_id}
                          className="p-1.5 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-rose-950/40 transition-colors"
                          title="Delete session"
                        >
                          <Trash2 className={`w-3.5 h-3.5 ${deletingId === s.session_id ? 'animate-spin' : ''}`} />
                        </button>
                        <span className="text-cyan-400 group-hover:translate-x-0.5 inline-flex items-center gap-1 transition-transform">
                          Detail <ArrowRight className="w-3.5 h-3.5" />
                        </span>
                      </div>
                    </td>

                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
