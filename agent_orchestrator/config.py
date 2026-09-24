import os
from pathlib import Path
from dataclasses import dataclass

from .security.secrets import secret_manager

# Base directory for the agent orchestrator
BASE_DIR = Path(__file__).resolve().parent

# Resolved credential (no hardcoded credentials in source)
DEFAULT_KEY = secret_manager.get_secret("api_key", default="mock-key-for-testing")


@dataclass
class OrchestratorConfig:
    api_key: str = os.getenv("OPENAI_API_KEY", os.getenv("GATEWAY_API_KEY", DEFAULT_KEY))
    base_url: str = os.getenv("GATEWAY_BASE_URL", os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1"))
    default_model: str = os.getenv("ORCHESTRATOR_MODEL", "auto")
    planner_model: str = os.getenv("PLANNER_MODEL", "auto")
    spec_model: str = os.getenv("SPEC_MODEL", "auto")
    arch_model: str = os.getenv("ARCH_MODEL", "auto")
    coder_model: str = os.getenv("CODER_MODEL", "auto")
    tester_model: str = os.getenv("TESTER_MODEL", "auto")
    reviewer_model: str = os.getenv("REVIEWER_MODEL", "auto")
    
    # Model Tier Configurations (Issue #34)
    fast_model: str = os.getenv("FAST_MODEL", "auto")
    coding_model: str = os.getenv("CODING_MODEL", "auto")
    reasoning_model: str = os.getenv("REASONING_MODEL", "auto")
    fallback_model: str = os.getenv("FALLBACK_MODEL", "auto")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "auto")
    
    # Budget & Cost Tracking settings (Issue #35)
    max_session_cost_usd: float = float(os.getenv("MAX_SESSION_COST_USD", "0.0"))
    max_task_cost_usd: float = float(os.getenv("MAX_TASK_COST_USD", "0.0"))
    max_task_tokens: int = int(os.getenv("MAX_TASK_TOKENS", "0"))
    max_context_tokens: int = int(os.getenv("MAX_CONTEXT_TOKENS", "64000"))

    # Context Budget Allocation & Scoping (Issue #36)
    context_budget_total: int = int(os.getenv("CONTEXT_BUDGET_TOTAL", "32000"))
    context_budget_focal_files: int = int(os.getenv("CONTEXT_BUDGET_FOCAL", "8000"))
    context_budget_repo_map: int = int(os.getenv("CONTEXT_BUDGET_REPO_MAP", "2000"))

    # Execution & Re-planning settings
    max_replan_iterations: int = int(os.getenv("MAX_REPLAN_ITERATIONS", "3"))
    max_iterations: int = 3
    timeout_seconds: float = float(os.getenv("LLM_TIMEOUT", "90.0"))
    temperature: float = 0.4
    workspace_dir: Path = BASE_DIR / "workspace_output"

    # Structured Logging Settings (Issue #53)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_format: str = os.getenv("LOG_FORMAT", "auto")
    log_to_file: bool = True
    log_dir: Path = BASE_DIR / "logs"

    # LLM Resilience & Retry Settings (Issue #55)
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "3"))
    llm_base_delay: float = float(os.getenv("LLM_BASE_DELAY", "0.5"))
    llm_max_delay: float = float(os.getenv("LLM_MAX_DELAY", "30.0"))
    fallback_base_url: str = os.getenv("FALLBACK_BASE_URL", "")
    fallback_api_key: str = os.getenv("FALLBACK_API_KEY", "")

    def __post_init__(self):
        if not self.api_key:
            self.api_key = secret_manager.get_secret("api_key", default="mock-key-for-testing")
        else:
            secret_manager.register_secret(self.api_key)

        # Synchronize max_iterations and max_replan_iterations
        if self.max_iterations != 3:
            self.max_replan_iterations = self.max_iterations
        elif self.max_replan_iterations != 3:
            self.max_iterations = self.max_replan_iterations


# Default global instance
config = OrchestratorConfig()
Config = OrchestratorConfig


