import React, { useState, useEffect } from 'react';
import { ArtifactMeta } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import { ArtifactCard } from '../components/ArtifactCard';
import {
  Boxes,
  Search,
  RefreshCw,
  FileText,
  FileSpreadsheet,
  GitPullRequest,
  CheckSquare,
  FileCode,
  Terminal
} from 'lucide-react';

interface ArtifactsPageProps {
  navigate: (route: string) => void;
}

export const ArtifactsPage: React.FC<ArtifactsPageProps> = () => {
  const [artifacts, setArtifacts] = useState<ArtifactMeta[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string>('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(true);

  const categories = [
    { id: 'ALL', label: 'All Artifacts', icon: Boxes },
    { id: 'plans', label: 'Plans', icon: FileText },
    { id: 'specifications', label: 'Specs', icon: FileSpreadsheet },
    { id: 'patches', label: 'Patches & Diffs', icon: GitPullRequest },
    { id: 'test_results', label: 'Test Results', icon: CheckSquare },
    { id: 'reports', label: 'Reports', icon: FileCode },
    { id: 'logs', label: 'Logs & Traces', icon: Terminal },
  ];

  const loadArtifacts = async () => {
    setLoading(true);
    try {
      const data = await orchestratorApi.getArtifacts({
        category: selectedCategory !== 'ALL' ? selectedCategory : undefined,
      });
      setArtifacts(data.artifacts || []);
    } catch (err) {
      console.error('Failed to load artifacts:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadArtifacts();
  }, [selectedCategory]);

  const filteredArtifacts = artifacts.filter((art) => {
    const text = `${art.name} ${art.path} ${art.session_id}`.toLowerCase();
    return searchQuery === '' || text.includes(searchQuery.toLowerCase());
  });

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold font-sans text-slate-100 tracking-tight flex items-center gap-2.5">
            <Boxes className="w-6 h-6 text-cyan-400" />
            Artifacts & Storage Browser
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-1">
            Immutable workspace artifacts, change specifications, patches, and execution logs
          </p>
        </div>

        <button
          onClick={loadArtifacts}
          className="p-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-slate-100 transition-colors"
          title="Refresh Artifacts"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-cyan-400' : ''}`} />
        </button>
      </div>

      {/* Category Tabs & Search Bar */}
      <div className="space-y-4">
        {/* Category Pills */}
        <div className="flex items-center gap-2 overflow-x-auto pb-1">
          {categories.map((cat) => {
            const Icon = cat.icon;
            const isSelected = selectedCategory === cat.id;

            return (
              <button
                key={cat.id}
                onClick={() => setSelectedCategory(cat.id)}
                className={`flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs font-mono transition-all shrink-0 ${
                  isSelected
                    ? 'bg-gradient-to-r from-cyan-600 to-blue-600 text-white font-bold shadow-md shadow-cyan-500/20'
                    : 'bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-slate-200'
                }`}
              >
                <Icon className="w-4 h-4" />
                <span>{cat.label}</span>
              </button>
            );
          })}
        </div>

        {/* Search */}
        <div className="relative max-w-md">
          <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search artifacts by name or path..."
            className="w-full bg-slate-900 border border-slate-800 rounded-xl pl-9 pr-4 py-2 text-xs font-mono text-slate-100 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
          />
        </div>
      </div>

      {/* Artifacts Grid */}
      {filteredArtifacts.length === 0 ? (
        <div className="p-16 text-center bg-slate-900 border border-slate-800 rounded-2xl space-y-2">
          <Boxes className="w-12 h-12 text-slate-600 mx-auto" />
          <h3 className="text-sm font-bold text-slate-100">No Artifacts Found</h3>
          <p className="text-xs font-mono text-slate-400">
            Generated plans, patches, test reports, and logs will be cataloged here.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredArtifacts.map((art) => (
            <ArtifactCard key={art.artifact_id} artifact={art} />
          ))}
        </div>
      )}
    </div>
  );
};
