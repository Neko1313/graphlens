---
sidebar_position: 2
---

# The graphlens CLI

`graphlens-cli` installs the `graphlens` command — a [Typer](https://typer.tiangolo.com/)
app with five subcommands. This page is a practical tour; the
[CLI API reference](../api-reference/cli.md) lists every flag and default.

```bash
pip install "graphlens-cli[all]"
graphlens --help
```

| Command | Purpose |
|---|---|
| [`analyze`](#analyze) | Parse a project; print stats or serialize the graph to JSON |
| [`query`](#query) | Run callers/callees/references/neighbors on a saved graph |
| [`visualize`](#visualize) | Build an interactive HTML graph viewer |
| [`neo4j`](#neo4j) | Export the graph to a Neo4j database |
| [`mcp`](#mcp) | Serve a saved graph to agents over the Model Context Protocol |

## Selecting languages

`analyze`, `visualize`, and `neo4j` accept a `--lang` option. The default,
`auto`, asks the registry for every installed adapter and keeps the ones whose
`can_handle()` returns true for your project. You can also name adapters
explicitly, comma-separated, to analyze a polyglot repository in one pass:

```bash
graphlens analyze ./monorepo --lang python,typescript
```

## analyze

```bash
# Print node/relation statistics
graphlens analyze ./my-project

# Serialize the graph to JSON (the indexing step for CI and agents)
graphlens analyze ./my-project --output graph.json
graphlens analyze ./my-project --format json        # JSON to stdout

# Fail the command if the resolver did not complete
graphlens analyze ./my-project --strict
```

| Option | Default | Description |
|---|---|---|
| `--lang` | `auto` | Adapter(s) to use, comma-separated |
| `--format`, `-f` | `text` | `text` (stats) or `json` (serialized graph) |
| `--output`, `-o` | — | Write the serialized JSON graph to this path |
| `--strict` | off | Exit non-zero if the resolver status is not `ok` |

`--strict` is the flag that makes graphlens safe to gate a pipeline on — see
[CI integration](../ci-integration/overview.md).

## query

Operate on a graph you already saved with `analyze --output`:

```bash
graphlens query process_order --graph graph.json --op callers
graphlens query process_order --graph graph.json --op callees
graphlens query OrderService.save --graph graph.json --op references
graphlens query OrderService.save --graph graph.json --op neighbors --depth 2
```

| Option | Default | Description |
|---|---|---|
| `node` (argument) | — | Node id, qualified name, or short name |
| `--graph`, `-g` | — | Path to a graph JSON file (required) |
| `--op` | `callers` | `callers` \| `callees` \| `references` \| `neighbors` |
| `--depth` | `1` | Hop depth for the `neighbors` operation |

The node argument is resolved by id first, then by qualified or short name, so
you can pass whichever you have on hand.

## visualize

Produce a self-contained HTML file (powered by vis.js) and open it in your
browser:

```bash
graphlens visualize ./my-project
graphlens visualize ./my-project --show-external --max-nodes 500
graphlens visualize . --output graph.html --no-open
```

| Option | Default | Description |
|---|---|---|
| `--lang` | `auto` | Adapter(s) to use |
| `--output`, `-o` | `graph-<name>.html` | Output HTML file |
| `--no-open` | off | Do not open the browser automatically |
| `--show-external` | off | Include stdlib / third-party external symbol nodes |
| `--show-structure` | off | Include `CONTAINS` / `DECLARES` structural edges |
| `--max-nodes` | `1500` | Prune low-degree nodes above this count |

See the dedicated [visualization guide](./visualization.md) for the viewer's
interactions (search, filters, **Show callers** focus mode).

## neo4j

Export straight into a Neo4j database with `UNWIND … MERGE` Cypher (no APOC
required). Install the exporter dependency first:

```bash
pip install "graphlens-cli[neo4j]"

graphlens neo4j ./my-project \
  --uri bolt://localhost:7687 --user neo4j --password secret

graphlens neo4j . --wipe --batch-size 200
```

| Option | Default | Description |
|---|---|---|
| `--lang` | `auto` | Adapter(s) to use |
| `--uri` | `bolt://localhost:7687` | Neo4j Bolt URI |
| `--user` | `neo4j` | Neo4j username |
| `--password` | `password` | Neo4j password |
| `--wipe` / `--no-wipe` | `--no-wipe` | Wipe existing `:Code` nodes first |
| `--batch-size` | `500` | Items per Cypher batch |

See the [Neo4j guide](./neo4j.md) for the resulting schema and example queries.

## mcp

Serve a saved graph to LLM agents over the Model Context Protocol. Install the
MCP dependency first:

```bash
pip install "graphlens-cli[mcp]"
graphlens mcp --graph graph.json
```

| Option | Default | Description |
|---|---|---|
| `--graph`, `-g` | — | Path to a graph JSON file (required) |

See the [MCP server guide](./mcp-server.md) for the exposed tools and how to
wire it into an agent client.

## Exit codes

Commands exit non-zero on errors (bad arguments, missing adapter, unreadable
graph). `analyze --strict` additionally exits non-zero when the resolver status
is not `ok`, which is what lets you fail a CI job on a degraded graph.
