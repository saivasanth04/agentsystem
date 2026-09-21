# Implementation Plan: React Calculator Application

## Overview
Build a modular, responsive, fully functional React calculator with history tracking, keyboard support, and clean separation of concerns.

## Architecture Decisions
- **calculatorEngine.js**: Pure calculation logic — no React dependencies, easily testable
- **Component hierarchy**: Calculator → Display + Keypad (+ HistoryLog)
- **State management**: useState in Calculator, lifted to parent, passed down as props
- **Keyboard**: Global keydown listener in Calculator, maps keys to button actions
- **Styling**: CSS modules / single stylesheet with CSS custom properties for theming

## Task List

### Phase 1: Foundation
- [ ] Task 1: Implement calculatorEngine.js — pure arithmetic engine with state machine
- [ ] Task 2: Create Button.jsx — reusable styled button component
- [ ] Task 3: Create Display.jsx — read-only display with expression + result

### Phase 2: Core Features
- [ ] Task 4: Create Keypad.jsx — grid layout for all buttons
- [ ] Task 5: Create HistoryLog.jsx — scrollable history with clear option
- [ ] Task 6: Create Calculator.jsx — main orchestrator with state & keyboard

### Phase 3: Polish
- [ ] Task 7: Create CSS stylesheet — responsive, modern, themed
- [ ] Task 8: Create App.jsx + index.jsx entry points
- [ ] Task 9: Verify keyboard shortcuts, edge cases, responsiveness

## Risks and Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| Division by zero | High | Engine returns 'Error' string, displayed gracefully |
| Keyboard event conflicts | Medium | Use keydown with preventDefault for calculator keys |
| State sync issues | Medium | Single source of truth in Calculator, engine is pure |
| Responsive layout break | Medium | CSS grid with minmax, media queries for small screens |

## Open Questions
- Should history persist across sessions? (localStorage — decided: yes, lightweight)
- Theme: dark mode default (modern calculator aesthetic)
