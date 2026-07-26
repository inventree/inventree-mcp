# AGENTS.md

This is an InvenTree plugin (entry point `inventree_mcp.core:InvenTreeMCP`) exposing an MCP
(Model Context Protocol) server over Streamable HTTP so MCP clients can query InvenTree data.
It only runs installed inside a real InvenTree instance - there is no standalone dev server for
this repo. The broader plan/rationale lives at `dev/todo/mcp.md` in the main InvenTree checkout.

## Core design rule

Tool code (`inventree_mcp/tools/*.py`) must never touch the Django ORM directly. Every tool calls
`proxy.call_view()`, which dispatches through the *real* InvenTree DRF view class (e.g.
`part.api.PartList`) as the authenticated caller - via `APIRequestFactory` + `force_authenticate`
for token/basic auth, or (see the OAuth2 section below) a re-presented real token for OAuth2. This
means `RolePermission`/`ModelPermission`/OAuth2 scope checks, filtering, and serialization all run
exactly as they do for the normal REST API - a caller can never see or do more via MCP than via
the regular API. This is the one thing the community reference plugin
(Fires04/inventree-mcp-plugin) gets wrong (it bypasses permissions entirely), and the reason this
plugin exists as a separate, from-scratch implementation rather than a fork.

New tools: pick (or add) an existing API view class and wrap it with `call_view()`. Don't
reimplement filtering/serialization by hand.

`call_view()` is also where the `MCP_READ_ONLY` plugin setting (default `True`) is enforced: any
non-GET call raises `ToolError` before it ever reaches the view, unless an administrator has
explicitly turned it off. No write tools exist yet, but this gate already applies to every future
one automatically since it lives in the shared chokepoint - don't duplicate the check per-tool,
and don't bypass `call_view()` for a "just this once" write.

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
- `tools/` - one module per resource (`parts.py`, `stock.py`, `locations.py`, `categories.py`),
  each a thin async wrapper around `call_view()`.
- `core.py` - the `InvenTreePlugin` subclass (`UrlsMixin` + `SettingsMixin`), `REQUIRE_AUTH` and
  `MCP_READ_ONLY` settings.

## The async/threading gotcha (read this before touching proxy.py or mcp_transport.py)

FastMCP calls tool functions directly in the request's event loop (it does not offload sync
functions to a thread), so tool functions are `async def` and the actual Django/DRF work in
`proxy._call_view_sync` has to be bridged in via `asgiref.sync.sync_to_async`. Getting the bridge
wrong is easy and the failure modes are nasty:

- `mcp_transport.py`'s `MCPView.dispatch()` must invoke the async handler via
  `asgiref.sync.async_to_sync(...)`, not a hand-rolled `asyncio.new_event_loop()` +
  `run_until_complete()`. Only `async_to_sync` establishes the "thread-sensitive" context that a
  nested `sync_to_async` call can route back onto.
- `proxy.call_view()` must use `sync_to_async(_call_view_sync)` with the **default**
  `thread_sensitive=True`. Using `thread_sensitive=False` looks like it works (it fixes the
  `SynchronousOnlyOperation` error and passes a quick manual smoke test) but actually runs the
  Django/DRF call on a *different* thread with its *own* DB connection. Under a plain dev server
  this mostly goes unnoticed; under Django's `TestCase` it's actively broken - fixture data
  created in `setUpTestData` lives in an uncommitted transaction only the *original* thread's
  connection can see, so permission checks silently see a user with no roles, and on Postgres a
  second connection contending for the same rows can hang for a very long time (this exact bug
  cost a ~40 minute hung test run before being tracked down - see git history / conversation for
  the full trace).

If you change either of these, run the test suite (not just a manual curl/client smoke test) -
the transaction-isolation bug does not reproduce against a live dev server, only against
`TestCase`.

## OAuth2 authentication (read this before touching mcp_transport.py's auth setup or oauth2_bridge.py)

