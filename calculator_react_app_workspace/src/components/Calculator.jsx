/**
 * Calculator.jsx
 * Main orchestrator component for the calculator.
 * Manages state, wires engine to UI, handles keyboard events, and persists history.
 */

import React, { useState, useEffect } from "react";
import Display from "./Display";
import Keypad from "./Keypad";
import HistoryLog from "./HistoryLog";
import calculatorEngine from "../calculatorEngine";

/**
 * Calculator component
 * @returns {JSX.Element} The rendered calculator
 */
const Calculator = () => {
  // State for current expression and result
  const [expression, setExpression] = useState("");
  const [result, setResult] = useState("");
  const [history, setHistory] = useState([]);

  // Load history from localStorage on mount
  useEffect(() => {
    const savedHistory = localStorage.getItem("calculatorHistory");
    if (savedHistory) {
      setHistory(JSON.parse(savedHistory));
    }
  }, []);

  // Save history to localStorage when it changes
  useEffect(() => {
    localStorage.setItem("calculatorHistory", JSON.stringify(history));
  }, [history]);

  // Handle button clicks
  const handleButtonClick = (label) => {
    let newExpression = expression;
    let newResult = result;

    // Handle special functions
    switch (label) {
      case "C":
        // Clear last character
        newExpression = newExpression.slice(0, -1);
        newResult = "";
        break;
      case "AC":
        // All clear
        newExpression = "";
        newResult = "";
        break;
      case "=":
        // Calculate result
        newResult = calculatorEngine.calculate(newExpression);
        if (newResult !== "Error") {
          // Add to history
          const newEntry = `${new Date().toLocaleTimeString()}: ${newExpression} = ${newResult}`;
          setHistory([...history, newEntry]);
        }
        break;
      default:
        // Append to expression
        newExpression += label;
        newResult = "";
    }

    setExpression(newExpression);
    setResult(newResult);
  };

  // Handle keyboard events
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Prevent default behavior for calculator keys
      if (
        /[0-9%/*\-+\.=]|Enter|Escape|Backspace/.test(e.key)
      ) {
        e.preventDefault();

        // Map keyboard keys to button labels
        const keyMap = {
          Enter: "=",
          Escape: "AC",
          Backspace: "C",
          "*": "*",
          "/": "/",
          "-": "-",
          "+": "+",
          "%": "%",
          ".": "."
        };

        const label = keyMap[e.key] || e.key;
        handleButtonClick(label);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [expression, result, history]);

  // Handle history restore
  const handleRestore = (expr, res) => {
    setExpression(expr);
    setResult(res);
  };

  // Handle history clear
  const handleClearHistory = () => {
    setHistory([]);
  };

  return (
    <div className="calculator-container">
      <div className="calculator-main">
        <Display expression={expression} result={result} />
        <Keypad onButtonClick={handleButtonClick} />
      </div>
      <HistoryLog
        history={history}
        onRestore={handleRestore}
        onClear={handleClearHistory}
      />
    </div>
  );
};

export default Calculator;