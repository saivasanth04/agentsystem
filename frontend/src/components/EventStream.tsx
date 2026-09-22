import React, { useState, useEffect, useRef } from 'react';
import { EventMessage } from '../types/orchestrator';
import {
  Terminal,
  Pause,
  Play,
  Trash2,
  Copy,
  Check,
  Search,
  AlertCircle,
  Info,
  AlertTriangle,
  Flame,
  ArrowDown
} from 'lucide-react';

interface EventStreamProps {
  events: EventMessage[];
  onClear?: () => void;
  className?: string;
  maxEvents?: number;
}

export const EventStream: React.FC<EventStreamProps> = ({
  events,
  onClear,
  className = '',
  maxEvents = 300,
}) => {
  const [isPaused, setIsPaused] = useState(false);
  const [filter, setFilter] = useState('');
  const [levelFilter, setLevelFilter] = useState<string>('ALL');
  const [copied, setCopied] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isPaused && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events, isPaused]);

  const filteredEvents = events.filter((ev) => {
    const text = `${ev.event_type} ${JSON.stringify(ev.data || ev.payload || '')}`.toLowerCase();
    const matchesQuery = filter === '' || text.includes(filter.toLowerCase());
    const matchesLevel = levelFilter === 'ALL' || ev.level === levelFilter;
    return matchesQuery && matchesLevel;
  });

  const handleCopy = () => {
    const jsonStr = JSON.stringify(filteredEvents, null, 2);
    navigator.clipboard.writeText(jsonStr);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const getEventBadge = (type: string, level?: string) => {
    const norm = (type || '').toUpperCase();
    if (norm.includes('ERROR') || norm.includes('FAIL') || level === 'ERROR' || level === 'CRITICAL') {
      return { bg: 'bg-rose-950/80 text-rose-300 border-rose-500/40', icon: AlertCircle };
    }
    if (norm.includes('WARN') || norm.includes('REPLAN') || norm.includes('BUDGET') || level === 'WARNING') {
      return { bg: 'bg-amber-950/80 text-amber-300 border-amber-500/40', icon: AlertTriangle };
    }
    if (norm.includes('COMPLETE') || norm.includes('PASS') || norm.includes('SUCCESS')) {
      return { bg: 'bg-emerald-950/80 text-emerald-300 border-emerald-500/40', icon: Check };
    }
    return { bg: 'bg-blue-950/80 text-blue-300 border-blue-500/40', icon: Info };
  };

  return (
    <div className={`flex flex-col bg-bg-panel border border-border-subtle rounded-xl overflow-hidden shadow-xl ${className}`}>
      {/* Stream Toolbar */}
      <div className="flex flex-wrap items-center justify-between px-4 py-2.5 bg-bg-elevated border-b border-border-subtle gap-3">
        <div className="flex items-center gap-2">
          <Terminal className="w-4 h-4 text-accent-primary" />
          <span className="text-xs font-mono font-bold text-content-primary uppercase">
            Live Event Stream
          </span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-bg-base text-content-muted">
            {filteredEvents.length} events
          </span>
        </div>

        {/* Search & Filters */}
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-2 text-content-muted" />
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter events..."
              className="bg-bg-base border border-border-subtle rounded-lg pl-8 pr-3 py-1 text-xs text-content-primary placeholder-content-muted focus:outline-none focus:border-accent-primary w-36 md:w-48 font-mono"
            />
          </div>

          <select
            value={levelFilter}
            onChange={(e) => setLevelFilter(e.target.value)}
            className="bg-bg-base border border-border-subtle rounded-lg px-2 py-1 text-xs text-content-primary font-mono focus:outline-none focus:border-accent-primary"
          >
            <option value="ALL">All Levels</option>
            <option value="INFO">Info</option>
            <option value="WARNING">Warning</option>
            <option value="ERROR">Error</option>
          </select>

          {/* Action Buttons */}
          <div className="flex items-center gap-1 bg-bg-base p-1 rounded-lg border border-border-subtle">
            <button
              onClick={() => setIsPaused(!isPaused)}
              className={`p-1 rounded text-xs transition-colors ${
                isPaused ? 'bg-amber-600 text-white' : 'text-content-secondary hover:text-content-primary'
              }`}
              title={isPaused ? 'Resume Auto-Scroll' : 'Pause Auto-Scroll'}
            >
              {isPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
            </button>

            <button
              onClick={handleCopy}
              className="p-1 rounded text-content-secondary hover:text-content-primary"
              title="Copy All Events"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-accent-success" /> : <Copy className="w-3.5 h-3.5" />}
            </button>

            {onClear && (
              <button
                onClick={onClear}
                className="p-1 rounded text-content-secondary hover:text-rose-400"
                title="Clear Log Stream"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Stream Messages List */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-3 space-y-1.5 font-mono text-xs max-h-[380px] bg-bg-base/80"
      >
        {filteredEvents.length === 0 ? (
          <div className="text-center py-10 text-content-muted text-xs">
            Waiting for live events from the Orchestrator Event Bus...
          </div>
        ) : (
          filteredEvents.map((ev, idx) => {
            const badge = getEventBadge(ev.event_type, ev.level);
            const Icon = badge.icon;
            const payload = ev.data || ev.payload;

            return (
              <div
                key={idx}
                className="flex items-start gap-2.5 p-2 rounded bg-bg-panel/50 hover:bg-bg-elevated/60 transition-colors border border-border-subtle/40"
              >
                <span className="text-[10px] text-content-muted shrink-0 mt-0.5 select-none">
                  {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : '00:00:00'}
                </span>

                <span
                  className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-bold uppercase shrink-0 ${badge.bg}`}
                >
                  <Icon className="w-3 h-3" />
                  {ev.event_type}
                </span>

                <div className="flex-1 text-content-secondary break-words leading-relaxed overflow-x-auto">
                  {typeof payload === 'string' ? (
                    payload
                  ) : (
                    <span className="text-content-primary">
                      {JSON.stringify(payload)}
                    </span>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Auto-scroll banner if paused */}
      {isPaused && (
        <div
          onClick={() => setIsPaused(false)}
          className="bg-amber-950/80 border-t border-amber-500/40 text-amber-300 px-3 py-1.5 text-xs font-mono text-center cursor-pointer flex items-center justify-center gap-1.5 hover:bg-amber-900/80"
        >
          <ArrowDown className="w-3.5 h-3.5" />
          <span>Stream paused. Click to resume auto-scroll.</span>
        </div>
      )}
    </div>
  );
};
