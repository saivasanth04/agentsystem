import React, { useState, useEffect } from 'react';
import { DAGSnapshot, EventMessage, ExecutableTask } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import { orchestratorWS } from '../api/websocket';
import { DAGCanvas } from '../components/DAGCanvas';
import { EventStream } from '../components/EventStream';
import { TokenMeter } from '../components/TokenMeter';
import { StatusBadge } from '../components/StatusBadge';
import {
  GitBranch,
  Play,
  Pause,
  StopCircle,
  RotateCcw,
  Activity,
  Layers,
  Sparkles,
  Terminal,
  Clock,
  CheckCircle2,
  RefreshCw
} from 'lucide-react';

interface LiveDAGPageProps {
  sessionId?: string | null;
  navigate: (route: string) => void;
}

export const LiveDAGPage: React.FC<LiveDAGPageProps> = ({ sessionId: propSessionId, navigate }) => {
  const [activeSessionId, setActiveSessionId] = useState<string | null>(propSessionId || null);
  const [dag, setDag] = useState<DAGSnapshot | null>(null);
  const [events, setEvents] = useState<EventMessage[]>([]);
  const [status, setStatus] = useState<string>('IN_PROGRESS');
  const [costUsd, setCostUsd] = useState<number>(0.0);
  const [tokens, setTokens] = useState<number>(0);
  const [isPaused, setIsPaused] = useState(false);
  const [loading, setLoading] = useState(true);

  // If no session ID in props, find latest active or recent session
  useEffect(() => {
    if (!activeSessionId) {
      orchestratorApi.getSessions({ limit: 1 }).then((sessions) => {
        if (sessions.length > 0) {
          setActiveSessionId(sessions[0].session_id);
        }
      }).catch(console.error);
    }
  }, [activeSessionId]);

  // Load DAG and Session Details
  const refreshDAG = async () => {
    if (!activeSessionId) return;
    try {
      const [dagData, detail] = await Promise.all([
        orchestratorApi.getSessionDAG(activeSessionId).catch(() => null),
        orchestratorApi.getSessionDetail(activeSessionId).catch(() => null),
      ]);

      if (dagData) setDag(dagData);
      if (detail) {
        setStatus(detail.status);
        setCostUsd(detail.total_cost_usd);
        setTokens(detail.total_tokens);
      }
    } catch (err) {
      console.error('Failed to load live DAG:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshDAG();
    const interval = setInterval(refreshDAG, 4000);
    return () => clearInterval(interval);
  }, [activeSessionId]);

  // Subscribe to real-time WebSocket events
  useEffect(() => {
    const unsubscribe = orchestratorWS.subscribe((event) => {
      setEvents((prev) => [event, ...prev].slice(0, 500));

      // If event matches current session, trigger immediate DAG refresh
      if (!event.session_id || event.session_id === activeSessionId) {
        if (event.event_type.includes('TASK') || event.event_type.includes('REPLAN') || event.event_type.includes('WORKFLOW')) {
          refreshDAG();
        }
      }
    });

    return () => unsubscribe();
  }, [activeSessionId]);

  const handleCancel = async () => {
    if (!activeSessionId) return;
    if (confirm(`Cancel active orchestration for session ${activeSessionId}?`)) {
      try {
        await orchestratorApi.cancelSession(activeSessionId, 'Operator clicked Cancel in Live DAG');
        setStatus('STOPPED');
        refreshDAG();
      } catch (err: any) {
        alert(`Cancel failed: ${err.message}`);
      }
    }
  };

  const handleResume = async () => {
    if (!activeSessionId) return;
    try {
      await orchestratorApi.resumeSession(activeSessionId);
      setStatus('IN_PROGRESS');
      refreshDAG();
    } catch (err: any) {
      alert(`Resume failed: ${err.message}`);
    }
  };

  const handleRollback = async () => {
    if (!activeSessionId) return;
    const snapId = prompt('Enter checkpoint snapshot ID to rollback to:');
    if (snapId) {
      try {
        const res = await orchestratorApi.rollbackSession(activeSessionId, snapId);
        alert(`Rollback completed: ${res.restored_files} files restored.`);
        refreshDAG();
      } catch (err: any) {
        alert(`Rollback failed: ${err.message}`);
      }
    }
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Live Header & Control Bar */}
      <div className="bg-bg-panel border border-border-subtle rounded-2xl p-5 shadow-xl flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3.5">
          <div className="w-10 h-10 rounded-xl bg-accent-primary/20 border border-accent-primary/40 flex items-center justify-center">
            <GitBranch className="w-5 h-5 text-accent-primary" />
          </div>
          <div>
            <div className="flex items-center gap-2.5">
              <h1 className="text-base font-bold font-mono text-content-primary">
                Live DAG Execution Theater
              </h1>
              <StatusBadge status={status} size="sm" />
            </div>
            <span className="text-xs font-mono text-content-muted">
              SESSION: {activeSessionId || 'No active session'}
            </span>
          </div>
        </div>

        {/* Live Control Bar */}
        <div className="flex items-center gap-2.5">
          <button
            onClick={() => setIsPaused(!isPaused)}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-mono font-semibold border transition-all ${
              isPaused
                ? 'bg-amber-950/60 border-amber-500/50 text-amber-300'
                : 'bg-bg-base hover:bg-bg-elevated border-border-subtle text-content-secondary hover:text-content-primary'
            }`}
          >
            {isPaused ? <Play className="w-3.5 h-3.5 fill-amber-300" /> : <Pause className="w-3.5 h-3.5" />}
            {isPaused ? 'Resume Execution' : 'Pause DAG'}
          </button>

          <button
            onClick={handleRollback}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-bg-base hover:bg-bg-elevated border border-border-subtle text-content-secondary hover:text-content-primary text-xs font-mono transition-colors"
            title="Rollback to previous checkpoint"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Rollback
          </button>

          <button
            onClick={handleCancel}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-rose-950/60 hover:bg-rose-900/60 border border-rose-500/40 text-rose-300 text-xs font-mono font-bold transition-colors"
          >
            <StopCircle className="w-3.5 h-3.5" />
            Cancel Session
          </button>
        </div>
      </div>

      {/* Main Execution Canvas */}
      <div className="space-y-4">
        <DAGCanvas dag={dag} />
      </div>

      {/* Real-time Telemetry & Live Event Log Stream */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Token & Cost Telemetry Meter */}
        <div className="lg:col-span-1 space-y-4">
          <TokenMeter
            currentCostUsd={costUsd}
            maxCostUsd={5.0}
            currentTokens={tokens}
            maxTokens={500000}
          />
        </div>

        {/* Live WebSocket Event Feed */}
        <div className="lg:col-span-2">
          <EventStream events={events} onClear={() => setEvents([])} />
        </div>
      </div>
    </div>
  );
};
