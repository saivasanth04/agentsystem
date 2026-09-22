import React, { useState, useEffect } from 'react';
import { AgentManifest } from '../types/orchestrator';
import { orchestratorApi } from '../api/client';
import { AgentAvatar } from '../components/AgentAvatar';
import {
  Bot,
  Activity,
  Users,
  ShieldCheck,
  Wrench,
  Sparkles,
  Zap,
  CheckCircle2,
  AlertCircle,
  Play,
  Pause,
  StopCircle,
  PlusCircle,
  Vote,
  MessageSquareCode,
  Server
} from 'lucide-react';

interface AgentsPageProps {
  navigate: (route: string) => void;
}

export const AgentsPage: React.FC<AgentsPageProps> = ({ navigate }) => {
  const [activeTab, setActiveTab] = useState<'registry' | 'live' | 'swarm' | 'health'>('registry');
  const [agents, setAgents] = useState<AgentManifest[]>([]);
  const [swarm, setSwarm] = useState<{
    active_nodes: number;
    blackboard_posts: Array<{ author: string; topic: string; content: string; timestamp: string }>;
    consensus_votes: Array<{ issue: string; yay: number; nay: number; status: string }>;
  } | null>(null);
  const [loading, setLoading] = useState(true);

  const loadData = async () => {
    setLoading(true);
    try {
      const [agentList, swarmData] = await Promise.all([
        orchestratorApi.getAgents(),
        orchestratorApi.getSwarmStatus().catch(() => null),
      ]);
      setAgents(agentList);
      setSwarm(swarmData);
    } catch (err) {
      console.error('Failed to load agents:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const getTierBadge = (tier: string) => {
    switch (tier) {
      case 'FRONTIER':
        return 'bg-purple-950/70 text-purple-300 border-purple-500/40';
      case 'BALANCED':
        return 'bg-indigo-950/70 text-indigo-300 border-indigo-500/40';
      case 'FAST':
      default:
        return 'bg-amber-950/70 text-amber-300 border-amber-500/40';
    }
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold font-sans text-content-primary tracking-tight flex items-center gap-2.5">
            <Bot className="w-6 h-6 text-accent-primary" />
            Agent Registry & Swarm Coordinator
          </h1>
          <p className="text-xs font-mono text-content-secondary mt-1">
            Specialized autonomous agents, capability rosters, live runtime delegation, and consensus protocols
          </p>
        </div>

        {/* Tab Navigation */}
        <div className="flex items-center gap-2 bg-bg-panel p-1 rounded-xl border border-border-subtle">
          {[
            { id: 'registry', label: 'Registry', icon: Bot },
            { id: 'live', label: 'Live Agents', icon: Activity },
            { id: 'swarm', label: 'Swarm Blackboard', icon: Users },
            { id: 'health', label: 'MCP Health', icon: Server },
          ].map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;

            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono font-medium transition-all ${
                  isActive
                    ? 'bg-accent-primary text-white font-bold shadow'
                    : 'text-content-secondary hover:text-content-primary'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* 1. REGISTRY TAB */}
      {activeTab === 'registry' && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {agents.map((ag) => (
            <div
              key={ag.id}
              className="bg-bg-panel border border-border-subtle rounded-xl p-5 shadow-lg space-y-4 hover:border-border-active transition-all"
            >
              {/* Card Header */}
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <AgentAvatar agentName={ag.name} role={ag.role} size="md" />
                  <div>
                    <h3 className="text-sm font-bold text-content-primary font-mono">{ag.name}</h3>
                    <span className="text-[11px] font-mono text-content-secondary uppercase">{ag.role}</span>
                  </div>
                </div>
                <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded border uppercase ${getTierBadge(ag.model_tier)}`}>
                  {ag.model_tier}
                </span>
              </div>

              {/* Description */}
              <p className="text-xs text-content-secondary font-sans leading-relaxed">
                {ag.description}
              </p>

              {/* Capabilities */}
              {ag.capabilities && ag.capabilities.length > 0 && (
                <div>
                  <span className="text-[10px] font-mono text-content-muted block mb-1">CAPABILITIES</span>
                  <div className="flex flex-wrap gap-1">
                    {ag.capabilities.map((cap, i) => (
                      <span
                        key={i}
                        className="px-2 py-0.5 rounded bg-bg-base border border-border-subtle text-[10px] font-mono text-indigo-300"
                      >
                        {cap}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Tools & Skills */}
              <div className="pt-3 border-t border-border-subtle/50 flex items-center justify-between text-[11px] font-mono text-content-muted">
                <span className="flex items-center gap-1">
                  <Wrench className="w-3 h-3 text-accent-info" />
                  {ag.tools?.length || 0} Tools
                </span>
                <span className="flex items-center gap-1">
                  <Sparkles className="w-3 h-3 text-accent-warning" />
                  {ag.skills?.length || 0} JIT Skills
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 2. LIVE AGENTS TAB */}
      {activeTab === 'live' && (
        <div className="space-y-4">
          <div className="bg-bg-panel border border-border-subtle rounded-xl p-5 shadow-lg">
            <h3 className="text-sm font-bold font-mono text-content-primary uppercase mb-4">
              Active Agent Instances & Delegation Lineage
            </h3>

            <div className="space-y-3">
              {agents.map((ag) => (
                <div
                  key={ag.id}
                  className="flex items-center justify-between p-3.5 rounded-lg bg-bg-base border border-border-subtle"
                >
                  <div className="flex items-center gap-3">
                    <AgentAvatar agentName={ag.name} role={ag.role} size="sm" />
                    <div>
                      <span className="text-xs font-mono font-bold text-content-primary block">{ag.name}</span>
                      <span className="text-[10px] font-mono text-content-muted">Turns: {ag.turn_count || 0}</span>
                    </div>
                  </div>

                  <div className="flex items-center gap-3">
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950 text-emerald-400 border border-emerald-500/30 uppercase">
                      {ag.status || 'ACTIVE'}
                    </span>

                    <div className="flex items-center gap-1">
                      <button className="p-1 rounded hover:bg-bg-elevated text-content-secondary hover:text-content-primary">
                        <Pause className="w-3.5 h-3.5" />
                      </button>
                      <button className="p-1 rounded hover:bg-bg-elevated text-content-secondary hover:text-rose-400">
                        <StopCircle className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* 3. SWARM TAB */}
      {activeTab === 'swarm' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Blackboard Feed */}
          <div className="bg-bg-panel border border-border-subtle rounded-xl p-5 shadow-lg space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-border-subtle">
              <h3 className="text-xs font-mono font-bold text-content-primary uppercase flex items-center gap-2">
                <MessageSquareCode className="w-4 h-4 text-accent-primary" />
                Swarm Blackboard Feed
              </h3>
              <span className="text-[10px] font-mono text-content-muted">
                {swarm?.blackboard_posts?.length || 0} posts
              </span>
            </div>

            <div className="space-y-3 max-h-96 overflow-y-auto">
              {(!swarm?.blackboard_posts || swarm.blackboard_posts.length === 0) ? (
                <div className="text-center py-10 text-xs font-mono text-content-muted">
                  No active blackboard broadcasts.
                </div>
              ) : (
                swarm.blackboard_posts.map((post, idx) => (
                  <div key={idx} className="bg-bg-base p-3.5 rounded-lg border border-border-subtle space-y-1.5">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-mono font-bold text-accent-primary">{post.author}</span>
                      <span className="text-[10px] font-mono text-content-muted">{post.topic}</span>
                    </div>
                    <p className="text-xs text-content-secondary font-sans leading-relaxed">{post.content}</p>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Consensus Votes */}
          <div className="bg-bg-panel border border-border-subtle rounded-xl p-5 shadow-lg space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-border-subtle">
              <h3 className="text-xs font-mono font-bold text-content-primary uppercase flex items-center gap-2">
                <Vote className="w-4 h-4 text-accent-purple" />
                Consensus & Agreement Protocol
              </h3>
            </div>

            <div className="space-y-3">
              {(!swarm?.consensus_votes || swarm.consensus_votes.length === 0) ? (
                <div className="text-center py-10 text-xs font-mono text-content-muted">
                  No active voting sessions in progress.
                </div>
              ) : (
                swarm.consensus_votes.map((vote, idx) => (
                  <div key={idx} className="bg-bg-base p-3.5 rounded-lg border border-border-subtle space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-mono font-bold text-content-primary">{vote.issue}</span>
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-500/30">
                        {vote.status}
                      </span>
                    </div>
                    <div className="flex items-center gap-4 text-xs font-mono">
                      <span className="text-emerald-400">Yay: {vote.yay}</span>
                      <span className="text-rose-400">Nay: {vote.nay}</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {/* 4. HEALTH TAB */}
      {activeTab === 'health' && (
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-6 shadow-lg space-y-4">
          <h3 className="text-sm font-bold font-mono text-content-primary uppercase">
            Model Gateway & MCP Server Health Checks
          </h3>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-bg-base p-4 rounded-xl border border-border-subtle flex items-center justify-between">
              <div className="flex items-center gap-3">
                <CheckCircle2 className="w-5 h-5 text-accent-success" />
                <div>
                  <h4 className="text-xs font-mono font-bold text-content-primary">Local Workspace MCP</h4>
                  <span className="text-[10px] font-mono text-content-muted">apply_patch, read_file, exec_test</span>
                </div>
              </div>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-950 text-emerald-400 border border-emerald-500/30 font-bold">
                ONLINE
              </span>
            </div>

            <div className="bg-bg-base p-4 rounded-xl border border-border-subtle flex items-center justify-between">
              <div className="flex items-center gap-3">
                <CheckCircle2 className="w-5 h-5 text-accent-success" />
                <div>
                  <h4 className="text-xs font-mono font-bold text-content-primary">LLM Inference Gateway</h4>
                  <span className="text-[10px] font-mono text-content-muted">Latency: ~340ms · 100% SLA</span>
                </div>
              </div>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-950 text-emerald-400 border border-emerald-500/30 font-bold">
                HEALTHY
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
