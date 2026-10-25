"""MCP tools for querying the InvenTree audit log (model change history).

The audit log (core PR inventree/InvenTree#13069, first released in
InvenTree 1.7.0) records a history entry each time an audited model is
created, updated or deleted - who changed what, when, and the old/new
values. Entries are created automatically by InvenTree itself; these tools
are read-only by nature, not just because of MCP_READ_ONLY.

Version compatibility: on an InvenTree core older than 1.7.0 the 'audit' app
doesn't exist at all - resolve_view() catches the resulting ImportError, so
these tools are hidden from tools/list (see tool_visibility.py) and a direct
call raises a clean ToolError via call_view(), rather than breaking plugin
load.

Staff only: the core API endpoint is restricted to staff users
(IsAdminOrAdminScope), and additionally filters entries down to the audited
models the user can view. _require_staff() below repeats the staff check
before dispatch - see its docstring.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ..context import get_current_user
from ..mcp_server import mcp
from ..proxy import call_view
from ..view_resolution import resolve_view
from ._common import build_query_params


def _audit_log_view() -> type | None:
    return resolve_view("audit.api", "AuditLogViewSet")


def _require_staff() -> None:
    """Reject non-staff callers before the request reaches the core view.

    Defence in depth on top of the core view's own IsAdminOrAdminScope: that
    class also admits any request whose OAuth2 token carries the 'a:staff'
    scope, without checking the user's own is_staff flag. This tool is
    specified as staff-only regardless of how the caller authenticated, so
    the user's staff status is checked here as well - an OAuth2 token still
    has to carry the right scope too (enforced by call_view()).
    """
    try:
        user = get_current_user()
    except PermissionError:
        user = None

    if user is None or not user.is_staff:
        raise ToolError("The audit log is only available to staff users.")


@mcp.tool()
async def list_audit_log(
    model_type: str | None = None,
    model_id: int | None = None,
    action: int | None = None,
    actor: int | None = None,
    search: str | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List audit log entries - the change history of InvenTree records (staff only).

    Each entry records one create/update/delete of an audited record (parts,
    orders, companies, build orders, attachments, parameters, settings,
    ...), including who made the change, when, and which fields changed
    (old -> new values in `changes`). Only entries for models the caller is
    allowed to view are returned. Requires InvenTree 1.7.0 or newer.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_audit_log_entry's return
    value. Results are newest first unless `ordering` says otherwise.

    Args:
        model_type: restrict to entries for this model type, e.g.
            "order.purchaseorder" or just "purchaseorder". Call
            describe_filters("audit_log") and check the model_type filter's
            choices for the audited model types.
        model_id: restrict to entries for the record with this ID - requires
            model_type. By default this also includes entries for that
            record's child objects (e.g. a purchase order's line items) -
            pass filters={"include_children": false} to exclude them.
        action: restrict to one action type: 0 = create, 1 = update,
            2 = delete (see describe_filters("audit_log") for all choices).
        actor: restrict to changes made by this User ID.
        search: free-text search against the record description and the
            acting user's username.
        ordering: field to sort results by ('-' prefix for descending, e.g.
            "timestamp" for oldest first). Call describe_filters("audit_log")
            and check its ordering_fields list for valid values.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("audit_log") to see what's
            available, e.g. filters={"min_date": "2026-01-01"}.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.

    Raises:
        ToolError: the caller is not a staff user, or the running InvenTree
            version doesn't provide the audit log.
    """
    _require_staff()

    base: dict[str, Any] = {}
    if model_type is not None:
        base["model_type"] = model_type
    if model_id is not None:
        base["model_id"] = model_id
    if action is not None:
        base["action"] = action
    if actor is not None:
        base["actor"] = actor
    if search is not None:
        base["search"] = search
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(_audit_log_view(), "GET", "/api/audit/", query_params=params)


@mcp.tool()
async def get_audit_log_entry(
    entry_id: int, filters: dict[str, Any] | None = None
) -> dict:
    """Get full detail for a single audit log entry by its ID (staff only).

    Returns the same object shape as one entry in list_audit_log's `results`
    array. Get a valid ID from list_audit_log (its `pk` field) if you don't
    already have one.

    Args:
        entry_id: the audit log entry's database ID.
        filters: optional-field toggles beyond what's returned by default -
            pass filters={"changes_detail": true} for a per-field breakdown
            of the change, with human-readable old/new values. The same
            toggle also works on list_audit_log's `filters`.

    Raises:
        ToolError: the caller is not a staff user, no entry exists with that
            ID (or the caller can't view the audited model), or the running
            InvenTree version doesn't provide the audit log.
    """
    _require_staff()

    return await call_view(
        _audit_log_view(),
        "GET",
        f"/api/audit/{entry_id}/",
        pk=entry_id,
        query_params=filters,
    )
