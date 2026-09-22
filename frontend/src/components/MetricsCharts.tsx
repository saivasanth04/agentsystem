import React from 'react';

// 1. Verdict Distribution Donut Chart (SVG)
interface VerdictDonutProps {
  verdicts: { pass: number; fail: number; undecided: number };
  className?: string;
}

export const VerdictDonut: React.FC<VerdictDonutProps> = ({ verdicts, className = '' }) => {
  const total = (verdicts.pass + verdicts.fail + verdicts.undecided) || 1;
  const passPct = (verdicts.pass / total) * 100;
  const failPct = (verdicts.fail / total) * 100;
  const undecidedPct = (verdicts.undecided / total) * 100;

  // SVG circle circumference for r=40 is 2 * PI * 40 = 251.32
  const c = 251.32;
  const passDash = (passPct / 100) * c;
  const failDash = (failPct / 100) * c;
  const undecidedDash = (undecidedPct / 100) * c;

  const passOffset = 0;
  const failOffset = -passDash;
  const undecidedOffset = -(passDash + failDash);

  return (
    <div className={`flex flex-col sm:flex-row items-center justify-between gap-6 p-4 bg-bg-panel border border-border-subtle rounded-xl shadow-md ${className}`}>
      <div className="relative w-32 h-32 flex items-center justify-center">
        <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
          {/* Background circle */}
          <circle cx="50" cy="50" r="40" className="stroke-bg-base" strokeWidth="12" fill="transparent" />
          {/* Pass segment */}
          <circle
            cx="50"
            cy="50"
            r="40"
            className="stroke-accent-success transition-all duration-700"
            strokeWidth="12"
            strokeDasharray={`${passDash} ${c}`}
            strokeDashoffset={passOffset}
            fill="transparent"
          />
          {/* Fail segment */}
          <circle
            cx="50"
            cy="50"
            r="40"
            className="stroke-accent-danger transition-all duration-700"
            strokeWidth="12"
            strokeDasharray={`${failDash} ${c}`}
            strokeDashoffset={failOffset}
            fill="transparent"
          />
          {/* Undecided segment */}
          <circle
            cx="50"
            cy="50"
            r="40"
            className="stroke-accent-warning transition-all duration-700"
            strokeWidth="12"
            strokeDasharray={`${undecidedDash} ${c}`}
            strokeDashoffset={undecidedOffset}
            fill="transparent"
          />
        </svg>
        <div className="absolute flex flex-col items-center justify-center text-center">
          <span className="text-xl font-bold font-mono text-content-primary">
            {Math.round(passPct)}%
          </span>
          <span className="text-[10px] font-mono text-content-muted">PASS RATE</span>
        </div>
      </div>

      <div className="flex flex-col gap-2 font-mono text-xs w-full max-w-[180px]">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-content-secondary">
            <span className="w-2.5 h-2.5 rounded-full bg-accent-success" />
            PASS
          </span>
          <span className="font-bold text-content-primary">{verdicts.pass}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-content-secondary">
            <span className="w-2.5 h-2.5 rounded-full bg-accent-danger" />
            FAIL
          </span>
          <span className="font-bold text-content-primary">{verdicts.fail}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-content-secondary">
            <span className="w-2.5 h-2.5 rounded-full bg-accent-warning" />
            UNDECIDED
          </span>
          <span className="font-bold text-content-primary">{verdicts.undecided}</span>
        </div>
      </div>
    </div>
  );
};

// 2. Cost Timeline Chart (SVG Line Chart)
interface CostTimelineProps {
  data: Array<{ date: string; fast_cost: number; balanced_cost: number; frontier_cost: number; total_tokens: number }>;
  className?: string;
}

