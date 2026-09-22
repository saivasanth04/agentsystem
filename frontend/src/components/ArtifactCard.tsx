import React, { useState } from 'react';
import { ArtifactMeta } from '../types/orchestrator';
import {
  FileCode,
  FileText,
  GitPullRequest,
  CheckSquare,
  FileSpreadsheet,
  Terminal,
  Download,
  Eye,
  X,
  Copy,
  Check
} from 'lucide-react';
import { orchestratorApi } from '../api/client';

interface ArtifactCardProps {
  artifact: ArtifactMeta;
  className?: string;
}

export const ArtifactCard: React.FC<ArtifactCardProps> = ({ artifact, className = '' }) => {
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const getCategoryConfig = (category: string) => {
    switch (category) {
      case 'plans':
        return { icon: FileText, color: 'text-indigo-400 bg-indigo-950/60 border-indigo-500/30' };
      case 'specifications':
        return { icon: FileSpreadsheet, color: 'text-cyan-400 bg-cyan-950/60 border-cyan-500/30' };
      case 'patches':
        return { icon: GitPullRequest, color: 'text-emerald-400 bg-emerald-950/60 border-emerald-500/30' };
      case 'test_results':
        return { icon: CheckSquare, color: 'text-amber-400 bg-amber-950/60 border-amber-500/30' };
      case 'reports':
        return { icon: FileCode, color: 'text-purple-400 bg-purple-950/60 border-purple-500/30' };
      case 'logs':
      default:
        return { icon: Terminal, color: 'text-slate-400 bg-slate-900/60 border-slate-700' };
    }
  };

  const config = getCategoryConfig(artifact.category);
  const Icon = config.icon;

  const handleOpenPreview = async () => {
    setIsPreviewOpen(true);
    if (!previewContent) {
      setLoading(true);
      try {
        const data = await orchestratorApi.getArtifactContent(artifact.artifact_id);
        setPreviewContent(data.content);
      } catch {
        setPreviewContent('Failed to load artifact content.');
      } finally {
        setLoading(false);
      }
    }
  };

  const handleCopy = () => {
    if (previewContent) {
      navigator.clipboard.writeText(previewContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${bytes} B`;
  };

  return (
    <>
      <div className={`flex flex-col justify-between p-4 bg-bg-panel border border-border-subtle rounded-xl hover:border-border-active transition-all shadow-md group ${className}`}>
        <div>
          {/* Header */}
          <div className="flex items-center justify-between mb-3">
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-mono font-medium border ${config.color}`}
            >
              <Icon className="w-3.5 h-3.5" />
              {artifact.category.toUpperCase()}
            </span>
            <span className="text-[11px] font-mono text-content-muted">
              {formatSize(artifact.size_bytes)}
            </span>
          </div>

          {/* Name & Path */}
          <h4 className="text-sm font-semibold text-content-primary mb-1 truncate group-hover:text-accent-primary transition-colors">
            {artifact.name}
          </h4>
          <p className="text-xs font-mono text-content-secondary truncate mb-3">
            {artifact.path}
          </p>
        </div>

        {/* Footer Actions */}
        <div className="flex items-center justify-between pt-3 border-t border-border-subtle text-xs">
          <span className="text-[10px] font-mono text-content-muted">
            {new Date(artifact.created_at).toLocaleDateString()}
          </span>

          <div className="flex items-center gap-2">
            <button
              onClick={handleOpenPreview}
              className="flex items-center gap-1 px-2.5 py-1 rounded bg-bg-base hover:bg-bg-elevated text-content-secondary hover:text-content-primary transition-colors font-mono"
            >
              <Eye className="w-3.5 h-3.5" />
              Preview
            </button>
            <a
              href={`/api/artifacts/${artifact.artifact_id}/download`}
              download
              className="p-1 rounded bg-bg-base hover:bg-bg-elevated text-content-secondary hover:text-content-primary transition-colors"
              title="Download Artifact"
            >
              <Download className="w-3.5 h-3.5" />
            </a>
          </div>
        </div>
      </div>

      {/* Preview Modal */}
      {isPreviewOpen && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-bg-elevated border border-border-subtle rounded-2xl w-full max-w-3xl flex flex-col max-h-[85vh] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-150">
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-border-subtle bg-bg-panel">
              <div className="flex items-center gap-3">
                <Icon className="w-5 h-5 text-accent-primary" />
                <div>
                  <h3 className="text-sm font-bold text-content-primary">{artifact.name}</h3>
                  <span className="text-xs font-mono text-content-muted">{artifact.path}</span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleCopy}
                  className="p-1.5 rounded-lg bg-bg-base text-content-secondary hover:text-content-primary transition-colors"
                  title="Copy Content"
                >
                  {copied ? <Check className="w-4 h-4 text-accent-success" /> : <Copy className="w-4 h-4" />}
                </button>
                <button
                  onClick={() => setIsPreviewOpen(false)}
                  className="p-1.5 rounded-lg bg-bg-base text-content-secondary hover:text-content-primary transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* Modal Body */}
            <div className="flex-1 overflow-auto p-6 bg-bg-base">
              {loading ? (
                <div className="text-center py-12 text-content-muted font-mono text-sm animate-pulse">
                  Loading artifact preview...
                </div>
              ) : (
                <pre className="text-xs font-mono text-content-primary whitespace-pre-wrap leading-relaxed selection:bg-accent-primary selection:text-white">
                  {previewContent}
                </pre>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
};
