# InvenTreeMCP

[![CI](https://github.com/inventree/inventree-mcp/actions/workflows/ci.yaml/badge.svg)](https://github.com/inventree/inventree-mcp/actions/workflows/ci.yaml)
[![codecov](https://codecov.io/gh/inventree/inventree-mcp/graph/badge.svg)](https://codecov.io/gh/inventree/inventree-mcp)

An MCP (Model Context Protocol) server for InvenTree, exposed as an InvenTree plugin. It lets MCP
clients (Claude Desktop, other MCP-aware agents) query InvenTree inventory data over a Streamable
HTTP endpoint.

## Design

Every tool is a thin wrapper around InvenTree's own REST API view classes (see
[`inventree_mcp/proxy.py`](inventree_mcp/proxy.py)), dispatched as the authenticated caller. This
means MCP requests go through exactly the same `RolePermission` / `ModelPermission` checks,
filtering, and serialization as the regular REST API - a user can never see or do more via MCP
than they could via the normal API. Tool code must never query the Django ORM directly; add new
tools by wrapping an existing (or new) API view, not by reimplementing queries.

Currently read-only: parts, stock items, stock locations, part categories, purchase orders, sales
orders, return orders, build orders (each with list + detail, plus line items, and - for sales/
build orders - stock allocations), companies, contacts, addresses, manufacturer parts, supplier
parts, BOM items and substitutes, attachments, parameters (with parameter templates), stock
tracking history, stock item test results, and project codes. No write tools are implemented yet -
and when they are, the `MCP_READ_ONLY` setting (see Configuration below) blocks any write action by
default regardless of the calling user's permissions, as a second layer on top of per-user roles.

Attachments and parameters are generic - they can be linked to almost any InvenTree record (a
part, a stock item, an order, ...) rather than being tied to one resource type - see
[`inventree_mcp/tools/attachments.py`](inventree_mcp/tools/attachments.py) and
[`inventree_mcp/tools/parameters.py`](inventree_mcp/tools/parameters.py) for the exact
`model_type`/`model_id` scoping mechanism (the two use different `model_type` string formats -
documented in each module).

Each `outputSchema` is generated from the real InvenTree serializer (not hand-maintained), so it
can't drift from the actual API shape as InvenTree evolves. Every list tool also takes `ordering`
(sort by a field, e.g. `"-in_stock"` for descending - combine with `limit` for a "top N by X"
result) and a `filters` argument for anything else, merged directly into the real API's query
parameters - call the `describe_filters` tool (e.g. `describe_filters("part")`) to see what's
available for a given resource: every field InvenTree's own filter/search/ordering options support,
read live from the same definitions the REST API uses.

The set of tools an MCP client sees (via `tools/list`) is filtered to what the calling user (and,
for OAuth2 requests, their token's scope) can actually use - a tool only appears if its underlying
API endpoint's real permission check succeeds for that caller. This is a discovery-time convenience
only, not a substitute for the permission enforcement described above: every tool call is still
checked for real, in full, regardless of what was advertised.

## Installation

### InvenTree Plugin Manager

... todo ...

### Command Line

To install manually via the command line, run the following command:

```bash
pip install inventree-mcp
```

## Configuration

The plugin has two settings, available under **Settings > Plugin Settings**:

- **Require Authentication** (`REQUIRE_AUTH`, default `True`): reject unauthenticated requests to
  the MCP endpoint. Only disable this for local testing.
- **Read Only** (`MCP_READ_ONLY`, default `True`): block all write actions via the MCP endpoint,
  regardless of the calling user's permissions. An administrator must explicitly disable this
  before any write tool can do anything - it's a plugin-wide kill switch independent of per-user
  roles, not a replacement for them.

Access is controlled by the calling user's normal InvenTree role assignments (Settings > User
Roles). For agent/service use, create a dedicated user with only the roles the agent actually
needs (e.g. `part.view`, `stock.view`), rather than reusing an admin account.

## Usage

The MCP endpoint is available at:

```
<your-inventree-server>/plugin/inventree-mcp/mcp/
```

Configure your MCP client to connect to this URL using Streamable HTTP transport, authenticating
with one of:

- An InvenTree API token: `Authorization: Token <token>`, belonging to a user with the
  appropriate roles.
- Basic auth (username/password).
- An OAuth2 access token: `Authorization: Bearer <token>`. If the token carries granular scopes
  (e.g. `r:view:part`), those scopes narrow access *below* whatever the underlying user's roles
  would otherwise allow - use this to issue an agent a token scoped more tightly than a full
  service-account user, without creating a separate low-privilege user for every agent.

Session/cookie auth is not supported for this endpoint (not meaningful for a machine client).
