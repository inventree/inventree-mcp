"""MCP server for InvenTree"""

from plugin import InvenTreePlugin

from plugin.mixins import SettingsMixin, UrlsMixin

from . import PLUGIN_VERSION


class InvenTreeMCP(SettingsMixin, UrlsMixin, InvenTreePlugin):
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

    # Optionally specify supported InvenTree versions
    # MIN_VERSION = '0.18.0'
    # MAX_VERSION = '2.0.0'

    # Plugin settings (from SettingsMixin)
    # Ref: https://docs.inventree.org/en/latest/plugins/mixins/settings/
    SETTINGS = {
        # Define your plugin settings here...
        "CUSTOM_VALUE": {
            "name": "Custom Value",
            "description": "A custom value",
            "validator": int,
            "default": 42,
        }
    }

    # Custom URL endpoints (from UrlsMixin)
    # Ref: https://docs.inventree.org/en/latest/plugins/mixins/urls/
    def setup_urls(self):
        """Configure custom URL endpoints for this plugin."""
        from django.urls import path
        from .views import ExampleView

        return [
            # Provide path to a simple custom view - replace this with your own views
            path("example/", ExampleView.as_view(), name="example-view"),
        ]
