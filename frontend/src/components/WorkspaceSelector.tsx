import React, { useState, useEffect } from 'react';
import { useWorkspace } from '../context/WorkspaceContext';
import { orchestratorApi } from '../api/client';

export const WorkspaceSelector: React.FC = () => {
  const { activeWorkspace, recentWorkspaces, openWorkspace, isValidating, error, clearError, removeRecentWorkspace } =
    useWorkspace();
  const [isOpen, setIsOpen] = useState(false);
  const [inputPath, setInputPath] = useState('');
  const [browsePath, setBrowsePath] = useState('');
  const [browseDirs, setBrowseDirs] = useState<Array<{ name: string; path: string; is_git: boolean }>>([]);
  const [browseParent, setBrowseParent] = useState<string | null>(null);
  const [isBrowsing, setIsBrowsing] = useState(false);

  useEffect(() => {
    if (activeWorkspace) {
      setInputPath(activeWorkspace.path);
      setBrowsePath(activeWorkspace.path);
    }
  }, [activeWorkspace]);

  const loadBrowseDirs = async (targetPath: string) => {
    setIsBrowsing(true);
    try {
      const res = await orchestratorApi.browseDirectories(targetPath);
      setBrowseDirs(res.directories);
      setBrowseParent(res.parent_path);
      setBrowsePath(res.current_path);
      setInputPath(res.current_path);
    } catch {
      // fallback
    } finally {
      setIsBrowsing(false);
    }
  };

  const handleOpenDropdown = () => {
    setIsOpen(!isOpen);
    clearError();
    if (!isOpen && activeWorkspace) {
      loadBrowseDirs(activeWorkspace.path);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputPath.trim()) return;
    const success = await openWorkspace(inputPath.trim());
    if (success) {
      setIsOpen(false);
    }
  };

  const handleSelectRecent = async (path: string) => {
    const success = await openWorkspace(path);
    if (success) {
      setIsOpen(false);
    }
  };

  return (
    <div className="relative">
      {/* Trigger Button in Header */}
      <button
        onClick={handleOpenDropdown}
        className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 hover:bg-slate-800 border border-slate-700/80 rounded-lg text-xs transition-all shadow-sm group"
        title="Active Workspace Directory"
      >
        <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
        <span className="text-slate-400 font-mono text-[11px]">Workspace:</span>
        <span className="font-semibold text-slate-100 max-w-[140px] truncate" title={activeWorkspace?.path}>
          {activeWorkspace?.project_name || 'Select Workspace'}
        </span>
        {activeWorkspace?.is_git && (
          <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-blue-950/80 border border-blue-800/60 text-blue-300 font-mono text-[10px]">
            <svg className="w-2.5 h-2.5" fill="currentColor" viewBox="0 0 16 16">
              <path fillRule="evenodd" d="M11.75 4a1.75 1.75 0 1 0-3.5 0 1.75 1.75 0 0 0 3.5 0ZM8 0a8 8 0 1 0 0 16A8 8 0 0 0 8 0Zm4.25 4a3.25 3.25 0 0 1-2.43 3.142A3.251 3.251 0 0 1 7.25 10.75v1.5a1.75 1.75 0 1 1-1.5 0v-6.5a1.75 1.75 0 1 1 1.5 0v2.75a1.75 1.75 0 0 0 1.75-1.75A3.25 3.25 0 0 1 12.25 4Z"/>
            </svg>
            {activeWorkspace.git_branch}
            {activeWorkspace.git_dirty && <span className="text-amber-400 font-bold">*</span>}
          </span>
        )}
        <svg
          className={`w-3.5 h-3.5 text-slate-400 transition-transform ${isOpen ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Modal / Dropdown */}
      {isOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setIsOpen(false)} />
          <div className="absolute left-0 mt-2 w-[480px] bg-slate-900 border border-slate-700 rounded-xl shadow-2xl z-50 overflow-hidden animate-in fade-in slide-in-from-top-2 duration-150">
            {/* Header */}
            <div className="p-3 bg-slate-800/80 border-b border-slate-700 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-base">📁</span>
                <span className="font-semibold text-xs text-slate-100 uppercase tracking-wider">
                  Open & Switch Workspace
                </span>
              </div>
              <span className="text-[10px] text-slate-400 bg-slate-950 px-2 py-0.5 rounded border border-slate-800">
                Persistent across sessions
              </span>
            </div>

            <div className="p-4 space-y-4 max-h-[80vh] overflow-y-auto">
              {/* Path Input Form */}
              <form onSubmit={handleSubmit} className="space-y-2">
                <label className="block text-xs font-medium text-slate-300">
                  Target Directory Path:
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={inputPath}
                    onChange={(e) => setInputPath(e.target.value)}
                    placeholder="e.g. C:\Users\name\projects\my-app or /home/user/app"
                    className="flex-1 px-3 py-2 bg-slate-950 border border-slate-700 rounded-lg text-xs font-mono text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                    disabled={isValidating}
                  />
                  <button
                    type="submit"
                    disabled={isValidating || !inputPath.trim()}
                    className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-1 shadow"
                  >
                    {isValidating ? (
                      <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    ) : (
                      'Open'
                    )}
                  </button>
                </div>
                {error && (
                  <div className="p-2.5 bg-rose-950/60 border border-rose-800/60 rounded-lg text-rose-300 text-xs flex items-center gap-2">
                    <span className="text-sm">⚠️</span>
                    <span>{error}</span>
                  </div>
                )}
              </form>

              {/* Quick Directory Navigator */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-[11px] font-semibold text-slate-400">
                  <span>FILESYSTEM DIRECTORY BROWSER</span>
                  <span className="text-[10px] text-slate-500 font-mono truncate max-w-[200px]">
                    {browsePath}
                  </span>
                </div>
                <div className="bg-slate-950/80 border border-slate-800 rounded-lg p-2 max-h-36 overflow-y-auto font-mono text-xs space-y-1">
                  {browseParent && (
                    <button
                      type="button"
                      onClick={() => loadBrowseDirs(browseParent)}
                      className="w-full text-left px-2 py-1 hover:bg-slate-800 rounded text-slate-400 flex items-center gap-1.5 transition-colors"
                    >
                      <span>📁</span> .. (Up one level)
                    </button>
                  )}
                  {browseDirs.map((dir) => (
                    <div
                      key={dir.path}
                      className="flex items-center justify-between px-2 py-1 hover:bg-slate-800/80 rounded group transition-colors"
                    >
                      <button
                        type="button"
                        onClick={() => loadBrowseDirs(dir.path)}
                        className="flex items-center gap-1.5 text-slate-300 hover:text-indigo-300 truncate text-left flex-1"
                      >
                        <span>📁</span>
                        <span className="truncate">{dir.name}</span>
                        {dir.is_git && <span className="text-[9px] px-1 py-0.2 bg-blue-900/60 text-blue-300 rounded">git</span>}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setInputPath(dir.path);
                          openWorkspace(dir.path).then((ok) => ok && setIsOpen(false));
                        }}
                        className="text-[10px] px-2 py-0.5 bg-indigo-900/60 hover:bg-indigo-700 text-indigo-200 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        Select
                      </button>
                    </div>
                  ))}
                  {browseDirs.length === 0 && !isBrowsing && (
                    <div className="text-slate-500 text-[11px] p-2 text-center">No subdirectories found</div>
                  )}
                </div>
              </div>

              {/* Recent Workspaces List */}
              {recentWorkspaces.length > 0 && (
                <div className="space-y-1.5 border-t border-slate-800 pt-3">
                  <span className="text-[11px] font-semibold text-slate-400">RECENT WORKSPACES</span>
                  <div className="space-y-1 max-h-40 overflow-y-auto">
                    {recentWorkspaces.map((recent) => (
                      <div
                        key={recent.path}
                        className={`flex items-center justify-between p-2 rounded-lg border transition-all text-xs group ${
                          activeWorkspace?.path === recent.path
                            ? 'bg-indigo-950/40 border-indigo-700/60 text-indigo-200'
                            : 'bg-slate-950/60 border-slate-800 hover:border-slate-700 text-slate-300'
                        }`}
                      >
                        <button
                          type="button"
                          onClick={() => handleSelectRecent(recent.path)}
                          className="flex items-center gap-2 flex-1 min-w-0 text-left"
                        >
                          <span className="text-sm">📁</span>
                          <div className="min-w-0 flex-1">
                            <div className="font-medium text-slate-100 flex items-center gap-1.5 truncate">
                              <span>{recent.project_name}</span>
                              {recent.is_git && (
                                <span className="text-[9px] px-1 py-0.2 bg-blue-950 border border-blue-800 text-blue-400 rounded">
                                  {recent.git_branch}
                                </span>
                              )}
                            </div>
                            <div className="text-[10px] text-slate-500 font-mono truncate">{recent.path}</div>
                          </div>
                        </button>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            removeRecentWorkspace(recent.path);
                          }}
                          className="text-slate-500 hover:text-rose-400 p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                          title="Remove from recents"
                        >
                          ✕
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
};
