import React, { useState, useEffect } from 'react';
import { MemoryItem } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import { JsonViewer } from '../components/JsonViewer';
import {
  Database,
  Search,
  BookOpen,
  Brain,
  History,
  Sparkles,
  RefreshCw
} from 'lucide-react';

interface MemoryPageProps {
  navigate: (route: string) => void;
}

export const MemoryPage: React.FC<MemoryPageProps> = () => {
  const [activeScope, setActiveScope] = useState<string>('working');
  const [memoryData, setMemoryData] = useState<any>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(true);

  const scopes = [
    { id: 'working', label: 'Working Memory', desc: 'Short-term scratchpad across graph nodes', icon: Brain },
    { id: 'episodic', label: 'Episodic Experiences', desc: 'Historical task runs and learned patterns', icon: History },
    { id: 'conventions', label: 'Project Conventions', desc: 'Coding style, architecture constraints', icon: BookOpen },
    { id: 'semantic', label: 'Semantic Knowledge', desc: 'Embeddings and conceptual codebase graph', icon: Sparkles },
  ];

  const loadMemory = async () => {
    setLoading(true);
    try {
      const data = await orchestratorApi.getMemory();
      setMemoryData(data);
    } catch (err) {
      console.error('Failed to load memory:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMemory();
  }, []);

  const getScopeData = () => {
    if (!memoryData) return null;
    if (activeScope === 'working') return memoryData.working_memory;
    if (activeScope === 'episodic') return memoryData.episodic_experiences;
    if (activeScope === 'conventions') return memoryData.project_conventions;
    if (activeScope === 'semantic') return { nodes_count: memoryData.semantic_knowledge_nodes };
    return memoryData;
  };

  const currentData = getScopeData();

  return (
    <div className="max-w-7xl mx-auto space-y-6 pb-12">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold font-sans text-slate-100 tracking-tight flex items-center gap-2.5">
            <Database className="w-6 h-6 text-cyan-400" />
            Codebase & Agent Memory Browser
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-1">
            Hierarchical context storage, scratchpad variables, episodic learnings, and project invariants
          </p>
        </div>

        <button
          onClick={loadMemory}
          className="p-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-slate-100 transition-colors"
          title="Refresh Memory"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-cyan-400' : ''}`} />
        </button>
      </div>

      {/* Scope Selector Tabs */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {scopes.map((s) => {
          const Icon = s.icon;
          const isSelected = activeScope === s.id;

          return (
            <button
              key={s.id}
              onClick={() => setActiveScope(s.id)}
              className={`p-3.5 rounded-2xl border text-left transition-all ${
                isSelected
                  ? 'bg-slate-900 border-cyan-500/60 shadow-lg ring-1 ring-cyan-500/40 text-slate-100'
                  : 'bg-slate-900/60 border-slate-800 hover:bg-slate-800/80 text-slate-400 hover:text-slate-200'
              }`}
            >
              <div className="flex items-center gap-2 mb-1">
                <Icon className={`w-4 h-4 ${isSelected ? 'text-cyan-400' : 'text-slate-500'}`} />
                <span className="text-xs font-mono font-bold">{s.label}</span>
              </div>
              <p className="text-[10px] text-slate-400 line-clamp-1">{s.desc}</p>
            </button>
          );
        })}
      </div>

      {/* Search Filter */}
      <div className="relative max-w-md">
        <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Filter memory entries..."
          className="w-full bg-slate-900 border border-slate-800 rounded-xl pl-9 pr-4 py-2 text-xs font-mono text-slate-100 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
        />
      </div>

      {/* Memory Items View */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-xl space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <div className="flex items-center space-x-2">
            <span className="text-xs font-mono font-bold text-slate-200 uppercase">{activeScope} payload</span>
          </div>
          <span className="text-[11px] font-mono text-slate-400">Live Memory State</span>
        </div>

        {currentData ? (
          <JsonViewer data={currentData} title={`${activeScope.toUpperCase()} JSON Store`} initialExpanded={true} />
        ) : (
          <div className="py-12 text-center text-xs font-mono text-slate-500">
            No records found for active scope.
          </div>
        )}
      </div>
    </div>
  );
};
