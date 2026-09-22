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
  Menu,
  ChevronLeft,
  ChevronRight,
  Activity,
  Wifi,
  WifiOff,
  Command,
  X
} from 'lucide-react';
import { orchestratorWS } from '../api/websocket';
import { orchestratorApi } from '../api/client';
import { SessionSummary } from '../types/orchestrator';

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
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [isWsConnected, setIsWsConnected] = useState(false);
  const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
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
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setIsSearchOpen((prev) => !prev);
      }
      if (e.key === 'Escape') {
        setIsSearchOpen(false);
      }
      if (!isSearchOpen && e.target instanceof HTMLElement && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
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
  }, [navigate, isSearchOpen]);

  // Load search sessions when modal opens
  useEffect(() => {
    if (isSearchOpen) {
      orchestratorApi.getSessions({ limit: 10 }).then(setRecentSessions).catch(console.error);
    }
  }, [isSearchOpen]);

  const navItems = [
    { route: '/', label: 'Dashboard', icon: LayoutDashboard },
    { route: '/tasks/new', label: 'New Task', icon: PlusCircle },
    { route: '/sessions', label: 'Sessions', icon: FolderKanban },
    { route: '/sessions/live', label: 'Live DAG', icon: GitBranch },
    { route: '/artifacts', label: 'Artifacts', icon: Boxes },
    { route: '/agents', label: 'Agents', icon: Bot },
    { route: '/budgets', label: 'Budgets', icon: CircleDollarSign },
    { route: '/memory', label: 'Memory', icon: Database },
    { route: '/settings', label: 'Settings', icon: Settings },
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

  const filteredSearchSessions = recentSessions.filter((s) =>
    `${s.session_id} ${s.user_request}`.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg-base text-content-primary">
      {/* Sidebar */}
      <aside
        className={`h-full bg-bg-panel border-r border-border-subtle flex flex-col justify-between transition-all duration-300 z-20 ${
          isSidebarCollapsed ? 'w-16' : 'w-60'
        }`}
      >
        <div>
          {/* Logo / Brand Header */}
          <div className="h-14 flex items-center justify-between px-4 border-b border-border-subtle">
            {!isSidebarCollapsed && (
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-md">
                  <Activity className="w-4 h-4 text-white" />
                </div>
                <div className="flex flex-col">
                  <span className="text-sm font-bold font-mono tracking-tight text-content-primary">
                    ORCHESTRATOR
                  </span>
                  <span className="text-[10px] font-mono text-content-secondary uppercase">
                    Mission Control
                  </span>
                </div>
              </div>
            )}

            <button
              onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
              className="p-1.5 rounded-lg text-content-secondary hover:text-content-primary hover:bg-bg-elevated transition-colors mx-auto"
              title={isSidebarCollapsed ? 'Expand Sidebar' : 'Collapse Sidebar'}
            >
              {isSidebarCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
            </button>
          </div>

          {/* Navigation Links */}
          <nav className="p-3 space-y-1.5">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = currentRoute === item.route || (item.route !== '/' && currentRoute.startsWith(item.route));

              return (
                <button
                  key={item.route}
                  onClick={() => navigate(item.route)}
                  className={`w-full flex items-center gap-3 px-3 py-2 rounded-xl text-xs font-medium transition-all ${
                    isActive
                      ? 'bg-accent-primary text-white shadow-md shadow-accent-primary/20 font-semibold'
                      : 'text-content-secondary hover:text-content-primary hover:bg-bg-elevated'
                  } ${isSidebarCollapsed ? 'justify-center px-0' : ''}`}
                  title={item.label}
                >
                  <Icon className={`w-4 h-4 shrink-0 ${isActive ? 'text-white' : 'text-content-muted'}`} />
                  {!isSidebarCollapsed && <span>{item.label}</span>}
                </button>
              );
            })}
          </nav>
        </div>

        {/* Footer status in sidebar */}
        <div className="p-3 border-t border-border-subtle text-xs font-mono">
          {!isSidebarCollapsed ? (
            <div className="flex items-center justify-between px-2 py-1 bg-bg-base rounded-lg border border-border-subtle text-[11px]">
              <span className="flex items-center gap-1.5 text-content-secondary">
                {isWsConnected ? (
                  <>
                    <span className="w-2 h-2 rounded-full bg-accent-success animate-pulse" />
                    WS CONNECTED
                  </>
                ) : (
                  <>
                    <span className="w-2 h-2 rounded-full bg-accent-danger" />
                    DISCONNECTED
                  </>
                )}
              </span>
              <span className="text-content-muted">v2.0</span>
            </div>
          ) : (
            <div className="flex justify-center">
              <span
                className={`w-2.5 h-2.5 rounded-full ${isWsConnected ? 'bg-accent-success' : 'bg-accent-danger'}`}
                title={isWsConnected ? 'WebSocket Connected' : 'WebSocket Disconnected'}
              />
            </div>
          )}
        </div>
      </aside>

      {/* Main App Layout */}
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        {/* Top Bar (56px) */}
        <header className="h-14 bg-bg-panel/90 backdrop-blur border-b border-border-subtle flex items-center justify-between px-6 z-10">
          {/* Global Search Bar Button */}
          <div className="flex items-center gap-3">
            <button
              onClick={() => setIsSearchOpen(true)}
              className="flex items-center gap-2.5 px-3 py-1.5 rounded-xl bg-bg-base hover:bg-bg-elevated border border-border-subtle text-content-secondary hover:text-content-primary transition-all text-xs font-mono w-64 md:w-80 justify-between"
            >
              <div className="flex items-center gap-2">
                <Search className="w-3.5 h-3.5 text-content-muted" />
                <span className="text-content-muted">Search sessions, DAGs, artifacts...</span>
              </div>
              <span className="px-1.5 py-0.5 rounded bg-bg-panel text-[10px] text-content-muted border border-border-subtle">
                ⌘K
              </span>
            </button>
          </div>

          {/* Top Right Controls */}
          <div className="flex items-center gap-4">
            {/* Active Session Status Pill */}
            {activeSessionId && (
              <div className="flex items-center gap-2 bg-indigo-950/40 border border-indigo-500/40 px-3 py-1 rounded-full text-xs font-mono">
                <span className="w-2 h-2 rounded-full bg-accent-primary animate-ping" />
                <span className="text-indigo-200">ACTIVE: {activeSessionId.slice(0, 10)}...</span>
                <button
                  onClick={handleGlobalCancel}
                  className="ml-1.5 text-rose-400 hover:text-rose-300 font-bold flex items-center gap-0.5"
                  title="Cancel Active Execution"
                >
                  <StopCircle className="w-3.5 h-3.5" />
                  Cancel
                </button>
              </div>
            )}

            {/* Notification Bell */}
            <button
              onClick={() => setUnreadAlerts(0)}
              className="relative p-2 rounded-xl bg-bg-base hover:bg-bg-elevated border border-border-subtle text-content-secondary hover:text-content-primary transition-colors"
              title="System Alerts"
            >
              <Bell className="w-4 h-4" />
              {unreadAlerts > 0 && (
                <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-accent-danger text-white text-[9px] font-bold font-mono flex items-center justify-center animate-bounce">
                  {unreadAlerts}
                </span>
              )}
            </button>

            {/* User Badge */}
            <div className="flex items-center gap-2.5 pl-2 border-l border-border-subtle">
              <div className="w-8 h-8 rounded-xl bg-bg-elevated border border-border-subtle flex items-center justify-center font-mono text-xs font-bold text-accent-primary">
                OP
              </div>
            </div>
          </div>
        </header>

        {/* Dynamic Route Content */}
        <main className="flex-1 overflow-y-auto p-6 bg-bg-base">
          {children}
        </main>
      </div>

      {/* Global Search Modal */}
      {isSearchOpen && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-start justify-center pt-20 p-4">
          <div className="bg-bg-elevated border border-border-subtle rounded-2xl w-full max-w-xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-150">
            <div className="flex items-center px-4 py-3 border-b border-border-subtle bg-bg-panel">
              <Search className="w-4 h-4 text-accent-primary mr-2" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search sessions, task IDs, artifacts, agents..."
                className="flex-1 bg-transparent text-sm text-content-primary placeholder-content-muted focus:outline-none font-mono"
                autoFocus
              />
              <button
                onClick={() => setIsSearchOpen(false)}
                className="p-1 rounded-lg text-content-muted hover:text-content-primary"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-3 max-h-80 overflow-y-auto space-y-1">
              {filteredSearchSessions.length === 0 ? (
                <div className="text-center py-6 text-xs text-content-muted font-mono">
                  No matching sessions found.
                </div>
              ) : (
                filteredSearchSessions.map((s) => (
                  <button
                    key={s.session_id}
                    onClick={() => {
                      setIsSearchOpen(false);
                      navigate(`/sessions/${s.session_id}`);
                    }}
                    className="w-full text-left p-2.5 rounded-lg hover:bg-bg-panel transition-colors flex items-center justify-between group"
                  >
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-bold text-accent-primary">
                          {s.session_id}
                        </span>
                        <span className="text-[10px] font-mono text-content-muted">
                          {s.status}
                        </span>
                      </div>
                      <p className="text-xs text-content-secondary line-clamp-1 mt-0.5">
                        {s.user_request}
                      </p>
                    </div>
                    <span className="text-xs font-mono text-content-muted group-hover:text-content-primary">
                      ${s.total_cost_usd.toFixed(4)}
                    </span>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
