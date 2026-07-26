# AGENTS.md

This is an InvenTree plugin (entry point `inventree_mcp.core:InvenTreeMCP`) exposing an MCP
(Model Context Protocol) server over Streamable HTTP so MCP clients can query InvenTree data.
It only runs installed inside a real InvenTree instance - there is no standalone dev server for
this repo. The broader plan/rationale lives at `dev/todo/mcp.md` in the main InvenTree checkout.

## Core design rule

Tool code (`inventree_mcp/tools/*.py`) must never touch the Django ORM directly. Every tool calls
`proxy.call_view()`, which dispatches through the *real* InvenTree DRF view class (e.g.
`part.api.PartList`) as the authenticated caller, via `APIRequestFactory` + `force_authenticate`.
This means `RolePermission`/`ModelPermission`, filtering, and serialization all run exactly as
they do for the normal REST API - a caller can never see or do more via MCP than via the regular
API. This is the one thing the community reference plugin (Fires04/inventree-mcp-plugin) gets
wrong (it bypasses permissions entirely), and the reason this plugin exists as a separate,
from-scratch implementation rather than a fork.

New tools: pick (or add) an existing API view class and wrap it with `call_view()`. Don't
reimplement filtering/serialization by hand.

## File map

- `context.py` - contextvar carrying the authenticated Django user into tool execution (tools
  don't receive a request object directly).
- `proxy.py` - `call_view()`, the permission boundary described above.
- `mcp_server.py` - the `FastMCP` instance; imports `tools/*` for their `@mcp.tool()` side effect.
- `mcp_transport.py` - Django view bridging Streamable HTTP onto the FastMCP server.
- `tools/` - one module per resource (`parts.py`, `stock.py`, `locations.py`, `categories.py`),
  each a thin async wrapper around `call_view()`.
- `core.py` - the `InvenTreePlugin` subclass (`UrlsMixin` + `SettingsMixin`), `REQUIRE_AUTH`
  setting.

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
