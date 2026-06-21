---
sidebar_position: 4
---

# Visualization

`graphlens visualize` renders the graph as a self-contained, interactive HTML
file powered by [vis.js](https://visjs.org/) and opens it in your browser. The
file has no external dependencies, so you can commit it as a CI artifact or
share it directly.

```bash
graphlens visualize ./my-project
```

## Options

| Flag | Default | Description |
|---|---|---|
| `--lang` | `auto` | Adapter(s) to use (auto-detect all by default) |
| `--show-external` | off | Include stdlib / third-party external symbol nodes |
| `--show-structure` | off | Add `CONTAINS` / `DECLARES` structural edges |
| `--max-nodes N` | `1500` | Prune low-degree nodes above `N` |
| `--output PATH`, `-o` | `graph-<name>.html` | Write HTML to `PATH` |
| `--no-open` | off | Do not open the browser automatically |

```bash
# Focus on application code, cap the graph, write to a fixed path, stay headless
graphlens visualize . --lang python --max-nodes 500 --output graph.html --no-open
```

## Pruning

Large graphs become unreadable. By default graphlens keeps the structural
backbone (`PROJECT`, `MODULE`, `FILE` nodes stay pinned) and removes the
lowest-degree nodes once the total exceeds `--max-nodes`. Raise the cap to see
more, lower it for a high-level overview.

By default external symbols and structural `CONTAINS`/`DECLARES` edges are
hidden so the call/reference graph stands out. Add `--show-external` and
`--show-structure` to bring them back.

## Interacting with the viewer

- **Search and filters** sit in the sidebar — filter by node kind or search by
  name.
- **Click a node** to open its info panel (kind, qualified name, file, span).
- For `FUNCTION` and `METHOD` nodes the panel has a **Show callers** button.
  Clicking it switches the graph into *focus mode*: only the selected node and
  everything that calls or references it are shown, with the caller list in the
  sidebar.
- **Click empty space** or press **← Back** to return to the full graph.

## Generating a viewer from a saved graph

The CLI re-analyzes the project each time. If you already have a `graph.json`
and want a viewer from it, the standalone script in the repository's
[`examples/visualize_graph.py`](https://github.com/Neko1313/graphlens/blob/main/examples/visualize_graph.py)
renders the same HTML from an in-memory `GraphLens`, which you can build with
`GraphLens.from_json(...)`.

## In CI

Because the output is a single static file, it drops neatly into a pipeline as a
downloadable artifact:

```yaml
- name: Build code graph viewer
  run: graphlens visualize . --no-open --output graph.html
- uses: actions/upload-artifact@v4
  with:
    name: code-graph
    path: graph.html
```

See [CI integration](../ci-integration/overview.md) for the full picture.
