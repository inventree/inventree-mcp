"""Resolve InvenTree API view classes without crashing on a version mismatch.

Every MCP tool wraps one specific DRF view class from InvenTree core (e.g.
part.api.PartList) - see proxy.py's module docstring for why dispatch has to
go through the real view rather than the ORM. Those view classes are
imported lazily, from inside each tool's function body (and from
tools/discovery.py's RESOURCE_LOADERS), specifically so a class renamed or
removed between InvenTree core versions surfaces as a normal, catchable
failure at first real use - not an AppRegistryNotReady crash at plugin load
time, and not a bare ImportError propagating out of a tool call either.

This module is the one place that failure is caught, cached, and logged, so
callers (mostly proxy.call_view(), which turns a None into a clean ToolError)
don't need to repeat that handling at each of the ~50 call sites that import
a view class.
"""

from __future__ import annotations

from importlib import import_module

import structlog

logger = structlog.get_logger("inventree")

# Caches only failures, not successes - Python's own sys.modules already
# caches a successful import_module(), so re-resolving a working class is
# already cheap; a broken one would otherwise re-attempt the import (and
# re-log the warning) on every single call.
_unresolved: set[tuple[str, str]] = set()


def resolve_view(module_path: str, class_name: str) -> type | None:
    """Import *class_name* from *module_path*, or None if it isn't there.

    Caches a miss for the life of the process - a class that's missing
    because this plugin doesn't match the running InvenTree core version
    won't reappear without a restart - so the warning below is logged once
    per class, not once per call.

    Args:
        module_path: dotted module path, e.g. "part.api".
        class_name: the class to import from it, e.g. "PartList".

    Returns:
        The resolved class, or None if it couldn't be imported.
    """
    key = (module_path, class_name)
    if key in _unresolved:
        return None

    try:
        return getattr(import_module(module_path), class_name)
    except (ImportError, AttributeError) as exc:
        _unresolved.add(key)
        logger.warning(
            "inventree_mcp: %s.%s could not be imported (%s) - tools that "
            "depend on it will report as unavailable. This usually means "
            "the running InvenTree core version doesn't match what this "
            "plugin expects.",
            module_path,
            class_name,
            exc,
        )
        return None
