# AGENTS.md

InvenTree plugin (entry point `inventree_mcp.core:InvenTreeMCP`) exposing an MCP (Model Context
Protocol) server over Streamable HTTP so MCP clients can query InvenTree data. Only runs installed
inside a real InvenTree instance - no standalone dev server for this repo. Plan/rationale/progress
log lives at `dev/todo/mcp.md` in the main InvenTree checkout.

## Core design rule

Tool code (`inventree_mcp/tools/*.py`) must never touch the Django ORM directly. Every tool calls
`proxy.call_view()`, which dispatches through the *real* InvenTree DRF view class (e.g.
`part.api.PartList`) as the authenticated caller, so `RolePermission`/`ModelPermission`/OAuth2 scope
checks, filtering, and serialization all run exactly as they do for the normal REST API - a caller
can never see or do more via MCP than via the regular API. This is the reason the plugin is a
from-scratch implementation rather than a fork of the community reference plugin
(Fires04/inventree-mcp-plugin), which bypasses permissions entirely.

New tools: wrap an existing (or new) API view class with `call_view()`. Don't reimplement
filtering/serialization by hand.

`call_view()` also enforces the `MCP_READ_ONLY` plugin setting (default `True`): any non-GET call
raises `ToolError` before it reaches the view, unless an administrator has turned it off. This gate
covers every future write tool automatically since it lives in the shared chokepoint - don't
duplicate the check per-tool, and don't bypass `call_view()` for a "just this once" write.

## File map

- `context.py` - contextvar carrying the authenticated Django user into tool execution (tools
  don't receive a request object directly).
- `proxy.py` - `call_view()`, the permission boundary described above, including the
  `MCP_READ_ONLY` gate.
- `settings.py` - `get_plugin_setting()`, shared helper for reading this plugin's own settings
  (`REQUIRE_AUTH`, `MCP_READ_ONLY`); fails safe to the restrictive default if the plugin instance
  can't be resolved (e.g. not yet activated, or outside a real request).
- `mcp_server.py` - the `FastMCP` instance; imports `tools/*` for their `@mcp.tool()` side effect.
- `mcp_transport.py` - Django view bridging Streamable HTTP onto the FastMCP server.
- `oauth2_bridge.py` - lets `call_view()` present an OAuth2-authenticated request's *real* token to
  the proxied view (instead of a synthetic one), and works around two InvenTree core bugs that
  would otherwise break OAuth2 scope enforcement entirely. See the OAuth2 section below.
- `tools/` - one module per resource, each a thin async wrapper around `call_view()`;
  `discovery.py` holds `describe_filters()`, which isn't tied to one resource.
- `schema_introspection.py` / `output_schemas.py` - derive each tool's MCP `outputSchema` from the
  real DRF serializer instead of hand-maintaining one. See the output schema section below.
- `filter_introspection.py` - same idea, for *input* filtering: `describe_filterset()` reads a
  view's real `filterset_class`/`search_fields`/`ordering_fields` (or, if the view only declares
  `filterset_fields` with no full `filterset_class`, falls back to reading field types off the
  model) and backs `describe_filters()` plus every list tool's `filters` argument.
- `core.py` - the `InvenTreePlugin` subclass (`UrlsMixin` + `SettingsMixin`), `REQUIRE_AUTH` and
  `MCP_READ_ONLY` settings.

## The async/threading gotcha (read before touching proxy.py or mcp_transport.py)

FastMCP calls tool functions directly in the request's event loop (it does not offload sync
functions to a thread), so tool functions are `async def` and the actual Django/DRF work in
`proxy._call_view_sync` is bridged in via `asgiref.sync.sync_to_async`.

- `mcp_transport.py`'s `MCPView.dispatch()` must invoke the async handler via
  `asgiref.sync.async_to_sync(...)`, not a hand-rolled event loop - only `async_to_sync` establishes
  the "thread-sensitive" context a nested `sync_to_async` call can route back onto.
