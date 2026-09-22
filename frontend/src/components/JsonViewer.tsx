import React, { useState } from 'react';
import { Copy, Check, ChevronRight, ChevronDown } from 'lucide-react';

interface JsonViewerProps {
  data: any;
  title?: string;
  className?: string;
  initialExpanded?: boolean;
}

export const JsonViewer: React.FC<JsonViewerProps> = ({
  data,
  title,
  className = '',
  initialExpanded = true,
}) => {
  const [copied, setCopied] = useState(false);
  const [isExpanded, setIsExpanded] = useState(initialExpanded);

  const jsonString = JSON.stringify(data, null, 2);

  const handleCopy = () => {
    navigator.clipboard.writeText(jsonString);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={`bg-bg-panel border border-border-subtle rounded-xl overflow-hidden font-mono text-xs ${className}`}>
      <div className="flex items-center justify-between px-3 py-2 bg-bg-elevated border-b border-border-subtle">
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center gap-1.5 font-bold text-content-primary hover:text-accent-primary transition-colors text-xs"
        >
          {isExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          <span>{title || 'JSON DATA'}</span>
        </button>
        <button
          onClick={handleCopy}
          className="p-1 rounded hover:bg-bg-panel text-content-secondary hover:text-content-primary transition-colors"
          title="Copy JSON"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-accent-success" /> : <Copy className="w-3.5 h-3.5" />}
        </button>
      </div>

      {isExpanded && (
        <div className="p-3 bg-bg-base/80 overflow-x-auto max-h-96">
          <pre className="text-content-secondary text-[11px] leading-relaxed">
            {jsonString}
          </pre>
        </div>
      )}
    </div>
  );
};
