import React, { useState, useEffect } from 'react';
import {
  LayoutDashboard,
  PlusCircle,
  FolderKanban,
  GitBranch,
  Boxes,
  Bot,
  CircleDollarSign,
  Database,
  Settings,
  Search,
  Bell,
  StopCircle,
  ChevronLeft,
  ChevronRight,
  Activity,
  Folder,
  Terminal,
  CheckCircle2,
  AlertCircle
} from 'lucide-react';
import { orchestratorWS } from '../api/websocket';
import { orchestratorApi } from '../api/client';
import { useWorkspace } from '../context/WorkspaceContext';
import { WorkspaceSelector } from './WorkspaceSelector';
import { CommandPalette } from './CommandPalette';
import { TerminalPanel } from './TerminalPanel';

interface AppShellProps {
  currentRoute: string;
  navigate: (route: string) => void;
  children: React.ReactNode;
  activeSessionId?: string | null;
}

export const AppShell: React.FC<AppShellProps> = ({
  currentRoute,
  navigate,
  children,
  activeSessionId,
}) => {
  const { activeWorkspace, openWorkspaceModal } = useWorkspace();
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState(false);
  const [isTerminalOpen, setIsTerminalOpen] = useState(false);
  const [isWsConnected, setIsWsConnected] = useState(false);
  const [unreadAlerts, setUnreadAlerts] = useState<number>(0);

  useEffect(() => {
    // Monitor WebSocket status
    const interval = setInterval(() => {
      setIsWsConnected(orchestratorWS.getStatus());
    }, 2000);

    // Initial WebSocket connect
    orchestratorWS.connect();

    // Subscribe to alert events
    const unsubscribe = orchestratorWS.subscribe((event) => {
      const type = (event.event_type || '').toUpperCase();
      if (type.includes('ERROR') || type.includes('HALT') || type.includes('CIRCUIT') || type.includes('BUDGET')) {
        setUnreadAlerts((prev) => prev + 1);
      }
    });

    // Keyboard shortcuts
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setIsCommandPaletteOpen((prev) => !prev);
      }
      if ((e.metaKey || e.ctrlKey) && e.key === '`') {
        e.preventDefault();
        setIsTerminalOpen((prev) => !prev);
      }
      if (e.key === 'Escape') {
        setIsCommandPaletteOpen(false);
      }
      if (!isCommandPaletteOpen && e.target instanceof HTMLElement && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
        if (e.key === 'n') {
          e.preventDefault();
          navigate('/tasks/new');
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);

    return () => {
      clearInterval(interval);
      unsubscribe();
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [navigate, isCommandPaletteOpen]);

  const navItems = [
    { route: '/', label: 'Dashboard', icon: LayoutDashboard },
    { route: '/tasks/new', label: 'New Task', icon: PlusCircle },
    { route: '/sessions', label: 'Sessions', icon: FolderKanban },
    { route: '/sessions/live', label: 'Live DAG', icon: GitBranch },
    { route: '/artifacts', label: 'Artifacts', icon: Boxes },
    { route: '/agents', label: 'Agents & Swarm', icon: Bot },
    { route: '/budgets', label: 'Budgets & Ledger', icon: CircleDollarSign },
    { route: '/memory', label: 'Memory & Context', icon: Database },
    { route: '/settings', label: 'Settings & MCP', icon: Settings },
  ];

  const handleGlobalCancel = async () => {
    if (activeSessionId && confirm(`Are you sure you want to cancel the active session (${activeSessionId})?`)) {
      try {
        await orchestratorApi.cancelSession(activeSessionId, 'Operator requested cancellation from top bar');
        alert('Cancellation requested.');
      } catch (err: any) {
        alert(`Failed to cancel: ${err.message}`);
      }
    }
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-950 text-slate-100 font-sans">
      {/* Sidebar Navigation */}
      <aside
        className={`h-full bg-slate-900 border-r border-slate-800 flex flex-col justify-between transition-all duration-300 z-20 select-none ${
          isSidebarCollapsed ? 'w-16' : 'w-60'
        }`}
      >
        <div>
          {/* Logo / Brand Header */}
          <div className="h-14 flex items-center justify-between px-3.5 border-b border-slate-800">
            {!isSidebarCollapsed && (
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-cyan-500 via-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-cyan-500/20 shrink-0">
                  <Activity className="w-4 h-4 text-white" />
                </div>
                <div className="flex flex-col min-w-0">
                  <span className="text-sm font-bold font-mono tracking-tight text-slate-100 truncate">
                    AGENTIC IDE
                  </span>
                  <span className="text-[10px] font-mono text-cyan-400 uppercase tracking-wider">
                    Orchestrator v2.0
                  </span>
                </div>
              </div>
            )}

            <button
              onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors mx-auto"
              title={isSidebarCollapsed ? 'Expand Sidebar' : 'Collapse Sidebar'}
            >
              {isSidebarCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
            </button>
          </div>

          {/* Navigation Links */}
          <nav className="p-3 space-y-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = currentRoute === item.route || (item.route !== '/' && currentRoute.startsWith(item.route));

              return (
                <button
                  key={item.route}
                  onClick={() => navigate(item.route)}
                  className={`w-full flex items-center gap-3 px-3 py-2 rounded-xl text-xs font-medium transition-all ${
                    isActive
                      ? 'bg-gradient-to-r from-cyan-600 to-blue-600 text-white shadow-md shadow-cyan-500/20 font-semibold'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/70'
                  } ${isSidebarCollapsed ? 'justify-center px-0' : ''}`}
                  title={item.label}
                >
                  <Icon className={`w-4 h-4 shrink-0 ${isActive ? 'text-white' : 'text-slate-400'}`} />
                  {!isSidebarCollapsed && <span className="truncate">{item.label}</span>}
                </button>
              );
            })}
          </nav>
        </div>

        {/* Sidebar Footer */}
        <div className="p-3 border-t border-slate-800 text-xs font-mono space-y-2">
          {!isSidebarCollapsed ? (
            <>
              {/* Workspace Pill */}
              <div 
                onClick={openWorkspaceModal}
                className="p-2 rounded-lg bg-slate-950/70 border border-slate-800 hover:border-cyan-500/50 cursor-pointer transition-all group"
                title={activeWorkspace ? activeWorkspace.path : 'Click to bind workspace'}
              >
                <div className="flex items-center justify-between text-[10px] text-slate-400 mb-1">
                  <span className="uppercase tracking-wider font-semibold text-cyan-400">Target Workspace</span>
                  {activeWorkspace?.is_git && (
                    <span className="flex items-center gap-1 text-slate-400 font-mono text-[9px]">
                      <GitBranch className="w-2.5 h-2.5 text-cyan-400" />
                      {activeWorkspace.git_branch || 'main'}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1.5 text-slate-200 font-mono text-xs truncate">
                  <Folder className="w-3.5 h-3.5 text-cyan-400 shrink-0 group-hover:scale-110 transition-transform" />
                  <span className="truncate">{activeWorkspace ? activeWorkspace.project_name : 'No workspace bound'}</span>
                </div>
              </div>

              {/* Status */}
              <div className="flex items-center justify-between px-2 py-1 bg-slate-950/40 rounded-lg text-[10px] text-slate-400">
                <span className="flex items-center gap-1.5">
                  <span className={`w-2 h-2 rounded-full ${isWsConnected ? 'bg-emerald-400 animate-pulse' : 'bg-rose-500'}`} />
                  {isWsConnected ? 'WS LIVE' : 'WS OFFLINE'}
                </span>
                <span className="text-slate-500">Ctrl+` Terminal</span>
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center gap-2">
              <span
                className={`w-2.5 h-2.5 rounded-full ${isWsConnected ? 'bg-emerald-400' : 'bg-rose-500'}`}
                title={isWsConnected ? 'WebSocket Connected' : 'WebSocket Disconnected'}
              />
            </div>
          )}
        </div>
      </aside>

      {/* Main Content Layout */}
      <div className="flex-1 flex flex-col h-full overflow-hidden bg-slate-950">
        {/* Top Header Bar */}
        <header className="h-14 bg-slate-900/90 backdrop-blur-md border-b border-slate-800 flex items-center justify-between px-5 z-10 gap-4 select-none">
          {/* Left: Workspace Selector Dropdown */}
          <div className="flex items-center gap-3 min-w-0">
            <WorkspaceSelector />
          </div>

          {/* Center: Command Palette Trigger */}
          <button
            onClick={() => setIsCommandPaletteOpen(true)}
            className="hidden md:flex items-center justify-between gap-3 px-3.5 py-1.5 rounded-xl bg-slate-950 hover:bg-slate-800/80 border border-slate-800 text-slate-400 hover:text-slate-200 transition-all text-xs font-mono w-72 lg:w-96 shadow-inner"
          >
            <div className="flex items-center gap-2 truncate">
              <Search className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
              <span className="truncate">Search commands, files, sessions...</span>
            </div>
            <span className="px-1.5 py-0.5 rounded bg-slate-800 text-[10px] text-slate-400 border border-slate-700 font-mono shrink-0">
              Ctrl+K
            </span>
          </button>

          {/* Right Controls */}
          <div className="flex items-center gap-3">
            {/* Terminal Toggle Button */}
            <button
              onClick={() => setIsTerminalOpen(!isTerminalOpen)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-mono font-medium border transition-all ${
                isTerminalOpen 
                  ? 'bg-cyan-500/20 border-cyan-500/40 text-cyan-300' 
                  : 'bg-slate-950 hover:bg-slate-800 border-slate-800 text-slate-300 hover:text-slate-100'
              }`}
              title="Toggle Workspace Terminal (Ctrl+`)"
            >
              <Terminal className="w-3.5 h-3.5 text-cyan-400" />
              <span className="hidden sm:inline">Terminal</span>
            </button>

            {/* Active Session Status Pill */}
            {activeSessionId && (
              <div className="flex items-center gap-2 bg-indigo-950/60 border border-indigo-500/40 px-3 py-1 rounded-full text-xs font-mono">
                <span className="w-2 h-2 rounded-full bg-cyan-400 animate-ping" />
                <span className="text-indigo-200 truncate max-w-[120px]">{activeSessionId}</span>
                <button
                  onClick={handleGlobalCancel}
                  className="ml-1 text-rose-400 hover:text-rose-300 font-bold flex items-center gap-0.5 text-[11px]"
                  title="Cancel Active Execution"
                >
                  <StopCircle className="w-3 h-3" />
                  Cancel
                </button>
              </div>
            )}

            {/* Notification Bell */}
            <button
              onClick={() => setUnreadAlerts(0)}
              className="relative p-2 rounded-xl bg-slate-950 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-slate-200 transition-colors"
              title="System Alerts"
            >
              <Bell className="w-4 h-4" />
              {unreadAlerts > 0 && (
                <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-rose-500 text-white text-[9px] font-bold font-mono flex items-center justify-center animate-bounce">
                  {unreadAlerts}
                </span>
              )}
            </button>

            {/* Operator Avatar */}
            <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-cyan-600 to-indigo-600 border border-cyan-400/30 flex items-center justify-center font-mono text-xs font-bold text-white shadow-md">
              OP
            </div>
          </div>
        </header>

        {/* Dynamic Route Body */}
        <main className="flex-1 overflow-y-auto p-5 bg-slate-950 relative">
          {children}

          {/* Floating Workspace Terminal Drawer */}
          {isTerminalOpen && (
            <div className="fixed bottom-3 right-3 left-64 z-30 shadow-2xl">
              <TerminalPanel
                workspacePath={activeWorkspace?.path}
                isCollapsible={true}
                defaultOpen={true}
                onClose={() => setIsTerminalOpen(false)}
              />
            </div>
          )}
        </main>

        {/* Persistent Bottom Status Bar (IDE footer) */}
        <footer className="h-6 bg-slate-900 border-t border-slate-800 px-4 flex items-center justify-between text-[11px] font-mono text-slate-400 select-none z-10">
          <div className="flex items-center space-x-4 min-w-0">
            {/* Workspace location */}
            <div className="flex items-center space-x-1.5 text-cyan-400 truncate">
              <Folder className="w-3 h-3 shrink-0" />
              <span className="truncate">{activeWorkspace ? activeWorkspace.path : 'No active workspace'}</span>
            </div>

            {/* Git status */}
            {activeWorkspace?.is_git && (
              <div className="hidden sm:flex items-center space-x-2 text-slate-300">
                <span className="flex items-center space-x-1 text-purple-400">
                  <GitBranch className="w-3 h-3 shrink-0" />
                  <span>{activeWorkspace.git_branch || 'main'}</span>
                </span>
                {activeWorkspace.git_dirty ? (
                  <span className="text-amber-400 flex items-center space-x-0.5">
                    <AlertCircle className="w-2.5 h-2.5" />
                    <span>{activeWorkspace.dirty_count} modified</span>
                  </span>
                ) : (
                  <span className="text-emerald-400 flex items-center space-x-0.5">
                    <CheckCircle2 className="w-2.5 h-2.5" />
                    <span>clean</span>
                  </span>
                )}
              </div>
            )}
          </div>

          <div className="flex items-center space-x-3 text-[10px]">
            <span className="text-slate-500">Autonomous DAG Engine</span>
            <span className="flex items-center space-x-1">
              <span className={`w-1.5 h-1.5 rounded-full ${isWsConnected ? 'bg-emerald-400' : 'bg-rose-500'}`} />
              <span className="text-slate-400">{isWsConnected ? 'Ready' : 'Offline'}</span>
            </span>
          </div>
        </footer>
      </div>

      {/* Global Command Palette Modal */}
      <CommandPalette
        isOpen={isCommandPaletteOpen}
        onClose={() => setIsCommandPaletteOpen(false)}
        navigate={navigate}
      />
    </div>
  );
};
