---
sidebar_position: 7
---

# MCP server

The `mcp` command exposes a saved graph to LLM agents over the
[Model Context Protocol](https://modelcontextprotocol.io/) as a set of query
tools. Instead of dumping an entire codebase into a prompt, an agent can ask
graphlens precise questions — "who calls this?", "what does this talk to?" — and
get structured answers.

## Install and run

```bash
pip install "graphlens-cli[mcp]"

# First produce a graph
graphlens analyze ./my-project --output graph.json

# Then serve it (stdio transport)
graphlens mcp --graph graph.json
```

| Option | Default | Description |
|---|---|---|
| `--graph`, `-g` | — | Path to a graph JSON file from `analyze --output` (required) |

The server speaks MCP over stdio, which is what desktop agent clients expect.

## Exposed tools

| Tool | Returns |
|---|---|
| `stats` | node/relation counts by kind and the resolver status |
| `find` | nodes whose name matches a query |
| `callers` | functions/methods that call a node |
| `callees` | functions/methods a node calls |
| `references` | nodes that reference a node |
| `neighbors` | nodes within `depth` hops of a node |
| `boundaries` | every cross-language boundary with its exposers and consumers |
| `communicates_with` | consumer → provider edges across languages |

Node results are returned as compact dicts (`id`, `kind`, `qualified_name`,
`name`, `file_path`), which keeps responses small enough to fit comfortably in
an agent's context.

## Wiring it into a client

Most MCP clients are configured with a command to launch. Point the client at
the `graphlens mcp` invocation, for example:

```json
{
  "mcpServers": {
    "graphlens": {
      "command": "graphlens",
      "args": ["mcp", "--graph", "/absolute/path/to/graph.json"]
    }
  }
}
```

Regenerate `graph.json` (with `graphlens analyze --output`) whenever the code
changes so the agent is querying a current graph — for example as a step in the
same [CI job](../ci-integration/overview.md) that indexes the repository.

## Why serve a graph instead of files?

- **Precision** — the agent gets resolved `CALLS`/`REFERENCES` edges, not a
  best-effort text search.
- **Token efficiency** — answers are small structured lists, not file dumps.
- **Cross-language awareness** — `communicates_with` and `boundaries` surface
  relationships no single-language tool can see.
