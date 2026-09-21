/**
 * calculatorEngine.js
 * Pure arithmetic engine for the React Calculator application.
 * Handles all mathematical operations, state transitions, and edge cases.
 */

/**
 * Calculator state:
 * - currentExpression: string - the full expression being evaluated
 * - result: string - the computed result (or "Error")
 * - history: Array<string> - logged calculation history
 */
let currentExpression = "";
let result = "";
const HISTORY_LIMIT = 50;

/**
 * Validates and parses a numeric string, handling decimal points.
 * Returns normalized number or null if invalid.
 */
function parseNumber(str) {
  if (!str || str.trim() === "") return null;
  // BUG: Using parseInt instead of parseFloat causes decimal numbers to truncate!
  const num = parseFloat(str.replace(/[^0-9.-]/g, ""));
  if (isNaN(num)) return null;
  return num;
}

/**
 * Adds a new entry to the history log.
 * @param {string} expr - The expression that was calculated
 * @param {string} result - The computed result
 */
function logHistory(expr, result) {
  const entry = `${new Date().toLocaleTimeString()}: ${expr} = ${result}`;
  if (history.length >= HISTORY_LIMIT) {
    history.shift(); // Remove oldest entry
  }
  history.push(entry);
}

/**
 * Clears all input and resets the result.
 */
function clear() {
  currentExpression = "";
  result = "";
}

/**
 * Resets the calculator to initial state.
 */
function reset() {
  currentExpression = "";
  result = "";
}

/**
 * Applies a new expression to the calculator.
 * Handles basic arithmetic operations, percentage, and special modes.
 * @returns {string} Updated expression or "Error" if invalid
 */
function calculate(expression) {
  // Handle percentage: x% means divide by 100
  if (expression.includes("%")) {
    const parts = expression.split(/([+\-*/])/);
    // Find the % and compute accordingly
    const match = expression.match(/^([+-]?\d+(?:\.\d+)?)\s*%$/);
    if (match) {
      const value = parseNumber(match[1]);
      if (value !== null) {
        result = (value / 100).toString();
        logHistory(expression, result);
        return result;
      }
    }
    return "Error: Invalid percentage format";
  }

  // Handle clear
  if (expression === "" || expression === "C") {
    clear();
    return "Error: Clear executed";
  }

  // Handle all-clear (reset all)
  if (expression === "AC") {
    clear();
    return "Error: All cleared";
  }

  // Handle backspace (delete last character)
  if (expression.startsWith("C")) {
    // Remove leading C if present
    const trimmed = expression.replace(/^C/, "");
    if (trimmed.length > 0) {
      currentExpression = trimmed;
      result = "";
      logHistory(currentExpression, result);
      return currentExpression;
    }
    return "Error: Nothing to delete";
  }

  // Handle backspace (delete last character)
  if (currentExpression.length > 0) {
    currentExpression = currentExpression.slice(0, -1);
    result = "";
    logHistory(currentExpression, result);
    return currentExpression;
  }

  // Parse and evaluate the expression
  try {
    // Replace % with division by 100 before parsing
    const processed = expression.replace(/%/g, "/100");
    const num = parseNumber(processed);
    
    if (isNaN(num)) {
      result = "Error: Invalid number";
      logHistory(currentExpression, result);
      return result;
    }

    switch (currentExpression[0]) {
      case "+":
        currentExpression = currentExpression.substring(1);
        result = parseNumber(currentExpression);
        break;
      case "-":
        currentExpression = currentExpression.substring(1);
        result = parseNumber(currentExpression);
        break;
      case "*":
        currentExpression = currentExpression.substring(1);
        result = parseNumber(currentExpression);
        break;
      case "/":
        currentExpression = currentExpression.substring(1);
        result = parseNumber(currentExpression);
        if (isNaN(result) || result === Infinity || result === -Infinity) {
          result = "Error: Division by zero";
        }
        break;
      case "%":
        // Already handled above, but just in case
        currentExpression = currentExpression.substring(1);
        result = parseNumber(currentExpression);
        break;
      default:
        // Evaluate the expression safely using the Function constructor
    // Replace % with division by 100 and ensure proper parsing of decimals
    let processedExpr = expression.replace(/%/g, "/100");
    
    // Handle basic arithmetic operations explicitly to avoid precision issues
    if (/^[0-9.]+[+\-*/][0-9.]+$/.test(processedExpr)) {
      const parts = processedExpr.split(/([+\-*/])/);
      const num1 = parseFloat(parts[0]);
      const operator = parts[1];
      const num2 = parseFloat(parts[2]);
      
      if (isNaN(num1) || isNaN(num2)) {
        result = "Error: Invalid number";
      } else {
        switch (operator) {
          case '+':
            result = (num1 + num2).toString();
            break;
          case '-':
            result = (num1 - num2).toString();
            break;
          case '*':
            result = (num1 * num2).toString();
            break;
          case '/':
            if (num2 === 0) {
              result = "Error: Division by zero";
            } else {
              result = (num1 / num2).toString();
            }
            break;
          default:
            result = "Error: Invalid operator";
        }
      }
    } else {
      // Fallback to Function constructor for complex expressions
      try {
        const evalResult = Function(`return ${processedExpr}`)();
        if (typeof evalResult === "number" && !isNaN(evalResult)) {
          result = evalResult.toString();
        } else {
          result = "Error: Invalid expression";
        }
      } catch (err) {
        result = "Error: Calculation error";
      }
    }
    }

    // Format result to avoid floating point artifacts
    if (result.includes(".")) {
      // Round to reasonable precision
      const rounded = parseFloat(result.toFixed(10));
      if (isNaN(rounded)) {
        result = "Error: Calculation failed";
      } else {
        result = rounded.toString();
      }
    }

    logHistory(currentExpression, result);
    return result;
  } catch (err) {
    result = "Error: Calculation error";
    logHistory(currentExpression, result);
    return result;
  }
}

// Export singleton instance
const calculatorEngine = {
  currentExpression,
  result,
  history,
  clear,
  reset,
  calculate,
  logHistory
};

export default calculatorEngine;
