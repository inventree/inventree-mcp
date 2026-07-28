"""Propagate the authenticated Django user (and OAuth2 auth, if any) into MCP tool execution.

Tool functions are invoked by the MCP SDK's dispatcher, which does not know
about Django requests. The transport view sets the current identity via a
contextvar before handing the request off to the MCP session manager, so
tools (and the proxy layer) can recover *who* is calling without threading a
request object through every tool signature.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser


@dataclass(frozen=True)
class _Identity:
    user: AbstractUser
    # The real oauth2_provider AccessToken, if the MCP request itself was
    # OAuth2-authenticated - None for token/basic/session auth. Propagated so
    # proxy.call_view() can make the proxied view see the token's *actual*
    # granted scopes, not just the user's role permissions. See proxy.py.
    oauth2_token: Any | None = None


_current_identity: ContextVar[_Identity | None] = ContextVar(
    "inventree_mcp_current_identity", default=None
)


def set_current_user(
    user: AbstractUser | None, oauth2_token: Any | None = None
) -> Token:
    """Bind *user* (and, if OAuth2-authenticated, the real access token) for this context."""
    identity = _Identity(user, oauth2_token) if user is not None else None
    return _current_identity.set(identity)


def reset_current_user(token: Token) -> None:
    """Undo a prior set_current_user call."""
    _current_identity.reset(token)


def has_current_user() -> bool:
    """Return whether a user is currently bound, without raising if not.

    For code that needs to behave differently outside a real MCP request
    (e.g. tool_visibility.py, which can't run a permission check without a
    caller to check permissions *for*) rather than treat "no caller" as a
    fatal error the way get_current_user() deliberately does everywhere else.
    """
    identity = _current_identity.get()
    return identity is not None and identity.user.is_authenticated


def get_current_user() -> AbstractUser:
    """Return the user bound to the current MCP request.

    Raises:
        PermissionError: if no user is bound (e.g. a tool is invoked outside
            of a real MCP request, or authentication was not enforced).
    """
    identity = _current_identity.get()

    if identity is None or not identity.user.is_authenticated:
        raise PermissionError("No authenticated user for this MCP request")

    return identity.user


def get_current_oauth2_token() -> Any | None:
    """Return the real OAuth2 access token for the current MCP request, if any."""
    identity = _current_identity.get()
    return identity.oauth2_token if identity else None
