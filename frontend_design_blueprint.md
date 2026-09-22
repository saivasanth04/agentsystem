# 🎨 Frontend Design Blueprint — Agent Orchestrator Console

> A production-grade web frontend for the Multi-Agent Task Orchestrator System.
> Based on full backend review of `agent_orchestrator` (LangGraph runtime, agent registry,
> DAG scheduler, budget tracker, SQLite persistence, telemetry/tracing, MCP tools, swarm).

---

## 1. Product Overview

**Product Name:** Orchestrator Console
**Purpose:** A mission-control dashboard where users can submit tasks, watch multi-agent
execution live, inspect agent reasoning/tool calls, review quality gates, manage budgets,
and resume/recover crashed sessions.

**Target Users:** ML engineers, platform teams, autonomous-agent developers, QA reviewers.

**Core UX Pillars**
1. **Observability First** — every agent decision, tool call, and token is traceable.
2. **Live Execution Theater** — DAG progress rendered in real time with status colors.
3. **Auditability** — ground-truth verification matrix and review verdicts front-and-center.
4. **Control** — cancel, pause, resume, rollback from the UI.

---

## 2. Design System (Tokens)

| Token | Value | Usage |
|---|---|---|
| `--bg-base` | `#0B0F17` (near-black navy) | App background |
| `--bg-panel` | `#111827` | Cards, panels |
| `--bg-elevated` | `#1F2937` | Modals, drawers |
| `--accent-primary` | `#6366F1` (indigo) | Primary actions, running states |
| `--accent-success` | `#10B981` | PASS / COMPLETED |
| `--accent-warning` | `#F59E0B` | NEED_VERIFICATION / UNDECIDED |
| `--accent-danger` | `#EF4444` | FAIL / errors / budget exceeded |
| `--accent-info` | `#3B82F6` | IN_PROGRESS |
| `--text-primary` | `#F9FAFB` | Headings |
| `--text-secondary` | `#9CA3AF` | Body, metadata |
| `--font-mono` | JetBrains Mono | Code, logs, traces, JSON |
| `--font-sans` | Inter | UI text |

**Status → Color Map (must match backend enums exactly):**
`COMPLETED 🟢 · IN_PROGRESS 🔵 · PENDING ⚪ · NEED_VERIFICATION 🟡 · INCOMPLETE 🟠 · FAILED ❌ · STOPPED 🔴 · NEED_MORE_EVIDENCE 🟣`

---

## 3. Application Shell & Navigation

**Layout:** Left collapsible sidebar (240px) + top bar (56px) + main content.

**Sidebar Sections:**
1. 🏠 **Dashboard** — sessions overview
2. 🚀 **New Task** — task launcher
3. 📁 **Sessions** — all orchestration runs
4. 🕸️ **Live DAG** — real-time execution view
5. 📦 **Artifacts** — files, patches, reports, traces
6. 🤖 **Agents** — registry, live agents, swarm
7. 💰 **Budgets** — cost/token ledger
8. 🗄️ **Memory** — working/project/semantic memory browser
9. ⚙️ **Settings** — models, gateway, MCP servers, env

**Top Bar:** Global search (sessions/tasks/files), session status pill, cancel button,
notification bell (budget warnings, circuit breakers), user profile.

---

## 4. Pages & Routes

### 4.1 Dashboard (`/`)
- **KPI cards:** Total sessions, PASS rate %, avg tokens/session, total cost (USD), active runs.
- **Chart 1:** Verdict distribution donut (PASS/FAIL/UNDECIDED).
- **Chart 2:** Cost & token usage over time (line, per model tier).
- **Chart 3:** Agent activity heatmap (agent × hour).
- **Recent Sessions table:** ID, request snippet, status badge, verdict, iterations, cost, duration, actions (Resume / Delete).
- **Alerts feed:** Budget exceeded, circuit breaker halts, rollback events.

### 4.2 New Task (`/tasks/new`)
- Large textarea for the user request with template chips
  ("Build an LRU cache", "Add REST API endpoint", "Fix failing tests").
- **Config accordion:**
  - Model selector (default / auto / per-role overrides: planner, coder, tester, reviewer…).
  - Max re-plan iterations slider (default 3).
  - Budget limits: max session cost, max task tokens, context budget fields.
  - Workspace directory picker, gateway base URL, API key (masked).
- **Dry-run / Validate** button → shows the decomposed DAG preview before launch.
- **Launch** button → redirects to Live DAG view with the new session.

### 4.3 Sessions List (`/sessions`)
- Filterable table: status, verdict, date range, model, cost range.
- Columns: Session ID, user request (truncated), status badge, verdict badge,
  replan iterations, tokens, cost, duration, created date.
- Row click → Session Detail.
- Bulk actions: export JSON, delete, re-run.

