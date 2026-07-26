"""Permission-safe bridge from MCP tools to InvenTree's existing REST API views.

Every MCP tool must read/write data through call_view() rather than the ORM
directly. Dispatching through the real DRF view class means the exact same
authentication, RolePermission / ModelPermission checks, filtering, and
serialization that the normal REST API enforces also apply here - a user can
never see or change more via MCP than they could via the regular API.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from .context import get_current_user

_factory = APIRequestFactory()


def call_view(
    view_cls: type[APIView],
    method: str,
    path: str,
    *,
    query_params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    **view_kwargs: Any,
) -> Any:
    """Invoke an existing InvenTree DRF view class as the current MCP user.

    Args:
        view_cls: the view class as used in the real API urlconf (e.g. part.api.PartList).
        method: HTTP method to simulate ('GET', 'POST', 'PATCH', ...).
        path: the API path being emulated (only used for logging/routing context, not resolved).
        query_params: query string parameters (GET requests).
        data: request body (write requests).
        view_kwargs: extra kwargs the URL pattern would normally supply (e.g. pk=...).

    Returns:
        The parsed JSON response body, exactly as a real API client would receive it.

    Raises:
        ToolError: the underlying API call did not succeed (permission denied,
            not found, validation error, ...). The message is safe to surface
            to the calling agent.
    """
    user = get_current_user()
    method = method.upper()

    factory_method = getattr(_factory, method.lower())

    if method == "GET":
        request = factory_method(path, data=query_params or {})
    else:
        request = factory_method(path, data=data or {}, format="json")

    force_authenticate(request, user=user)

    response = view_cls.as_view()(request, **view_kwargs)
    response.render()

    body = json.loads(response.rendered_content or b"{}")

    if response.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else None
        raise ToolError(
            detail or f"Request failed with status {response.status_code}: {body}"
        )

    return body
