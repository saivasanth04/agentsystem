import React, { useState, useEffect, useCallback } from 'react';
import { orchestratorApi } from '../api/client';
import { WorkspaceFile } from '../types/orchestrator';

interface CodeEditorProps {
  filepath: string;
  workspacePath?: string;
  onClose?: () => void;
  className?: string;
}

export const CodeEditor: React.FC<CodeEditorProps> = ({
  filepath,
  workspacePath,
  onClose,
  className = '',
}) => {
  const [fileData, setFileData] = useState<WorkspaceFile | null>(null);
  const [content, setContent] = useState<string>('');
  const [originalContent, setOriginalContent] = useState<string>('');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<boolean>(false);
  const [isEditing, setIsEditing] = useState<boolean>(false);

  const isDirty = content !== originalContent;

  const loadFile = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await orchestratorApi.readWorkspaceFile(filepath, workspacePath);
      setFileData(data);
      setContent(data.content);
      setOriginalContent(data.content);
    } catch (err: any) {
      setError(err.message || `Failed to read file ${filepath}`);
    } finally {
      setIsLoading(false);
    }
  }, [filepath, workspacePath]);

  useEffect(() => {
    loadFile();
  }, [loadFile]);

  const handleSave = async () => {
    if (!isDirty) return;
    setIsSaving(true);
    setError(null);
    try {
      const res = await orchestratorApi.saveWorkspaceFile(filepath, content, workspacePath);
      if (res.success) {
        setOriginalContent(content);
        setSaveSuccess(true);
        setTimeout(() => setSaveSuccess(false), 2500);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to save file.');
    } finally {
      setIsSaving(false);
    }
  };

  // Keyboard shortcut Ctrl+S / Cmd+S
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        handleSave();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [content, originalContent]);

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    alert('File contents copied to clipboard!');
  };

  const handleRevert = () => {
    if (confirm('Discard unsaved edits and reload file?')) {
      setContent(originalContent);
    }
  };

  const lines = content.split('\n');

  return (
    <div className={`flex flex-col h-full bg-slate-950 border border-slate-800 rounded-xl overflow-hidden shadow-xl ${className}`}>
      {/* Editor Tab Bar */}
      <div className="flex items-center justify-between px-3 py-2 bg-slate-900 border-b border-slate-800">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs">📄</span>
          <span className="font-mono text-xs text-slate-100 font-semibold truncate" title={filepath}>
            {filepath}
          </span>
          {isDirty && (
            <span className="px-1.5 py-0.5 rounded bg-amber-950/80 border border-amber-700/60 text-amber-400 text-[10px] font-mono flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
              Unsaved
            </span>
          )}
          {saveSuccess && (
            <span className="px-1.5 py-0.5 rounded bg-emerald-950/80 border border-emerald-700/60 text-emerald-400 text-[10px] font-mono">
              ✓ Saved
            </span>
          )}
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsEditing(!isEditing)}
            className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
              isEditing ? 'bg-indigo-600 text-white' : 'bg-slate-800 hover:bg-slate-700 text-slate-300'
            }`}
          >
            {isEditing ? '✏️ Edit Mode' : '👁️ View Mode'}
          </button>
          {isDirty && (
            <>
              <button
                onClick={handleRevert}
                className="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs"
                title="Revert changes"
              >
                Revert
              </button>
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white rounded text-xs font-medium shadow flex items-center gap-1"
              >
                {isSaving ? 'Saving...' : '💾 Save (Ctrl+S)'}
              </button>
            </>
          )}
          <button
            onClick={handleCopy}
            className="p-1 hover:bg-slate-800 rounded text-slate-400 hover:text-slate-200 text-xs"
            title="Copy content"
          >
            📋
          </button>
          {onClose && (
            <button
              onClick={onClose}
              className="p-1 hover:bg-slate-800 rounded text-slate-400 hover:text-rose-400 text-xs font-bold"
              title="Close editor"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Editor Body */}
      {isLoading ? (
        <div className="flex-1 flex items-center justify-center text-xs text-slate-500 gap-2 font-mono">
          <span className="w-4 h-4 border-2 border-slate-400 border-t-transparent rounded-full animate-spin" />
          Reading file content...
        </div>
      ) : error ? (
        <div className="p-4 text-xs text-rose-400 bg-rose-950/20 font-mono">
          ⚠️ {error}
        </div>
      ) : isEditing ? (
        <div className="flex-1 relative flex bg-slate-950 font-mono text-xs overflow-hidden">
          {/* Line Numbers */}
          <div className="py-3 px-2.5 bg-slate-900/80 text-slate-600 text-right select-none border-r border-slate-800/80 font-mono text-[11px] leading-relaxed">
            {lines.map((_, i) => (
              <div key={i}>{i + 1}</div>
            ))}
          </div>
          {/* Textarea */}
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="flex-1 p-3 bg-transparent text-slate-100 focus:outline-none resize-none font-mono text-[11px] leading-relaxed overflow-y-auto whitespace-pre tab-4"
            spellCheck={false}
          />
        </div>
      ) : (
        <div className="flex-1 flex overflow-hidden font-mono text-xs bg-slate-950">
          {/* Line Numbers */}
          <div className="py-3 px-2.5 bg-slate-900/80 text-slate-600 text-right select-none border-r border-slate-800/80 font-mono text-[11px] leading-relaxed">
            {lines.map((_, i) => (
              <div key={i}>{i + 1}</div>
            ))}
          </div>
          {/* Content Viewer */}
          <pre className="flex-1 p-3 text-slate-200 overflow-y-auto font-mono text-[11px] leading-relaxed whitespace-pre selection:bg-indigo-900">
            {content}
          </pre>
        </div>
      )}

      {/* Status Bar */}
      <div className="px-3 py-1.5 bg-slate-900/90 border-t border-slate-800 flex items-center justify-between text-[10px] font-mono text-slate-400">
        <div className="flex items-center gap-3">
          <span>Lines: {lines.length}</span>
          <span>Size: {fileData?.size ? (fileData.size / 1024).toFixed(1) + ' KB' : '0 KB'}</span>
          {fileData?.modified_at && (
            <span>Modified: {new Date(fileData.modified_at).toLocaleTimeString()}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span>UTF-8</span>
          <span>{filepath.endsWith('.ts') || filepath.endsWith('.tsx') ? 'TypeScript' : filepath.endsWith('.py') ? 'Python' : 'Plain Text'}</span>
        </div>
      </div>
    </div>
  );
};