### 4.4 Session Detail (`/sessions/:id`) — *the core page*
Tabbed layout:

**Tab A — Overview**
- Header: status badge, verdict badge, score `/100`, iteration count, duration, total cost/tokens.
- **Reproducibility card:** snapshot ID, manifest hash (12-char), GitDirty flag, seed — with "Compare Sessions" action.
- **Execution summary grid:** tasks completed/failed/running/pending, attempts, checkpoints, observations, tools & skills used.
- **Final report panel:** reviewer summary, strengths, issues list (severity-tagged), remediation plan.

**Tab B — DAG Execution** (see §4.5)
**Tab C — Messages / Trace**
- Full conversation timeline of `AgentMessage` entries.
- Each entry: agent avatar chip, stage tag, timestamp, expandable JSON structured data.
- Filter by agent or stage; search box.

**Tab D — Verification Evidence**
- Ground-truth matrix: Build (PASS/FAIL), Tests (passed/total, exit code), Lint (errors/warnings), Diff coverage %, Acceptance criteria checklist (verified/pending).
- **Adversarial override banner** (amber/red): shown when reviewer PASS was vetoed.
- Tautological assertions counter with drill-down.
- Trace tree visualization (span waterfall: nodes → agents → tool calls → retrievals).

**Tab E — Files & Diff**
- File tree of generated workspace.
- Click file → side-by-side diff viewer (before/after SHA from change manifest).
- Agent Blame: per-file attribution (which agent/model/prompt-hash authored each change).

**Tab F — Re-plan History**
- Iteration timeline: trigger reason, feedback summary, injected/pruned task IDs, parallel groups.
- Rollback events highlighted with restored/deleted file counts.

### 4.5 Live DAG View (`/sessions/:id/live`)
- **Interactive DAG canvas** (React Flow): nodes = tasks (`T-01`…), edges = dependencies.
- Node states: PENDING ⚪ → READY → RUNNING 🔵 (pulsing) → VERIFYING → COMPLETED 🟢 / FAILED ❌ / BLOCKED / SKIPPED.
- Click node → drawer with: objective, capabilities, tools, inputs/outputs, acceptance tests,
  attempts, per-attempt tool history, observations, error messages.
- **Wave indicator:** shows which parallel wave is executing (scheduler max_workers=4).
- Live event stream (WebSocket): `TASK_CREATED`, `REPLAN_STARTED`, `REPLAN_COMPLETED`,
  `TRANSACTION ROLLBACK`, `CIRCUIT BREAKER`, `BUDGET EXCEEDED`, `WORKFLOW_COMPLETED`.
- **Control bar:** ⏸ Pause agent · ▶ Resume · ⏹ Cancel session · ↩ Rollback to checkpoint.
- Re-plan visualization: animated diff of DAG between iterations (new nodes green, pruned nodes struck-through).

### 4.6 Artifacts (`/artifacts`)
- Grid/table of all stored artifacts categorized: `plans`, `specifications`, `patches`, `test_results`, `reports`, `logs`.
- Preview pane: JSON pretty-print, patch viewer, JSONL log viewer with level filters, trace waterfall.
- Download buttons per artifact.

### 4.7 Agents (`/agents`)
- **Registry tab:** card per agent manifest (name, role description, capabilities chips, tools, skills, model tier badge FAST/BALANCED/FRONTIER).
- **Live Agents tab:** spawn/pause/resume/terminate controls; agent frame viewer (messages, turn count); delegation graph (parent → child edges).
- **Swarm tab:** blackboard posts, consensus votes, swarm coordinator status.
- **Health tab:** tool health-check results, MCP server connection status (green/red).

### 4.8 Budgets (`/budgets`)
- Session cost ledger table: task-level token usage (prompt/completion), cost USD, model used per task.
- Spec display: max session cost, max per-task cost, max tokens; progress bars with amber at 80%, red on exceed.
- **Budget Exceeded events** feed with the `HALT/WARN` action taken.

### 4.9 Memory (`/memory`)
- Tabs: Working Memory (key-value scratchpad), Task Memory, Episodic Experiences, Project Conventions, Semantic Knowledge.
- Search + tag filters; JSON view with copy button.

### 4.10 Settings (`/settings`)
- Gateway URL, API keys (vaulted, masked), fallback endpoint config.
- Model tier mapping (FAST / CODING / REASONING / EMBEDDING / FALLBACK).
- Retry policy, timeouts, logging (level, format, file sink), context budgets.
- MCP servers manager (add/remove servers, tool discovery list).

---

## 5. Component Library

