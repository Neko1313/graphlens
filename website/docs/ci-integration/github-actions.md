---
sidebar_position: 2
---

# GitHub Actions

Ready-to-copy workflows for indexing a repository with graphlens on every push
and pull request. Pick the one that matches how you want to provide the
toolchains.

## Recommended: the Docker image

The published image bundles the CLI with every adapter **and** every toolchain
(`ty`, Node, `gopls`, `rust-analyzer`, `phpantom_lsp`), so the job needs no
language setup. This is the only supported way to use the Go, Rust, and PHP
adapters.

```yaml title=".github/workflows/code-graph.yml"
name: Code graph

on:
  push:
    branches: [main]
  pull_request:

jobs:
  analyze:
    runs-on: ubuntu-latest
    container:
      image: ghcr.io/neko1313/graphlens:latest
    steps:
      - uses: actions/checkout@v4

      - name: Analyze and serialize the graph
        run: graphlens analyze . --strict --output graph.json

      - name: Upload the graph
        uses: actions/upload-artifact@v4
        with:
          name: code-graph
          path: graph.json
```

`--strict` fails the job if the resolver did not complete; drop it if you only
want the artifact and are happy with a structure-only graph when a toolchain is
missing.

### Pin the version

Use a version tag instead of `:latest` for reproducible runs:

```yaml
    container:
      image: ghcr.io/neko1313/graphlens:0.4.0
```

The registry publishes `:latest` plus `:X.Y.Z` and `:X.Y` tags on each release.

## Alternative: pip install (Python / TypeScript repos)

If you would rather not run inside the container — for example a pure
Python + TypeScript repo where Node is already on the runner — install the CLI
with the extras you need:

```yaml title=".github/workflows/code-graph.yml"
name: Code graph

on:
  push:
    branches: [main]
  pull_request:

jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      # Required only for the TypeScript adapter
      - uses: actions/setup-node@v4
        with:
          node-version: 20

      - name: Install graphlens
        run: pip install "graphlens-cli[python,typescript]"

      - name: Analyze
        run: graphlens analyze . --strict --output graph.json

      - uses: actions/upload-artifact@v4
        with:
          name: code-graph
          path: graph.json
```

## Adding the interactive viewer

Attach a browsable HTML graph to every run — it is a single static file:

```yaml
      - name: Build the graph viewer
        run: graphlens visualize . --no-open --output graph.html
      - uses: actions/upload-artifact@v4
        with:
          name: code-graph-viewer
          path: graph.html
```

## Failing a PR on graph changes (optional)

Because node IDs are deterministic, you can diff against a committed baseline and
surface what changed. A minimal version:

```yaml
      - name: Diff against baseline
        run: |
          python - <<'PY'
          from graphlens import GraphLens
          old = GraphLens.from_json(open("graph.baseline.json").read())
          new = GraphLens.from_json(open("graph.json").read())
          diff = old.diff(new)
          print(f"added nodes:    {len(diff.added_nodes)}")
          print(f"removed nodes:  {len(diff.removed_nodes)}")
          print(f"added edges:    {len(diff.added_relations)}")
          print(f"removed edges:  {len(diff.removed_relations)}")
          PY
```

## Note: this site's own deployment

This documentation site is itself deployed by a GitHub Actions workflow
(`.github/workflows/docs.yml`) that builds the Docusaurus site and publishes it
to GitHub Pages on pushes to `main` that touch `website/`. It is a separate
concern from analyzing your code, but it is a working example of a docs pipeline
in the same repository — see the [Docker guide](./docker.md) and the
[`website/README.md`](https://github.com/Neko1313/graphlens/blob/main/website/README.md).
