import React, { useState, useEffect } from 'react';
import { gatewayApi } from '../api/gatewayApi';
import { GatewayModel } from '../types/gateway';
import {
  Cpu,
  Search,
  CheckCircle2,
  XCircle,
  Sparkles,
  Zap,
  Brain,
  Code2,
  Eye,
  Layers,
  Filter,
  RefreshCw,
} from 'lucide-react';

interface GatewayModelsPageProps {
  navigate: (route: string) => void;
}

export const GatewayModelsPage: React.FC<GatewayModelsPageProps> = () => {
  const [models, setModels] = useState<GatewayModel[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCapability, setSelectedCapability] = useState<string>('all');
  const [freeOnly, setFreeOnly] = useState(false);
  const [healthyOnly, setHealthyOnly] = useState(false);

  const fetchModels = async () => {
    try {
      const data = await gatewayApi.getModels({
        capability: selectedCapability === 'all' ? undefined : selectedCapability,
        free_only: freeOnly,
        healthy_only: healthyOnly,
        search: searchQuery || undefined,
      });
      setModels(data);
    } catch (err: any) {
      console.error('Failed to fetch model registry:', err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchModels();
  }, [selectedCapability, freeOnly, healthyOnly, searchQuery]);

  const capabilities = [
    { id: 'all', label: 'All Models' },
    { id: 'chat', label: 'Chat' },
    { id: 'coding', label: 'Coding' },
    { id: 'reasoning', label: 'Reasoning' },
    { id: 'vision', label: 'Vision' },
    { id: 'embedding', label: 'Embedding' },
    { id: 'audio', label: 'Audio' },
    { id: 'image', label: 'Image' },
  ];

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-5 bg-slate-900 border border-slate-800 rounded-2xl shadow-xl">
        <div className="flex items-center gap-3.5">
          <div className="w-11 h-11 rounded-xl bg-gradient-to-tr from-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-500/20">
            <Cpu className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold font-mono text-slate-100">
              Live Model Registry
            </h1>
            <p className="text-xs text-slate-400">
              Persistent Runtime Model Registry • Real-time Health Scores • Free-Model Auto-Detection
            </p>
          </div>
        </div>

        <button
          onClick={fetchModels}
          className="flex items-center gap-2 px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-mono font-medium text-slate-200 border border-slate-700 cursor-pointer"
        >
          <RefreshCw className="w-3.5 h-3.5 text-cyan-400" />
          <span>Refresh Registry</span>
        </button>
      </div>

      {/* Filter and Category Pills */}
      <div className="p-4 bg-slate-900 border border-slate-800 rounded-xl space-y-3">
        <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
          <div className="relative w-full sm:w-80">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search model by name or provider..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs font-mono text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
            />
          </div>

          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs font-mono text-slate-300 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={freeOnly}
                onChange={(e) => setFreeOnly(e.target.checked)}
                className="rounded accent-emerald-500 cursor-pointer"
              />
              <span className="text-emerald-400 font-semibold">Free Tier Only</span>
            </label>

            <label className="flex items-center gap-2 text-xs font-mono text-slate-300 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={healthyOnly}
                onChange={(e) => setHealthyOnly(e.target.checked)}
                className="rounded accent-cyan-500 cursor-pointer"
              />
              <span>Healthy Only (&ge;0.5)</span>
            </label>
          </div>
        </div>

        {/* Capability Pills */}
        <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-slate-800/80">
          <span className="text-[11px] font-mono text-slate-500 mr-1 flex items-center gap-1">
            <Filter className="w-3 h-3" />
            CAPABILITY:
          </span>
          {capabilities.map((c) => (
            <button
              key={c.id}
              onClick={() => setSelectedCapability(c.id)}
              className={`px-3 py-1 rounded-lg text-xs font-mono transition-all cursor-pointer ${
                selectedCapability === c.id
                  ? 'bg-indigo-600 text-white font-semibold shadow-md shadow-indigo-600/30'
                  : 'bg-slate-950 text-slate-400 border border-slate-800 hover:border-slate-700'
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>
      </div>

      {/* Model Registry Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs font-mono">
            <thead>
              <tr className="bg-slate-950/80 border-b border-slate-800 text-slate-400 uppercase text-[11px]">
                <th className="py-3 px-4">Provider</th>
                <th className="py-3 px-4">Model Identifier</th>
                <th className="py-3 px-4">Cost Tier</th>
                <th className="py-3 px-4">Capabilities</th>
                <th className="py-3 px-4">Latency</th>
                <th className="py-3 px-4">Health</th>
                <th className="py-3 px-4">Context Window</th>
                <th className="py-3 px-4 text-right">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {isLoading ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-slate-500">
                    Loading live model registry...
                  </td>
                </tr>
              ) : models.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-slate-500">
                    No models matching filter criteria.
                  </td>
                </tr>
              ) : (
                models.map((m, idx) => (
                  <tr key={idx} className="hover:bg-slate-850/60 transition-colors">
                    <td className="py-3 px-4 text-cyan-300 font-semibold">{m.provider}</td>
                    <td className="py-3 px-4 text-slate-200 font-bold max-w-xs truncate">{m.model}</td>
                    <td className="py-3 px-4">
                      {m.free ? (
                        <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950/30 text-emerald-400 border border-emerald-800/30">
                          ZERO-COST
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-400">
                          STANDARD
                        </span>
                      )}
                    </td>
                    <td className="py-3 px-4">
                      <div className="flex flex-wrap gap-1">
                        {m.capabilities.map((c) => (
                          <span
                            key={c}
                            className="px-1.5 py-0.5 rounded text-[10px] bg-slate-950 text-slate-400 border border-slate-800"
                          >
                            {c}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-3 px-4 text-amber-300">
                      {m.latency > 0 ? `${(m.latency * 1000).toFixed(0)} ms` : '--'}
                    </td>
                    <td className="py-3 px-4">
                      <div className="flex items-center gap-1.5">
                        <div className="w-12 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                          <div
                            className={`h-full ${
                              m.health >= 0.8
                                ? 'bg-emerald-400'
                                : m.health >= 0.5
                                ? 'bg-amber-400'
                                : 'bg-red-400'
                            }`}
                            style={{ width: `${Math.round(m.health * 100)}%` }}
                          />
                        </div>
                        <span className="text-[11px] text-slate-400">
                          {(m.health * 100).toFixed(0)}%
                        </span>
                      </div>
                    </td>
                    <td className="py-3 px-4 text-slate-400">
                      {m.contextWindow ? `${(m.contextWindow / 1000).toFixed(0)}k` : '128k'}
                    </td>
                    <td className="py-3 px-4 text-right">
                      {m.accessible ? (
                        <span className="inline-flex items-center gap-1 text-emerald-400 text-[11px]">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          Ready
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-red-400 text-[11px]">
                          <XCircle className="w-3.5 h-3.5" />
                          Offline
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
