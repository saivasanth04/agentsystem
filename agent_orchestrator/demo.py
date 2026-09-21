"""
Quick Start Demo for the Multi-Agent Task Orchestrator System.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(BASE_DIR.parent))

from agent_orchestrator.config import OrchestratorConfig
from agent_orchestrator.llm import LLMClient
from agent_orchestrator.orchestrator import TaskOrchestrator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    print("===============================================================")
    print(" 🤖 Multi-Agent Task Orchestrator System: Live Demo")
    print("===============================================================")

    # Sample user request
    request = "Build an in-memory Thread-Safe LRU Cache in Python with TTL expiration, hit/miss metrics, and automated unit tests."

    cfg = OrchestratorConfig(
        base_url="http://127.0.0.1:8000/v1",
        default_model="auto",
        max_replan_iterations=3,
        workspace_dir=BASE_DIR / "workspace_output",
    )

    llm = LLMClient(api_key=cfg.api_key, base_url=cfg.base_url, default_model=cfg.default_model)
    orchestrator = TaskOrchestrator(cfg=cfg, llm=llm)

    print(f"\n[Task Request]: {request}")
    print(f"[Gateway]:      {cfg.base_url}")
    print(f"[Model]:        {cfg.default_model}")
    print(f"[Workspace]:    {cfg.workspace_dir}\n")

    state = orchestrator.run(request)

    print("\n" + "=" * 60)
    print(" 📊 FINAL EXECUTION SUMMARY")
    print("=" * 60)
    print(f"Status:             {state.status.value}")
    print(f"Final Verdict:      {state.verdict.value}")
    print(f"Re-plan Iterations: {state.current_iteration}")
    print(f"Generated Files:    {orchestrator.workspace.list_files()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
