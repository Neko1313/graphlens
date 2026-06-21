---
sidebar_position: 1
---

# CI integration overview

graphlens is built to run in a pipeline. Adapters are pure data producers,
node IDs are deterministic, and the graph round-trips through JSON — so a CI job
can index your repository, publish the graph as an artifact, and fail the build
when analysis degrades.

This page covers the building blocks. The next two pages give concrete
[GitHub Actions](./github-actions.md) and [Docker](./docker.md) recipes.

## The two things you produce

1. **A serialized graph** — `graphlens analyze . --output graph.json`. This is
   the artifact downstream steps (agents, the [MCP server](../guides/mcp-server.md),
   dashboards) consume.
2. **A pass/fail signal** — `graphlens analyze . --strict` exits non-zero if the
   resolver did not complete, so the job fails loudly instead of shipping a
   half-resolved graph.

You usually want both at once:

```bash
graphlens analyze . --strict --output graph.json
```

## Strict mode

Type-aware analysis can degrade — a toolchain may be missing, or a file may not
type-check. graphlens records the outcome on `graph.metadata[RESOLVER_STATUS_KEY]`:

| Status | Meaning | `--strict` result |
|---|---|---|
| `ok` | the type-aware layer ran to completion | exit 0 |
| `degraded` | the resolver started but some queries failed | exit non-zero |
| `unavailable` | the resolver never started | exit non-zero |

Without `--strict`, a degraded graph is still produced (and still useful for
structure-only views) — the status just lives in the metadata. With `--strict`,
anything other than `ok` fails the command. Gate your pipeline on it so agents
never receive an incomplete graph.

```python
# The same check in code
from graphlens import RESOLVER_STATUS_KEY
assert graph.metadata[RESOLVER_STATUS_KEY] == "ok"
```

## Two ways to get the toolchains

Each resolver drives an external engine (`ty`, Node, `gopls`, `rust-analyzer`).
You have two options in CI:

- **The Docker image** — `ghcr.io/neko1313/graphlens` bundles the CLI with every
  adapter **and** every toolchain pre-installed. Nothing to set up, and the only
  supported way to get the Go and Rust adapters. **Recommended for CI.** See the
  [Docker guide](./docker.md).
- **pip/uv install** — install `graphlens-cli` with the extras you need. Good
  for Python/TypeScript-only repos where you already have Node available.

## Running it locally

Everything CI does, you can do on your machine before pushing.

### With the project's task runner

If you are working **in** the graphlens repository, the
[Taskfile](https://taskfile.dev/) wraps the common workflows:

```bash
task install     # uv sync --all-groups
task lint        # ruff + ty + bandit across all packages
task tests       # all tests with coverage
```

### As a pre-commit-style check

Run analysis as a local guard before committing. For example, a simple Git
`pre-push` hook (`.git/hooks/pre-push`):

```bash
#!/usr/bin/env bash
set -euo pipefail
graphlens analyze . --strict --output /tmp/graphlens.json
```

Or with the Docker image so you do not need the toolchains installed:

```bash
#!/usr/bin/env bash
set -euo pipefail
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
  analyze /workspace --strict
```

## Caching the graph

Because node IDs are deterministic, a graph is cacheable and diffable. A common
pattern is to store the previous run's `graph.json` and diff against it to report
what changed:

```python
from graphlens import GraphLens

old = GraphLens.from_json(open("graph.prev.json").read())
new = GraphLens.from_json(open("graph.json").read())
diff = old.diff(new)
if not diff.is_empty:
    print(f"+{len(diff.added_nodes)} -{len(diff.removed_nodes)} nodes")
```

## Next

- [GitHub Actions](./github-actions.md) — ready-to-copy workflows.
- [Docker](./docker.md) — the pre-built image in depth.
