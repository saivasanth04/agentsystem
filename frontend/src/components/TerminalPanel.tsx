import React, { useState, useRef, useEffect } from 'react';
import { 
  Terminal as TerminalIcon, 
  Play, 
  Trash2, 
  CheckCircle2, 
  XCircle, 
  Clock, 
  Folder, 
  ChevronDown, 
  Maximize2,
  Minimize2,
  Copy,
  Check
} from 'lucide-react';
import { orchestratorApi } from '../api/client';
import { TerminalExecutionResult } from '../types/orchestrator';

interface TerminalPanelProps {
  workspacePath?: string;
  isCollapsible?: boolean;
  defaultOpen?: boolean;
  onClose?: () => void;
  className?: string;
}

interface CommandEntry {
  id: string;
  command: string;
  result: TerminalExecutionResult;
  timestamp: string;
}

export const TerminalPanel: React.FC<TerminalPanelProps> = ({
  workspacePath,
  isCollapsible = true,
  defaultOpen = true,
  className = ''
}) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [isExpanded, setIsExpanded] = useState(false);
  const [inputCommand, setInputCommand] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [history, setHistory] = useState<CommandEntry[]>([]);
  const [historyIndex, setHistoryIndex] = useState<number>(-1);
  const [commandHistory, setCommandHistory] = useState<string[]>([]);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [history, isOpen]);

  const handleRunCommand = async (cmdToRun?: string) => {
    const cmd = cmdToRun || inputCommand.trim();
    if (!cmd || isRunning) return;

    setIsRunning(true);
    setCommandHistory(prev => [cmd, ...prev.filter(c => c !== cmd)]);
    setHistoryIndex(-1);

    const startTime = Date.now();
    try {
      const result = await orchestratorApi.runTerminalCommand(cmd, workspacePath);
      setHistory(prev => [
        ...prev,
        {
          id: Math.random().toString(36).substring(7),
          command: cmd,
          result,
          timestamp: new Date().toLocaleTimeString()
        }
      ]);
      setInputCommand('');
    } catch (err: any) {
      setHistory(prev => [
        ...prev,
        {
          id: Math.random().toString(36).substring(7),
          command: cmd,
          result: {
            command: cmd,
            stdout: '',
            stderr: err.message || 'Execution failed',
            exit_code: 1,
            duration_ms: Date.now() - startTime,
            workspace_path: workspacePath || 'unknown'
          },
          timestamp: new Date().toLocaleTimeString()
        }
      ]);
    } finally {
      setIsRunning(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleRunCommand();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (commandHistory.length > 0 && historyIndex < commandHistory.length - 1) {
        const nextIdx = historyIndex + 1;
        setHistoryIndex(nextIdx);
        setInputCommand(commandHistory[nextIdx]);
      }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (historyIndex > 0) {
        const nextIdx = historyIndex - 1;
        setHistoryIndex(nextIdx);
        setInputCommand(commandHistory[nextIdx]);
      } else if (historyIndex === 0) {
        setHistoryIndex(-1);
        setInputCommand('');
      }
    }
  };

  const handleCopy = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const clearHistory = () => {
    setHistory([]);
  };

  const quickCommands = [
    { label: 'git status', cmd: 'git status' },
    { label: 'git diff', cmd: 'git diff --stat' },
    { label: 'pytest', cmd: 'pytest -q' },
    { label: 'npm test', cmd: 'npm test' },
    { label: 'dir / ls', cmd: navigator.platform.includes('Win') ? 'dir' : 'ls -la' }
  ];

  if (!isOpen && isCollapsible) {
    return (
      <button
        onClick={() => setIsOpen(true)}
        className="fixed bottom-4 right-4 z-40 flex items-center space-x-2 bg-slate-900/90 text-slate-200 border border-slate-700/80 hover:border-cyan-500/50 px-3.5 py-2 rounded-xl shadow-xl backdrop-blur-md transition-all text-xs font-mono font-medium hover:text-cyan-400 group"
      >
        <TerminalIcon className="w-4 h-4 text-cyan-400 group-hover:rotate-6 transition-transform" />
        <span>Workspace Terminal</span>
        {history.length > 0 && (
          <span className="bg-cyan-500/20 text-cyan-300 px-1.5 py-0.5 rounded text-[10px]">
            {history.length}
          </span>
        )}
      </button>
    );
  }

  return (
    <div 
      className={`flex flex-col bg-slate-950/95 border border-slate-800/90 rounded-xl overflow-hidden shadow-2xl backdrop-blur-md transition-all font-mono ${
        isExpanded ? 'h-[75vh]' : 'h-80'
      } ${className}`}
    >
      {/* Terminal Top Bar */}
      <div className="flex items-center justify-between px-3.5 py-2 bg-slate-900/80 border-b border-slate-800/80 select-none text-xs">
        <div className="flex items-center space-x-2 min-w-0">
          <TerminalIcon className="w-4 h-4 text-cyan-400 shrink-0" />
          <span className="font-semibold text-slate-200 tracking-wide">Workspace Terminal</span>
          {workspacePath ? (
            <div className="flex items-center space-x-1 px-2 py-0.5 rounded bg-slate-800 text-slate-400 text-[11px] truncate max-w-xs" title={workspacePath}>
              <Folder className="w-3 h-3 shrink-0 text-cyan-500/70" />
              <span className="truncate">{workspacePath}</span>
            </div>
          ) : (
            <span className="text-[11px] text-amber-400/80 italic">(No workspace root bound)</span>
          )}
        </div>

        <div className="flex items-center space-x-1.5">
          {/* Quick buttons */}
          <div className="hidden sm:flex items-center space-x-1 mr-2">
            {quickCommands.slice(0, 3).map(qc => (
              <button
                key={qc.label}
                onClick={() => handleRunCommand(qc.cmd)}
                disabled={isRunning}
                className="px-2 py-0.5 text-[10px] bg-slate-800/60 hover:bg-slate-700/80 text-slate-300 hover:text-cyan-300 rounded border border-slate-700/50 transition-colors disabled:opacity-50"
              >
                {qc.label}
              </button>
            ))}
          </div>

          <button
            onClick={clearHistory}
            title="Clear output"
            className="p-1 hover:bg-slate-800 text-slate-400 hover:text-slate-200 rounded transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
          
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            title={isExpanded ? "Collapse height" : "Expand height"}
            className="p-1 hover:bg-slate-800 text-slate-400 hover:text-slate-200 rounded transition-colors"
          >
            {isExpanded ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
          </button>

          {isCollapsible && (
            <button
              onClick={() => setIsOpen(false)}
              title="Close terminal"
              className="p-1 hover:bg-slate-800 text-slate-400 hover:text-slate-200 rounded transition-colors"
            >
              <ChevronDown className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Output Console Log */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3 text-xs font-mono text-slate-300 select-text">
        {history.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-600 space-y-2 py-8">
            <TerminalIcon className="w-8 h-8 stroke-1 text-slate-700" />
            <p className="text-[11px]">Direct execution inside active workspace directory.</p>
            <div className="flex flex-wrap gap-1.5 justify-center max-w-md pt-2">
              {quickCommands.map(qc => (
                <button
                  key={qc.label}
                  onClick={() => handleRunCommand(qc.cmd)}
                  className="px-2 py-1 bg-slate-900 border border-slate-800 hover:border-cyan-500/40 text-slate-400 hover:text-cyan-300 rounded text-[11px] transition-colors"
                >
                  {qc.cmd}
                </button>
              ))}
            </div>
          </div>
        ) : (
          history.map(item => (
            <div key={item.id} className="group border border-slate-800/60 rounded-lg bg-slate-900/40 p-2.5 space-y-1.5">
              <div className="flex items-center justify-between text-[11px] text-slate-400 border-b border-slate-800/40 pb-1">
                <div className="flex items-center space-x-2">
                  <span className="text-cyan-400 font-bold">$</span>
                  <span className="font-semibold text-slate-100">{item.command}</span>
                </div>
                <div className="flex items-center space-x-3 text-[10px]">
                  <span className="flex items-center space-x-1 text-slate-500">
                    <Clock className="w-3 h-3" />
                    <span>{(item.result.duration_ms || 0).toFixed(0)}ms</span>
                  </span>
                  {item.result.exit_code === 0 ? (
                    <span className="flex items-center space-x-1 text-emerald-400 font-medium">
                      <CheckCircle2 className="w-3 h-3" />
                      <span>exit 0</span>
                    </span>
                  ) : (
                    <span className="flex items-center space-x-1 text-rose-400 font-medium">
                      <XCircle className="w-3 h-3" />
                      <span>exit {item.result.exit_code}</span>
                    </span>
                  )}
                  <button
                    onClick={() => handleCopy(item.result.stdout || item.result.stderr, item.id)}
                    className="opacity-0 group-hover:opacity-100 p-0.5 hover:text-slate-200 transition-opacity"
                    title="Copy output"
                  >
                    {copiedId === item.id ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                  </button>
                </div>
              </div>

              {item.result.stdout && (
                <pre className="whitespace-pre-wrap break-words text-slate-300 leading-relaxed max-h-60 overflow-y-auto font-mono text-[11.5px] p-1">
                  {item.result.stdout}
                </pre>
              )}

              {item.result.stderr && (
                <pre className="whitespace-pre-wrap break-words text-rose-300/90 leading-relaxed max-h-60 overflow-y-auto font-mono text-[11.5px] bg-rose-950/20 p-1.5 rounded border border-rose-900/30">
                  {item.result.stderr}
                </pre>
              )}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="p-2.5 bg-slate-900/90 border-t border-slate-800/90 flex items-center space-x-2">
        <span className="text-cyan-400 font-bold text-sm select-none pl-1">$</span>
        <input
          ref={inputRef}
          type="text"
          value={inputCommand}
          onChange={e => setInputCommand(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isRunning}
          placeholder={isRunning ? "Running command in workspace..." : "Run shell command in workspace (Enter to submit, Up/Down for history)..."}
          className="flex-1 bg-transparent text-slate-100 placeholder-slate-500 text-xs focus:outline-none disabled:opacity-50 font-mono"
        />
        <button
          onClick={() => handleRunCommand()}
          disabled={isRunning || !inputCommand.trim()}
          className="px-3 py-1 bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg text-xs font-semibold flex items-center space-x-1.5 transition-colors disabled:opacity-40 disabled:cursor-not-allowed shadow-md font-mono"
        >
          {isRunning ? (
            <>
              <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              <span>Executing...</span>
            </>
          ) : (
            <>
              <Play className="w-3 h-3 fill-current" />
              <span>Run</span>
            </>
          )}
        </button>
      </div>
    </div>
  );
};
