"""
Backward compatibility shim for runtime.compatibility_matrix.
Re-exports authoritative implementation from runtime.compatibility_enforcer.
"""
from runtime.compatibility_enforcer import (
    CompatibilityMatrixEnforcer,
    CompatibilityValidationReport,
    CompatibilityValidationReport as CompatibilityReport,
    ToolRequirementStatus,
)

__all__ = [
    "CompatibilityMatrixEnforcer",
    "CompatibilityValidationReport",
    "CompatibilityReport",
    "ToolRequirementStatus",
]
