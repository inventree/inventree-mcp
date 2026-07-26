# InvenTreeMCP

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

Currently read-only: parts, stock items, stock locations, and part categories (list + detail).
Write tools are intentionally not implemented yet.

## Installation

### InvenTree Plugin Manager

... todo ...

### Command Line

To install manually via the command line, run the following command:

```bash
pip install inventree-mcp
```

## Configuration

The plugin has one setting, available under **Settings > Plugin Settings**:

- **Require Authentication** (`REQUIRE_AUTH`, default `True`): reject unauthenticated requests to
  the MCP endpoint. Only disable this for local testing.

There is no separate scoping setting - access is controlled entirely by the calling user's normal
InvenTree role assignments (Settings > User Roles). For agent/service use, create a dedicated user
with only the roles the agent actually needs (e.g. `part.view`, `stock.view`), rather than reusing
an admin account.

## Usage

The MCP endpoint is available at:

```
<your-inventree-server>/plugin/inventree-mcp/mcp/
```

Configure your MCP client to connect to this URL using Streamable HTTP transport, authenticating
with an InvenTree API token (`Authorization: Token <token>`) belonging to a user with the
appropriate roles.
