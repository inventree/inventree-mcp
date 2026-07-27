"""MCP tools for querying InvenTree Attachment data.

Attachments are generic: a single model (`common.models.Attachment`) links a
file or external URL to almost any other InvenTree record via a
(`model_type`, `model_id`) pair, rather than each resource having its own
attachment table. See list_attachments' docstring for the exact
`model_type` string format this uses, and note it differs from
`parameters.py`'s.
"""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import build_query_params


@mcp.tool()
async def list_attachments(
    model_type: str | None = None,
    model_id: int | None = None,
    is_image: bool | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List attachments (uploaded files or external links) linked to InvenTree records.

    Attachments can be linked to almost any InvenTree record - parts, stock
    items, purchase/sales/return/transfer orders, builds, companies,
    manufacturer/supplier parts, sales order shipments. Pass both
    `model_type` and `model_id` together to see everything attached to one
    specific record, e.g. "what's attached to part 42?".

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_attachment's return value -
    use its `pk` field with get_attachment to fetch full detail for one of
    them. The `attachment`/`thumbnail` fields are URLs to the file, not
    embedded file content - this tool cannot return raw file bytes.

    Args:
        model_type: restrict to attachments on this record type - a plain
            lowercase model name, e.g. "part", "stockitem", "build",
            "company", "purchaseorder", "salesorder", "returnorder",
            "transferorder", "manufacturerpart", "supplierpart",
            "salesordershipment". Note this is a *different* string format
            from list_parameters' `model_type` (which uses
            "app_label.modelname") - the two generic-metadata systems don't
            share a format.
        model_id: restrict to attachments on this specific record ID (use
            together with model_type to scope to one record).
        is_image: True for only attachments with a generated thumbnail
            (image files), False for the inverse. Omit to include both. See
            also filters={"is_file": true} / filters={"is_link": true} to
            distinguish uploaded files from external URL links (a link
            attachment is never an image).
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("attachment") and
            check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("attachment") to see what's
            available, e.g. filters={"upload_user": <id>}.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from common.api import AttachmentList

    base: dict[str, Any] = {}
    if model_type is not None:
        base["model_type"] = model_type
    if model_id is not None:
        base["model_id"] = model_id
    if is_image is not None:
        base["is_image"] = is_image
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        AttachmentList, "GET", "/api/attachment/", query_params=params
    )


@mcp.tool()
async def get_attachment(attachment_id: int) -> dict:
    """Get full detail for a single attachment by its ID.

    Returns the same object shape as one entry in list_attachments's
    `results` array. Get a valid ID from list_attachments (its `pk` field)
    if you don't already have one.

    Args:
        attachment_id: the Attachment's database ID.

    Raises:
        ToolError: no attachment exists with that ID, or the caller doesn't
            have permission to view it.
    """
    from common.api import AttachmentDetail

    return await call_view(
        AttachmentDetail, "GET", f"/api/attachment/{attachment_id}/", pk=attachment_id
    )
