"""Propagate the authenticated Django user into MCP tool execution.

Tool functions are invoked by the MCP SDK's dispatcher, which does not know
about Django requests. The transport view sets the current user via a
contextvar before handing the request off to the MCP session manager, so
tools (and the proxy layer) can recover *who* is calling without threading a
request object through every tool signature.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser


_current_user: ContextVar[AbstractUser | None] = ContextVar(
    "inventree_mcp_current_user", default=None
)


def set_current_user(user: AbstractUser | None) -> Token:
    """Bind *user* as the acting user for the current request context."""
    return _current_user.set(user)


def reset_current_user(token: Token) -> None:
    """Undo a prior set_current_user call."""
    _current_user.reset(token)


def get_current_user() -> AbstractUser:
    """Return the user bound to the current MCP request.

    Raises:
        PermissionError: if no user is bound (e.g. a tool is invoked outside
            of a real MCP request, or authentication was not enforced).
    """
    user = _current_user.get()

    if user is None or not user.is_authenticated:
        raise PermissionError("No authenticated user for this MCP request")

    return user
