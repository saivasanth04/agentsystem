/**
 * Button.jsx
 * Reusable styled button component for the calculator.
 * Accepts label, variant (number/operator/function/equals), onClick handler, and disabled state.
 */

import React from "react";
import "./calculator.css";

/**
 * Props interface for Button component
 * @typedef {Object} ButtonProps
 * @property {string} label - The text displayed on the button
 * @property {string} variant - Visual style variant: 'number', 'operator', 'function', 'equals'
 * @property {boolean} disabled - Whether the button is disabled
 * @property {Function} onClick - Click handler
 */

const Button = ({ label, variant = "number", disabled = false, onClick }) => {
  // Determine button class based on variant
  let buttonClass = "btn";
  let textClass = "";

  switch (variant) {
    case "number":
      buttonClass += " btn-number";
      textClass = "btn-text-number";
      break;
    case "operator":
      buttonClass += " btn-operator";
      textClass = "btn-text-operator";
      break;
    case "function":
      buttonClass += " btn-function";
      textClass = "btn-text-function";
      break;
    case "equals":
      buttonClass += " btn-equals";
      textClass = "btn-text-equals";
      break;
    default:
      buttonClass += " btn-number";
  }

  return (
    <button
      className={buttonClass}
      onClick={onClick}
      disabled={disabled}
      aria-label={`Calculator ${label} button`}
      title={label}
    >
      <span className={textClass}>{label}</span>
    </button>
  );
};

export default Button;