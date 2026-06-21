---
sidebar_position: 5
---

# Neo4j export

The `neo4j` command writes the graph into a [Neo4j](https://neo4j.com/) database
so you can explore it with Cypher and the Neo4j Browser. It uses plain
`UNWIND … MERGE` Cypher and does **not** require APOC.

## Install and run

```bash
pip install "graphlens-cli[neo4j]"

graphlens neo4j ./my-project \
  --uri bolt://localhost:7687 \
  --user neo4j \
  --password secret
```

A quick local Neo4j with Docker:

```bash
docker run --rm -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/secret neo4j:5
```

## Options

| Flag | Default | Description |
|---|---|---|
| `--lang` | `auto` | Adapter(s) to use |
| `--uri` | `bolt://localhost:7687` | Neo4j Bolt URI |
| `--user` | `neo4j` | Username |
| `--password` | `password` | Password |
| `--wipe` / `--no-wipe` | `--no-wipe` | Delete existing `:Code` nodes before import |
| `--batch-size` | `500` | Items per Cypher batch |

Use `--wipe` for a clean reload; omit it to merge into whatever is already
there (nodes are `MERGE`d by id, so re-imports are idempotent).

## Schema

- Every node gets a generic `:Code` label **plus** a kind-specific label:
  `:Function`, `:Method`, `:Class`, `:Module`, `:ExternalSymbol`, `:Boundary`,
  and so on.
- A uniqueness constraint is created on `Code.id`.
- Relations are created grouped by kind, so a graphlens `CALLS` relation becomes
  a `[:CALLS]` edge, `REFERENCES` becomes `[:REFERENCES]`, etc.
- Scalar node metadata (including span fields) is stored as properties prefixed
  with `meta_`.

## Example queries

```cypher
// The 20 most-called functions
MATCH (f:Function)<-[:CALLS]-(caller)
RETURN f.qualified_name AS fn, count(caller) AS callers
ORDER BY callers DESC
LIMIT 20;
```

```cypher
// Everything a class reaches within 2 hops
MATCH path = (c:Class {name: 'OrderService'})-[*1..2]-(n)
RETURN path;
```

```cypher
// Cross-language communication (after running graphlens-link)
MATCH (consumer)-[r:COMMUNICATES_WITH]->(provider)
RETURN consumer.qualified_name, provider.qualified_name, r.mechanism;
```

```cypher
// Third-party surface area
MATCH (e:ExternalSymbol {meta_origin: 'third_party'})<-[:RESOLVES_TO|CALLS|HAS_TYPE]-(n)
RETURN e.name, count(n) AS uses
ORDER BY uses DESC;
```

## Programmatic export

Prefer to drive it from Python? Analyze, then store the graph through a backend
that implements the `GraphBackend` contract (`store` / `clear`). The repository's
[`examples/neo4j_export.py`](https://github.com/Neko1313/graphlens/blob/main/examples/neo4j_export.py)
shows a complete export-and-query script you can adapt.
