import React from 'react';
import { TaskStatus, ReviewVerdict } from '../types/orchestrator';

interface StatusBadgeProps {
  status: TaskStatus | ReviewVerdict | string;
  size?: 'sm' | 'md' | 'lg';
  showDot?: boolean;
  className?: string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  size = 'md',
  showDot = true,
  className = '',
}) => {
  const normStatus = (status || '').toUpperCase();

  let bg = 'bg-gray-800/80 text-gray-300 border-gray-700';
  let dotColor = 'bg-gray-400';
  let icon = '⚪';

  switch (normStatus) {
    case 'COMPLETED':
    case 'PASS':
      bg = 'bg-emerald-950/60 text-emerald-400 border-emerald-500/40 shadow-emerald-950/20';
      dotColor = 'bg-emerald-400';
      icon = '🟢';
      break;
    case 'IN_PROGRESS':
    case 'RUNNING':
      bg = 'bg-blue-950/60 text-blue-400 border-blue-500/40 animate-pulse-slow shadow-blue-950/20';
      dotColor = 'bg-blue-400 animate-ping';
      icon = '🔵';
      break;
    case 'READY':
      bg = 'bg-indigo-950/60 text-indigo-400 border-indigo-500/40';
      dotColor = 'bg-indigo-400';
      icon = '🔷';
      break;
    case 'VERIFYING':
    case 'NEED_VERIFICATION':
    case 'UNDECIDED':
      bg = 'bg-amber-950/60 text-amber-400 border-amber-500/40';
      dotColor = 'bg-amber-400';
      icon = '🟡';
      break;
    case 'INCOMPLETE':
      bg = 'bg-orange-950/60 text-orange-400 border-orange-500/40';
      dotColor = 'bg-orange-400';
      icon = '🟠';
      break;
    case 'FAILED':
    case 'FAIL':
      bg = 'bg-rose-950/60 text-rose-400 border-rose-500/40';
      dotColor = 'bg-rose-400';
      icon = '❌';
      break;
    case 'STOPPED':
      bg = 'bg-red-950/60 text-red-400 border-red-500/40';
      dotColor = 'bg-red-500';
      icon = '🔴';
      break;
    case 'NEED_MORE_EVIDENCE':
      bg = 'bg-purple-950/60 text-purple-400 border-purple-500/40';
      dotColor = 'bg-purple-400';
      icon = '🟣';
      break;
    case 'BLOCKED':
      bg = 'bg-neutral-900 text-neutral-400 border-neutral-700';
      dotColor = 'bg-neutral-500';
      icon = '⛔';
      break;
    case 'SKIPPED':
      bg = 'bg-slate-900/60 text-slate-400 border-slate-700';
      dotColor = 'bg-slate-500';
      icon = '⏭️';
      break;
    case 'PENDING':
    default:
      bg = 'bg-gray-900/60 text-gray-400 border-gray-700/60';
      dotColor = 'bg-gray-400';
      icon = '⚪';
      break;
  }

  const sizeClasses = {
    sm: 'text-[10px] px-2 py-0.5 gap-1 font-mono font-medium',
    md: 'text-xs px-2.5 py-1 gap-1.5 font-mono font-semibold',
    lg: 'text-sm px-3 py-1.5 gap-2 font-mono font-bold',
  }[size];

  return (
    <span
      className={`inline-flex items-center rounded-full border shadow-sm tracking-wider uppercase ${bg} ${sizeClasses} ${className}`}
    >
      {showDot && (
        <span className="relative flex h-1.5 w-1.5">
          {normStatus === 'RUNNING' || normStatus === 'IN_PROGRESS' ? (
            <span className={`absolute inline-flex h-full w-full rounded-full opacity-75 ${dotColor}`} />
          ) : null}
          <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${dotColor.replace(' animate-ping', '')}`} />
        </span>
      )}
      <span>{normStatus}</span>
    </span>
  );
};
