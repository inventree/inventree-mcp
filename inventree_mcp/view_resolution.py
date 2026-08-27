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

from collections.abc import Sequence
from importlib import import_module

import structlog

logger = structlog.get_logger("inventree")

# Caches only failures, not successes - Python's own sys.modules already
# caches a successful import_module(), so re-resolving a working class is
# already cheap; a broken one would otherwise re-attempt the import (and
# re-log the warning) on every single call.
_unresolved: set[tuple[str, str]] = set()

# Same idea as _unresolved above, but keyed on a resolve_view_any() call's
# full candidate list - only reached once none of them resolve.
_unresolved_any: set[tuple[str, tuple[str, ...]]] = set()


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


def resolve_view_any(module_path: str, class_names: Sequence[str]) -> type | None:
    """Import the first of *class_names* found in *module_path*, or None if none exist.

    For a resource whose view class(es) changed shape between InvenTree core
    versions in a way a single name can't cover - e.g. PurchaseOrder's
    separate PurchaseOrderList/PurchaseOrderDetail views on the 'stable'
    core release became one combined PurchaseOrderViewSet on 'master' after
    core PR #12317 - list every name this plugin has ever used for the
    resource, newest first. Whichever one exists on the running core
    version resolves silently; only exhausting the whole list logs a
    warning (an intermediate name not existing is expected on some core
    versions, not itself a version-mismatch problem - resolve_view()'s
    per-name warning would be misleading here since a working fallback may
    still exist).

    Args:
        module_path: dotted module path, e.g. "order.api".
        class_names: candidate class names to try in order, e.g.
            ["PurchaseOrderViewSet", "PurchaseOrderList"].

    Returns:
        The first resolved class, or None if none of them could be imported.
    """
    key = (module_path, tuple(class_names))
    if key in _unresolved_any:
        return None

    try:
        module = import_module(module_path)
    except ImportError:
        module = None

    if module is not None:
        for class_name in class_names:
            view_cls = getattr(module, class_name, None)
            if view_cls is not None:
                return view_cls

    _unresolved_any.add(key)
    logger.warning(
        "inventree_mcp: none of %s could be found in %s - tools that depend "
        "on it will report as unavailable. This usually means the running "
        "InvenTree core version doesn't match what this plugin expects.",
        list(class_names),
        module_path,
    )
    return None
