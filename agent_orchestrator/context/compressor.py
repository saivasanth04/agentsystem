"""
Backward-compatibility delegation shim for compressor.
Authoritative implementation resides in context/pack.py.
"""
from context.pack import (
    CompressionLevel,
    CodeCompressor,
    JSONCompressor,
    LogCompressor,
    ContextCompressor,
)

__all__ = [
    "CompressionLevel",
    "CodeCompressor",
    "JSONCompressor",
    "LogCompressor",
    "ContextCompressor",
]
