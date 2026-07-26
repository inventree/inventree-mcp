"""Helpers for reading InvenTreeMCP's own plugin settings.

Shared by mcp_transport.py (REQUIRE_AUTH) and proxy.py (MCP_READ_ONLY) so
there is one place that resolves the plugin instance and fails safe.
"""

from __future__ import annotations

import contextlib
from typing import Any


def _get_plugin_instance() -> Any:
    from plugin import registry

    return registry.get_plugin("inventree-mcp")


def get_plugin_setting(key: str, default: bool = True) -> bool:
    """Read one of this plugin's own boolean settings.

    Fails safe to *default* if the plugin instance can't be resolved (e.g.
    outside of a real request) or the setting lookup raises - both of this
    plugin's settings (REQUIRE_AUTH, MCP_READ_ONLY) default to the
    more restrictive value, so "can't tell" and "restricted" should behave
    the same.
    """
    plugin = _get_plugin_instance()

    if plugin is None:
        return default

    with contextlib.suppress(Exception):
        return bool(plugin.get_setting(key))

    return default