export const CostTimeline: React.FC<CostTimelineProps> = ({ data, className = '' }) => {
  if (!data || data.length === 0) {
    return <div className="p-8 text-center text-content-muted font-mono text-xs">No timeline data</div>;
  }

  const maxCost = Math.max(...data.map(d => d.fast_cost + d.balanced_cost + d.frontier_cost), 0.1);
  const width = 500;
  const height = 160;
  const padding = 20;

  const points = data.map((d, i) => {
    const total = d.fast_cost + d.balanced_cost + d.frontier_cost;
    const x = padding + (i / Math.max(1, data.length - 1)) * (width - 2 * padding);
    const y = height - padding - (total / maxCost) * (height - 2 * padding);
    return { x, y, total, date: d.date };
  });

  const pathD = points.length > 1
    ? `M ${points.map(p => `${p.x} ${p.y}`).join(' L ')}`
    : '';

  const areaD = points.length > 1
    ? `${pathD} L ${points[points.length - 1].x} ${height - padding} L ${points[0].x} ${height - padding} Z`
    : '';

  return (
    <div className={`bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-md ${className}`}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-mono font-bold text-content-muted uppercase">
          Cost & Token Consumption Over Time
        </span>
        <span className="text-xs font-mono text-accent-primary font-bold">
          Max: ${maxCost.toFixed(3)}
        </span>
      </div>

      <div className="relative w-full overflow-hidden">
        <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-40">
          <defs>
            <linearGradient id="costGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#6366F1" stopOpacity="0.4" />
              <stop offset="100%" stopColor="#6366F1" stopOpacity="0.0" />
            </linearGradient>
          </defs>

          {/* Grid lines */}
          <line x1={padding} y1={padding} x2={width - padding} y2={padding} stroke="#1F2937" strokeDasharray="3 3" />
          <line x1={padding} y1={height / 2} x2={width - padding} y2={height / 2} stroke="#1F2937" strokeDasharray="3 3" />
          <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} stroke="#374151" />

          {/* Area */}
          {areaD && <path d={areaD} fill="url(#costGrad)" />}

          {/* Line */}
          {pathD && (
            <path d={pathD} fill="none" stroke="#6366F1" strokeWidth="2.5" strokeLinecap="round" />
          )}

          {/* Points */}
          {points.map((p, i) => (
            <circle key={i} cx={p.x} cy={p.y} r="4" className="fill-accent-primary stroke-bg-panel stroke-2 hover:r-6 cursor-pointer" />
          ))}
        </svg>
      </div>

      <div className="flex justify-between mt-2 text-[10px] font-mono text-content-muted">
        <span>{data[0]?.date || ''}</span>
        <span>{data[data.length - 1]?.date || ''}</span>
      </div>
    </div>
  );
};

// 3. Agent Activity Heatmap
interface AgentHeatmapProps {
  data: Array<{ agent: string; hour: number; actions: number }>;
  className?: string;
}

export const AgentHeatmap: React.FC<AgentHeatmapProps> = ({ data, className = '' }) => {
  const agents = ['Planner', 'Specifier', 'Architect', 'Coder', 'Tester', 'Reviewer'];
  const hours = Array.from({ length: 12 }, (_, i) => i * 2);

  const getIntensity = (agent: string, hour: number) => {
    const entry = data.find(d => d.agent.toLowerCase().includes(agent.toLowerCase()) && (d.hour === hour || d.hour === hour + 1));
    const actions = entry?.actions || 0;
    if (actions > 30) return 'bg-accent-primary text-white';
    if (actions > 15) return 'bg-indigo-600/70 text-white';
    if (actions > 5) return 'bg-indigo-900/60 text-indigo-300';
    if (actions > 0) return 'bg-indigo-950/40 text-indigo-400';
    return 'bg-bg-base/60 text-content-muted';
  };

  return (
    <div className={`bg-bg-panel border border-border-subtle rounded-xl p-4 shadow-md overflow-x-auto ${className}`}>
      <span className="text-xs font-mono font-bold text-content-muted uppercase block mb-3">
        Agent Execution Heatmap (Agent × Hour)
      </span>

      <div className="space-y-1.5 min-w-[400px]">
        {agents.map((agent) => (
          <div key={agent} className="flex items-center gap-2">
            <span className="text-xs font-mono text-content-secondary w-20 truncate">{agent}</span>
            <div className="flex-1 grid grid-cols-12 gap-1">
              {hours.map((h) => (
                <div
                  key={h}
                  className={`h-5 rounded flex items-center justify-center text-[9px] font-mono transition-transform hover:scale-110 cursor-pointer ${getIntensity(agent, h)}`}
                  title={`${agent} at ${h}:00`}
                >
                  {h}h
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