| Component | Description |
|---|---|
| `<StatusBadge>` | Colored pill for TaskStatus / ReviewVerdict enums |
| `<AgentAvatar>` | Colored monogram avatar per agent (PL/SPEC/ARCH/CODE/TEST/REV) |
| `<DAGCanvas>` | React Flow graph with animated status transitions |
| `<TokenMeter>` | Progress bar with token/cost formatting |
| `<EvidenceMatrix>` | 5-gate verification grid (build/tests/lint/coverage/AC) |
| `<DiffViewer>` | Side-by-side diff with SHA attribution |
| `<TraceWaterfall>` | Hierarchical span timeline (tracer spans) |
| `<EventStream>` | Live-tailing WebSocket log feed with severity colors |
| `<ArtifactCard>` | Categorized artifact tile with preview modal |
| `<ReplanTimeline>` | Vertical timeline of ReplanRecords with rollback markers |
| `<ContextInspector>` | Collapsible prompt-context viewer (trust-level tags: USER_INSTRUCTION / CONTROL_SYSTEM / UNTRUSTED_REPOSITORY) |
| `<SkillChip>` / `<ToolChip>` | Tag chips for JIT skills & tools |
| `<BudgetBar>` | Cost/token consumption vs. limit with threshold colors |
| `<ModelSelector>` | Dropdown with tier badges and per-role override grid |
| `<JsonViewer>` | Collapsible, syntax-highlighted JSON |

---

## 6. Key Frontend Features (Mapped to Backend)

| Backend Capability | Frontend Feature |
|---|---|
| LangGraph workflow nodes | Live stage indicator: Discover → Understand → Decompose → Execute → Review → Replan |
| `TaskDAG` + `ConcurrentDAGScheduler` | Interactive parallel-wave DAG canvas |
| `OrchestratorState.messages` | Full message/trace timeline with structured-data expansion |
| `VerificationEvidenceContract` + ground-truth veto | Evidence matrix + adversarial override banner |
| `budget_tracker` + `BudgetExceededError` | Budget ledger, live meters, halt alerts |
| `SQLiteStateStore` + `SessionRecoveryEngine` | Sessions list, crash recovery banner, "Resume" button |
| Checkpoints + rollback (`WorkspaceCheckpointManager`) | Checkpoint picker + rollback-to-preview modal |
| `ArtifactStore` | Artifacts browser with categorized previews |
| `AgentRegistry` / lifecycle APIs (`spawn_agent`, `delegate`, `handoff`) | Agents page with live lifecycle controls & delegation graph |
| `SwarmCoordinator` (blackboard, consensus) | Swarm tab with blackboard feed and vote widgets |
| `ReproducibilityRecorder` | Snapshot card + session-diff comparison view |
| Telemetry engine + tracer | Metrics dashboards + trace waterfall |
| `CodebaseMemory` / retrieval hierarchy | Context inspector showing what context was injected per agent turn |
| Cancellation (`CancellationSource`) | Global ⏹ Cancel with reason prompt |
| Model tier routing (`ModelTier.FAST/BALANCED/FRONTIER`) | Model selector with tier badges & capability constraints tooltip |

---

## 7. Small-Scale Design Details

- **Micro-animations:** DAG nodes pulse while RUNNING; edges draw-in on task creation;
  rollback events flash a red rewind animation; PASS verdict triggers a subtle green sweep.
- **Empty states:** friendly illustrations with a "Launch your first task" CTA.
- **Loading skeletons:** shimmer on tables/cards; DAG shows ghost nodes while decomposing.
- **Keyboard shortcuts:** `⌘K` global search, `g d` dashboard, `n` new task, `c` cancel session.
- **Responsive:** sidebar collapses to icons < 1024px; DAG canvas gets a compact "list mode" toggle on small screens.
- **Accessibility:** WCAG AA contrast, full keyboard nav on DAG, ARIA live regions for event stream.
- **Dark-mode first** (matches developer tooling); light mode optional via settings.
- **Copy-to-clipboard** on all IDs, hashes, and JSON blocks.
- **Time formatting:** relative ("2m ago") with hover tooltip for ISO timestamps.

---

## 8. Suggested Tech Stack

- **Framework:** React 18 + TypeScript + Vite
- **State/Data:** TanStack Query (REST/WebSocket), Zustand for live-session state
- **DAG/Graph:** React Flow (`@xyflow/react`) with custom node types
- **Charts:** Recharts (dashboard metrics)
- **Diff:** `react-diff-viewer` (or Monaco Diff Editor)
- **Styling:** Tailwind CSS + custom token theme
- **Backend comms:** WebSocket channel for `EventBus` events; REST for CRUD (sessions, artifacts, settings)

---

*Artifact generated from backend review: orchestrator.py, state.py, contracts.py, agents/*,
runtime/* (scheduler, verification, replan, diagnostics), persistence/*, cost/*, telemetry/*, swarm/*.*
