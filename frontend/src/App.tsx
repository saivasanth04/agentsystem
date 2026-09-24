import React, { useState, useEffect } from 'react';
import { WorkspaceProvider } from './context/WorkspaceContext';
import { AppShell } from './components/AppShell';

// Orchestrator Pages
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

// Enterprise LiteLLM Gateway Admin Platform Pages
import { GatewayDashboardPage } from './pages/GatewayDashboardPage';
import { GatewayProvidersPage } from './pages/GatewayProvidersPage';
import { GatewayModelsPage } from './pages/GatewayModelsPage';
import { GatewayInspectorPage } from './pages/GatewayInspectorPage';
import { GatewayAnalyticsPage } from './pages/GatewayAnalyticsPage';
import { GatewaySettingsPage } from './pages/GatewaySettingsPage';

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
    // -----------------------------------------------------------------------
    // Enterprise LiteLLM Gateway Administration Platform
    // -----------------------------------------------------------------------
    if (currentRoute === '/gateway') {
      return <GatewayDashboardPage navigate={navigate} />;
    }
    if (currentRoute.startsWith('/gateway/providers')) {
      return <GatewayProvidersPage navigate={navigate} />;
    }
    if (currentRoute.startsWith('/gateway/models')) {
      return <GatewayModelsPage navigate={navigate} />;
    }
    if (currentRoute.startsWith('/gateway/inspector')) {
      return <GatewayInspectorPage navigate={navigate} />;
    }
    if (currentRoute.startsWith('/gateway/analytics')) {
      return <GatewayAnalyticsPage navigate={navigate} />;
    }
    if (currentRoute.startsWith('/gateway/settings')) {
      return <GatewaySettingsPage navigate={navigate} />;
    }

    // -----------------------------------------------------------------------
    // Multi-Agent Task Orchestrator Platform
    // -----------------------------------------------------------------------
    if (currentRoute === '/' || currentRoute === '') {
      return <DashboardPage navigate={navigate} />;
    }
    if (currentRoute === '/tasks/new') {
      return <NewTaskPage navigate={navigate} onSessionLaunched={setActiveSessionId} />;
    }
    if (currentRoute === '/sessions') {
      return <SessionsPage navigate={navigate} />;
    }
    if (currentRoute.includes('/live')) {
      const match = currentRoute.match(/\/sessions\/([^/]+)\/live/);
      const sid = match ? match[1] : activeSessionId;
      return <LiveDAGPage sessionId={sid} navigate={navigate} />;
    }
    if (currentRoute.startsWith('/sessions/')) {
      const sid = currentRoute.replace('/sessions/', '');
      return <SessionDetailPage sessionId={sid} navigate={navigate} />;
    }
    if (currentRoute === '/artifacts') {
      return <ArtifactsPage navigate={navigate} />;
    }
    if (currentRoute === '/agents') {
      return <AgentsPage navigate={navigate} />;
    }
    if (currentRoute === '/budgets') {
      return <BudgetsPage navigate={navigate} />;
    }
    if (currentRoute === '/memory') {
      return <MemoryPage navigate={navigate} />;
    }
    if (currentRoute === '/settings') {
      return <SettingsPage navigate={navigate} />;
    }

    return <DashboardPage navigate={navigate} />;
  };

  return (
    <WorkspaceProvider>
      <AppShell currentRoute={currentRoute} navigate={navigate} activeSessionId={activeSessionId}>
        {renderCurrentPage()}
      </AppShell>
    </WorkspaceProvider>
  );
};

export default App;
