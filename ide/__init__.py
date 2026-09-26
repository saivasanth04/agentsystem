"""
Production IDE Package.
Operationalizes repository intelligence, capability runtime, and closed-loop verification:
Verification Lifecycle:
Edit -> Build -> Lint -> Tests -> Security -> Review -> PASS

Failure Repair Loop:
Failure -> Observation -> Repository Brain -> Context Compiler -> Replanner -> Repair

Direct mature library integrations:
- Testing: pytest
- Lint: ruff
- Type check: mypy
- Git diff: GitPython (git)
- Security: bandit

Reused existing core components (no rewriting):
- VerificationGate (TaskVerificationGate)
- TesterAgent
- ReviewerAgent
- EpistemicReplanner
"""
from .verification_pipeline import (
    IDEVerificationPipeline,
    VerificationStage,
    StageOutcome,
    VerificationReport,
    VerificationGate,
)
from .repair_pipeline import (
    IDERepairPipeline,
    RepairResult,
)
from .production_ide import (
    ProductionIDE,
)
from agent_orchestrator.agents.tester import TesterAgent
from agent_orchestrator.agents.reviewer import ReviewerAgent
from agent_orchestrator.runtime.replan_engine import EpistemicReplanner

__all__ = [
    "ProductionIDE",
    "IDEVerificationPipeline",
    "VerificationStage",
    "StageOutcome",
    "VerificationReport",
    "VerificationGate",
    "IDERepairPipeline",
    "RepairResult",
    "TesterAgent",
    "ReviewerAgent",
    "EpistemicReplanner",
]
