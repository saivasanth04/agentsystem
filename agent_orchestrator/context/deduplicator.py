"""
Backward-compatibility delegation shim for deduplicator.
Authoritative implementation resides in context/dedup.py.
"""
from context.dedup import ContextDeduplicator

ContentDeduplicator = ContextDeduplicator

__all__ = [
    "ContextDeduplicator",
    "ContentDeduplicator",
]
