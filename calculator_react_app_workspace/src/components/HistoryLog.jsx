/**
 * HistoryLog.jsx
 * Scrollable history log showing past calculations.
 * Allows click-to-restore and clear history functionality.
 */

import React from "react";
import Button from "./Button";

/**
 * Props interface for HistoryLog component
 * @typedef {Object} HistoryLogProps
 * @property {Array<string>} history - Array of history entries
 * @property {Function} onRestore - Handler for restoring a history entry
 * @property {Function} onClear - Handler for clearing history
 */

const HistoryLog = ({ history = [], onRestore, onClear }) => {
  // Format timestamp for display
  const formatTimestamp = (entry) => {
    const match = entry.match(/^(\d+:\d+:\d+):/);
    return match ? match[1] : "";
  };

  // Extract expression and result from history entry
  const parseEntry = (entry) => {
    const match = entry.match(/^(\d+:\d+:\d+):\s*(.+?)\s*=\s*(.+)$/);
    if (match) {
      return {
        timestamp: match[1],
        expression: match[2],
        result: match[3]
      };
    }
    return null;
  };

  return (
    <div className="history-log-container">
      <div className="history-header">
        <h3>History</h3>
        <Button
          label="Clear"
          variant="function"
          onClick={onClear}
          aria-label="Clear history"
        />
      </div>
      <div className="history-list" role="list" aria-label="Calculation history">
        {history.length === 0 ? (
          <div className="history-empty">No history yet</div>
        ) : (
          history.map((entry, index) => {
            const parsed = parseEntry(entry);
            if (!parsed) return null;

            return (
              <div
                key={index}
                className="history-item"
                role="listitem"
                onClick={() => onRestore(parsed.expression, parsed.result)}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onRestore(parsed.expression, parsed.result);
                  }
                }}
                aria-label={`Restore ${parsed.expression} = ${parsed.result}`}
              >
                <div className="history-timestamp">{parsed.timestamp}</div>
                <div className="history-expression">{parsed.expression}</div>
                <div className="history-result">{parsed.result}</div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

export default HistoryLog;