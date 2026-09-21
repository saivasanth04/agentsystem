/**
 * Display.jsx
 * Read-only display component showing the current expression and result.
 * Handles overflow gracefully with truncation.
 */

import React from "react";
import "./calculator.css";

/**
 * Props interface for Display component
 * @typedef {Object} DisplayProps
 * @property {string} expression - The current input expression
 * @property {string} result - The computed result
 */

const Display = ({ expression = "", result = "" }) => {
  // Truncate expression if too long
  const displayExpression =
    expression.length > 20
      ? "..." + expression.slice(-17)
      : expression;

  // Truncate result if too long
  const displayResult =
    result.length > 15
      ? result.slice(0, 14) + "…"
      : result;

  return (
    <div className="display-container">
      <div className="display-expression" aria-label="Current expression">
        {displayExpression || "0"}
      </div>
      <div
        className="display-result"
        aria-label="Result"
        role="status"
        aria-live="polite"
      >
        {displayResult || "0"}
      </div>
    </div>
  );
};

export default Display;