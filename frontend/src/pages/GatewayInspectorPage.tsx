import React, { useState, useEffect } from 'react';
import { gatewayApi } from '../api/gatewayApi';
import { GatewayRoutingTrace } from '../types/gateway';
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  AlertTriangle,
  Repeat,
  Share2,
  Clock,
  Layers,
  Sparkles,
  Zap,
  Brain,
  Code2,
  Filter,
  RefreshCw,
  Search,
} from 'lucide-react';

interface GatewayInspectorPageProps {
  navigate: (route: string) => void;
}

export const GatewayInspectorPage: React.FC<GatewayInspectorPageProps> = () => {
  const [traces, setTraces] = useState<GatewayRoutingTrace[]>([]);
  const [selectedTrace, setSelectedTrace] = useState<GatewayRoutingTrace | null>(null);
  const [filterStatus, setFilterStatus] = useState<string>('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoading, setIsLoading] = useState(true);

  const fetchTraces = async () => {
    try {
      const data = await gatewayApi.getInspectorTraces(50);
      setTraces(data);
      if (data.length > 0 && !selectedTrace) {
        setSelectedTrace(data[0]);
      }
    } catch (err: any) {
      console.error('Failed to load routing traces:', err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchTraces();
    const interval = setInterval(fetchTraces, 4000);
    return () => clearInterval(interval);
  }, []);

  const filteredTraces = traces.filter((t) => {
    const matchesSearch =
      t.logical_mode.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.selected_provider.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.selected_model.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.trace_id.toLowerCase().includes(searchQuery.toLowerCase());

    if (!matchesSearch) return false;
    if (filterStatus === 'SUCCESS') return t.status === 'SUCCESS';
    if (filterStatus === 'RETRY') return t.status === 'RETRY' || t.retries > 0;
    if (filterStatus === 'FALLBACK') return t.status === 'FALLBACK' || t.fallbacks_taken.length > 0;
    if (filterStatus === 'ERROR') return t.status === 'ERROR';
    return true;
  });

  const getModeIcon = (mode: string) => {
    switch (mode.toLowerCase()) {
      case 'fast':
        return <Zap className="w-3.5 h-3.5 text-amber-400" />;
      case 'smart':
        return <Brain className="w-3.5 h-3.5 text-purple-400" />;
      case 'coder':
        return <Code2 className="w-3.5 h-3.5 text-emerald-400" />;
      default:
        return <Sparkles className="w-3.5 h-3.5 text-cyan-400" />;
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-5 bg-slate-900 border border-slate-800 rounded-2xl shadow-xl">
        <div className="flex items-center gap-3.5">
          <div className="w-11 h-11 rounded-xl bg-gradient-to-tr from-cyan-500 via-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
            <Activity className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold font-mono text-slate-100">
              Routing Inspector & Observability
            </h1>
            <p className="text-xs text-slate-400">
              End-to-End Decision Traceability • LiteLLM Candidate Selection • Fallback & Latency Verification
            </p>
          </div>
        </div>

        <button
          onClick={fetchTraces}
          className="flex items-center gap-2 px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-mono font-medium text-slate-200 border border-slate-700 cursor-pointer"
        >
          <RefreshCw className="w-3.5 h-3.5 text-cyan-400" />
          <span>Refresh Traces</span>
        </button>
      </div>

      {/* Visual Pipeline Flow Diagram (Matches prompt diagram) */}
      {selectedTrace && (
        <div className="p-6 bg-slate-900 border border-slate-800 rounded-2xl shadow-lg space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono font-bold text-slate-400 uppercase tracking-wider flex items-center gap-2">
              <Layers className="w-4 h-4 text-cyan-400" />
              Routing Decision Pipeline Trace ({selectedTrace.trace_id})
            </span>
            <span className="text-[11px] font-mono text-slate-500">
              Timestamp: {new Date(selectedTrace.timestamp).toLocaleTimeString()}
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            {/* Step 1: Logical Mode (Registry) */}
            <div className="p-4 rounded-xl bg-emerald-950/20 border border-emerald-500/40 relative">
              <div className="text-[10px] font-mono text-emerald-400 uppercase font-bold mb-1">
                Stage 1: Logical Mode
              </div>
              <div className="text-base font-mono font-bold text-slate-100 flex items-center gap-2">
                {getModeIcon(selectedTrace.logical_mode)}
                {selectedTrace.logical_mode.toUpperCase()}
              </div>
              <div className="text-[11px] text-slate-400 mt-1 font-mono">
                {selectedTrace.candidates_count} registry candidates
              </div>
            </div>

            {/* Step 2: LiteLLM Candidate Ranking */}
            <div className="p-4 rounded-xl bg-blue-950/20 border border-blue-500/40 relative">
              <div className="text-[10px] font-mono text-blue-400 uppercase font-bold mb-1">
                Stage 2: LiteLLM Ranking
              </div>
              <div className="text-sm font-mono font-bold text-slate-100 truncate">
                Latency Router
              </div>
              <div className="text-[11px] text-slate-400 mt-1 font-mono">
                Top pool: {selectedTrace.candidates_sample.slice(0, 2).join(', ') || 'Auto'}
              </div>
            </div>

            {/* Step 3: Retry / Fallback Engine */}
            <div className="p-4 rounded-xl bg-red-950/15 border border-red-500/30 relative">
              <div className="text-[10px] font-mono text-red-400 uppercase font-bold mb-1">
                Stage 3: Retry / Fallback
              </div>
              <div className="text-sm font-mono font-bold text-slate-100">
                {selectedTrace.fallbacks_taken.length > 0
                  ? `Fallback: ${selectedTrace.fallbacks_taken.join(' → ')}`
                  : 'Direct Pass (0 Retries)'}
              </div>
              <div className="text-[11px] text-slate-400 mt-1 font-mono">
                Retries: {selectedTrace.retries}
              </div>
            </div>

            {/* Step 4: Final Model Response */}
            <div className="p-4 rounded-xl bg-purple-950/20 border border-purple-500/40 relative">
              <div className="text-[10px] font-mono text-purple-400 uppercase font-bold mb-1">
                Stage 4: Final Execution
              </div>
              <div className="text-sm font-mono font-bold text-purple-300 truncate">
                {selectedTrace.selected_provider}/{selectedTrace.selected_model}
              </div>
              <div className="text-[11px] text-slate-400 mt-1 font-mono flex items-center justify-between">
                <span>{selectedTrace.latency_ms} ms</span>
                <span className="text-emerald-400 font-bold">{selectedTrace.status}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Main Content Layout: Trace List & Details */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column: Trace Stream & Filters */}
        <div className="lg:col-span-1 space-y-3">
          {/* Search & Filters */}
          <div className="p-3 bg-slate-900 border border-slate-800 rounded-xl space-y-2">
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                placeholder="Filter traces..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-8 pr-3 py-1.5 text-xs font-mono text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
              />
            </div>

            <div className="flex flex-wrap gap-1 text-[10px] font-mono pt-1">
              {['ALL', 'SUCCESS', 'RETRY', 'FALLBACK', 'ERROR'].map((st) => (
                <button
                  key={st}
                  onClick={() => setFilterStatus(st)}
                  className={`px-2 py-0.5 rounded border transition-all cursor-pointer ${
                    filterStatus === st
                      ? 'bg-cyan-950/50 text-cyan-300 border-cyan-500/50 font-bold'
                      : 'bg-slate-950 text-slate-400 border-slate-800 hover:border-slate-700'
                  }`}
                >
                  {st}
                </button>
              ))}
            </div>
          </div>

          {/* Trace Stream List */}
          <div className="space-y-2 max-h-[580px] overflow-y-auto pr-1">
            {isLoading ? (
              <div className="p-8 text-center text-xs font-mono text-slate-500">Loading traces...</div>
            ) : filteredTraces.length === 0 ? (
              <div className="p-8 text-center text-xs font-mono text-slate-500 bg-slate-900 rounded-xl border border-slate-800">
                No matching routing traces found.
              </div>
            ) : (
              filteredTraces.map((t) => {
                const isSelected = selectedTrace?.trace_id === t.trace_id;
                return (
                  <div
                    key={t.trace_id}
                    onClick={() => setSelectedTrace(t)}
                    className={`p-3 rounded-xl border cursor-pointer transition-all text-xs font-mono select-none ${
                      isSelected
                        ? 'border-cyan-500 bg-slate-850 shadow-md ring-1 ring-cyan-500/40'
                        : 'border-slate-800 bg-slate-900 hover:bg-slate-850'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-1.5 font-bold text-slate-200">
                        {getModeIcon(t.logical_mode)}
                        <span className="uppercase">{t.logical_mode}</span>
                      </div>
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          t.status === 'SUCCESS'
                            ? 'bg-emerald-950/40 text-emerald-400 border border-emerald-800/40'
                            : t.status === 'RETRY'
                            ? 'bg-amber-950/40 text-amber-400 border border-amber-800/40'
                            : 'bg-red-950/40 text-red-400 border border-red-800/40'
                        }`}
                      >
                        {t.status}
                      </span>
                    </div>

                    <div className="text-[11px] text-slate-400 truncate">
                      {t.selected_provider} / {t.selected_model}
                    </div>

                    <div className="flex items-center justify-between text-[10px] text-slate-500 mt-1.5 pt-1.5 border-t border-slate-800/80">
                      <span>{t.latency_ms} ms</span>
                      <span>{new Date(t.timestamp).toLocaleTimeString()}</span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Right Column: Selected Trace Inspection Detail */}
        <div className="lg:col-span-2">
          {selectedTrace ? (
            <div className="p-5 bg-slate-900 border border-slate-800 rounded-2xl space-y-5">
              <div className="flex items-center justify-between border-b border-slate-800 pb-4">
                <div>
                  <h3 className="text-base font-bold font-mono text-slate-100 flex items-center gap-2">
                    Routing Decision Inspector
                    <span className="text-xs font-mono text-slate-500">({selectedTrace.trace_id})</span>
                  </h3>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Full execution forensics, candidate deployments, and LiteLLM selection rationale
                  </p>
                </div>

                <div className="text-right font-mono">
                  <div className="text-xs font-bold text-cyan-400">{selectedTrace.latency_ms} ms</div>
                  <div className="text-[10px] text-slate-500">Latency</div>
                </div>
              </div>

              {/* Forensic Details Grid */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono">
                <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                  <span className="text-[10px] text-slate-500 uppercase block mb-1">Logical Mode</span>
                  <span className="font-bold text-cyan-400 uppercase">{selectedTrace.logical_mode}</span>
                </div>
                <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                  <span className="text-[10px] text-slate-500 uppercase block mb-1">Routed Provider</span>
                  <span className="font-bold text-indigo-300">{selectedTrace.selected_provider}</span>
                </div>
                <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                  <span className="text-[10px] text-slate-500 uppercase block mb-1">Physical Model</span>
                  <span className="font-bold text-slate-200 truncate block">{selectedTrace.selected_model}</span>
                </div>
                <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                  <span className="text-[10px] text-slate-500 uppercase block mb-1">Tokens Used</span>
                  <span className="font-bold text-emerald-400">{selectedTrace.tokens_used || '--'}</span>
                </div>
              </div>

              {/* LiteLLM Deployment Candidate Pool */}
              <div className="space-y-2">
                <span className="text-xs font-mono font-bold text-slate-400 uppercase block">
                  Eligible Candidate Deployments Considered ({selectedTrace.candidates_count})
                </span>
                <div className="bg-slate-950 p-3 rounded-xl border border-slate-800 space-y-1.5">
                  {selectedTrace.candidates_sample.length > 0 ? (
                    selectedTrace.candidates_sample.map((c, i) => (
                      <div
                        key={i}
                        className={`flex items-center justify-between text-xs font-mono px-2.5 py-1.5 rounded ${
                          c.includes(selectedTrace.selected_model)
                            ? 'bg-cyan-950/40 text-cyan-300 border border-cyan-800/40 font-bold'
                            : 'text-slate-400'
                        }`}
                      >
                        <span>{c}</span>
                        {c.includes(selectedTrace.selected_model) && (
                          <span className="text-[10px] font-bold text-cyan-400 uppercase">
                            SELECTED BY LITELLM
                          </span>
                        )}
                      </div>
                    ))
                  ) : (
                    <div className="text-xs text-slate-500">Auto-routed across all accessible free models.</div>
                  )}
                </div>
              </div>

              {/* Fallback & Retry Analysis */}
              <div className="space-y-2">
                <span className="text-xs font-mono font-bold text-slate-400 uppercase block">
                  Failover & Resilience Lineage
                </span>
                <div className="bg-slate-950 p-3 rounded-xl border border-slate-800 text-xs font-mono text-slate-300 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">Native LiteLLM Retries:</span>
                    <span className="font-bold text-slate-200">{selectedTrace.retries}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">Fallback Sequence:</span>
                    <span className="font-bold text-slate-200">
                      {selectedTrace.fallbacks_taken.length > 0
                        ? selectedTrace.fallbacks_taken.join(' → ')
                        : 'None (Direct First Attempt Success)'}
                    </span>
                  </div>
                  {selectedTrace.error && (
                    <div className="p-2 rounded bg-red-950/30 border border-red-900/40 text-red-400 text-xs">
                      Error details: {selectedTrace.error}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className="p-12 text-center text-xs font-mono text-slate-500 bg-slate-900 rounded-2xl border border-slate-800">
              Select a trace from the left stream to inspect routing decision details.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
