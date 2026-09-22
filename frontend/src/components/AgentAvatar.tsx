import React from 'react';

interface AgentAvatarProps {
  agentName?: string;
  role?: string;
  size?: 'sm' | 'md' | 'lg';
  showRoleLabel?: boolean;
  className?: string;
}

export const AgentAvatar: React.FC<AgentAvatarProps> = ({
  agentName = 'Agent',
  role = 'Agent',
  size = 'md',
  showRoleLabel = false,
  className = '',
}) => {
  const norm = (role || agentName || 'AG').toUpperCase();

  let initials = 'AG';
  let gradient = 'from-indigo-600 to-purple-600 text-white';
  let border = 'border-indigo-500/30';

  if (norm.includes('PLAN') || norm.includes('PL')) {
    initials = 'PL';
    gradient = 'from-blue-600 to-indigo-700 text-blue-100';
    border = 'border-blue-400/40';
  } else if (norm.includes('SPEC')) {
    initials = 'SP';
    gradient = 'from-cyan-600 to-teal-700 text-cyan-100';
    border = 'border-cyan-400/40';
  } else if (norm.includes('ARCH')) {
    initials = 'AR';
    gradient = 'from-violet-600 to-purple-800 text-violet-100';
    border = 'border-violet-400/40';
  } else if (norm.includes('CODE') || norm.includes('DEV') || norm.includes('IMP')) {
    initials = 'CD';
    gradient = 'from-emerald-600 to-green-700 text-emerald-100';
    border = 'border-emerald-400/40';
  } else if (norm.includes('TEST') || norm.includes('QA')) {
    initials = 'TS';
    gradient = 'from-amber-600 to-yellow-700 text-amber-100';
    border = 'border-amber-400/40';
  } else if (norm.includes('REV') || norm.includes('JUDGE')) {
    initials = 'RV';
    gradient = 'from-rose-600 to-red-700 text-rose-100';
    border = 'border-rose-400/40';
  } else if (norm.includes('SWARM')) {
    initials = 'SW';
    gradient = 'from-fuchsia-600 to-pink-700 text-fuchsia-100';
    border = 'border-fuchsia-400/40';
  } else if (norm.includes('SYSTEM') || norm.includes('ORCH')) {
    initials = 'SYS';
    gradient = 'from-slate-700 to-gray-800 text-gray-200';
    border = 'border-gray-500/40';
  } else {
    initials = norm.slice(0, 2);
  }

  const sizeClasses = {
    sm: 'w-6 h-6 text-[10px]',
    md: 'w-8 h-8 text-xs',
    lg: 'w-10 h-10 text-sm font-bold',
  }[size];

  return (
    <div className={`inline-flex items-center gap-2 ${className}`}>
      <div
        className={`flex items-center justify-center rounded-lg bg-gradient-to-br font-mono font-semibold shadow-md border ${gradient} ${border} ${sizeClasses} transition-transform hover:scale-105 select-none`}
        title={`${agentName} (${role})`}
      >
        {initials}
      </div>
      {showRoleLabel && (
        <div className="flex flex-col">
          <span className="text-xs font-medium text-content-primary leading-tight">{agentName}</span>
          <span className="text-[10px] font-mono text-content-secondary uppercase">{role}</span>
        </div>
      )}
    </div>
  );
};