- `proxy.call_view()` must use `sync_to_async(_call_view_sync)` with the **default**
  `thread_sensitive=True`. `thread_sensitive=False` looks like it works (fixes a
  `SynchronousOnlyOperation` error, passes a manual smoke test) but runs the Django/DRF call on a
  *different* thread with its own DB connection - breaks silently under Django's `TestCase`
  (fixture data in `setUpTestData` is invisible to a second connection, so permission checks see a
  user with no roles) and can hang badly on Postgres under contention.

If you change either of these, run the test suite, not just a manual smoke test - this bug class
doesn't reproduce against a live dev server, only under `TestCase`.

## OAuth2 authentication (read before touching mcp_transport.py's auth setup or oauth2_bridge.py)

MCP clients can authenticate with an InvenTree `ApiToken`, Basic auth, or an OAuth2 bearer token.
Getting the token's *scope* to correctly narrow access below the user's role (not just
"authenticate") required four fixes:

1. **`MCPView` must be `auth_exempt`.** `InvenTree.middleware.AuthRequiredMiddleware` gates on a
   fixed path allowlist that includes `/api/` but not `/plugin/` - without `auth_exempt`, an OAuth2
   bearer token 401s before `dispatch()` ever runs. Applied to the URL pattern, not the view class:
   `path("mcp/", auth_exempt(MCPView.as_view()), ...)`.
2. **`SessionAuthentication` must be excluded from `MCPView.authentication_classes`.**
   `oauth2_provider.middleware.OAuth2TokenMiddleware` (already in InvenTree's `MIDDLEWARE`, runs
   after `AuthRequiredMiddleware`) resolves the Bearer token and sets the raw `request.user` before
   our view's own DRF authentication starts. `SessionAuthentication` then sees an already-active
   user, treats it as session auth, and calls `enforce_csrf()` - which fails, since a Bearer client
   never sends a CSRF token. Fix: set `authentication_classes` explicitly to
   `[ApiTokenAuthentication, BasicAuthentication, ExtendedOAuth2Authentication]`.
3. **Cache `request.body` before calling `self.initialize_request()`.** DRF's `Request` wrapping
   touches the underlying stream during authentication; a later raw-request `request.body` read
   (needed to build the ASGI scope) then raises `RawPostDataException`. Fix: `_ = request.body` at
   the top of `dispatch()`, before `initialize_request()`.
