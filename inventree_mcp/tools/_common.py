"""Shared helpers for MCP tool implementations."""

from __future__ import annotations

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def clamp_limit(limit: int) -> int:
    """Clamp a caller-supplied page size to a sane range.

    Prevents a single tool call from dumping an unbounded number of records
    (or being handed a negative/zero value which some list views treat as
    "no limit").
    """
    if limit <= 0:
        return DEFAULT_LIMIT

    return min(limit, MAX_LIMIT)
