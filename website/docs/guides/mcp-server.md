---
sidebar_position: 7
---

# MCP server (graphlens-mcp)

graphlens is **only an analysis engine** — it parses code into a graph IR and
stops there (see [Scope & non-goals](../intro.md)). It deliberately ships no
long-running server and no agent runtime.

If you want to serve a graph to coding agents (Claude Code, Cursor, and
compatible clients) over the
[Model Context Protocol](https://modelcontextprotocol.io/), use the dedicated
project built on top of this engine:

> **[graphlens-mcp](https://github.com/Neko1313/graphlens-mcp)** — a free,
> MIT-licensed MCP server.
> Docs: [neko1313.github.io/graphlens-mcp](https://neko1313.github.io/graphlens-mcp/)

It is a thin runtime layer over `graphlens`: the engine provides the mechanisms
(parsing, stable node identity, resolvers); `graphlens-mcp` owns the storage,
the freshness model (a filesystem watcher that re-indexes the connected set on
every edit) and the agent-facing tool surface — everything graphlens itself is
intentionally not.

## Quickstart

```bash
uv tool install graphlens-mcp          # or: pipx install graphlens-mcp
cd your-project && graphlens-mcp init  # index + configure your agent
```

`init` detects the project's languages, indexes the code into a local graph,
writes the MCP server entry into your agent's config and installs a navigation
skill. Your agent then launches the server itself; restart it and ask things
like *"what breaks if I change the signature of `create_order`?"*.

## Agent tools

| Tool | Purpose |
|---|---|
| `search_symbols` | Full-text search over symbol names — **start here** |
| `get_node_info` | Source snippet + signature + location for a node |
| `get_file_structure` | Symbol outline of a file |
| `get_callees` | What a function calls (outgoing, up to `max_depth`) |
| `get_callers` | Who calls a function — primary impact-analysis tool |
| `get_neighbors` | Nodes within N hops in any direction |
| `find_references` | Non-call usages (type annotations, assignments) |
| `get_cross_language_calls` | Connections across service boundaries (HTTP/gRPC/queues) |

See the [graphlens-mcp documentation](https://neko1313.github.io/graphlens-mcp/)
for the full command set, the freshness model, supported languages and the agent
configuration flags.

## Using the engine yourself

`graphlens-mcp` is also a worked **example of how to build on the engine**: how
to drive the adapter registry, persist and refresh the graph, and expose it to a
consumer. If you are building your own tool on top of graphlens, it is a good
reference. Start from the [Library API guide](./library-api.md).
