import React, { useState, useEffect } from 'react';
import { FileTreeNode } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import { useWorkspace } from '../context/WorkspaceContext';

interface FileExplorerProps {
  onSelectFile: (filepath: string) => void;
  selectedFile?: string | null;
  workspacePath?: string;
  className?: string;
}

export const FileExplorer: React.FC<FileExplorerProps> = ({
  onSelectFile,
  selectedFile,
  workspacePath,
  className = '',
}) => {
  const { activeWorkspace } = useWorkspace();
  const effectivePath = workspacePath || activeWorkspace?.path;

  const [tree, setTree] = useState<FileTreeNode[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set());

  // Modal dialog states for file mutations
  const [createModal, setCreateModal] = useState<{ isOpen: boolean; isDir: boolean; parentPath: string } | null>(null);
  const [newItemName, setNewItemName] = useState('');
  const [renameModal, setRenameModal] = useState<{ isOpen: boolean; oldPath: string } | null>(null);
  const [newName, setNewName] = useState('');

  const fetchFiles = async () => {
    if (!effectivePath) return;
    setIsLoading(true);
    setError(null);
    try {
      const res = await orchestratorApi.getWorkspaceFiles(effectivePath);
      setTree(res.tree);
      // Auto-expand root directories
      const initialExpanded = new Set<string>();
      res.tree.forEach((node) => {
        if (node.type === 'directory') initialExpanded.add(node.path);
      });
      setExpandedPaths(initialExpanded);
    } catch (err: any) {
      setError(err.message || 'Failed to load workspace files.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchFiles();
  }, [effectivePath]);

  const toggleExpand = (path: string) => {
    const next = new Set(expandedPaths);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    setExpandedPaths(next);
  };

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!createModal || !newItemName.trim() || !effectivePath) return;

    const fullRelPath = createModal.parentPath
      ? `${createModal.parentPath}/${newItemName.trim()}`
      : newItemName.trim();

    try {
      await orchestratorApi.createWorkspaceFile(fullRelPath, createModal.isDir, '', effectivePath);
      setCreateModal(null);
      setNewItemName('');
      await fetchFiles();
      if (!createModal.isDir) {
        onSelectFile(fullRelPath);
      }
    } catch (err: any) {
      alert(`Error creating item: ${err.message}`);
    }
  };

  const handleRenameSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!renameModal || !newName.trim() || !effectivePath) return;

    const parent = renameModal.oldPath.includes('/')
      ? renameModal.oldPath.substring(0, renameModal.oldPath.lastIndexOf('/'))
      : '';
    const newPath = parent ? `${parent}/${newName.trim()}` : newName.trim();

    try {
      await orchestratorApi.renameWorkspaceFile(renameModal.oldPath, newPath, effectivePath);
      setRenameModal(null);
      setNewName('');
      await fetchFiles();
      if (selectedFile === renameModal.oldPath) {
        onSelectFile(newPath);
      }
    } catch (err: any) {
      alert(`Error renaming item: ${err.message}`);
    }
  };

  const handleDelete = async (filepath: string) => {
    if (!effectivePath) return;
    if (!confirm(`Are you sure you want to delete "${filepath}"?`)) return;

    try {
      await orchestratorApi.deleteWorkspaceFile(filepath, effectivePath);
      await fetchFiles();
    } catch (err: any) {
      alert(`Error deleting item: ${err.message}`);
    }
  };

  // Helper file icon renderer
  const getFileIcon = (node: FileTreeNode) => {
    if (node.type === 'directory') return '📁';
    const ext = node.extension?.toLowerCase() || '';
    if (['.ts', '.tsx'].includes(ext)) return '🔷';
    if (['.js', '.jsx'].includes(ext)) return '🟨';
    if (['.py'].includes(ext)) return '🐍';
    if (['.json'].includes(ext)) return '📦';
    if (['.md', '.txt'].includes(ext)) return '📝';
    if (['.css', '.scss', '.html'].includes(ext)) return '🎨';
    return '📄';
  };

  const filterTree = (nodes: FileTreeNode[], query: string): FileTreeNode[] => {
    if (!query) return nodes;
    const lowerQuery = query.toLowerCase();

    return nodes
      .map((node) => {
        if (node.type === 'directory') {
          const filteredChildren = filterTree(node.children || [], query);
          if (filteredChildren.length > 0 || node.name.toLowerCase().includes(lowerQuery)) {
            return { ...node, children: filteredChildren };
          }
          return null;
        }
        return node.name.toLowerCase().includes(lowerQuery) || node.path.toLowerCase().includes(lowerQuery)
          ? node
          : null;
      })
      .filter(Boolean) as FileTreeNode[];
  };

  const renderNode = (node: FileTreeNode, depth = 0) => {
    const isExpanded = expandedPaths.has(node.path);
    const isSelected = selectedFile === node.path;

    if (node.type === 'directory') {
      return (
        <div key={node.path} className="select-none">
          <div
            className={`flex items-center justify-between py-1 px-2 rounded hover:bg-slate-800/80 cursor-pointer group text-xs text-slate-300 transition-colors`}
            style={{ paddingLeft: `${depth * 12 + 6}px` }}
            onClick={() => toggleExpand(node.path)}
          >
            <div className="flex items-center gap-1.5 truncate flex-1 min-w-0">
              <span className="text-[10px] text-slate-500">{isExpanded ? '▼' : '▶'}</span>
              <span>📁</span>
              <span className="font-medium text-slate-200 truncate">{node.name}</span>
            </div>
            {/* Quick Actions */}
            <div className="opacity-0 group-hover:opacity-100 flex items-center gap-1 text-[10px] text-slate-400">
              <button
                title="New File"
                onClick={(e) => {
                  e.stopPropagation();
                  setCreateModal({ isOpen: true, isDir: false, parentPath: node.path });
                }}
                className="hover:text-slate-100 p-0.5"
              >
                +📄
              </button>
              <button
                title="New Folder"
                onClick={(e) => {
                  e.stopPropagation();
                  setCreateModal({ isOpen: true, isDir: true, parentPath: node.path });
                }}
                className="hover:text-slate-100 p-0.5"
              >
                +📁
              </button>
              <button
                title="Rename"
                onClick={(e) => {
                  e.stopPropagation();
                  setRenameModal({ isOpen: true, oldPath: node.path });
                  setNewName(node.name);
                }}
                className="hover:text-indigo-300 p-0.5"
              >
                ✎
              </button>
              <button
                title="Delete"
                onClick={(e) => {
                  e.stopPropagation();
                  handleDelete(node.path);
                }}
                className="hover:text-rose-400 p-0.5"
              >
                🗑
              </button>
            </div>
          </div>
          {isExpanded && node.children && (
            <div>{node.children.map((child) => renderNode(child, depth + 1))}</div>
          )}
        </div>
      );
    }

    return (
      <div
        key={node.path}
        className={`flex items-center justify-between py-1 px-2 rounded cursor-pointer group text-xs transition-colors ${
          isSelected
            ? 'bg-indigo-950/70 text-indigo-200 border-l-2 border-indigo-500 font-medium'
            : 'hover:bg-slate-800/60 text-slate-300'
        }`}
        style={{ paddingLeft: `${depth * 12 + 18}px` }}
        onClick={() => onSelectFile(node.path)}
      >
        <div className="flex items-center gap-1.5 truncate flex-1 min-w-0">
          <span>{getFileIcon(node)}</span>
          <span className="truncate">{node.name}</span>
        </div>
        <div className="opacity-0 group-hover:opacity-100 flex items-center gap-1 text-[10px] text-slate-400">
          <button
            title="Rename"
            onClick={(e) => {
              e.stopPropagation();
              setRenameModal({ isOpen: true, oldPath: node.path });
              setNewName(node.name);
            }}
            className="hover:text-indigo-300 p-0.5"
          >
            ✎
          </button>
          <button
            title="Delete"
            onClick={(e) => {
              e.stopPropagation();
              handleDelete(node.path);
            }}
            className="hover:text-rose-400 p-0.5"
          >
            🗑
          </button>
        </div>
      </div>
    );
  };

  const displayedNodes = filterTree(tree, searchQuery);

  return (
    <div className={`flex flex-col h-full bg-slate-950 border-r border-slate-800 ${className}`}>
      {/* Explorer Header */}
      <div className="p-2.5 border-b border-slate-800 flex items-center justify-between bg-slate-900/60">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Explorer
          </span>
          {activeWorkspace?.project_name && (
            <span className="text-[11px] font-mono text-indigo-300 truncate max-w-[120px]" title={effectivePath}>
              ({activeWorkspace.project_name})
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setCreateModal({ isOpen: true, isDir: false, parentPath: '' })}
            title="New File in Root"
            className="p-1 hover:bg-slate-800 rounded text-slate-400 hover:text-slate-100 text-xs"
          >
            +📄
          </button>
          <button
            onClick={() => setCreateModal({ isOpen: true, isDir: true, parentPath: '' })}
            title="New Folder in Root"
            className="p-1 hover:bg-slate-800 rounded text-slate-400 hover:text-slate-100 text-xs"
          >
            +📁
          </button>
          <button
            onClick={fetchFiles}
            title="Refresh Files"
            className="p-1 hover:bg-slate-800 rounded text-slate-400 hover:text-slate-100 text-xs"
          >
            🔄
          </button>
        </div>
      </div>

      {/* Search Input */}
      <div className="p-2 border-b border-slate-900">
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Filter workspace files..."
          className="w-full px-2.5 py-1 bg-slate-900 border border-slate-800 rounded text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-slate-700"
        />
      </div>

      {/* Tree Content */}
      <div className="flex-1 overflow-y-auto p-1 font-mono">
        {isLoading ? (
          <div className="flex items-center justify-center p-6 text-xs text-slate-500 gap-2">
            <span className="w-3.5 h-3.5 border-2 border-slate-400 border-t-transparent rounded-full animate-spin" />
            Loading files...
          </div>
        ) : error ? (
          <div className="p-3 text-xs text-rose-400 bg-rose-950/40 rounded border border-rose-900/60 m-2">
            ⚠️ {error}
          </div>
        ) : displayedNodes.length === 0 ? (
          <div className="text-center p-6 text-xs text-slate-500">
            {searchQuery ? 'No matching files found' : 'No files in workspace'}
          </div>
        ) : (
          displayedNodes.map((node) => renderNode(node))
        )}
      </div>

      {/* Create Modal */}
      {createModal?.isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-sm p-4 space-y-3 shadow-2xl">
            <h3 className="text-xs font-semibold text-slate-100 uppercase tracking-wider">
              {createModal.isDir ? 'Create New Folder' : 'Create New File'}
            </h3>
            <form onSubmit={handleCreateSubmit} className="space-y-3">
              <input
                type="text"
                autoFocus
                value={newItemName}
                onChange={(e) => setNewItemName(e.target.value)}
                placeholder={createModal.isDir ? 'folder-name' : 'filename.ext'}
                className="w-full px-3 py-1.5 bg-slate-950 border border-slate-700 rounded text-xs text-slate-100 focus:outline-none focus:border-indigo-500 font-mono"
              />
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setCreateModal(null)}
                  className="px-3 py-1 bg-slate-800 hover:bg-slate-700 rounded text-xs text-slate-300"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!newItemName.trim()}
                  className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded text-xs"
                >
                  Create
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Rename Modal */}
      {renameModal?.isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-sm p-4 space-y-3 shadow-2xl">
            <h3 className="text-xs font-semibold text-slate-100 uppercase tracking-wider">Rename Item</h3>
            <form onSubmit={handleRenameSubmit} className="space-y-3">
              <input
                type="text"
                autoFocus
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                className="w-full px-3 py-1.5 bg-slate-950 border border-slate-700 rounded text-xs text-slate-100 focus:outline-none focus:border-indigo-500 font-mono"
              />
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setRenameModal(null)}
                  className="px-3 py-1 bg-slate-800 hover:bg-slate-700 rounded text-xs text-slate-300"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!newName.trim()}
                  className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded text-xs"
                >
                  Rename
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
