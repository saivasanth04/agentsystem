import React, { useState, useEffect, useRef } from 'react';
import { ProjectRuntimeInfo, ProjectRuntimeLog } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import { orchestratorWS } from '../api/websocket';
import {
  Play,
  Square,
  RotateCcw,
  ExternalLink,
  Terminal,
  Activity,
  CheckCircle2,
  AlertCircle,
  Clock,
  Globe,
  Maximize2,
  Minimize2,
  Trash2,
  RefreshCw,
  Cpu,
  Layers,
  ShieldCheck,
  ChevronDown,
  ChevronUp
} from 'lucide-react';

interface ProjectRuntimeCardProps {
  sessionId: string;
  workspacePath?: string;
}

export const ProjectRuntimeCard: React.FC<ProjectRuntimeCardProps> = ({ sessionId, workspacePath }) => {
  const [runtime, setRuntime] = useState<ProjectRuntimeInfo | null>(null);
  const [logs, setLogs] = useState<ProjectRuntimeLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [customCommand, setCustomCommand] = useState('');
  const [customPort, setCustomPort] = useState<number | ''>('');
  const [showConfig, setShowConfig] = useState(false);
  const [showIframe, setShowIframe] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [streamFilter, setStreamFilter] = useState<'ALL' | 'stdout' | 'stderr' | 'system'>('ALL');
  const logContainerRef = useRef<HTMLDivElement>(null);

  // Load active runtime for session
  const fetchRuntime = async () => {
    try {
      const res = await orchestratorApi.getSessionRuntime(sessionId);
      if (res.active_runtime) {
        setRuntime(res.active_runtime);
        // If command input empty, populate with runtime command
        if (!customCommand) {
          setCustomCommand(res.active_runtime.command);
        }
      } else {
        setRuntime(null);
      }
    } catch (err) {
      console.error('Failed to fetch session runtime:', err);
    }
  };

  // Fetch recent logs
  const fetchLogs = async (runtimeId: string) => {
    try {
      const res = await orchestratorApi.getRuntimeLogs(sessionId, runtimeId, 200);
      setLogs(res.logs || []);
    } catch {
      // ignore log fetch error
    }
  };

  useEffect(() => {
    fetchRuntime();
  }, [sessionId]);

  // Polling while active
  useEffect(() => {
    if (!runtime || ['STOPPED', 'FAILED'].includes(runtime.status)) return;
    const interval = setInterval(() => {
      fetchRuntime();
      if (runtime?.runtime_id) {
        fetchLogs(runtime.runtime_id);
      }
    }, 2500);
    return () => clearInterval(interval);
  }, [runtime?.status, runtime?.runtime_id]);

  // Real-time WebSocket event listener
  useEffect(() => {
    const unsub = orchestratorWS.subscribe((ev) => {
      if (ev.payload?.session_id === sessionId || ev.session_id === sessionId) {
        if (ev.event_type.startsWith('RUNTIME_')) {
          fetchRuntime();
          if (ev.event_type === 'RUNTIME_LOG' && ev.payload) {
            setLogs((prev) => [
              ...prev,
              {
                stream: ev.payload.stream || 'stdout',
                text: ev.payload.text || '',
                timestamp: ev.payload.timestamp || new Date().toISOString(),
              },
            ].slice(-500));
          }
        }
      }
    });
    return () => unsub();
  }, [sessionId]);

  // Auto-scroll logs to bottom
  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const handleStart = async () => {
    setIsStarting(true);
    try {
      const res = await orchestratorApi.startProjectRuntime(
        sessionId,
        customCommand || undefined,
        customPort ? Number(customPort) : undefined
      );
      setRuntime(res.runtime);
      setShowConfig(false);
      setLogs([]);
    } catch (err: any) {
      alert(`Failed to start project runtime: ${err.message}`);
    } finally {
      setIsStarting(false);
    }
  };

  const handleStop = async () => {
    if (!runtime) return;
    try {
      await orchestratorApi.stopProjectRuntime(sessionId, runtime.runtime_id);
      setRuntime((prev) => (prev ? { ...prev, status: 'STOPPED', health: 'UNKNOWN' } : null));
    } catch (err: any) {
      alert(`Failed to stop runtime: ${err.message}`);
    }
  };

  const handleRestart = async () => {
    if (!runtime) return;
    try {
      const res = await orchestratorApi.restartProjectRuntime(sessionId, runtime.runtime_id);
      setRuntime(res.runtime);
      setLogs([]);
    } catch (err: any) {
      alert(`Failed to restart runtime: ${err.message}`);
    }
  };

  const isRunning = runtime && ['RUNNING', 'PORT_DETECTED', 'HEALTHY', 'STARTING'].includes(runtime.status);
  const isHealthy = runtime?.health === 'HEALTHY' || runtime?.status === 'HEALTHY';
  const previewUrl = runtime?.preview_url;

  const filteredLogs = logs.filter(
    (l) => streamFilter === 'ALL' || l.stream === streamFilter
  );

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl shadow-xl overflow-hidden font-sans space-y-0">
      {/* Top Banner: Status, Preview URL & Lifecycle Buttons */}
      <div className="p-4 bg-slate-950/70 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center space-x-3">
          <div className="w-9 h-9 rounded-xl bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center shrink-0">
            <Globe className="w-5 h-5 text-cyan-400" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono font-bold text-slate-100 uppercase tracking-wide">
                Project Runtime & Dev Server
              </span>
              {/* Dynamic Status Badge */}
              {runtime ? (
                <span
                  className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-mono font-bold border uppercase tracking-wider ${
                    isHealthy
                      ? 'bg-emerald-950/80 text-emerald-300 border-emerald-500/40 animate-pulse'
                      : runtime.status === 'STARTING' || runtime.status === 'RUNNING'
                      ? 'bg-amber-950/80 text-amber-300 border-amber-500/40'
                      : runtime.status === 'STOPPED'
                      ? 'bg-slate-800 text-slate-400 border-slate-700'
                      : 'bg-rose-950/80 text-rose-300 border-rose-500/40'
                  }`}
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      isHealthy
                        ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]'
                        : runtime.status === 'STARTING'
                        ? 'bg-amber-400 animate-spin'
                        : 'bg-slate-500'
                    }`}
                  />
                  {runtime.status}
                </span>
              ) : (
                <span className="text-[10px] font-mono text-slate-500 px-2 py-0.5 rounded bg-slate-800">
                  INACTIVE
                </span>
              )}

              {/* Port Badge */}
              {runtime?.port && (
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-cyan-950/60 border border-cyan-500/30 text-cyan-300 font-bold">
                  PORT {runtime.port}
                </span>
              )}
            </div>

            <div className="flex items-center gap-3 text-[11px] font-mono text-slate-400 mt-0.5">
              <span>{runtime?.command || 'Auto-detecting build/dev command'}</span>
              {runtime?.pid && <span>PID: {runtime.pid}</span>}
              <span className="text-slate-500 text-[10px]">
                (Ports 3000, 8000, 8080 reserved for IDE)
              </span>
            </div>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2">
          {isRunning ? (
            <>
              {previewUrl && (
                <>
                  <a
                    href={previewUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-mono text-xs font-semibold shadow-md shadow-cyan-900/30 transition-all"
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                    <span>Open Preview</span>
                  </a>
                  <button
                    onClick={() => setShowIframe(!showIframe)}
                    className="flex items-center gap-1 px-2.5 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white font-mono text-xs transition-colors"
                    title={showIframe ? 'Collapse in-app preview' : 'Embed live preview window'}
                  >
                    {showIframe ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
                    <span>{showIframe ? 'Hide Frame' : 'Live Frame'}</span>
                  </button>
                </>
              )}

              <button
                onClick={handleRestart}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white font-mono text-xs transition-colors"
                title="Restart dev server"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>Restart</span>
              </button>

              <button
                onClick={handleStop}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-xl bg-rose-950/60 hover:bg-rose-900/80 border border-rose-500/40 text-rose-300 hover:text-white font-mono text-xs transition-colors"
                title="Stop dev server and kill process tree"
              >
                <Square className="w-3.5 h-3.5 fill-rose-300" />
                <span>Stop</span>
              </button>
            </>
          ) : (
            <div className="flex items-center gap-2">
              <button
                onClick={handleStart}
                disabled={isStarting}
                className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-mono text-xs font-semibold shadow-md shadow-emerald-900/30 transition-all"
              >
                <Play className="w-3.5 h-3.5 fill-white" />
                <span>{isStarting ? 'Starting...' : 'Start Dev Server'}</span>
              </button>
              <button
                onClick={() => setShowConfig(!showConfig)}
                className="p-1.5 rounded-xl text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors"
                title="Configure custom command and port"
              >
                {showConfig ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Optional Command Configuration Drawer */}
      {showConfig && (
        <div className="p-4 bg-slate-950/90 border-b border-slate-800 text-xs font-mono space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="md:col-span-2">
              <label className="text-[10px] uppercase text-slate-400 font-bold block mb-1">
                Custom Start / Dev Command (leave blank to auto-detect from package.json / python)
              </label>
              <input
                type="text"
                value={customCommand}
                onChange={(e) => setCustomCommand(e.target.value)}
                placeholder="e.g. npm run dev -- -p 5173, python -m uvicorn main:app --port 8001"
                className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
              />
            </div>
            <div>
              <label className="text-[10px] uppercase text-slate-400 font-bold block mb-1">
                Requested Port (Avoids 3000, 8000, 8080)
              </label>
              <input
                type="number"
                value={customPort}
                onChange={(e) => setCustomPort(e.target.value ? Number(e.target.value) : '')}
                placeholder="e.g. 5173 or 8001"
                className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
              />
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button
              onClick={() => setShowConfig(false)}
              className="px-3 py-1.5 rounded-lg text-slate-400 hover:text-slate-200"
            >
              Cancel
            </button>
            <button
              onClick={handleStart}
              className="px-4 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white font-bold"
            >
              Launch with Settings
            </button>
          </div>
        </div>
      )}

      {/* Embedded Live Iframe Window (if enabled) */}
      {showIframe && previewUrl && (
        <div className="border-b border-slate-800 bg-slate-950 p-2 space-y-1">
          <div className="flex items-center justify-between text-[11px] font-mono text-slate-400 px-2 py-1">
            <div className="flex items-center gap-2">
              <Globe className="w-3.5 h-3.5 text-cyan-400" />
              <span className="font-bold text-slate-200">{previewUrl}</span>
            </div>
            <a
              href={previewUrl}
              target="_blank"
              rel="noreferrer"
              className="hover:text-cyan-300 flex items-center gap-1"
            >
              <span>Full Screen</span>
              <ExternalLink className="w-3 h-3" />
            </a>
          </div>
          <iframe
            src={previewUrl}
            title="Project Preview"
            className="w-full h-80 rounded-xl border border-slate-800 bg-white"
          />
        </div>
      )}

      {/* Live Stdout / Stderr Stream Console */}
      <div className="p-3 bg-slate-950 text-slate-300 font-mono text-xs space-y-2">
        <div className="flex items-center justify-between pb-2 border-b border-slate-800/80">
          <div className="flex items-center gap-2 text-slate-400 text-[11px]">
            <Terminal className="w-3.5 h-3.5 text-cyan-400" />
            <span>Process Logs ({filteredLogs.length})</span>
          </div>

          <div className="flex items-center gap-2 text-[10px]">
            {/* Stream Filter Pills */}
            <div className="flex bg-slate-900 border border-slate-800 rounded-lg p-0.5">
              {(['ALL', 'stdout', 'stderr', 'system'] as const).map((filter) => (
                <button
                  key={filter}
                  onClick={() => setStreamFilter(filter)}
                  className={`px-2 py-0.5 rounded font-mono ${
                    streamFilter === filter
                      ? 'bg-cyan-950 text-cyan-300 font-bold border border-cyan-500/30'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {filter}
                </button>
              ))}
            </div>

            <button
              onClick={() => setAutoScroll(!autoScroll)}
              className={`px-2 py-1 rounded border text-[10px] ${
                autoScroll ? 'border-cyan-500/40 text-cyan-300' : 'border-slate-800 text-slate-500'
              }`}
            >
              Auto-Scroll: {autoScroll ? 'ON' : 'OFF'}
            </button>
            <button
              onClick={() => setLogs([])}
              className="p-1 text-slate-500 hover:text-slate-300"
              title="Clear log console"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Scrollable Log Output */}
        <div
          ref={logContainerRef}
          className="h-44 overflow-y-auto space-y-1 font-mono text-[11px] select-text pr-1"
        >
          {filteredLogs.length === 0 ? (
            <div className="py-8 text-center text-slate-600">
              {isRunning
                ? 'Listening for process stdout/stderr...'
                : 'Project runtime stopped. Click "Start Dev Server" to launch.'}
            </div>
          ) : (
            filteredLogs.map((l, i) => (
              <div
                key={i}
                className={`flex items-start gap-2 leading-tight ${
                  l.stream === 'stderr'
                    ? 'text-rose-400'
                    : l.stream === 'system'
                    ? 'text-cyan-400 font-bold'
                    : 'text-slate-300'
                }`}
              >
                <span className="text-slate-600 text-[10px] shrink-0 select-none">
                  {new Date(l.timestamp).toLocaleTimeString()}
                </span>
                <span className="text-[10px] uppercase font-bold text-slate-500 shrink-0 select-none">
                  [{l.stream}]
                </span>
                <span className="break-all whitespace-pre-wrap">{l.text}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};
