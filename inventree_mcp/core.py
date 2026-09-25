"""MCP server for InvenTree"""

from typing import ClassVar

from django.urls import reverse_lazy
from plugin import InvenTreePlugin
from plugin.mixins import SettingsMixin, UrlsMixin

from . import PLUGIN_VERSION
from ._well_known_compat import WellKnownMixin


class InvenTreeMCP(WellKnownMixin, SettingsMixin, UrlsMixin, InvenTreePlugin):
    """InvenTreeMCP - custom InvenTree plugin."""

    # Plugin metadata
    TITLE = "InvenTree MCP"
    NAME = "InvenTreeMCP"
    SLUG = "inventree-mcp"
    DESCRIPTION = "MCP server for InvenTree"
    VERSION = PLUGIN_VERSION

    # Additional project information
    AUTHOR = "Oliver Walters"
    WEBSITE = "https://github.com/inventree/inventree-mcp"
    LICENSE = "MIT"

    # Supported InvenTree versions
    MIN_VERSION = "1.5.0"

    # Plugin settings (from SettingsMixin)
    # Ref: https://docs.inventree.org/en/latest/plugins/mixins/settings/
    SETTINGS: ClassVar[dict] = {
        "REQUIRE_AUTH": {
            "name": "Require Authentication",
            "description": "Reject unauthenticated requests to the MCP endpoint. Disable only for local testing.",
            "validator": bool,
            "default": True,
        },
        "MCP_READ_ONLY": {
            "name": "Read Only",
            "description": "Block all write actions via the MCP endpoint, regardless of the calling user's permissions.",
            "validator": bool,
            "default": True,
        },
        "OIDC_ISSUER": {
            "name": "OIDC Issuer",
            "description": "Accept bearer JWTs from this OpenID provider (exact 'iss', trailing slash included). Empty disables OIDC. Env override: INVENTREE_MCP_OIDC_ISSUER.",
            "default": "",
        },
        "OIDC_AUDIENCE": {
            "name": "OIDC Audience",
            "description": "Required 'aud' entry, usually the MCP resource URL at your gateway. Env override: INVENTREE_MCP_OIDC_AUDIENCE.",
            "default": "",
        },
        "OIDC_PROVIDER": {
            "name": "OIDC SSO Provider",
            "description": "django-allauth provider id whose linked account uid equals the token 'sub'. Env override: INVENTREE_MCP_OIDC_PROVIDER.",
            "default": "",
        },
        "OIDC_JWKS_URL": {
            "name": "OIDC JWKS URL",
            "description": "Signing keys URL. Empty uses jwks_uri from the issuer's discovery document. Env override: INVENTREE_MCP_OIDC_JWKS_URL.",
            "default": "",
        },
        "OIDC_CLIENT_USERS": {
            "name": "OIDC Client Users",
            "description": "Comma separated client_id=username pairs for machine tokens (sub == azp). Env override: INVENTREE_MCP_OIDC_CLIENT_USERS.",
            "default": "",
        },
        "MCP_LOG_TOOL_CALLS": {
            "name": "Log Tool Calls",
            "description": "Log every MCP tool call (tool name, arguments, calling user, and outcome) to the 'inventree' logger for debugging.",
            "validator": bool,
            "default": False,
        },
    }

    # Custom URL endpoints (from UrlsMixin)
    # Ref: https://docs.inventree.org/en/latest/plugins/mixins/urls/
    def setup_urls(self):
        """Configure custom URL endpoints for this plugin."""
        from .mcp_transport import urlpatterns as mcp_urlpatterns
        from .server_card import urlpatterns as server_card_urlpatterns

        return mcp_urlpatterns + server_card_urlpatterns

    # Well-known URLs (from WellKnownMixin, if the running InvenTree provides it)
    # Ref: https://github.com/inventree/inventree-mcp/issues/33
    def get_well_known_urls(self, request=None):
        """Advertise this plugin's MCP Server Card under /.well-known/."""
        return [("mcp-server-card", reverse_lazy(f"plugin:{self.slug}:server-card"))]
