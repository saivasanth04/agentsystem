import React, { useState, useEffect } from 'react';
import { MemoryItem } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import { JsonViewer } from '../components/JsonViewer';
import {
  Database,
  Search,
  Tag,
  BookOpen,
  Brain,
  History,
  FileCode,
  Sparkles,
  RefreshCw
} from 'lucide-react';

interface MemoryPageProps {
  navigate: (route: string) => void;
}

export const MemoryPage: React.FC<MemoryPageProps> = ({ navigate }) => {
  const [activeScope, setActiveScope] = useState<string>('working');
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(true);

  const scopes = [
    { id: 'working', label: 'Working Memory', desc: 'Short-term scratchpad across graph nodes', icon: Brain },
    { id: 'task', label: 'Task Memory', desc: 'Task-specific outputs and intermediate states', icon: FileCode },
    { id: 'episodic', label: 'Episodic Experiences', desc: 'Historical task runs and learned patterns', icon: History },
    { id: 'conventions', label: 'Project Conventions', desc: 'Coding style, architecture constraints', icon: BookOpen },
    { id: 'semantic', label: 'Semantic Knowledge', desc: 'Embeddings and conceptual codebase graph', icon: Sparkles },
  ];

  const loadMemory = async () => {
    setLoading(true);
    try {
      const data = await orchestratorApi.getMemory(activeScope, searchQuery);
      setMemories(data);
    } catch (err) {
      console.error('Failed to load memory:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMemory();
  }, [activeScope, searchQuery]);

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold font-sans text-content-primary tracking-tight flex items-center gap-2.5">
            <Database className="w-6 h-6 text-accent-primary" />
            Codebase & Agent Memory Browser
          </h1>
          <p className="text-xs font-mono text-content-secondary mt-1">
            Hierarchical context storage, scratchpad variables, episodic learnings, and project invariants
          </p>
        </div>

        <button
          onClick={loadMemory}
          className="p-2 rounded-xl bg-bg-panel hover:bg-bg-elevated border border-border-subtle text-content-secondary hover:text-content-primary transition-colors"
          title="Refresh Memory"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Scope Selector Tabs */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
        {scopes.map((s) => {
          const Icon = s.icon;
          const isSelected = activeScope === s.id;

          return (
            <button
              key={s.id}
              onClick={() => setActiveScope(s.id)}
              className={`p-3.5 rounded-xl border text-left transition-all ${
                isSelected
                  ? 'bg-bg-panel border-accent-primary shadow-lg ring-1 ring-accent-primary'
                  : 'bg-bg-panel/60 border-border-subtle hover:bg-bg-elevated'
              }`}
            >
              <div className="flex items-center gap-2 mb-1">
                <Icon className={`w-4 h-4 ${isSelected ? 'text-accent-primary' : 'text-content-muted'}`} />
                <span className="text-xs font-mono font-bold text-content-primary">{s.label}</span>
              </div>
              <p className="text-[10px] text-content-secondary line-clamp-1">{s.desc}</p>
            </button>
          );
        })}
      </div>

      {/* Search Filter */}
      <div className="relative max-w-md">
        <Search className="w-4 h-4 absolute left-3 top-2.5 text-content-muted" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Filter memory keys or values..."
          className="w-full bg-bg-panel border border-border-subtle rounded-xl pl-9 pr-4 py-2 text-xs font-mono text-content-primary placeholder-content-muted focus:outline-none focus:border-accent-primary"
        />
      </div>

      {/* Memory Items List */}
      <div className="space-y-4">
        {memories.length === 0 ? (
          <div className="p-16 text-center bg-bg-panel border border-border-subtle rounded-2xl space-y-2">
            <Database className="w-12 h-12 text-content-muted mx-auto" />
            <h3 className="text-sm font-bold text-content-primary">No Memory Records Found</h3>
            <p className="text-xs font-mono text-content-secondary">
              Variables and episodic insights stored under <span className="text-accent-primary font-bold">{activeScope}</span> will appear here.
            </p>
          </div>
        ) : (
          memories.map((item) => (
            <div
              key={item.id}
              className="bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-md space-y-3"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono font-bold text-accent-primary">{item.key}</span>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-bg-base border border-border-subtle text-content-muted uppercase">
                    {item.scope}
                  </span>
                </div>
                <span className="text-[10px] font-mono text-content-muted">
                  Updated: {new Date(item.updated_at).toLocaleString()}
                </span>
              </div>

              {/* Tags */}
              {item.tags && item.tags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {item.tags.map((tag, i) => (
                    <span
                      key={i}
                      className="px-2 py-0.5 rounded bg-bg-base border border-border-subtle text-[10px] font-mono text-content-secondary flex items-center gap-1"
                    >
                      <Tag className="w-2.5 h-2.5 text-accent-info" />
                      {tag}
                    </span>
                  ))}
                </div>
              )}

              {/* Value JSON view */}
              <JsonViewer data={item.value} title="Memory Value" initialExpanded={true} />
            </div>
          ))
        )}
      </div>
    </div>
  );
};
