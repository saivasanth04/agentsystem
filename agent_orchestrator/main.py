"""
Main CLI Entrypoint for the Multi-Agent Task Orchestrator System.
"""
import argparse
import sys
import json
from pathlib import Path

# Ensure package importability
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(BASE_DIR.parent))

from agent_orchestrator.config import OrchestratorConfig
from agent_orchestrator.llm import LLMClient
from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.state import TaskStatus, ReviewVerdict

# Windows UTF-8 output setup
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def print_banner():
    banner = r"""
========================================================================
   🤖 TASK-ORCHESTRATOR MULTI-AGENT AUTONOMOUS SYSTEM
========================================================================
  Lifecycle:
    1. Understand Task ──► 2. Decompose Task ──► 3. Select Agent
    4. Execute ──────────► 5. Check Result   ──► 6. Re-plan (on FAIL)
    
  Agents:
    • Planner        • Specification     • Architecture
    • Coder          • Tester            • Reviewer
========================================================================
"""
    print(banner)


def run_cli():
    parser = argparse.ArgumentParser(description="Multi-Agent Task Orchestrator CLI")
    parser.add_argument("--prompt", "-p", type=str, help="User request / task description")
    parser.add_argument("--model", "-m", type=str, default="auto", help="LLM model (default: auto)")
    parser.add_argument("--base-url", "-u", type=str, default="http://127.0.0.1:8000/v1", help="Gateway Base URL")
    parser.add_argument("--api-key", "-k", type=str, default=None, help="Unified Gateway API Key")
    parser.add_argument("--max-iter", type=int, default=3, help="Max re-planning iterations (default: 3)")
    parser.add_argument("--output-dir", "-o", type=str, default=None, help="Workspace output directory")

    args = parser.parse_args()
    print_banner()

    cfg = OrchestratorConfig(
        base_url=args.base_url,
        default_model=args.model,
        max_replan_iterations=args.max_iter,
    )
    if args.api_key:
        cfg.api_key = args.api_key
    if args.output_dir:
        cfg.workspace_dir = Path(args.output_dir)

    print(f"[*] Gateway URL: {cfg.base_url}")
    print(f"[*] Model:       {cfg.default_model}")
    print(f"[*] Workspace:   {cfg.workspace_dir}")
    print(f"[*] Max Re-plan: {cfg.max_replan_iterations}")

    llm = LLMClient(api_key=cfg.api_key, base_url=cfg.base_url, default_model=cfg.default_model)
    orchestrator = TaskOrchestrator(cfg=cfg, llm=llm)

    prompt = args.prompt
    if not prompt:
        print("\nEnter your task request below (or type 'exit' to quit):")
        try:
            prompt = input("User Request > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            return

    if not prompt or prompt.lower() in ("exit", "quit"):
        return

    print(f"\n🚀 Launching Orchestrator for: {prompt}\n")
    state = orchestrator.run(prompt)

    print("\n" + "=" * 60)
    print(" 🏁 ORCHESTRATION SUMMARY REPORT")
    print("=" * 60)
    status_badges = {
        TaskStatus.COMPLETED: "🟢 COMPLETED (Verified)",
        TaskStatus.NEED_VERIFICATION: "🟡 NEED_VERIFICATION (Unverified deliverables)",
        TaskStatus.INCOMPLETE: "🟠 INCOMPLETE (Tasks remaining)",
        TaskStatus.STOPPED: "🔴 STOPPED (Halted / max iterations reached)",
        TaskStatus.FAILED: "❌ FAILED (Defects detected)",
        TaskStatus.IN_PROGRESS: "🔵 IN_PROGRESS",
        TaskStatus.PENDING: "⚪ PENDING",
    }
    status_label = status_badges.get(state.status, state.status.value)
    print(f"Status:             {status_label}")
    print(f"Final Verdict:      {state.verdict.value}")
    print(f"Re-plan Iterations: {state.current_iteration}")
    print(f"Total Agent Steps:  {len(state.messages)}")
    print(f"Generated Files:    {orchestrator.workspace.list_files()}")

    if state.review_output:
        ev = state.review_output.get("evidence")
        if ev and isinstance(ev, dict):
            print("\n--- Deterministic Verification Evidence ---")
            b_val = "PASS" if ev.get("build", {}).get("success") else "FAIL"
            t_data = ev.get("tests", {})
            t_val = f"{t_data.get('passed', 0)}/{t_data.get('total', 0)} passed (exit: {t_data.get('exit_code', 0)})"
            l_val = f"{ev.get('lint', {}).get('errors', 0)} errors, {ev.get('lint', {}).get('warnings', 0)} warnings"
            cov_val = f"{ev.get('diff_coverage', {}).get('percentage', 100.0):.1f}%"
            ac_val = f"{sum(1 for v in ev.get('acceptance_criteria', {}).values() if v == 'verified')}/{len(ev.get('acceptance_criteria', {}))} verified" if ev.get('acceptance_criteria') else "N/A"
            print(f"Build Pipeline:      {b_val}")
            print(f"Automated Tests:     {t_val}")
            print(f"Static Analysis:     {l_val}")
            print(f"Diff Code Coverage:  {cov_val}")
            print(f"Acceptance Criteria: {ac_val}")
        print(f"\nReview Score:       {state.review_output.get('score_out_of_100', 'N/A')}/100")
        print(f"Review Summary:     {state.review_output.get('summary')}")

    print(f"\nWorkspace output written to: {cfg.workspace_dir.resolve()}\n")


if __name__ == "__main__":
    run_cli()
