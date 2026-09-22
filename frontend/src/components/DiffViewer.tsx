import React, { useState } from 'react';
import { FileDiffItem } from '../types/orchestrator';
import { AgentAvatar } from './AgentAvatar';
import {
  FileCode,
  FilePlus,
  FileMinus,
  FileEdit,
  Columns,
  AlignLeft,
  Copy,
  Check,
  Cpu,
  Hash
} from 'lucide-react';

interface DiffViewerProps {
  diffs: FileDiffItem[];
  className?: string;
}

export const DiffViewer: React.FC<DiffViewerProps> = ({ diffs, className = '' }) => {
  const [selectedFileIdx, setSelectedFileIdx] = useState<number>(0);
  const [viewMode, setViewMode] = useState<'unified' | 'split'>('unified');
  const [copied, setCopied] = useState(false);

  if (!diffs || diffs.length === 0) {
    return (
      <div className={`p-8 bg-bg-panel border border-border-subtle rounded-xl text-center ${className}`}>
        <FileCode className="w-10 h-10 text-content-muted mx-auto mb-2" />
        <h4 className="text-sm font-semibold text-content-primary">No File Modifications</h4>
        <p className="text-xs text-content-secondary mt-1">
          Files changed during task execution will appear here with line diffs and agent author attribution.
        </p>
      </div>
    );
  }

  const currentDiff = diffs[selectedFileIdx] || diffs[0];

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const renderDiffLines = (diffText?: string) => {
    if (!diffText) return <div className="text-content-muted text-xs p-4">No diff content available.</div>;

    const lines = diffText.split('\n');
    return lines.map((line, idx) => {
      let lineStyle = 'text-content-secondary bg-transparent';
      let prefix = ' ';

      if (line.startsWith('+') && !line.startsWith('+++')) {
        lineStyle = 'text-emerald-300 bg-emerald-950/40 border-l-2 border-emerald-500';
        prefix = '+';
      } else if (line.startsWith('-') && !line.startsWith('---')) {
        lineStyle = 'text-rose-300 bg-rose-950/40 border-l-2 border-rose-500';
        prefix = '-';
      } else if (line.startsWith('@@')) {
        lineStyle = 'text-indigo-400 bg-indigo-950/20 font-semibold my-1';
      }

      return (
        <div key={idx} className={`px-3 py-0.5 font-mono text-xs whitespace-pre ${lineStyle}`}>
          <span className="inline-block w-8 text-right mr-3 select-none text-content-muted text-[10px]">
            {idx + 1}
          </span>
          {line}
        </div>
      );
    });
  };

  return (
    <div className={`grid grid-cols-1 lg:grid-cols-4 gap-4 bg-bg-panel border border-border-subtle rounded-xl overflow-hidden shadow-xl ${className}`}>
      {/* File List Sidebar */}
      <div className="lg:col-span-1 border-r border-border-subtle bg-bg-base/60 p-3 flex flex-col h-[520px]">
        <div className="flex items-center justify-between pb-2 mb-2 border-b border-border-subtle">
          <span className="text-xs font-mono font-bold text-content-muted uppercase">Changed Files</span>
          <span className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-bg-elevated text-content-primary">
            {diffs.length}
          </span>
        </div>

        <div className="flex-1 overflow-y-auto space-y-1">
          {diffs.map((file, idx) => {
            const isSelected = selectedFileIdx === idx;
            let FileIcon = FileEdit;
            let iconColor = 'text-amber-400';

            if (file.status === 'ADDED') {
              FileIcon = FilePlus;
              iconColor = 'text-emerald-400';
            } else if (file.status === 'DELETED') {
              FileIcon = FileMinus;
              iconColor = 'text-rose-400';
            }

            return (
              <button
                key={idx}
                onClick={() => setSelectedFileIdx(idx)}
                className={`w-full flex items-center justify-between p-2 rounded-lg text-left text-xs transition-colors ${
                  isSelected
                    ? 'bg-accent-primary text-white font-medium shadow'
                    : 'hover:bg-bg-elevated text-content-secondary hover:text-content-primary'
                }`}
              >
                <div className="flex items-center gap-2 truncate">
                  <FileIcon className={`w-4 h-4 shrink-0 ${isSelected ? 'text-white' : iconColor}`} />
                  <span className="font-mono truncate">{file.path}</span>
                </div>
                <span className={`text-[10px] font-mono uppercase px-1 rounded ${
                  isSelected ? 'bg-black/20 text-white' : 'text-content-muted'
                }`}>
                  {file.status}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Main Diff Content */}
      <div className="lg:col-span-3 flex flex-col h-[520px] bg-bg-panel">
        {/* Diff Header with Agent Blame */}
        <div className="flex flex-wrap items-center justify-between px-4 py-3 border-b border-border-subtle bg-bg-elevated/70">
          <div className="flex items-center gap-3">
            <span className="font-mono text-xs font-bold text-content-primary">
              {currentDiff.path}
            </span>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-bg-base border border-border-subtle text-content-secondary">
              {currentDiff.status}
            </span>
          </div>

          {/* Agent Blame Attribution */}
          <div className="flex items-center gap-4 text-xs font-mono text-content-secondary">
            {currentDiff.author_agent && (
              <div className="flex items-center gap-1.5 bg-bg-base px-2 py-1 rounded border border-border-subtle">
                <AgentAvatar agentName={currentDiff.author_agent} size="sm" />
                <span className="text-content-primary font-semibold">{currentDiff.author_agent}</span>
              </div>
            )}
            {currentDiff.model_used && (
              <div className="flex items-center gap-1 bg-bg-base px-2 py-1 rounded border border-border-subtle text-[11px]">
                <Cpu className="w-3 h-3 text-accent-info" />
                <span>{currentDiff.model_used}</span>
              </div>
            )}
            {currentDiff.prompt_hash && (
              <div className="flex items-center gap-1 bg-bg-base px-2 py-1 rounded border border-border-subtle text-[11px]">
                <Hash className="w-3 h-3 text-content-muted" />
                <span>{currentDiff.prompt_hash.slice(0, 7)}</span>
              </div>
            )}
            <button
              onClick={() => handleCopy(currentDiff.diff_text || currentDiff.after_content || '')}
              className="p-1 rounded hover:bg-bg-panel text-content-secondary hover:text-content-primary"
              title="Copy Diff Content"
            >
              {copied ? <Check className="w-4 h-4 text-accent-success" /> : <Copy className="w-4 h-4" />}
            </button>
          </div>
        </div>

        {/* Diff Line Viewer */}
        <div className="flex-1 overflow-auto bg-bg-base/70 p-2">
          {renderDiffLines(currentDiff.diff_text || currentDiff.after_content)}
        </div>
      </div>
    </div>
  );
};
