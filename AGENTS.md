# AGENTS.md

InvenTree plugin (entry point `inventree_mcp.core:InvenTreeMCP`) exposing an MCP (Model Context
Protocol) server over Streamable HTTP so MCP clients can query InvenTree data. Only runs installed
inside a real InvenTree instance - there is no standalone dev server for this repo.

## Core design rule

Tool code (`inventree_mcp/tools/*.py`) must never touch the Django ORM directly. Every tool calls
`proxy.call_view()`, which dispatches through the *real* InvenTree DRF view class as the
authenticated caller, so permission checks, filtering, and serialization all run exactly as they do
for the normal REST API. New tools should wrap an existing (or new) API view via `call_view()`
rather than reimplementing filtering/serialization by hand.

`call_view()` also enforces the `MCP_READ_ONLY` plugin setting (default `True`): any non-GET call
raises `ToolError` unless an administrator has turned it off. This is a shared chokepoint - don't
duplicate the check per-tool or bypass `call_view()`.

## File map

- `context.py` - contextvar carrying the authenticated Django user into tool execution.
- `proxy.py` - `call_view()`, the permission boundary described above.
- `settings.py` - `get_plugin_setting()` for this plugin's own settings (`REQUIRE_AUTH`,
  `MCP_READ_ONLY`).
- `mcp_server.py` - the `MCPServer` instance; imports `tools/*` for their `@mcp.tool()` side effect.
- `mcp_transport.py` - Django view bridging Streamable HTTP onto the MCP server.
- `oauth2_bridge.py` - lets `call_view()` present an OAuth2-authenticated request's real token to
  the proxied view, and works around upstream InvenTree OAuth2 scope-enforcement bugs.
- `tools/` - one module per resource, each a thin async wrapper around `call_view()`;
  `discovery.py` holds `describe_filters()`.
- `schema_introspection.py` / `output_schemas.py` - derive each tool's MCP `outputSchema` from the
  real DRF serializer.
- `filter_introspection.py` - derives each list tool's `filters` argument from a view's real
  `filterset_class`/`search_fields`/`ordering_fields`.
- `expand_introspection.py` - derives optional output-expansion flags (e.g. `part_detail`) from a
  view's real `output_options`.
- `core.py` - the `InvenTreePlugin` subclass, `REQUIRE_AUTH` and `MCP_READ_ONLY` settings.

## Testing

```
# from this repo, inside the InvenTree devcontainer:
./run_tests.sh                                            # full suite, --keepdb
./run_tests.sh inventree_mcp.test_mcp.MCPTransportTest     # one class
```

Equivalent by hand:

```
pip install -e dev/InvenTreeMCP --no-deps
invoke dev.test -r inventree_mcp.test_mcp.MCPToolPermissionTest
```

CI (`.github/workflows/ci.yaml`) runs `ruff check` + a build check, then a matrix job against the
real `inventree/inventree:stable` and `:latest` Docker images with Postgres.

## Lint/format

```
ruff check --preview inventree_mcp
ruff format --preview inventree_mcp
```

`--preview` matches `.pre-commit-config.yaml`, which pins `ruff==0.12.0`.
