"""Django view adapter bridging the MCP Streamable HTTP transport onto InvenTree.

This view is marked auth_exempt so it can run its own, full DRF
authentication (Token / Basic / OAuth2 - see authentication_classes below).
That's necessary, not just a style choice:
InvenTree.middleware.AuthRequiredMiddleware only recognizes session cookies
and InvenTree's own ApiToken, and gates on a fixed path allowlist that
doesn't include plugin URLs - an OAuth2 bearer token would otherwise never
even reach this view (verified: /api/part/ accepts it, this endpoint 401s
before dispatch() runs at all). We gate on REQUIRE_AUTH ourselves, then bind
the authenticated identity into the MCP request context (see context.py) so
tools can act as that user - and, for OAuth2 requests, under that token's
actual granted scopes, not just the user's role permissions - via
proxy.call_view(). This is what makes per-tool permission enforcement
possible, instead of trusting "authenticated = allowed to do anything" as a
single plugin-wide bypass.

authentication_classes deliberately excludes SessionAuthentication (DRF's
default authenticator list includes it, for browser/UI convenience). MCP
clients are machine-to-machine and never send a CSRF token, and including it
actively breaks OAuth2 here: oauth2_provider's own OAuth2TokenMiddleware
(already in InvenTree's MIDDLEWARE, running after AuthRequiredMiddleware)
resolves the Bearer token and sets request.user at the middleware level for
every request. SessionAuthentication then sees an already-active user and
treats it as session auth, calling enforce_csrf() - which fails with "CSRF
Failed: CSRF cookie not set." for every OAuth2 request. Verified via a direct
trace: request.user was already the correct user by the time
SessionAuthentication.authenticate() ran, purely from that middleware.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, ClassVar

from asgiref.sync import async_to_sync
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from InvenTree.permissions import auth_exempt
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from oauth2_provider.contrib.rest_framework.authentication import OAuth2Authentication
from rest_framework import exceptions
from rest_framework.authentication import BasicAuthentication
from rest_framework.views import APIView
from users.authentication import ApiTokenAuthentication, ExtendedOAuth2Authentication

from .context import reset_current_user, set_current_user
from .mcp_server import mcp
from .settings import get_plugin_setting

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from starlette.types import Scope

_REQUEST_TIMEOUT_SECONDS = 60.0

# Hop-by-hop headers (RFC 7230 6.1) that must never be forwarded from the
# ASGI response onto the Django HttpResponse - WSGI servers (e.g. the stdlib
# wsgiref-based `runserver`) reject them outright, since only the server
# itself (not the application) is allowed to control connection handling.
_HOP_BY_HOP_HEADERS = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
})


def _new_session_manager() -> StreamableHTTPSessionManager:
    """StreamableHTTPSessionManager.run() can only be called once per instance."""
    return StreamableHTTPSessionManager(
        app=mcp._lowlevel_server, json_response=True, stateless=True
    )


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
        if name.lower() in _HOP_BY_HOP_HEADERS:
            continue
        val = value.decode("latin-1") if isinstance(value, bytes) else value
        response[name] = val

    return response


async def _handle_mcp_request_with_timeout(request: HttpRequest) -> HttpResponse:
    return await asyncio.wait_for(
        _handle_mcp_request(request), timeout=_REQUEST_TIMEOUT_SECONDS
    )


def _error_response(status: int, message: str) -> JsonResponse:
    return JsonResponse(
        {"jsonrpc": "2.0", "error": {"code": -32000, "message": message}, "id": None},
        status=status,
    )


class MCPView(APIView):
    """DRF view handling MCP Streamable HTTP transport requests.

    Runs its own authentication (Token / Basic / OAuth2 - see module
    docstring for why SessionAuthentication is deliberately excluded) rather
    than relying on permission_classes - REQUIRE_AUTH is admin-configurable,
    and per-tool authorization happens downstream in proxy.call_view(), not
    here.
    """

    authentication_classes: ClassVar[list] = [
        ApiTokenAuthentication,
        BasicAuthentication,
        ExtendedOAuth2Authentication,
    ]
    permission_classes: ClassVar[list] = []

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Force Django to cache the raw body now, before initialize_request()
        # wraps it in a DRF Request - DRF's own body/stream handling during
        # authentication otherwise leaves the raw request unable to satisfy a
        # later `request.body` read (_handle_mcp_request needs it for the
        # ASGI scope), raising RawPostDataException.
        _ = request.body

        self.args = args
        self.kwargs = kwargs
        drf_request = self.initialize_request(request, *args, **kwargs)

        try:
            self.perform_authentication(drf_request)
        except exceptions.APIException as exc:
            return _error_response(getattr(exc, "status_code", 401), str(exc))

        user = drf_request.user

        if get_plugin_setting("REQUIRE_AUTH") and not (user and user.is_authenticated):
            return _error_response(
                401,
                "Authentication required. Provide a valid Token, Bearer, or Basic credential.",
            )

        is_oauth2 = isinstance(
            drf_request.successful_authenticator, OAuth2Authentication
        )
        token = set_current_user(user, drf_request.auth if is_oauth2 else None)
        try:
            # async_to_sync (rather than a hand-rolled event loop) is what lets
            # proxy.call_view()'s sync_to_async(thread_sensitive=True) route
            # back onto *this* thread - see the note in proxy.py.
            return async_to_sync(_handle_mcp_request_with_timeout)(request)
        except TimeoutError:
            return _error_response(
                504, f"Request timed out after {_REQUEST_TIMEOUT_SECONDS:.0f}s"
            )
        except Exception:  # noqa: BLE001 - last-resort guard so a tool bug returns JSON-RPC, not an HTML 500
            return _error_response(500, "Internal server error")
        finally:
            reset_current_user(token)


urlpatterns = [
    path("mcp/", csrf_exempt(auth_exempt(MCPView.as_view())), name="mcp-endpoint")
]
