# 🤖 Autonomous Multi-Agent Task Orchestrator System

An enterprise-grade, autonomous multi-agent software engineering system implementing a 6-stage orchestration lifecycle with closed-loop verification and dynamic re-planning.

---

## 🏛️ System Architecture

```
                         USER REQUEST
                              │
                              ▼
                  ┌──────────────────────┐
                  │   TASK-ORCHESTRATOR  │
                  │                      │
                  │  1. Understand task  │
                  │  2. Decompose task   │
                  │  3. Select agent     │
                  │  4. Execute         │
                  │  5. Check result    │
                  │  6. Re-plan         │
                  └──────────┬───────────┘
                             │
                ┌────────────┼────────────┐
                │            │            │
                ▼            ▼            ▼
          ┌──────────┐ ┌────────────┐ ┌───────────┐
          │  PLANNER │ │SPECIFICATION│ │ARCHITECTURE│
          └────┬─────┘ └──────┬─────┘ └─────┬─────┘
               │              │             │
               └──────────────┼─────────────┘
                              ▼
                         ┌─────────┐
                         │  CODER  │
                         └────┬────┘
                              │
                              ▼
                         ┌─────────┐
                         │ TESTER  │
                         └────┬────┘
                              │
                              ▼
                         ┌─────────┐
                         │ REVIEWER│
                         └────┬────┘
                              │
                       PASS ──┴── FAIL
                              │
                    ┌─────────▼─────────┐
                    │ TASK-ORCHESTRATOR │
                    │    RE-PLANS       │
                    └───────────────────┘
```

---

## 🔄 6-Stage Orchestration Lifecycle

1. **Understand Task**: Extracts goals, domain constraints, complexity, and target tech stack.
2. **Decompose Task**: Breaks the problem into sequential milestones and agent deliverables.
3. **Select Agent**: Dynamically routes to the right specialized agent (Planner, Specification, Architecture, Coder, Tester, Reviewer).
4. **Execute**: Dispatches prompts with strict JSON schemas, tools, and execution lineage.
5. **Check Result**: Validates deliverables, runs unit tests in subprocesses, and inspects exit codes.
6. **Re-Plan**: If Reviewer or Tester emits `FAIL`, orchestrator aggregates actionable feedback, updates state, and loops back to Coder (or upstream specs) until `PASS` or max iteration limit.

---

## 👥 Specialized Agent Roles

| Agent | Responsibility | Deliverables |
|---|---|---|
| **`PLANNER`** | Phased execution roadmap, risk mitigations, milestone planning | Structured project roadmap (`plan_output`) |
| **`SPECIFICATION`** | Functional & non-functional requirements, input/output contracts, edge cases | Requirement specifications (`specification_output`) |
| **`ARCHITECTURE`** | System topology, modular structure, file layout, design patterns | Architecture diagrams & schemas (`architecture_output`) |
| **`CODER`** | Modular, production-ready code implementation (no placeholders) | Working source code files in workspace |
| **`TESTER`** | Comprehensive unit & integration test suites, automated execution | Passing unit tests & execution logs |
| **`REVIEWER`** | Rigorous verification of correctness, security, tests, and architecture | PASS / FAIL verdict, score, remediation plan |

---

## 🚀 Quick Start

### 1. Installation
```bash
pip install -r requirements.txt
```

### 2. Run Interactive CLI
```bash
python main.py
```
Or with custom arguments:
```bash
python main.py --prompt "Build a Thread-Safe LRU Cache with TTL" --model "auto" --base-url "http://127.0.0.1:8000/v1"
```

### 3. Programmatic Usage in Python
```python
from agent_orchestrator import TaskOrchestrator, OrchestratorConfig, LLMClient

# 1. Configure settings (api_key resolved automatically from UNIFIED_API_KEY or OPENAI_API_KEY)
cfg = OrchestratorConfig(
    base_url="http://127.0.0.1:8000/v1",
    default_model="auto",
    max_replan_iterations=3
)

# 2. Initialize LLM Client & Orchestrator
llm = LLMClient(api_key=cfg.api_key, base_url=cfg.base_url, default_model=cfg.default_model)
orchestrator = TaskOrchestrator(cfg=cfg, llm=llm)

# 3. Run full autonomous workflow
state = orchestrator.run("Build a rate-limited REST API client in Python")
print("Status:", state.status.value)
print("Verdict:", state.verdict.value)
print("Generated Files:", orchestrator.workspace.list_files())
```

---

## 🧪 Running Automated Tests
```bash
python -m unittest agent_orchestrator/test_orchestrator.py
```
