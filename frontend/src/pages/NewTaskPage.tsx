import React, { useState, useEffect } from 'react';
import { ModelTier, DAGSnapshot, PreflightResult, ContextPreview } from '../types/orchestrator';
import { ModelSelector } from '../components/ModelSelector';
import { DAGCanvas } from '../components/DAGCanvas';
import { orchestratorApi } from '../api/client';
import { useWorkspace } from '../context/WorkspaceContext';
import {
  Rocket,
  Sliders,
  Play,
  Eye,
  CheckCircle2,
  AlertCircle,
  Folder,
  ShieldCheck,
  RefreshCw,
  FileCode,
  Sparkles,
  AlertTriangle,
  FolderOpen
} from 'lucide-react';

interface NewTaskPageProps {
  navigate: (route: string) => void;
  onSessionLaunched?: (sessionId: string) => void;
}

export const NewTaskPage: React.FC<NewTaskPageProps> = ({ navigate, onSessionLaunched }) => {
  const { activeWorkspace, openWorkspaceModal } = useWorkspace();
  const [userRequest, setUserRequest] = useState('');
  const [modelTier, setModelTier] = useState<ModelTier>('auto');
  const [roleOverrides, setRoleOverrides] = useState<Record<string, string>>({});
  const [maxReplanIterations, setMaxReplanIterations] = useState<number>(3);
  const [maxCostUsd, setMaxCostUsd] = useState<number>(5.0);
  const [maxTokens, setMaxTokens] = useState<number>(500000);
  const [contextBudget, setContextBudget] = useState<number>(100000);

  // Preflight check & Context preview
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [isPreflightRunning, setIsPreflightRunning] = useState(false);
  const [contextPreview, setContextPreview] = useState<ContextPreview | null>(null);

  // Dry run state
  const [isDryRunning, setIsDryRunning] = useState(false);
  const [dryRunResult, setDryRunResult] = useState<{
    dag: DAGSnapshot;
    estimated_cost_usd: number;
    estimated_tokens: number;
  } | null>(null);

  // Launch state
  const [isLaunching, setIsLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);

  // Run preflight check whenever workspace changes
  const runPreflight = async () => {
    setIsPreflightRunning(true);
    try {
      const res = await orchestratorApi.runPreflight(activeWorkspace?.path);
      setPreflight(res);
    } catch {
      setPreflight(null);
    } finally {
      setIsPreflightRunning(false);
    }
  };

  // Preview context for current prompt and workspace
  const fetchContextPreview = async () => {
    if (!userRequest.trim()) return;
    try {
      const ctx = await orchestratorApi.previewContext(userRequest, activeWorkspace?.path);
      setContextPreview(ctx);
    } catch {
      setContextPreview(null);
    }
  };

  useEffect(() => {
    runPreflight();
  }, [activeWorkspace]);

  const promptTemplates = [
    { label: '⚡ LRU Cache', prompt: 'Build a thread-safe LRU Cache with TTL expiration and comprehensive unit tests in the workspace.' },
    { label: '🚀 API Endpoint', prompt: 'Add a new secure REST API endpoint with Pydantic validation, error recovery, and unit tests.' },
    { label: '🧪 Fix Failing Tests', prompt: 'Analyze failing unit tests in this repository, identify root cause, fix the regression, and verify green.' },
    { label: '🛡️ Security Hardening', prompt: 'Audit codebase for injection flaws, unhandled exceptions, and harden input validation routines.' },
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
        workspace_path: activeWorkspace?.path,
        config: {
          model_tier: modelTier,
          role_overrides: roleOverrides,
          max_replan_iterations: maxReplanIterations,
          max_cost_usd: maxCostUsd,
          max_tokens: maxTokens,
          workspace_dir: activeWorkspace?.path || '.',
        },
      });
      setDryRunResult(result);
      fetchContextPreview();
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
        workspace_path: activeWorkspace?.path,
        config: {
          model_tier: modelTier,
          role_overrides: roleOverrides,
          max_replan_iterations: maxReplanIterations,
          max_cost_usd: maxCostUsd,
          max_tokens: maxTokens,
          context_budget_tokens: contextBudget,
          workspace_dir: activeWorkspace?.path || '.',
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
    <div className="max-w-5xl mx-auto space-y-6 pb-12">
      {/* Page Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold font-sans text-slate-100 tracking-tight flex items-center gap-2.5">
            <Rocket className="w-6 h-6 text-cyan-400" />
            Autonomous Task Studio & Launchpad
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-1">
            Decompose requirements into executable DAGs scoped to your active workspace with multi-agent orchestration
          </p>
        </div>

        {/* Workspace Authoritative Boundary Card */}
        <div className="flex items-center space-x-3 bg-slate-900 border border-slate-800 hover:border-slate-700 p-3 rounded-2xl text-xs font-mono shadow-md transition-colors">
          <div className="w-8 h-8 rounded-xl bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center shrink-0">
            <Folder className="w-4 h-4 text-cyan-400" />
          </div>
          <div className="min-w-0 pr-2">
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-cyan-400 uppercase tracking-wider font-bold">Session Workspace Boundary</span>
              {activeWorkspace?.is_git && (
                <span className="text-[9px] bg-slate-800 text-slate-300 px-1.5 py-0.5 rounded font-mono">
                  {activeWorkspace.git_branch || 'main'}{activeWorkspace.git_dirty ? ' *' : ''}
                </span>
              )}
            </div>
            <div className="text-slate-100 font-semibold truncate max-w-[260px]" title={activeWorkspace?.path}>
              {activeWorkspace ? activeWorkspace.project_name : 'No workspace bound'}
            </div>
            <div className="text-[10px] text-slate-500 truncate max-w-[260px]" title={activeWorkspace?.path}>
              {activeWorkspace?.path || 'Global default root'}
            </div>
          </div>
          <button
            onClick={openWorkspaceModal}
            className="p-2 hover:bg-slate-800 rounded-xl text-slate-400 hover:text-cyan-300 transition-colors border border-transparent hover:border-slate-700"
            title="Change workspace folder"
          >
            <FolderOpen className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* 5-Gate Preflight Integrity Check Bar */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-2xl p-4 shadow-lg">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center space-x-2 text-xs font-mono font-bold text-slate-200 uppercase tracking-wider">
            <ShieldCheck className="w-4 h-4 text-cyan-400" />
            <span>5-Gate Autonomous Execution Preflight</span>
          </div>
          <button
            onClick={runPreflight}
            disabled={isPreflightRunning}
            className="flex items-center space-x-1 text-[11px] font-mono text-slate-400 hover:text-cyan-300 transition-colors"
          >
            <RefreshCw className={`w-3 h-3 ${isPreflightRunning ? 'animate-spin text-cyan-400' : ''}`} />
            <span>Re-verify Gates</span>
          </button>
        </div>

        {preflight ? (
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5 text-xs font-mono">
            {preflight.checks.map((chk, i) => (
              <div
                key={i}
                className={`p-2.5 rounded-xl border flex flex-col justify-between ${
                  chk.status === 'PASS' 
                    ? 'bg-slate-950/60 border-emerald-500/30 text-slate-300' 
                    : chk.status === 'WARN'
                    ? 'bg-amber-950/30 border-amber-500/30 text-amber-300'
                    : 'bg-rose-950/30 border-rose-500/40 text-rose-200'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] text-slate-400 font-bold uppercase truncate">{chk.name}</span>
                  {chk.status === 'PASS' ? (
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                  ) : (
                    <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                  )}
                </div>
                <div className="text-[11px] truncate text-slate-300">{chk.message}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="py-2 text-center text-xs font-mono text-slate-500">Checking environment integrity...</div>
        )}
      </div>

      {launchError && (
        <div className="bg-rose-950/40 border border-rose-500/50 p-4 rounded-xl flex items-center gap-3 text-xs font-mono text-rose-300 shadow-lg">
          <AlertCircle className="w-5 h-5 text-rose-400 shrink-0" />
          <span>{launchError}</span>
        </div>
      )}

      {/* Main Request Form */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
        <div>
          <label className="block text-xs font-mono font-bold text-slate-200 uppercase mb-2">
            Task Description & Engineering Objectives
          </label>
          <textarea
            value={userRequest}
            onChange={(e) => setUserRequest(e.target.value)}
            placeholder="Describe what you want the multi-agent system to achieve in this workspace (e.g. 'Build a thread-safe LRU cache in python with 100% test coverage, verify ground truth, and generate doc artifact')..."
            rows={4}
            className="w-full bg-slate-950 border border-slate-700/80 rounded-xl p-4 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-cyan-500 font-sans leading-relaxed resize-y transition-colors"
          />
        </div>

        {/* Template Prompt Chips */}
        <div>
          <span className="text-[11px] font-mono text-slate-400 block mb-2 font-semibold">
            QUICK TASK PRESETS:
          </span>
          <div className="flex flex-wrap gap-2">
            {promptTemplates.map((tpl, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setUserRequest(tpl.prompt)}
                className="px-3 py-1.5 rounded-xl bg-slate-950 hover:bg-slate-800 border border-slate-800 hover:border-cyan-500/50 text-xs font-mono text-slate-300 hover:text-cyan-300 transition-all shadow-sm"
              >
                {tpl.label}
              </button>
            ))}
          </div>
        </div>

        {/* Model Tier & Agent Configurations */}
        <div className="pt-4 border-t border-slate-800 space-y-5">
          <div className="flex items-center gap-2">
            <Sliders className="w-4 h-4 text-cyan-400" />
            <h3 className="text-xs font-mono font-bold text-slate-200 uppercase">
              Model Selection & Swarm Role Allocation
            </h3>
          </div>

          <ModelSelector
            selectedTier={modelTier}
            onChangeTier={setModelTier}
            roleOverrides={roleOverrides}
            onRoleOverrideChange={handleRoleOverrideChange}
          />

          {/* Sliders & Constraints */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2">
            {/* Max Replan Iterations */}
            <div className="bg-slate-950 p-4 rounded-xl border border-slate-800 space-y-2">
              <div className="flex items-center justify-between text-xs font-mono">
                <span className="text-slate-400">MAX REPLANS:</span>
                <span className="font-bold text-amber-400">{maxReplanIterations} cycles</span>
              </div>
              <input
                type="range"
                min={1}
                max={8}
                value={maxReplanIterations}
                onChange={(e) => setMaxReplanIterations(Number(e.target.value))}
                className="w-full accent-amber-400 cursor-pointer"
              />
            </div>

            {/* Max Cost Budget */}
            <div className="bg-slate-950 p-4 rounded-xl border border-slate-800 space-y-2">
              <div className="flex items-center justify-between text-xs font-mono">
                <span className="text-slate-400">HARD COST CEILING:</span>
                <span className="font-bold text-emerald-400">${maxCostUsd.toFixed(2)} USD</span>
              </div>
              <input
                type="range"
                min={1}
                max={25}
                step={0.5}
                value={maxCostUsd}
                onChange={(e) => setMaxCostUsd(Number(e.target.value))}
                className="w-full accent-emerald-400 cursor-pointer"
              />
            </div>

            {/* Max Tokens */}
            <div className="bg-slate-950 p-4 rounded-xl border border-slate-800 space-y-2">
              <div className="flex items-center justify-between text-xs font-mono">
                <span className="text-slate-400">TOKEN QUOTA:</span>
                <span className="font-bold text-cyan-400">{Math.round(maxTokens / 1000)}k tokens</span>
              </div>
              <input
                type="range"
                min={50000}
                max={1000000}
                step={25000}
                value={maxTokens}
                onChange={(e) => setMaxTokens(Number(e.target.value))}
                className="w-full accent-cyan-400 cursor-pointer"
              />
            </div>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center justify-between pt-4 border-t border-slate-800">
          <div className="text-xs font-mono text-slate-400 flex items-center space-x-2">
            <Folder className="w-3.5 h-3.5 text-cyan-400" />
            <span className="truncate max-w-xs">Root: {activeWorkspace?.path || 'workspace-local'}</span>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={handleDryRun}
              disabled={isDryRunning || isLaunching}
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-xs font-mono font-semibold transition-all disabled:opacity-50"
            >
              <Eye className="w-4 h-4 text-cyan-400" />
              {isDryRunning ? 'Synthesizing DAG...' : 'Dry-Run / Preview DAG'}
            </button>

            <button
              type="button"
              onClick={handleLaunch}
              disabled={isLaunching || isDryRunning || (preflight !== null && !preflight.ready)}
              className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white text-xs font-mono font-bold shadow-lg shadow-cyan-500/25 transition-all disabled:opacity-50"
            >
              <Play className="w-4 h-4 fill-current" />
              {isLaunching ? 'Dispatching Swarm...' : 'Launch Execution'}
            </button>
          </div>
        </div>
      </div>

      {/* Context Preview Card */}
      {contextPreview && (
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg space-y-3 animate-in fade-in">
          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
            <span className="text-xs font-mono font-bold text-slate-200 uppercase flex items-center space-x-2">
              <Sparkles className="w-4 h-4 text-cyan-400" />
              <span>Workspace Context & Skills Bound</span>
            </span>
            <span className="text-[11px] font-mono text-slate-400">{contextPreview.tech_stack.join(' • ')}</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
            {/* Focal files */}
            <div className="bg-slate-950 p-3 rounded-xl border border-slate-800/80 space-y-1.5">
              <div className="text-[10px] text-slate-400 font-bold uppercase">Candidate Focal Files</div>
              {contextPreview.focal_files.length === 0 ? (
                <div className="text-slate-500 text-[11px]">No specific files identified yet</div>
              ) : (
                <div className="space-y-1">
                  {contextPreview.focal_files.map((ff, idx) => (
                    <div key={idx} className="flex items-center space-x-1.5 text-cyan-300">
                      <FileCode className="w-3 h-3 shrink-0" />
                      <span className="truncate">{ff.filepath}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Applicable skills */}
            <div className="bg-slate-950 p-3 rounded-xl border border-slate-800/80 space-y-1.5">
              <div className="text-[10px] text-slate-400 font-bold uppercase">Applicable Specialized Skills</div>
              <div className="flex flex-wrap gap-1.5">
                {(contextPreview.retrieved_skills || []).map((sk, idx) => (
                  <span key={idx} className="px-2 py-0.5 rounded bg-slate-900 border border-slate-700 text-slate-300 text-[11px]">
                    {sk}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Dry Run Preview Section */}
      {dryRunResult && (
        <div className="space-y-4 animate-in fade-in duration-300">
          <div className="flex items-center justify-between px-2">
            <span className="text-xs font-mono font-bold text-emerald-400 flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4" />
              DECOMPOSED DAG PREVIEW ({dryRunResult.dag.nodes.length} TASKS)
            </span>
            <div className="flex items-center gap-4 text-xs font-mono text-slate-400">
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
