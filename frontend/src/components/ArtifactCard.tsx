import React, { useState } from 'react';
import { ArtifactMeta } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import {
  FileText,
  FileSpreadsheet,
  GitPullRequest,
  CheckSquare,
  FileCode,
  Terminal,
  Download,
  Copy,
  Check,
  Eye,
  X,
  Clock,
  HardDrive
} from 'lucide-react';

interface ArtifactCardProps {
  artifact: ArtifactMeta;
}

export const ArtifactCard: React.FC<ArtifactCardProps> = ({ artifact }) => {
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
        return { icon: GitPullRequest, color: 'text-amber-400 bg-amber-950/60 border-amber-500/30' };
      case 'test_results':
        return { icon: CheckSquare, color: 'text-emerald-400 bg-emerald-950/60 border-emerald-500/30' };
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
        const data = await orchestratorApi.getArtifact(artifact.artifact_id);
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
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-lg hover:border-slate-700 transition-all flex flex-col justify-between space-y-3 group">
        <div>
          {/* Top Bar: Icon + Category Badge */}
          <div className="flex items-center justify-between mb-2.5">
            <div className={`p-2 rounded-xl border ${config.color}`}>
              <Icon className="w-4 h-4" />
            </div>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-950 text-slate-400 border border-slate-800 uppercase font-semibold">
              {artifact.category}
            </span>
          </div>

          {/* Name & Session ID */}
          <h3 className="text-xs font-mono font-bold text-slate-100 truncate" title={artifact.name}>
            {artifact.name}
          </h3>
          <span className="text-[10px] font-mono text-slate-500 block truncate mt-0.5">
            Session: {artifact.session_id}
          </span>
        </div>

        {/* Footer Meta + Actions */}
        <div className="pt-3 border-t border-slate-800 flex items-center justify-between text-[11px] font-mono text-slate-400">
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1">
              <HardDrive className="w-3 h-3 text-slate-500" />
              {formatSize(artifact.size_bytes)}
            </span>
          </div>

          <div className="flex items-center gap-1">
            <button
              onClick={handleOpenPreview}
              className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-cyan-300 transition-colors"
              title="Preview Artifact"
            >
              <Eye className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* Preview Modal */}
      {isPreviewOpen && (
        <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-3xl max-h-[80vh] flex flex-col shadow-2xl overflow-hidden animate-in zoom-in-95 duration-150">
            {/* Modal Header */}
            <div className="px-5 py-3.5 bg-slate-800/80 border-b border-slate-700 flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Icon className="w-4 h-4 text-cyan-400" />
                <h3 className="text-xs font-mono font-bold text-slate-100 truncate max-w-md">
                  {artifact.name}
                </h3>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={handleCopy}
                  className="p-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 hover:text-white transition-colors"
                  title="Copy Content"
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
                <button
                  onClick={() => setIsPreviewOpen(false)}
                  className="p-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-400 hover:text-white transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* Modal Body Content */}
            <div className="p-4 overflow-y-auto flex-1 font-mono text-xs bg-slate-950 text-slate-200">
              {loading ? (
                <div className="py-12 text-center text-slate-500">Loading artifact content...</div>
              ) : (
                <pre className="whitespace-pre-wrap break-words leading-relaxed font-mono">
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
