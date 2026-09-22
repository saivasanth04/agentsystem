import React, { useState } from 'react';
import { DAGSnapshot, ExecutableTask } from '../types/orchestrator';
import { StatusBadge } from './StatusBadge';
import { AgentAvatar } from './AgentAvatar';
import {
  Layers,
  ArrowRight,
  CheckCircle2,
  AlertCircle,
  Clock,
  Wrench,
  Sparkles,
  Maximize2,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  List,
  GitBranch,
  X,
  Code2,
  FileCheck,
  Terminal,
  Activity
} from 'lucide-react';

interface DAGCanvasProps {
  dag: DAGSnapshot | null;
  onTaskSelect?: (task: ExecutableTask) => void;
  selectedTaskId?: string | null;
  className?: string;
}

export const DAGCanvas: React.FC<DAGCanvasProps> = ({
  dag,
  onTaskSelect,
  selectedTaskId,
  className = '',
}) => {
  const [zoom, setZoom] = useState<number>(1);
  const [viewMode, setViewMode] = useState<'wave' | 'list'>('wave');
  const [activeTaskDrawer, setActiveTaskDrawer] = useState<ExecutableTask | null>(null);

  if (!dag || !dag.nodes || dag.nodes.length === 0) {
    return (
      <div className={`flex flex-col items-center justify-center p-12 bg-bg-panel border border-border-subtle rounded-xl text-center ${className}`}>
        <GitBranch className="w-12 h-12 text-content-muted mb-3 animate-pulse" />
        <h4 className="text-base font-semibold text-content-primary">No DAG Plan Available</h4>
        <p className="text-sm text-content-secondary max-w-sm mt-1">
          When a task is decomposed by the Planner agent, the executable dependency graph will render here in real time.
        </p>
      </div>
    );
  }

  // Calculate waves if not provided
  const nodes = dag.nodes;
  const waves = dag.waves && dag.waves.length > 0 
    ? dag.waves 
    : [nodes.map(n => n.id)];

  const handleNodeClick = (task: ExecutableTask) => {
    setActiveTaskDrawer(task);
    if (onTaskSelect) {
      onTaskSelect(task);
    }
  };

  return (
    <div className={`relative flex flex-col bg-bg-panel border border-border-subtle rounded-xl overflow-hidden shadow-2xl ${className}`}>
      {/* Canvas Toolbar */}
      <div className="flex items-center justify-between px-4 py-3 bg-bg-elevated/80 border-b border-border-subtle backdrop-blur">
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-2 text-xs font-mono font-semibold text-content-primary">
            <GitBranch className="w-4 h-4 text-accent-primary" />
            DAG GRAPH ({dag.completed_tasks}/{dag.total_tasks} COMPLETED)
          </span>
          {dag.active_wave !== undefined && (
            <span className="px-2 py-0.5 rounded bg-accent-primary/20 text-accent-primary border border-accent-primary/30 text-[11px] font-mono font-medium">
              WAVE {dag.active_wave + 1} OF {waves.length}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* View mode toggle */}
          <div className="flex bg-bg-base p-0.5 rounded-lg border border-border-subtle">
            <button
              onClick={() => setViewMode('wave')}
              className={`px-2.5 py-1 text-xs font-medium rounded-md flex items-center gap-1.5 transition-colors ${
                viewMode === 'wave' ? 'bg-accent-primary text-white' : 'text-content-secondary hover:text-content-primary'
              }`}
              title="Parallel Wave Layout"
            >
              <Layers className="w-3.5 h-3.5" />
              Waves
            </button>
            <button
              onClick={() => setViewMode('list')}
              className={`px-2.5 py-1 text-xs font-medium rounded-md flex items-center gap-1.5 transition-colors ${
                viewMode === 'list' ? 'bg-accent-primary text-white' : 'text-content-secondary hover:text-content-primary'
              }`}
              title="Sequential Task List"
            >
              <List className="w-3.5 h-3.5" />
              List
            </button>
          </div>

          {/* Zoom controls */}
          {viewMode === 'wave' && (
            <div className="flex items-center gap-1 bg-bg-base px-2 py-1 rounded-lg border border-border-subtle text-content-secondary">
              <button
                onClick={() => setZoom((z) => Math.max(0.6, z - 0.1))}
                className="hover:text-content-primary p-0.5"
                title="Zoom Out"
              >
                <ZoomOut className="w-3.5 h-3.5" />
              </button>
              <span className="text-[11px] font-mono px-1">{Math.round(zoom * 100)}%</span>
              <button
                onClick={() => setZoom((z) => Math.min(1.4, z + 0.1))}
                className="hover:text-content-primary p-0.5"
                title="Zoom In"
              >
                <ZoomIn className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => setZoom(1)}
                className="hover:text-content-primary p-0.5 ml-1 border-l border-border-subtle pl-1"
                title="Reset Zoom"
              >
                <RotateCcw className="w-3 h-3" />
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Main Graph Area */}
      <div className="relative flex-1 overflow-auto p-6 min-h-[420px] bg-bg-base/40">
        {viewMode === 'wave' ? (
          <div
            className="flex gap-8 transition-transform duration-200 origin-top-left"
            style={{ transform: `scale(${zoom})` }}
          >
            {waves.map((waveNodeIds, waveIdx) => {
              const isWaveActive = dag.active_wave === waveIdx;
              const waveNodes = waveNodeIds
                .map((id) => nodes.find((n) => n.id === id))
                .filter((n): n is ExecutableTask => !!n);

              return (
                <div
                  key={`wave-${waveIdx}`}
                  className={`flex flex-col min-w-[280px] max-w-[320px] rounded-xl border p-4 transition-all duration-300 ${
                    isWaveActive
                      ? 'bg-accent-primary/5 border-accent-primary/50 shadow-lg shadow-accent-primary/10 ring-1 ring-accent-primary/30'
                      : 'bg-bg-panel/60 border-border-subtle'
                  }`}
                >
                  {/* Wave Header */}
                  <div className="flex items-center justify-between pb-3 mb-3 border-b border-border-subtle">
                    <div className="flex items-center gap-2">
                      <span className="w-5 h-5 rounded-full bg-bg-elevated flex items-center justify-center text-[10px] font-mono font-bold text-accent-primary border border-accent-primary/30">
                        {waveIdx + 1}
                      </span>
                      <span className="text-xs font-semibold text-content-primary uppercase tracking-wider">
                        Wave {waveIdx + 1}
                      </span>
                    </div>
                    <span className="text-[11px] font-mono text-content-secondary">
                      {waveNodes.filter((n) => n.status === 'COMPLETED').length}/{waveNodes.length} tasks
                    </span>
                  </div>

                  {/* Wave Tasks */}
                  <div className="flex flex-col gap-3">
                    {waveNodes.map((task) => {
                      const isSelected = selectedTaskId === task.id || activeTaskDrawer?.id === task.id;
                      const isRunning = task.status === 'RUNNING' || task.status === 'IN_PROGRESS';

                      return (
                        <div
                          key={task.id}
                          onClick={() => handleNodeClick(task)}
                          className={`group relative p-3.5 rounded-lg border transition-all cursor-pointer select-none ${
                            isSelected
                              ? 'bg-bg-elevated border-accent-primary shadow-md ring-1 ring-accent-primary'
                              : 'bg-bg-panel hover:bg-bg-elevated/90 border-border-subtle hover:border-border-active'
                          } ${isRunning ? 'animate-pulse-node border-blue-500/80 bg-blue-950/20' : ''}`}
                        >
                          {/* Task ID & Status */}
                          <div className="flex items-center justify-between mb-2">
                            <span className="font-mono text-xs font-bold text-content-primary flex items-center gap-1.5">
                              <span className="w-2 h-2 rounded-full bg-accent-primary" />
                              {task.id}
                            </span>
                            <StatusBadge status={task.status} size="sm" />
                          </div>

                          {/* Task Description */}
                          <p className="text-xs text-content-secondary font-sans line-clamp-2 leading-relaxed mb-2.5">
                            {task.objective || task.description}
                          </p>

                          {/* Assigned Agent / Tools Footer */}
                          <div className="flex items-center justify-between pt-2 border-t border-border-subtle/50 text-[11px]">
                            {task.assigned_agent ? (
                              <AgentAvatar
                                agentName={task.assigned_agent}
                                role={task.assigned_agent}
                                size="sm"
                                showRoleLabel={false}
                              />
                            ) : (
                              <span className="text-content-muted font-mono">Unassigned</span>
                            )}

                            <div className="flex items-center gap-1.5 font-mono text-content-secondary">
                              {task.tools && task.tools.length > 0 && (
                                <span className="flex items-center gap-0.5 bg-bg-base px-1.5 py-0.5 rounded border border-border-subtle text-[10px]">
                                  <Wrench className="w-2.5 h-2.5 text-accent-info" />
                                  {task.tools.length}
                                </span>
                              )}
                              {task.acceptance_tests && task.acceptance_tests.length > 0 && (
                                <span className="flex items-center gap-0.5 bg-bg-base px-1.5 py-0.5 rounded border border-border-subtle text-[10px]">
                                  <FileCheck className="w-2.5 h-2.5 text-accent-success" />
                                  {task.acceptance_tests.length}
                                </span>
                              )}
                              {task.attempts !== undefined && task.attempts > 1 && (
                                <span className="text-accent-warning text-[10px]">
                                  att: {task.attempts}
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Dependency connectors visual indicator */}
                          {task.dependencies && task.dependencies.length > 0 && (
                            <div className="mt-2 text-[10px] font-mono text-content-muted flex items-center gap-1">
                              <span>Prereqs:</span>
                              <span className="truncate">{task.dependencies.join(', ')}</span>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          /* List Mode */
          <div className="flex flex-col gap-2 max-w-4xl mx-auto">
            {nodes.map((task, idx) => (
              <div
                key={task.id}
                onClick={() => handleNodeClick(task)}
                className={`flex items-center justify-between p-3 rounded-lg border transition-all cursor-pointer ${
                  activeTaskDrawer?.id === task.id
                    ? 'bg-bg-elevated border-accent-primary'
                    : 'bg-bg-panel hover:bg-bg-elevated border-border-subtle'
                }`}
              >
                <div className="flex items-center gap-3">
                  <span className="text-xs font-mono font-bold text-content-muted w-6">
                    #{idx + 1}
                  </span>
                  <span className="font-mono text-xs font-bold text-accent-primary">
                    {task.id}
                  </span>
                  <p className="text-xs text-content-primary font-medium truncate max-w-lg">
                    {task.objective || task.description}
                  </p>
                </div>

                <div className="flex items-center gap-4">
                  {task.assigned_agent && (
                    <span className="text-xs font-mono text-content-secondary">
                      {task.assigned_agent}
                    </span>
                  )}
                  <StatusBadge status={task.status} size="sm" />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Task Detail Drawer / Modal */}
      {activeTaskDrawer && (
        <div className="absolute inset-y-0 right-0 w-full md:w-[480px] bg-bg-elevated border-l border-border-subtle shadow-2xl z-30 flex flex-col animate-in slide-in-from-right duration-200">
          {/* Drawer Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle bg-bg-panel">
            <div className="flex items-center gap-2.5">
              <span className="font-mono text-sm font-bold text-accent-primary">
                {activeTaskDrawer.id}
              </span>
              <StatusBadge status={activeTaskDrawer.status} size="sm" />
            </div>
            <button
              onClick={() => setActiveTaskDrawer(null)}
              className="p-1 rounded-lg hover:bg-bg-elevated text-content-secondary hover:text-content-primary"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Drawer Content */}
          <div className="flex-1 overflow-y-auto p-5 space-y-5">
            {/* Objective & Description */}
            <div>
              <h5 className="text-xs font-mono uppercase text-content-muted mb-1.5">Task Objective</h5>
              <p className="text-sm text-content-primary font-medium leading-relaxed bg-bg-base p-3 rounded-lg border border-border-subtle">
                {activeTaskDrawer.objective || activeTaskDrawer.description}
              </p>
            </div>

            {/* Execution Metadata Grid */}
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-bg-panel p-3 rounded-lg border border-border-subtle">
                <span className="text-[10px] font-mono text-content-muted block mb-1">ASSIGNED AGENT</span>
                <span className="text-xs font-mono font-semibold text-content-primary">
                  {activeTaskDrawer.assigned_agent || 'Auto-Assigned'}
                </span>
              </div>
              <div className="bg-bg-panel p-3 rounded-lg border border-border-subtle">
                <span className="text-[10px] font-mono text-content-muted block mb-1">ATTEMPTS</span>
                <span className="text-xs font-mono font-semibold text-content-primary">
                  {activeTaskDrawer.attempts || 1} / {activeTaskDrawer.max_attempts || 3}
                </span>
              </div>
            </div>

            {/* Dependencies */}
            {activeTaskDrawer.dependencies && activeTaskDrawer.dependencies.length > 0 && (
              <div>
                <h5 className="text-xs font-mono uppercase text-content-muted mb-1.5">Prerequisites</h5>
                <div className="flex flex-wrap gap-1.5">
                  {activeTaskDrawer.dependencies.map((dep) => (
                    <span
                      key={dep}
                      className="px-2 py-0.5 rounded bg-bg-base border border-border-subtle font-mono text-xs text-content-secondary"
                    >
                      {dep}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Capabilities Required */}
            {activeTaskDrawer.capabilities && activeTaskDrawer.capabilities.length > 0 && (
              <div>
                <h5 className="text-xs font-mono uppercase text-content-muted mb-1.5">Required Capabilities</h5>
                <div className="flex flex-wrap gap-1.5">
                  {activeTaskDrawer.capabilities.map((cap) => (
                    <span
                      key={cap}
                      className="px-2 py-0.5 rounded bg-indigo-950/40 border border-indigo-500/30 font-mono text-xs text-indigo-300"
                    >
                      {cap}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Tools Granted */}
            {activeTaskDrawer.tools && activeTaskDrawer.tools.length > 0 && (
              <div>
                <h5 className="text-xs font-mono uppercase text-content-muted mb-1.5">Tools Available</h5>
                <div className="flex flex-wrap gap-1.5">
                  {activeTaskDrawer.tools.map((tool) => (
                    <span
                      key={tool}
                      className="px-2 py-0.5 rounded bg-blue-950/40 border border-blue-500/30 font-mono text-xs text-blue-300 flex items-center gap-1"
                    >
                      <Wrench className="w-2.5 h-2.5" />
                      {tool}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Acceptance Tests */}
            {activeTaskDrawer.acceptance_tests && activeTaskDrawer.acceptance_tests.length > 0 && (
              <div>
                <h5 className="text-xs font-mono uppercase text-content-muted mb-1.5">Acceptance Criteria</h5>
                <div className="space-y-1.5">
                  {activeTaskDrawer.acceptance_tests.map((test, i) => (
                    <div
                      key={i}
                      className="flex items-start gap-2 bg-bg-base p-2.5 rounded-lg border border-border-subtle text-xs text-content-secondary"
                    >
                      <FileCheck className="w-4 h-4 text-accent-success shrink-0 mt-0.5" />
                      <span>{test}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Tool History / Executions */}
            {activeTaskDrawer.tool_history && activeTaskDrawer.tool_history.length > 0 && (
              <div>
                <h5 className="text-xs font-mono uppercase text-content-muted mb-1.5">Tool Invocation History</h5>
                <div className="space-y-2">
                  {activeTaskDrawer.tool_history.map((th, idx) => (
                    <div
                      key={idx}
                      className="bg-bg-base p-3 rounded-lg border border-border-subtle font-mono text-xs space-y-1"
                    >
                      <div className="flex items-center justify-between text-content-primary font-semibold">
                        <span className="text-accent-info">{th.tool}</span>
                        <span className="text-[10px] text-content-muted">{th.timestamp}</span>
                      </div>
                      <div className="text-[11px] text-content-secondary overflow-x-auto">
                        <span className="text-content-muted">Input: </span>
                        {typeof th.input === 'string' ? th.input : JSON.stringify(th.input)}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Result / Output */}
            {activeTaskDrawer.result && (
              <div>
                <h5 className="text-xs font-mono uppercase text-content-muted mb-1.5">Task Result</h5>
                <pre className="bg-bg-base p-3 rounded-lg border border-emerald-500/30 text-emerald-300 font-mono text-xs whitespace-pre-wrap overflow-x-auto max-h-48">
                  {activeTaskDrawer.result}
                </pre>
              </div>
            )}

            {/* Error Message */}
            {activeTaskDrawer.error && (
              <div>
                <h5 className="text-xs font-mono uppercase text-rose-400 mb-1.5">Failure Error</h5>
                <pre className="bg-rose-950/30 p-3 rounded-lg border border-rose-500/40 text-rose-300 font-mono text-xs whitespace-pre-wrap overflow-x-auto">
                  {activeTaskDrawer.error}
                </pre>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
