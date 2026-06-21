---
sidebar_position: 5
---

# CLI reference

The `graphlens` command, provided by `graphlens-cli`. This page is the exhaustive
flag reference; for a task-oriented tour see the [CLI guide](../guides/cli.md).

```bash
pip install "graphlens-cli[all]"
graphlens --help
```

The app runs no-args-is-help, so `graphlens` with no subcommand prints usage.

---

## `graphlens analyze`

Parse a project and print statistics or serialize the graph.

```bash
graphlens analyze ROOT [OPTIONS]
```

| Argument / Option | Type | Default | Description |
|---|---|---|---|
| `ROOT` | path | — | Project root to analyse (must exist, be a directory) |
| `--lang` | str | `auto` | Adapter(s) to use: `auto` \| `python` \| `typescript` \| comma-separated |
| `--format`, `-f` | str | `text` | Output format: `text` (stats) or `json` (serialized graph) |
| `--output`, `-o` | path | — | Write the serialized graph (JSON) to this path |
| `--strict` | flag | off | Exit non-zero if the resolver status is not `ok` |

---

## `graphlens query`

Run a query against a saved graph.

```bash
graphlens query NODE --graph GRAPH [OPTIONS]
```

| Argument / Option | Type | Default | Description |
|---|---|---|---|
| `NODE` | str | — | Node id, qualified name, or short name |
| `--graph`, `-g` | path | — | Path to a graph JSON file (from `analyze --output`) — required |
| `--op` | str | `callers` | `callers` \| `callees` \| `references` \| `neighbors` |
| `--depth` | int | `1` | Hop depth for the `neighbors` operation |

---

## `graphlens visualize`

Build an interactive HTML graph viewer.

```bash
graphlens visualize ROOT [OPTIONS]
```

| Argument / Option | Type | Default | Description |
|---|---|---|---|
| `ROOT` | path | — | Project root to analyse |
| `--lang` | str | `auto` | Adapter(s) to use |
| `--output`, `-o` | path | `graph-<name>.html` | Output HTML file |
| `--no-open` | flag | off | Do not open the browser automatically |
| `--show-external` | flag | off | Include stdlib/third-party external symbol nodes |
| `--show-structure` | flag | off | Include `CONTAINS`/`DECLARES` structural edges |
| `--max-nodes` | int | `1500` | Prune low-degree nodes above this count (min 1) |

---

## `graphlens neo4j`

Export the graph to a Neo4j database. Requires the `neo4j` extra
(`pip install "graphlens-cli[neo4j]"`).

```bash
graphlens neo4j ROOT [OPTIONS]
```

| Argument / Option | Type | Default | Description |
|---|---|---|---|
| `ROOT` | path | — | Project root to analyse |
| `--lang` | str | `auto` | Adapter(s) to use |
| `--uri` | str | `bolt://localhost:7687` | Neo4j Bolt URI |
| `--user` | str | `neo4j` | Neo4j username |
| `--password` | str | `password` | Neo4j password |
| `--wipe` / `--no-wipe` | flag | `--no-wipe` | Wipe `:Code` nodes first |
| `--batch-size` | int | `500` | Items per Cypher batch (min 1) |

---

## `graphlens mcp`

Serve a saved graph to agents over the Model Context Protocol. Requires the
`mcp` extra (`pip install "graphlens-cli[mcp]"`).

```bash
graphlens mcp --graph GRAPH
```

| Option | Type | Default | Description |
|---|---|---|---|
| `--graph`, `-g` | path | — | Path to a graph JSON file (from `analyze --output`) — required |

Exposed MCP tools: `stats`, `find`, `callers`, `callees`, `references`,
`neighbors`, `boundaries`, `communicates_with`. See the
[MCP server guide](../guides/mcp-server.md).
