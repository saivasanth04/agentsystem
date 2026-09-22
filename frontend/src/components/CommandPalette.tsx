import React, { useState, useEffect, useRef } from 'react';
import { 
  Search, 
  FileCode, 
  Folder, 
  Play, 
  Cpu, 
  History, 
  Settings, 
  Layers, 
  Sparkles,
  ArrowRight,
  Database,
  ShieldCheck
} from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import { orchestratorApi } from '../api/client';
import { FileTreeNode, SessionSummary } from '../types/orchestrator';

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectFile?: (path: string) => void;
  navigate?: (route: string) => void;
}

interface PaletteItem {
  id: string;
  title: string;
  subtitle?: string;
  category: 'Files' | 'Navigation' | 'Sessions' | 'Actions';
  icon: React.ReactNode;
  action: () => void;
}

export const CommandPalette: React.FC<CommandPaletteProps> = ({
  isOpen,
  onClose,
  onSelectFile,
  navigate = (route: string) => {
    window.history.pushState({}, '', route);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }
}) => {
  const { activeWorkspace, openWorkspaceModal } = useWorkspace();
  const [query, setQuery] = useState('');
  const [files, setFiles] = useState<FileTreeNode[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setQuery('');
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);

      // Load files if workspace is active
      if (activeWorkspace) {
        orchestratorApi.getWorkspaceFiles(activeWorkspace.path, 3)
          .then(data => {
            const flatten = (nodes: FileTreeNode[]): FileTreeNode[] => {
              let res: FileTreeNode[] = [];
              for (const n of nodes) {
                if (n.type === 'file') res.push(n);
                if (n.children) res = res.concat(flatten(n.children));
              }
              return res;
            };
            setFiles(flatten(data.tree || []));
          })
          .catch(() => setFiles([]));
      }

      // Load recent sessions
      orchestratorApi.getSessions({ limit: 10 })
        .then(res => setSessions(res.sessions || []))
        .catch(() => setSessions([]));
    }
  }, [isOpen, activeWorkspace]);

  // Build items based on query
  const navigationItems: PaletteItem[] = [
    {
      id: 'nav-new-task',
      title: 'New Autonomous Task',
      subtitle: 'Decompose and launch multi-agent coding workflow',
      category: 'Navigation',
      icon: <Play className="w-4 h-4 text-emerald-400" />,
      action: () => { navigate('/tasks/new'); onClose(); }
    },
    {
      id: 'nav-live-dag',
      title: 'Live DAG Execution',
      subtitle: 'View real-time task graph and state transitions',
      category: 'Navigation',
      icon: <Layers className="w-4 h-4 text-cyan-400" />,
      action: () => { navigate('/sessions/live'); onClose(); }
    },
    {
      id: 'nav-open-ws',
      title: 'Switch / Open Workspace',
      subtitle: activeWorkspace ? `Active: ${activeWorkspace.path}` : 'Bind local repository folder',
      category: 'Actions',
      icon: <Folder className="w-4 h-4 text-amber-400" />,
      action: () => { openWorkspaceModal(); onClose(); }
    },
    {
      id: 'nav-agents',
      title: 'Agent Topology & Roster',
      subtitle: 'Inspect system roles, tools, and capability permissions',
      category: 'Navigation',
      icon: <Cpu className="w-4 h-4 text-purple-400" />,
      action: () => { navigate('/agents'); onClose(); }
    },
    {
      id: 'nav-sessions',
      title: 'Session History & Replays',
      subtitle: 'Browse all past runs, checkpoints, and telemetry logs',
      category: 'Navigation',
      icon: <History className="w-4 h-4 text-blue-400" />,
      action: () => { navigate('/sessions'); onClose(); }
    },
    {
      id: 'nav-budgets',
      title: 'Cost & Token Budgets',
      subtitle: 'LLM consumption ledger and hard quota controls',
      category: 'Navigation',
      icon: <ShieldCheck className="w-4 h-4 text-emerald-400" />,
      action: () => { navigate('/budgets'); onClose(); }
    },
    {
      id: 'nav-memory',
      title: 'Memory & Experience Store',
      subtitle: 'Short-term and long-term vector/graph knowledge',
      category: 'Navigation',
      icon: <Database className="w-4 h-4 text-pink-400" />,
      action: () => { navigate('/memory'); onClose(); }
    },
    {
      id: 'nav-settings',
      title: 'System Settings & MCP Config',
      subtitle: 'Configure LLM providers, API keys, and MCP servers',
      category: 'Navigation',
      icon: <Settings className="w-4 h-4 text-slate-400" />,
      action: () => { navigate('/settings'); onClose(); }
    }
  ];

  const fileItems: PaletteItem[] = files.map(f => ({
    id: `file-${f.path}`,
    title: f.name,
    subtitle: f.path,
    category: 'Files',
    icon: <FileCode className="w-4 h-4 text-cyan-400" />,
    action: () => {
      if (onSelectFile) {
        onSelectFile(f.path);
      }
      onClose();
    }
  }));

  const sessionItems: PaletteItem[] = sessions.slice(0, 5).map(s => ({
    id: `session-${s.session_id}`,
    title: s.user_request || s.session_id,
    subtitle: `Status: ${s.status} | Created: ${new Date(s.created_at).toLocaleTimeString()}`,
    category: 'Sessions',
    icon: <Sparkles className="w-4 h-4 text-amber-400" />,
    action: () => {
      navigate(`/sessions/${s.session_id}`);
      onClose();
    }
  }));

  const allItems = [...navigationItems, ...sessionItems, ...fileItems];

  const filteredItems = query.trim() === '' 
    ? allItems.slice(0, 10)
    : allItems.filter(item => 
        item.title.toLowerCase().includes(query.toLowerCase()) ||
        (item.subtitle && item.subtitle.toLowerCase().includes(query.toLowerCase()))
      ).slice(0, 15);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(prev => (prev < filteredItems.length - 1 ? prev + 1 : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(prev => (prev > 0 ? prev - 1 : filteredItems.length - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (filteredItems[selectedIndex]) {
        filteredItems[selectedIndex].action();
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-start justify-center pt-24 px-4">
      <div 
        className="w-full max-w-2xl bg-slate-900 border border-slate-700/80 rounded-2xl shadow-2xl overflow-hidden flex flex-col animate-in fade-in zoom-in-95 duration-150"
        onClick={e => e.stopPropagation()}
      >
        {/* Search Input */}
        <div className="flex items-center px-4 py-3.5 border-b border-slate-800 bg-slate-900/90 space-x-3">
          <Search className="w-5 h-5 text-slate-400 shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
            onKeyDown={handleKeyDown}
            placeholder="Type a command, filename, or search sessions (Ctrl+K)..."
            className="flex-1 bg-transparent text-slate-100 placeholder-slate-500 text-sm focus:outline-none font-medium"
          />
          <kbd className="px-2 py-0.5 text-[10px] font-mono bg-slate-800 text-slate-400 rounded border border-slate-700">
            ESC
          </kbd>
        </div>

        {/* Results List */}
        <div className="max-h-96 overflow-y-auto p-2 divide-y divide-slate-800/30">
          {filteredItems.length === 0 ? (
            <div className="py-12 text-center text-slate-500 text-sm font-medium">
              No matching commands, files, or sessions found.
            </div>
          ) : (
            filteredItems.map((item, index) => {
              const isSelected = index === selectedIndex;
              return (
                <div
                  key={item.id}
                  onClick={item.action}
                  onMouseEnter={() => setSelectedIndex(index)}
                  className={`flex items-center justify-between px-3 py-2.5 rounded-xl cursor-pointer transition-all ${
                    isSelected ? 'bg-cyan-500/10 border border-cyan-500/30 text-cyan-200' : 'text-slate-300 hover:bg-slate-800/60'
                  }`}
                >
                  <div className="flex items-center space-x-3 min-w-0">
                    <div className={`p-2 rounded-lg ${isSelected ? 'bg-cyan-500/20 text-cyan-300' : 'bg-slate-800 text-slate-400'}`}>
                      {item.icon}
                    </div>
                    <div className="min-w-0">
                      <div className="font-semibold text-xs truncate flex items-center space-x-2">
                        <span>{item.title}</span>
                        <span className="text-[10px] px-1.5 py-0.2 rounded bg-slate-800 text-slate-400 uppercase tracking-wider font-mono">
                          {item.category}
                        </span>
                      </div>
                      {item.subtitle && (
                        <div className="text-[11px] text-slate-400 truncate mt-0.5">
                          {item.subtitle}
                        </div>
                      )}
                    </div>
                  </div>

                  <ArrowRight className={`w-4 h-4 shrink-0 transition-opacity ${isSelected ? 'opacity-100 text-cyan-400' : 'opacity-0'}`} />
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-2 bg-slate-950/60 border-t border-slate-800/80 flex items-center justify-between text-[11px] text-slate-500">
          <div className="flex items-center space-x-3">
            <span><kbd className="font-mono bg-slate-800 px-1.5 py-0.5 rounded text-slate-400">↑↓</kbd> to navigate</span>
            <span><kbd className="font-mono bg-slate-800 px-1.5 py-0.5 rounded text-slate-400">↵</kbd> to select</span>
          </div>
          {activeWorkspace && (
            <div className="truncate max-w-xs text-cyan-400/80 font-mono">
              ws: {activeWorkspace.project_name}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
