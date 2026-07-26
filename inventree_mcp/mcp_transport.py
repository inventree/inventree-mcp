"""Django view adapter bridging the MCP Streamable HTTP transport onto InvenTree.

InvenTree's own auth middleware populates request.user (from a Token,
Bearer, Basic, or session credential) before this view runs. We gate on
REQUIRE_AUTH the same way the rest of the plugin does, then bind the
authenticated user into the MCP request context (see context.py) so tools
can act as that user via proxy.call_view() - this is what makes per-tool
permission enforcement possible, instead of trusting "authenticated =
allowed to do anything" as a single plugin-wide bypass.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from .context import reset_current_user, set_current_user
from .mcp_server import mcp

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from starlette.types import Scope

_REQUEST_TIMEOUT_SECONDS = 60.0


def _new_session_manager() -> StreamableHTTPSessionManager:
    """StreamableHTTPSessionManager.run() can only be called once per instance."""
    return StreamableHTTPSessionManager(
        app=mcp._mcp_server, json_response=True, stateless=True
    )


def _get_plugin_instance() -> Any:
    from plugin import registry

    return registry.get_plugin("inventree-mcp")


def _require_auth() -> bool:
    plugin = _get_plugin_instance()

    if plugin is None:
        return True

    with contextlib.suppress(Exception):
        return bool(plugin.get_setting("REQUIRE_AUTH"))

    return True


def _build_asgi_scope(request: HttpRequest) -> Scope:
    """Convert a Django HttpRequest into a minimal ASGI 'http' scope."""
    body = request.body
    headers: list[tuple[bytes, bytes]] = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in request.headers.items()
        if key.lower() != "content-length"
    ]
    headers.append((b"content-length", str(len(body)).encode("latin-1")))

    return {
        "type": "http",
        "http_version": "1.1",
        "method": request.method,
        "headers": headers,
        "path": request.path,
        "raw_path": request.get_full_path().encode("utf-8"),
        "query_string": request.META.get("QUERY_STRING", "").encode("latin-1"),
        "scheme": "https" if request.is_secure() else "http",
        "client": (request.META.get("REMOTE_ADDR", "127.0.0.1"), 0),
        "server": (request.get_host(), int(request.META.get("SERVER_PORT", 80))),
    }


async def _handle_mcp_request(request: HttpRequest) -> HttpResponse:
    """Dispatch a single Django request through the MCP session manager via ASGI."""
    session_manager = _new_session_manager()
    body = request.body
    scope = _build_asgi_scope(request)

    response_started: dict[str, Any] = {}
    response_body = bytearray()
    consumed = False

    async def receive() -> dict[str, Any]:
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            response_started["status"] = message["status"]
            response_started["headers"] = message.get("headers", [])
        elif message["type"] == "http.response.body":
            response_body.extend(message.get("body", b""))

    async with session_manager.run():
        await session_manager.handle_request(scope, receive, send)

    response = HttpResponse(
        bytes(response_body), status=response_started.get("status", 500)
    )
    for key, value in response_started.get("headers", []):
        name = key.decode("latin-1") if isinstance(key, bytes) else key
        val = value.decode("latin-1") if isinstance(value, bytes) else value
        response[name] = val

    return response


def _error_response(status: int, message: str) -> JsonResponse:
    return JsonResponse(
        {"jsonrpc": "2.0", "error": {"code": -32000, "message": message}, "id": None},
        status=status,
    )


@method_decorator(csrf_exempt, name="dispatch")
class MCPView(View):
    """Django view handling MCP Streamable HTTP transport requests."""

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if _require_auth() and not (
            hasattr(request, "user") and request.user.is_authenticated
        ):
            return _error_response(
                401,
                "Authentication required. Provide a valid Token, Bearer, or Basic credential.",
            )

        token = set_current_user(request.user if hasattr(request, "user") else None)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                asyncio.wait_for(
                    _handle_mcp_request(request), timeout=_REQUEST_TIMEOUT_SECONDS
                )
            )
        except TimeoutError:
            return _error_response(
                504, f"Request timed out after {_REQUEST_TIMEOUT_SECONDS:.0f}s"
            )
        except Exception:  # noqa: BLE001 - last-resort guard so a tool bug returns JSON-RPC, not an HTML 500
            return _error_response(500, "Internal server error")
        finally:
            reset_current_user(token)
            pending = asyncio.all_tasks(loop)
            for pending_task in pending:
                pending_task.cancel()
            if pending:
                with contextlib.suppress(Exception):
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            loop.close()


urlpatterns = [path("mcp/", MCPView.as_view(), name="mcp-endpoint")]
