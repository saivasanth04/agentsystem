import React, { useState } from 'react';
import { ModelTier, DAGSnapshot } from '../types/orchestrator';
import { ModelSelector } from '../components/ModelSelector';
import { DAGCanvas } from '../components/DAGCanvas';
import { orchestratorApi } from '../api/client';
import {
  Rocket,
  Sparkles,
  Sliders,
  DollarSign,
  Cpu,
  FolderTree,
  Eye,
  Play,
  Layers,
  CheckCircle2,
  AlertCircle
} from 'lucide-react';

interface NewTaskPageProps {
  navigate: (route: string) => void;
  onSessionLaunched?: (sessionId: string) => void;
}

export const NewTaskPage: React.FC<NewTaskPageProps> = ({ navigate, onSessionLaunched }) => {
  const [userRequest, setUserRequest] = useState('');
  const [modelTier, setModelTier] = useState<ModelTier>('BALANCED');
  const [roleOverrides, setRoleOverrides] = useState<Record<string, string>>({});
  const [maxReplanIterations, setMaxReplanIterations] = useState<number>(3);
  const [maxCostUsd, setMaxCostUsd] = useState<number>(5.0);
  const [maxTokens, setMaxTokens] = useState<number>(500000);
  const [contextBudget, setContextBudget] = useState<number>(100000);
  const [workspaceDir, setWorkspaceDir] = useState<string>('.');

  const [isDryRunning, setIsDryRunning] = useState(false);
  const [dryRunResult, setDryRunResult] = useState<{
    dag: DAGSnapshot;
    estimated_cost_usd: number;
    estimated_tokens: number;
  } | null>(null);

  const [isLaunching, setIsLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);

  const promptTemplates = [
    { label: '⚡ LRU Cache', prompt: 'Build a thread-safe LRU Cache with TTL expiration and comprehensive unit tests.' },
    { label: '🚀 FastAPI Endpoint', prompt: 'Add a new secure REST API endpoint in FastAPI with Pydantic v2 validation and rate limiting.' },
    { label: '🧪 Fix Failing Tests', prompt: 'Analyze failing unit tests, identify the root cause, fix the regression, and verify all tests pass.' },
    { label: '🛡️ Hardening & Auth', prompt: 'Harden API input validation against injection attacks and add JWT token verification.' },
  ];

  const handleRoleOverrideChange = (role: string, model: string) => {
    setRoleOverrides((prev) => ({ ...prev, [role]: model }));
  };

  const handleDryRun = async () => {
    if (!userRequest.trim()) {
      alert('Please enter a task description before running a dry run.');
      return;
    }

    setIsDryRunning(true);
    setLaunchError(null);
    try {
      const result = await orchestratorApi.dryRunTask({
        user_request: userRequest,
        config: {
          model_tier: modelTier,
          role_overrides: roleOverrides,
          max_replan_iterations: maxReplanIterations,
          max_cost_usd: maxCostUsd,
          max_tokens: maxTokens,
          workspace_dir: workspaceDir,
        },
      });
      setDryRunResult(result);
    } catch (err: any) {
      setLaunchError(`Dry run failed: ${err.message}`);
    } finally {
      setIsDryRunning(false);
    }
  };

  const handleLaunch = async () => {
    if (!userRequest.trim()) {
      alert('Please enter a task description before launching.');
      return;
    }

    setIsLaunching(true);
    setLaunchError(null);
    try {
      const result = await orchestratorApi.launchTask({
        user_request: userRequest,
        config: {
          model_tier: modelTier,
          role_overrides: roleOverrides,
          max_replan_iterations: maxReplanIterations,
          max_cost_usd: maxCostUsd,
          max_tokens: maxTokens,
          context_budget_tokens: contextBudget,
          workspace_dir: workspaceDir,
        },
      });

      if (onSessionLaunched) {
        onSessionLaunched(result.session_id);
      }
      navigate(`/sessions/${result.session_id}/live`);
    } catch (err: any) {
      setLaunchError(`Launch failed: ${err.message}`);
      setIsLaunching(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Page Header */}
      <div>
        <h1 className="text-2xl font-bold font-sans text-content-primary tracking-tight flex items-center gap-2.5">
          <Rocket className="w-6 h-6 text-accent-primary" />
          Task Launcher & Plan Studio
        </h1>
        <p className="text-xs font-mono text-content-secondary mt-1">
          Decompose natural language requests into parallel executable DAGs across specialized agent roles
        </p>
      </div>

      {launchError && (
        <div className="bg-rose-950/40 border border-rose-500/50 p-4 rounded-xl flex items-center gap-3 text-xs font-mono text-rose-300">
          <AlertCircle className="w-5 h-5 text-rose-400 shrink-0" />
          <span>{launchError}</span>
        </div>
      )}

      {/* Main Request Form */}
      <div className="bg-bg-panel border border-border-subtle rounded-xl p-6 shadow-xl space-y-5">
        <div>
          <label className="block text-xs font-mono font-bold text-content-primary uppercase mb-2">
            Task Description & Objectives
          </label>
          <textarea
            value={userRequest}
            onChange={(e) => setUserRequest(e.target.value)}
            placeholder="Describe what you want the multi-agent system to achieve (e.g. 'Implement a concurrent bounded task queue with worker pool, write unit tests, and verify 90% diff coverage')..."
            rows={5}
            className="w-full bg-bg-base border border-border-subtle rounded-xl p-4 text-sm text-content-primary placeholder-content-muted focus:outline-none focus:border-accent-primary font-sans leading-relaxed resize-y"
          />
        </div>

        {/* Template Prompt Chips */}
        <div>
          <span className="text-[11px] font-mono text-content-muted block mb-2">
            QUICK TEMPLATES:
          </span>
          <div className="flex flex-wrap gap-2">
            {promptTemplates.map((tpl, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setUserRequest(tpl.prompt)}
                className="px-3 py-1.5 rounded-lg bg-bg-base hover:bg-bg-elevated border border-border-subtle hover:border-accent-primary text-xs font-mono text-content-secondary hover:text-content-primary transition-all"
              >
                {tpl.label}
              </button>
            ))}
          </div>
        </div>

        {/* Configuration Accordion / Panel */}
        <div className="pt-4 border-t border-border-subtle space-y-6">
          <div className="flex items-center gap-2">
            <Sliders className="w-4 h-4 text-accent-primary" />
            <h3 className="text-xs font-mono font-bold text-content-primary uppercase">
              Execution & Budget Constraints
            </h3>
          </div>

          {/* Model Selector */}
          <div>
            <label className="block text-[11px] font-mono font-bold text-content-muted uppercase mb-2">
              Model Tier & Agent Role Assignments
            </label>
            <ModelSelector
              selectedTier={modelTier}
              onChangeTier={setModelTier}
              roleOverrides={roleOverrides}
              onRoleOverrideChange={handleRoleOverrideChange}
            />
          </div>

          {/* Sliders & Inputs */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2">
            {/* Max Replan Iterations */}
            <div className="bg-bg-base p-3.5 rounded-xl border border-border-subtle space-y-2">
              <div className="flex items-center justify-between text-xs font-mono">
                <span className="text-content-secondary">MAX REPLANS:</span>
                <span className="font-bold text-accent-warning">{maxReplanIterations} cycles</span>
              </div>
              <input
                type="range"
                min={1}
                max={8}
                value={maxReplanIterations}
                onChange={(e) => setMaxReplanIterations(Number(e.target.value))}
                className="w-full accent-accent-warning cursor-pointer"
              />
            </div>

            {/* Max Cost Budget */}
            <div className="bg-bg-base p-3.5 rounded-xl border border-border-subtle space-y-2">
              <div className="flex items-center justify-between text-xs font-mono">
                <span className="text-content-secondary">MAX COST LIMIT:</span>
                <span className="font-bold text-accent-success">${maxCostUsd.toFixed(2)} USD</span>
              </div>
              <input
                type="range"
                min={1}
                max={25}
                step={0.5}
                value={maxCostUsd}
                onChange={(e) => setMaxCostUsd(Number(e.target.value))}
                className="w-full accent-accent-success cursor-pointer"
              />
            </div>

            {/* Max Tokens */}
            <div className="bg-bg-base p-3.5 rounded-xl border border-border-subtle space-y-2">
              <div className="flex items-center justify-between text-xs font-mono">
                <span className="text-content-secondary">MAX TOKEN BUDGET:</span>
                <span className="font-bold text-accent-info">{Math.round(maxTokens / 1000)}k</span>
              </div>
              <input
                type="range"
                min={50000}
                max={1000000}
                step={25000}
                value={maxTokens}
                onChange={(e) => setMaxTokens(Number(e.target.value))}
                className="w-full accent-accent-info cursor-pointer"
              />
            </div>
          </div>
        </div>

        {/* Action Buttons: Dry Run & Launch */}
        <div className="flex items-center justify-end gap-3 pt-4 border-t border-border-subtle">
          <button
            type="button"
            onClick={handleDryRun}
            disabled={isDryRunning || isLaunching}
            className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-bg-elevated hover:bg-bg-base border border-border-subtle text-content-primary text-xs font-mono font-semibold transition-all disabled:opacity-50"
          >
            <Eye className="w-4 h-4" />
            {isDryRunning ? 'Validating & Decomposing...' : 'Dry-Run / Preview DAG'}
          </button>

          <button
            type="button"
            onClick={handleLaunch}
            disabled={isLaunching || isDryRunning}
            className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-accent-primary hover:bg-indigo-600 text-white text-xs font-mono font-bold shadow-lg shadow-accent-primary/25 transition-all disabled:opacity-50"
          >
            <Play className="w-4 h-4 fill-white" />
            {isLaunching ? 'Launching Multi-Agent Swarm...' : 'Launch Execution'}
          </button>
        </div>
      </div>

      {/* Dry Run Preview Section */}
      {dryRunResult && (
        <div className="space-y-4 animate-in fade-in duration-300">
          <div className="flex items-center justify-between px-2">
            <span className="text-xs font-mono font-bold text-emerald-400 flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4" />
              DECOMPOSED DAG PREVIEW ({dryRunResult.dag.nodes.length} TASKS)
            </span>
            <div className="flex items-center gap-4 text-xs font-mono text-content-secondary">
              <span>Est. Tokens: ~{Math.round(dryRunResult.estimated_tokens / 1000)}k</span>
              <span>Est. Cost: ~${dryRunResult.estimated_cost_usd.toFixed(3)}</span>
            </div>
          </div>

          <DAGCanvas dag={dryRunResult.dag} />
        </div>
      )}
    </div>
  );
};
