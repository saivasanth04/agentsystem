import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { WorkspaceInfo, RecentWorkspace } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';

interface WorkspaceContextType {
  activeWorkspace: WorkspaceInfo | null;
  recentWorkspaces: RecentWorkspace[];
  isLoading: boolean;
  isValidating: boolean;
  error: string | null;
  isModalOpen: boolean;
  openWorkspace: (path: string) => Promise<boolean>;
  refreshWorkspace: () => Promise<void>;
  clearError: () => void;
  removeRecentWorkspace: (path: string) => void;
  openWorkspaceModal: () => void;
  closeWorkspaceModal: () => void;
  setIsModalOpen: (open: boolean) => void;
}

const WorkspaceContext = createContext<WorkspaceContextType | undefined>(undefined);

const STORAGE_ACTIVE_KEY = 'orchestrator_active_workspace';
const STORAGE_RECENT_KEY = 'orchestrator_recent_workspaces';

export const WorkspaceProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceInfo | null>(null);
  const [recentWorkspaces, setRecentWorkspaces] = useState<RecentWorkspace[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isValidating, setIsValidating] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);

  // Load recent workspaces from localStorage
  useEffect(() => {
    try {
      const storedRecent = localStorage.getItem(STORAGE_RECENT_KEY);
      if (storedRecent) {
        setRecentWorkspaces(JSON.parse(storedRecent));
      }
    } catch {
      // ignore parse error
    }
  }, []);

  // Save recent workspaces
  const saveRecent = useCallback((updated: RecentWorkspace[]) => {
    setRecentWorkspaces(updated);
    try {
      localStorage.setItem(STORAGE_RECENT_KEY, JSON.stringify(updated));
    } catch {
      // ignore
    }
  }, []);

  const openWorkspace = useCallback(async (path: string): Promise<boolean> => {
    const trimmed = path.trim();
    if (!trimmed) {
      setError('Workspace path cannot be empty.');
      return false;
    }

    setIsValidating(true);
    setError(null);
    try {
      const res = await orchestratorApi.validateWorkspace(trimmed);
      if (!res.valid) {
        setError(res.error || `Could not access directory: ${trimmed}`);
        setIsValidating(false);
        return false;
      }

      const wsInfo: WorkspaceInfo = {
        path: res.resolved_path || res.path,
        project_name: res.project_name || 'Project',
        is_git: res.is_git,
        git_branch: res.git_branch || 'main',
        git_commit: res.git_commit,
        git_dirty: res.git_dirty,
        dirty_count: res.dirty_count,
        is_accessible: res.accessible,
        file_count: res.file_count,
      };

      setActiveWorkspace(wsInfo);
      localStorage.setItem(STORAGE_ACTIVE_KEY, wsInfo.path);

      // Add to recent list
      const recentItem: RecentWorkspace = {
        path: wsInfo.path,
        project_name: wsInfo.project_name,
        last_opened: new Date().toISOString(),
        is_git: wsInfo.is_git,
        git_branch: wsInfo.git_branch,
      };

      const filtered = recentWorkspaces.filter((r) => r.path !== wsInfo.path);
      const newRecent = [recentItem, ...filtered].slice(0, 10);
      saveRecent(newRecent);

      setIsValidating(false);
      return true;
    } catch (err: any) {
      setError(err.message || 'Error validating workspace path.');
      setIsValidating(false);
      return false;
    }
  }, [recentWorkspaces, saveRecent]);

  const refreshWorkspace = useCallback(async () => {
    if (!activeWorkspace) return;
    try {
      const res = await orchestratorApi.validateWorkspace(activeWorkspace.path);
      if (res.valid) {
        setActiveWorkspace({
          path: res.resolved_path || res.path,
          project_name: res.project_name,
          is_git: res.is_git,
          git_branch: res.git_branch,
          git_commit: res.git_commit,
          git_dirty: res.git_dirty,
          dirty_count: res.dirty_count,
          is_accessible: res.accessible,
          file_count: res.file_count,
        });
      }
    } catch {
      // keep current state if refresh probe fails
    }
  }, [activeWorkspace]);

  const removeRecentWorkspace = useCallback((pathToRemove: string) => {
    const updated = recentWorkspaces.filter((r) => r.path !== pathToRemove);
    saveRecent(updated);
  }, [recentWorkspaces, saveRecent]);

  // Initial workspace load
  useEffect(() => {
    const initializeWorkspace = async () => {
      setIsLoading(true);
      const savedPath = localStorage.getItem(STORAGE_ACTIVE_KEY);
      if (savedPath) {
        const success = await openWorkspace(savedPath);
        if (success) {
          setIsLoading(false);
          return;
        }
      }

      // Fallback: fetch default backend workspace
      try {
        const defaultInfo = await orchestratorApi.getWorkspaceInfo();
        if (defaultInfo && defaultInfo.path) {
          await openWorkspace(defaultInfo.path);
        }
      } catch (e: any) {
        setError(e.message || 'Failed to initialize workspace.');
      } finally {
        setIsLoading(false);
      }
    };

    initializeWorkspace();
  }, []);

  return (
    <WorkspaceContext.Provider
      value={{
        activeWorkspace,
        recentWorkspaces,
        isLoading,
        isValidating,
        error,
        isModalOpen,
        openWorkspace,
        refreshWorkspace,
        clearError: () => setError(null),
        removeRecentWorkspace,
        openWorkspaceModal: () => setIsModalOpen(true),
        closeWorkspaceModal: () => setIsModalOpen(false),
        setIsModalOpen,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
};

export const useWorkspace = () => {
  const context = useContext(WorkspaceContext);
  if (!context) {
    throw new Error('useWorkspace must be used within a WorkspaceProvider');
  }
  return context;
};
