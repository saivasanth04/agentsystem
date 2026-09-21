# Task List: React Calculator Application

## Phase 1: Foundation
- [ ] **Task 1**: Implement calculatorEngine.js — pure arithmetic engine with state machine
  - **Acceptance criteria**: Handles +, -, *, /, %, decimal, clear, backspace; returns expression strings and results
  - **Verification**: Unit tests pass; engine functions return correct values for all operations
  - **Dependencies**: None
  - **Files**: `src/calculatorEngine.js`
  - **Estimated scope**: S (1-2 files)

- [ ] **Task 2**: Create Button.jsx — reusable styled button component
  - **Acceptance criteria**: Accepts label, variant (number/operator/function/equals), onClick handler, disabled state
  - **Verification**: Renders correctly, click handler fires, variant classes applied
  - **Dependencies**: None
  - **Files**: `src/components/Button.jsx`
  - **Estimated scope**: XS (1 file)

- [ ] **Task 3**: Create Display.jsx — read-only display with expression + result
  - **Acceptance criteria**: Shows current expression (small) and result (large); handles overflow
  - **Verification**: Displays correct values, overflow truncation works
  - **Dependencies**: None
  - **Files**: `src/components/Display.jsx`
  - **Estimated scope**: XS (1 file)

## Phase 2: Core Features
- [ ] **Task 4**: Create Keypad.jsx — grid layout for all buttons
  - **Acceptance criteria**: Renders all buttons in grid; passes click handlers to Button; supports dynamic button config
  - **Verification**: All buttons render, click events propagate correctly
  - **Dependencies**: Task 2 (Button)
  - **Files**: `src/components/Keypad.jsx`
  - **Estimated scope**: M (3-5 files)

- [ ] **Task 5**: Create HistoryLog.jsx — scrollable history with clear option
  - **Acceptance criteria**: Shows past calculations, click-to-restore, clear history button
  - **Verification**: History items render, click restores expression, clear works
  - **Dependencies**: Task 3 (Display)
  - **Files**: `src/components/HistoryLog.jsx`
  - **Estimated scope**: S (1-2 files)

- [ ] **Task 6**: Create Calculator.jsx — main orchestrator with state & keyboard
  - **Acceptance criteria**: Manages all state, wires engine to UI, handles keyboard events, persists history to localStorage
  - **Verification**: Full calculator works, keyboard shortcuts functional, history persists
  - **Dependencies**: Tasks 3, 4, 5
  - **Files**: `src/components/Calculator.jsx`
  - **Estimated scope**: L (5-8 files) — break into sub-tasks if needed

## Phase 3: Polish
- [ ] **Task 7**: Create CSS stylesheet — responsive, modern, themed
  - **Acceptance criteria**: Dark theme, responsive grid, hover/active states, reduced motion support
  - **Verification**: Looks good on mobile/desktop, all interactive states work
  - **Dependencies**: Task 6
  - **Files**: `src/styles/calculator.css`
  - **Estimated scope**: M (3-5 files)

- [ ] **Task 8**: Create App.jsx + index.jsx entry points
  - **Acceptance criteria**: App renders Calculator, index mounts React
  - **Verification**: Application starts without errors
  - **Dependencies**: Task 6, Task 7
  - **Files**: `src/App.jsx`, `src/index.jsx`
  - **Estimated scope**: XS (1-2 files)

- [ ] **Task 9**: Verify keyboard shortcuts, edge cases, responsiveness
  - **Acceptance criteria**: All keyboard shortcuts work, division by zero handled, responsive on all viewports
  - **Verification**: Manual testing + automated tests pass
  - **Dependencies**: Task 7, Task 8
  - **Files**: Tests in `src/__tests__/`
  - **Estimated scope**: M (3-5 files)

## Checkpoint: After Tasks 1-3
- [ ] All tests pass
- [ ] Application builds without errors
- [ ] Core user flow works end-to-end
- [ ] Review with human before proceeding

## Checkpoint: After Tasks 4-6
- [ ] Full calculator functional
- [ ] Keyboard shortcuts work
- [ ] History log operational

## Checkpoint: After Tasks 7-9
- [ ] All acceptance criteria met
- [ ] Ready for review
