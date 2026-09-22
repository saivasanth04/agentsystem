import React, { useState, useEffect } from 'react';
import { AppShell } from './components/AppShell';
import { DashboardPage } from './pages/DashboardPage';
import { NewTaskPage } from './pages/NewTaskPage';
import { SessionsPage } from './pages/SessionsPage';
import { SessionDetailPage } from './pages/SessionDetailPage';
import { LiveDAGPage } from './pages/LiveDAGPage';
import { ArtifactsPage } from './pages/ArtifactsPage';
import { AgentsPage } from './pages/AgentsPage';
import { BudgetsPage } from './pages/BudgetsPage';
import { MemoryPage } from './pages/MemoryPage';
import { SettingsPage } from './pages/SettingsPage';

export const App: React.FC = () => {
  const [currentRoute, setCurrentRoute] = useState<string>(window.location.pathname || '/');
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

  useEffect(() => {
    const handlePopState = () => {
      setCurrentRoute(window.location.pathname || '/');
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const navigate = (route: string) => {
    window.history.pushState({}, '', route);
    setCurrentRoute(route);
  };

  const renderCurrentPage = () => {
    // 1. Dashboard
    if (currentRoute === '/' || currentRoute === '') {
      return <DashboardPage navigate={navigate} />;
    }

    // 2. New Task
    if (currentRoute === '/tasks/new') {
      return <NewTaskPage navigate={navigate} onSessionLaunched={setActiveSessionId} />;
    }

    // 3. Sessions List
    if (currentRoute === '/sessions') {
      return <SessionsPage navigate={navigate} />;
    }

    // 4. Live DAG (e.g. /sessions/:id/live or /sessions/live)
    if (currentRoute.includes('/live')) {
      const match = currentRoute.match(/\/sessions\/([^/]+)\/live/);
      const sid = match ? match[1] : activeSessionId;
      return <LiveDAGPage sessionId={sid} navigate={navigate} />;
    }

    // 5. Session Detail (e.g. /sessions/:id)
    if (currentRoute.startsWith('/sessions/')) {
      const sid = currentRoute.replace('/sessions/', '');
      return <SessionDetailPage sessionId={sid} navigate={navigate} />;
    }

    // 6. Artifacts
    if (currentRoute === '/artifacts') {
      return <ArtifactsPage navigate={navigate} />;
    }

    // 7. Agents & Swarm
    if (currentRoute === '/agents') {
      return <AgentsPage navigate={navigate} />;
    }

    // 8. Budgets
    if (currentRoute === '/budgets') {
      return <BudgetsPage navigate={navigate} />;
    }

    // 9. Memory
    if (currentRoute === '/memory') {
      return <MemoryPage navigate={navigate} />;
    }

    // 10. Settings
    if (currentRoute === '/settings') {
      return <SettingsPage navigate={navigate} />;
    }

    return <DashboardPage navigate={navigate} />;
  };

  return (
    <AppShell currentRoute={currentRoute} navigate={navigate} activeSessionId={activeSessionId}>
      {renderCurrentPage()}
    </AppShell>
  );
};
