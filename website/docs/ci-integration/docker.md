---
sidebar_position: 3
---

# Docker image

`ghcr.io/neko1313/graphlens` is the supported, batteries-included way to run
graphlens — especially in CI. It bundles the CLI with **every** adapter and
**every** toolchain their resolvers drive:

- `ty` for the Python resolver
- Node for the TypeScript resolver
- Go + `gopls` for the Go resolver
- Rust + `rust-analyzer` for the Rust resolver
- PHP + `phpantom_lsp` for the PHP resolver

It is also the only supported way to get the Go, Rust, and PHP adapters, which
are not published to PyPI.

## Pull

```bash
docker pull ghcr.io/neko1313/graphlens:latest
```

The image is published to the GitHub Container Registry on each release:

| Tag | Points at |
|---|---|
| `:latest` | the most recent release |
| `:X.Y.Z` | an exact version (e.g. `:0.4.0`) |
| `:X.Y` | the latest patch of a minor line (e.g. `:0.4`) |

Pin to `:X.Y.Z` in CI for reproducible runs.

## Run

The container's entry point is the `graphlens` CLI, so the arguments you pass
**are** the CLI arguments. Mount your project at `/workspace`:

```bash
# Print stats
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace

# Serialize the graph, strict
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace --strict --output /workspace/graph.json

# Build the interactive viewer (headless)
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    visualize /workspace --no-open --output /workspace/graph.html
```

:::tip Write outputs under the mount
`--output /workspace/graph.json` writes the file back to your host through the
volume mount. Writing anywhere outside `/workspace` keeps the result inside the
ephemeral container, where it is lost when the container exits.
:::

## Selecting languages

Auto-detection runs every bundled adapter that recognizes the project. Narrow it
with `--lang`:

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace --lang go,rust --output /workspace/graph.json
```

## Exporting to Neo4j from the container

Point `--uri` at a reachable database. From a container, `localhost` is the
container itself — use `host.docker.internal` (Docker Desktop) or a service name
on a shared network to reach a Neo4j running on the host:

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    neo4j /workspace \
    --uri bolt://host.docker.internal:7687 \
    --user neo4j --password secret
```

## In CI

The same image powers the recommended
[GitHub Actions workflow](./github-actions.md#recommended-the-docker-image) —
run the job inside the container and skip all language setup:

```yaml
jobs:
  analyze:
    runs-on: ubuntu-latest
    container:
      image: ghcr.io/neko1313/graphlens:0.4.0
    steps:
      - uses: actions/checkout@v4
      - run: graphlens analyze . --strict --output graph.json
```

## Local pre-push hook

Run analysis before every push without installing any toolchains
(`.git/hooks/pre-push`):

```bash
#!/usr/bin/env bash
set -euo pipefail
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
  analyze /workspace --strict
```
