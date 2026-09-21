/**
 * Keypad.jsx
 * Grid layout for all calculator buttons.
 * Renders buttons dynamically based on configuration.
 */

import React from "react";
import Button from "./Button";

/**
 * Button configuration for the calculator keypad.
 * Each button has a label, variant, and optional keyboard key.
 */
const buttonRows = [
  // Row 1: Clear buttons
  [
    { label: "C", variant: "function", key: "Escape" },
    { label: "AC", variant: "function", key: "Escape" },
    { label: "%", variant: "operator", key: "%" },
    { label: "/", variant: "operator", key: "/" }
  ],
  // Row 2: Numbers and multiply
  [
    { label: "7", variant: "number", key: "7" },
    { label: "8", variant: "number", key: "8" },
    { label: "9", variant: "number", key: "9" },
    { label: "*", variant: "operator", key: "*" }
  ],
  // Row 3: Numbers and subtract
  [
    { label: "4", variant: "number", key: "4" },
    { label: "5", variant: "number", key: "5" },
    { label: "6", variant: "number", key: "6" },
    { label: "-", variant: "operator", key: "-" }
  ],
  // Row 4: Numbers and add
  [
    { label: "1", variant: "number", key: "1" },
    { label: "2", variant: "number", key: "2" },
    { label: "3", variant: "number", key: "3" },
    { label: "+", variant: "operator", key: "+" }
  ],
  // Row 5: Numbers, decimal, and equals
  [
    { label: "0", variant: "number", key: "0" },
    { label: ".", variant: "number", key: "." },
    { label: "⌫", variant: "function", key: "Backspace" },
    { label: "=", variant: "equals", key: "Enter" }
  ]
];

/**
 * Props interface for Keypad component
 * @typedef {Object} KeypadProps
 * @property {Function} onButtonClick - Handler for button clicks, receives button label
 */

const Keypad = ({ onButtonClick }) => {
  return (
    <div className="keypad" role="presentation">
      {buttonRows.map((row, rowIndex) => (
        <div
          className="keypad-row"
          key={`row-${rowIndex}`}
          role="presentation"
        >
          {row.map((button) => (
            <Button
              key={`${button.label}-${rowIndex}`}
              label={button.label}
              variant={button.variant}
              onClick={() => onButtonClick(button.label)}
            />
          ))}
        </div>
      ))}
    </div>
  );
};

export default Keypad;