MCP clients can authenticate with an InvenTree `ApiToken`, Basic auth, or an OAuth2 bearer token.
Making OAuth2 actually work end-to-end (not just "authenticate" but have the token's *scope*
correctly narrow access below the user's role) took four separate, non-obvious fixes. All were
verified with real tokens against a real running server, not just unit tests - several of these
bugs only reproduce over a genuine HTTP request, not via `APIRequestFactory` or Django's test
`Client` (which disables CSRF checks by default, masking one of them). If you touch this area,
retest with real curl + the real `mcp` client SDK, not just `invoke dev.test`.

1. **`MCPView` must be `auth_exempt`.** `InvenTree.middleware.AuthRequiredMiddleware` runs before
   any OAuth2 handling and only recognizes session cookies and InvenTree's own `ApiToken`. It also
   gates on a fixed path allowlist (`paths_own_security` in `InvenTree/middleware.py`) that
   includes `/api/` but not `/plugin/`. Without `auth_exempt`, an OAuth2 bearer token gets a plain
   401 before `dispatch()` ever runs - verified directly: `/api/part/` accepts the same token,
   `/plugin/inventree-mcp/mcp/` doesn't. `auth_exempt` (`InvenTree.permissions.auth_exempt`) is
   applied to the URL pattern, not the view class: `path("mcp/", auth_exempt(MCPView.as_view()), ...)`.

2. **`SessionAuthentication` must be excluded from `MCPView.authentication_classes`.**
   `oauth2_provider.middleware.OAuth2TokenMiddleware` is already in InvenTree's `MIDDLEWARE` and
   runs *after* `AuthRequiredMiddleware` - meaning it still runs for our `auth_exempt` view (that
   only skips `AuthRequiredMiddleware` itself, not the rest of the chain) and resolves the Bearer
   token, setting the *raw* Django `request.user` before our view's own DRF authentication even
   starts. DRF's `SessionAuthentication.authenticate()` then sees an already-active user on
   `request._request.user` and treats it as session auth, calling `enforce_csrf()` - which fails
   with `PermissionDenied("CSRF Failed: CSRF cookie not set.")`, since a Bearer client never sends
   a CSRF token. `SessionAuthentication` is in DRF's `DEFAULT_AUTHENTICATION_CLASSES` and runs
   *before* `ExtendedOAuth2Authentication` in that list, so it intercepts every OAuth2 request
   before OAuth2Authentication gets a chance. Fix: set `MCPView.authentication_classes` explicitly
   to `[ApiTokenAuthentication, BasicAuthentication, ExtendedOAuth2Authentication]` - MCP is
   machine-to-machine and has no legitimate use for session auth anyway.

3. **Cache `request.body` before calling `self.initialize_request()`.** DRF's `Request` wrapping
   touches the underlying stream during authentication; a later `request.body` access on the raw
   Django request (which `_handle_mcp_request` needs, to build the ASGI scope) then raises
   `RawPostDataException: You cannot access body after reading from request's data stream`. Fix is
   one line at the top of `dispatch()`: `_ = request.body` before `initialize_request()` is called,
   forcing Django to cache it first.

4. **Two independent InvenTree core bugs in the OAuth2 *scope* permission chain** (not
   authentication - a validly-authenticated OAuth2 request still can't get a correct scope
   decision without the workarounds in `oauth2_bridge.py`). Both reproduced directly against
   `part.api.PartList` with no plugin code involved - they affect the real REST API too:
   - `InvenTree.permissions.OASTokenMixin.check_oauth2_authentication()` instantiates the *raw*
     `oauth2_provider.TokenMatchesOASRequirements()` directly instead of going through `self`
     (which has InvenTree's dynamic `get_required_alternate_scopes()` override). Since no
     InvenTree view defines a static `required_alternate_scopes` attribute, this raises
     `ImproperlyConfigured` for *any* genuinely OAuth2-authenticated request, regardless of scope.
   - Once that's worked around, a token that's authenticated but lacks the required scope should
     be cleanly denied - instead `OASTokenMixin.has_permission()`'s fallback branch
     (`... or super().has_permission(request, view)`) crashes with
     `AttributeError: 'super' object has no attribute 'has_permission'`, because neither
     `OASTokenMixin` nor its subclasses in this chain actually inherit from
     `rest_framework.permissions.BasePermission`.

   `oauth2_bridge.scoped_view_class()` works around both by building a throwaway subclass of the
   proxied view with a pre-computed `required_alternate_scopes` (calling InvenTree's own dynamic
   resolver directly, not reimplementing it) and a corrected permission class swapped in for
   `InvenTreeTokenMatchesOASRequirements`. `authentication_classes_for()` separately re-presents
   the already-validated `(user, token)` pair from the *original* MCP request to the proxied view
   as a genuine `OAuth2Authentication` subclass - `force_authenticate()` can't be used for this
   path, since it always produces a synthetic `ForcedAuthentication` that
   `InvenTree.permissions.is_oauth2ed()` doesn't recognize as OAuth2 at all, silently falling back
   to role-only checks and defeating the whole point of scope propagation.

   Remove the `oauth2_bridge.py` workarounds if/when the two core bugs are fixed upstream (they
   should ideally be reported/fixed in InvenTree itself, not permanently patched around here).

## Testing

```
# from the main InvenTree checkout, with dev/InvenTreeMCP installed editable:
pip install -e dev/InvenTreeMCP --no-deps
invoke dev.test -r inventree_mcp.test_mcp.MCPToolPermissionTest
```

Tests are `async def` methods on an `InvenTreeTestCase` subclass (Django 5.2 runs async test
methods natively). The point of `test_mcp.py` is permission enforcement: a user with the relevant
role succeeds, a user with none of the roles gets a `ToolError`, and a call with no bound user at
all fails closed (`PermissionError`). When binding a user in a test, use
`context.set_current_user(user)` + `self.addCleanup(context.set_current_user, None)` - **not**
`context.reset_current_user(token)` - because Django's async test wrapper runs `addCleanup`
callbacks outside the test coroutine's own `contextvars.Context`, and a `Token` can only be reset
in the exact `Context` that created it.

If a test needs `registry.get_plugin("inventree-mcp")` to actually resolve (e.g. to toggle
`MCP_READ_ONLY` via `plugin.set_setting(...)`), note that a pip-installed, entry-point-distributed
plugin is normally invisible to the registry during a test run at all. You need, in
`setUpTestData`: the class decorated with `@override_settings(PLUGIN_TESTING_SETUP=True)`, then
`registry.reload_plugins(full_reload=True, collect=True)` (makes the registry aware of it) *and*
`registry.set_plugin_state("inventree-mcp", True)` (`get_plugin()` filters on active by default).
Both are sync ORM calls - if done inside an `async def` test body rather than the sync
`setUpTestData`, wrap them in `sync_to_async` too (same reasoning as the threading section above).
See `MCPToolPermissionTest` in `test_mcp.py` for the working pattern.

To test against a real running dev server instead of/in addition to the test suite, mint a
short-lived API token for a real user (`users.models.ApiToken.objects.create(user=..., name=...)`,
then read `.key` off the created row) and drive the endpoint with the real `mcp` client SDK
(`mcp.client.streamable_http.streamablehttp_client` + `mcp.ClientSession`) rather than hand-rolled
JSON-RPC - the Streamable HTTP framing is easy to get subtly wrong by hand. Delete the token
afterwards.

## Lint/format

```
ruff check --preview inventree_mcp
ruff format --preview inventree_mcp
```

`--preview` matches `.pre-commit-config.yaml`. The pre-commit hook pins `ruff==0.12.0`, which is
*stricter* than whatever `ruff` you may have installed globally on E402 in `mcp_server.py` (the
deferred `from .tools import ...` at the bottom of that file needs `# noqa: E402,F401` - a newer
ruff doesn't flag E402 there, so if you drop the noqa because "ruff check passes," check it
against 0.12.0 specifically before assuming it's safe to remove).
