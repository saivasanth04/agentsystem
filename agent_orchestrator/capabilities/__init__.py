"""
Capabilities module: Declarative agent capability modeling, tool scoping,
skill resolution, model constraints, and permission policies.
"""
from .fallback import (
    CapabilityExecutionResult,
    CapabilityFallbackEngine,
    CapabilityProvider,
    CapabilityTier,
    CBMProvider,
    FilesystemProvider,
    GraftProvider,
    ProviderUnavailableError,
    RipgrepProvider,
    TreeSitterProvider,
)
from .model import (
    CapabilityDefinition,
    CapabilityRegistry,
    ModelConstraint,
    ModelTier,
    TOOL_GROUPS,
    default_capability_registry,
    expand_tool_names,
)

__all__ = [
    "CapabilityDefinition",
    "CapabilityRegistry",
    "ModelConstraint",
    "ModelTier",
    "TOOL_GROUPS",
    "default_capability_registry",
    "expand_tool_names",
    "CapabilityExecutionResult",
    "CapabilityFallbackEngine",
    "CapabilityProvider",
    "CapabilityTier",
    "GraftProvider",
    "CBMProvider",
    "RipgrepProvider",
    "TreeSitterProvider",
    "FilesystemProvider",
    "ProviderUnavailableError",
]