4. **Two independent InvenTree core bugs in the OAuth2 *scope* permission chain**, both reproducible
   directly against `part.api.PartList` with no plugin code involved:
   - `InvenTree.permissions.OASTokenMixin.check_oauth2_authentication()` instantiates the raw
     `oauth2_provider.TokenMatchesOASRequirements()` instead of going through `self` (which has
     InvenTree's dynamic `get_required_alternate_scopes()` override) - raises `ImproperlyConfigured`
     for any genuinely OAuth2-authenticated request.
   - `OASTokenMixin.has_permission()`'s fallback branch crashes with
     `AttributeError: 'super' object has no attribute 'has_permission'` instead of cleanly denying a
     wrong-scope token, because that mixin chain doesn't inherit from
     `rest_framework.permissions.BasePermission`.

   `oauth2_bridge.scoped_view_class()` works around both by building a throwaway subclass of the
   proxied view with a pre-computed `required_alternate_scopes` and a corrected permission class.
   `authentication_classes_for()` re-presents the already-validated `(user, token)` pair to the
   proxied view as a genuine `OAuth2Authentication` subclass - `force_authenticate()` can't be used
   here, since it produces a synthetic `ForcedAuthentication` that
   `InvenTree.permissions.is_oauth2ed()` doesn't recognize, silently falling back to role-only
   checks. Remove these workarounds if/when the two core bugs are fixed upstream (report/fix in
   InvenTree itself, don't leave this as a permanent patch).

Retest with real curl + the real `mcp` client SDK against a running server when touching this area,
not just `APIRequestFactory`/Django's test `Client` (which disables CSRF checks by default and can
mask bug #2).

## Output schemas (read before touching schema_introspection.py or output_schemas.py)

Tools return a bare `dict` (the real shape comes from whatever InvenTree serializer backs it), so
FastMCP's default `outputSchema` derivation produces nothing. `schema_introspection.py` builds a
JSON Schema directly from the real serializer's fields instead - best-effort, not exhaustive
(`SerializerMethodField`/`ReadOnlyField` fall back to an unconstrained `{}`).
`output_schemas.apply()` (called once from `mcp_server.py`, after the tool imports) attaches the
computed schema to each already-registered `Tool`.

- No public FastMCP API for a custom output schema - `apply()` reaches into
  `mcp._tool_manager.get_tool(name)` and sets `tool.fn_metadata.output_schema` directly, at import
  time (before `Tool.output_schema`'s `@cached_property` could be touched by a real request). Don't
  move this call to run lazily without re-checking that.
- **`fn_metadata.output_model` must be set alongside `output_schema`, not left `None`.** FastMCP
  separately validates every actual result against `output_model` when a tool is called, and asserts
  `"Output model must be set if output schema is defined"` otherwise. Use a permissive
  `pydantic.RootModel[dict[str, Any]]` here, not a model matching the schema field-for-field - the
  declared schema (informative) and the runtime validation model (permissive, never rejects real
  data) are deliberately different things.
- **Every concrete field type must be nullable unconditionally, regardless of DRF's `allow_null`.**
  `allow_null` governs input validation, not what `to_representation()` can actually emit - a field
  can be `allow_null=False` while its underlying DB column is nullable.
- **`ChoiceField`'s JSON type depends on its choices, not its field class** - InvenTree has
  integer-keyed choice fields (custom status codes) that serialize as numbers, not strings. Map
  `ChoiceField`/`MultipleChoiceField` permissively (`["string", "integer", "number", "boolean"]`)
  rather than assuming string.
- **`DecimalField`'s JSON type depends on which subclass/override is active, and `isinstance()`
  can't tell them apart** - a plain `DecimalField` defaults to DRF's `coerce_to_string=True` (string
  output), but `InvenTree.serializers.InvenTreeMoneySerializer` (every money field) is also a
  `DecimalField` subclass that overrides `to_representation()` to return a float. Accept both
  (`["string", "number"]`).
- **Nested single-object serializer fields (`*_detail`) need the same unconditional-nullable
  treatment as plain fields** - they're routinely `None` (FK unset, or an `OptionalField` whose
  detail wasn't requested).
- **Format-constrained string types (`uri`, `date-time`, `email`, ...) must not assume a non-null
  value always matches the format** - blank (`""`) is a common real value for optional URL/date
  fields in InvenTree, distinct from `None`, and breaks strict format validation on the client side.

This bug class only reproduces through `mcp.call_tool()` / a real MCP client call, or (stricter
still) a real HTTP request through `mcp.server.lowlevel.server.py`'s handler, which runs
`jsonschema.validate()` against the *declared* schema - `mcp.list_tools()` alone won't catch it
(schema declaration succeeds fine; only the *call* path breaks). See `OutputSchemaTest` in
`test_mcp.py` for the pattern, and prefer a live sweep of every list/get tool pair (not just the one
you're touching) over real HTTP after any change here - several of these bugs were only ever caught
that way, not by the existing unit tests.

## Filtering (read before touching filter_introspection.py or a list tool's `filters` handling)

Each list tool exposes a small, curated set of named parameters (`search`, `category`, `active`,
...) for the common cases, plus a catch-all `filters: dict[str, Any] | None` for everything else -
the real views support far more (`PartFilter` alone has 43 filters). `filters` is merged straight
into the query params sent to `call_view()` (`tools/_common.py`'s `build_query_params()`), so
anything the real DRF view accepts as a query string works here too - same permission/
`MCP_READ_ONLY` checks apply.

`describe_filters(resource)` (`tools/discovery.py`) is how an agent discovers what's available -
static metadata only, no DB access, safe regardless of role.

`build_query_params()` applies `filters` *before* setting `limit`/`offset` from the named
arguments, so a caller can't pass `filters={"limit": 99999}` to bypass `clamp_limit()`'s pagination
cap. Don't reorder that without preserving the equivalent protection.

## Testing

```
# from this repo, inside the InvenTree devcontainer:
./run_tests.sh                                            # full suite, --keepdb
./run_tests.sh inventree_mcp.test_mcp.MCPTransportTest     # one class
```

`run_tests.sh` installs this plugin editable into the devcontainer's InvenTree venv, sets
`INVENTREE_PLUGINS_MANDATORY=inventree-mcp` (activates it without the `PLUGIN_TESTING_SETUP` dance
below - CI uses the same env var), and runs InvenTree's own `manage.py test`. Equivalent by hand:

```
pip install -e dev/InvenTreeMCP --no-deps
invoke dev.test -r inventree_mcp.test_mcp.MCPToolPermissionTest
```

CI (`.github/workflows/ci.yaml`) runs `ruff check` + a build check, then a matrix job against the
real `inventree/inventree:stable` and `:latest` Docker images with a Postgres service, installing
this plugin editable and running `manage.py test inventree_mcp.test_mcp` inside the container. Runs
on push/PR to `main` plus a weekly cron, to catch upstream InvenTree breakage between manual runs.

Tests are `async def` methods on an `InvenTreeTestCase` subclass. The point of `test_mcp.py` is
permission enforcement: a user with the relevant role succeeds, a user with none of the roles gets a
`ToolError`, and a call with no bound user at all fails closed (`PermissionError`). When binding a
user in a test, use `context.set_current_user(user)` + `self.addCleanup(context.set_current_user,
None)` - **not** `context.reset_current_user(token)` - because Django's async test wrapper runs
`addCleanup` callbacks outside the test coroutine's own `contextvars.Context`, and a `Token` can
only be reset in the exact `Context` that created it.

If a test needs `registry.get_plugin("inventree-mcp")` to resolve (e.g. to toggle `MCP_READ_ONLY`
via `plugin.set_setting(...)`), note that a pip-installed, entry-point-distributed plugin is
normally invisible to the registry during a test run. You need, in `setUpTestData`: the class
decorated with `@override_settings(PLUGIN_TESTING_SETUP=True)`, then
`registry.reload_plugins(full_reload=True, collect=True)` *and*
`registry.set_plugin_state("inventree-mcp", True)` (`get_plugin()` filters on active by default).
Both are sync ORM calls - wrap in `sync_to_async` if called from an `async def` test body. See
`MCPToolPermissionTest` for the working pattern.

To test against a real running dev server instead of/in addition to the test suite, mint a
short-lived API token for a real user (`users.models.ApiToken.objects.create(user=..., name=...)`,
read `.key` off the created row) and drive the endpoint with the real `mcp` client SDK
(`mcp.client.streamable_http.streamablehttp_client` + `mcp.ClientSession`) rather than hand-rolled
JSON-RPC. Delete the token afterwards.

### What's covered where

Most tests call tool functions directly (`await list_parts(...)`) with a user bound via
`context.set_current_user()` - fast, but bypasses `MCPView.dispatch()` entirely.
`MCPTransportTest` covers what that misses:

- Anything about `dispatch()` itself - auth resolution, the `REQUIRE_AUTH` gate, exception handling.
- The three OAuth2 transport bugs above (`auth_exempt`, `SessionAuthentication` exclusion, body
  caching) - these only reproduce over a request through Django's real middleware stack with CSRF
  enforcement genuinely active. `MCPTransportTest` uses `Client(enforce_csrf_checks=True)` for this
  reason - the default `Client()` disables CSRF checking and would silently pass even with
  `SessionAuthentication` back in the authenticator list. Re-run
  `MCPTransportTest.test_oauth2_bearer_auth_succeeds` (or retest live) after touching
  `mcp_transport.py`'s auth handling.

Everything else (`schema_introspection.py`, `filter_introspection.py`, `_common.py`) has direct
unit tests, since they're pure functions with no Django request/DB dependency.

## Lint/format

```
ruff check --preview inventree_mcp
ruff format --preview inventree_mcp
```

`--preview` matches `.pre-commit-config.yaml`, which pins `ruff==0.12.0` - stricter than whatever
`ruff` you may have installed globally on E402 in `mcp_server.py` (the deferred
`from .tools import ...` at the bottom needs `# noqa: E402,F401`). Check against 0.12.0 specifically
before assuming the noqa is safe to drop.
