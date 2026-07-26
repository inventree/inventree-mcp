"""Shared helpers for MCP tool implementations."""

from __future__ import annotations

from typing import Any

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


def build_query_params(
    base: dict[str, Any],
    filters: dict[str, Any] | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Merge a tool's named arguments with its free-form `filters` dict.

    `filters` is applied *before* limit/offset are set, so a caller can never
    use it to bypass clamp_limit()'s pagination cap by passing
    filters={"limit": 99999} - our own values always win.
    """
    params = dict(base)

    if filters:
        params.update(filters)

    params["limit"] = clamp_limit(limit)
    params["offset"] = offset

    return params
