# graphlens load benchmarks

Measures graphlens analysis throughput on large, real-world projects. The
numbers in the root [`README.md`](../README.md) (`## Benchmarks`) are produced
by this harness and refreshed automatically on every release by
[`.github/workflows/bench.yml`](../.github/workflows/bench.yml).

The benchmark only **reports** — it never fails CI. A project that cannot be
cloned or analysed is recorded as an error row and the run continues.

## What is measured

For each project the harness clones a pinned ref, runs the relevant adapter(s),
and records:

| Metric | Meaning |
|---|---|
| **LOC / Files** | Lines and files the adapter actually collected |
| **Nodes / Relations** | Size of the produced graph IR |
| **Time** | Wall-clock of `adapter.analyze()` only (clone excluded) |
| **Peak RSS** | High-water memory of the **whole process tree** — including the ty / gopls / rust-analyzer LSP subprocesses — read from the cgroup peak (falls back to `getrusage`) |
| **KLOC/s** | Analysed thousands-of-lines per second (the headline number) |
| **Resolver** | Worst resolver status across the project's languages (`ok` / `partial` / `unavailable`) |

Timings come from a single cold run on a shared CI runner, so treat them as
**indicative**, not microbenchmark-grade.

## Projects

Targets live in [`projects.json`](projects.json) — a medium-to-large size
gradient per adapter (with extra Go and Rust coverage), each pinned to an
upstream tag for reproducibility:

| Adapter | Projects |
|---|---|
| Python | apache/superset |
| TypeScript | colinhacks/zod |
| Go | gin-gonic/gin · casdoor/casdoor · gohugoio/hugo |
| Rust | BurntSushi/ripgrep · tokio-rs/axum · astral-sh/ruff |
| PHP | laravel/framework |

Edit the file to add or swap projects; the workflow loops over every entry and
the README table follows the manifest order. If a pinned `ref` has gone missing
the harness falls back to the default branch and reports the actual commit SHA,
so a stale entry degrades gracefully instead of breaking the run.

## Run it locally

Requires the adapters and their toolchains. The simplest way is the published
image, which bundles everything:

```bash
# one project, isolated container (accurate peak-memory reading)
docker run --rm -v "$PWD:/repo" -w /repo --entrypoint python \
    ghcr.io/neko1313/graphlens:latest \
    benchmarks/run_benchmarks.py analyze \
        --project apache/superset \
        --manifest /repo/benchmarks/projects.json \
        --workdir /tmp/bench \
        --out /repo/benchmarks/results/apache__superset.json

# render every results/*.json into the README block (host-side, stdlib only)
python3 benchmarks/run_benchmarks.py render \
    --results benchmarks/results --readme README.md --image-tag latest

# or just preview the Markdown without touching the README
python3 benchmarks/run_benchmarks.py render --results benchmarks/results --print
```

Without Docker, install the adapters into the current environment
(`uv sync --all-packages`) plus their toolchains (`ty`, Node, Go + `gopls`,
Rust + `rust-analyzer`) and run the same `run_benchmarks.py` commands with your
interpreter.

## How it runs in CI

`bench.yml` triggers on `release: published` (and `workflow_dispatch` for a
manual run). It pulls the release image — retrying while the Docker build for
the same tag finishes — runs every project in its own container, splices the
results into the README `## Benchmarks` block, uploads the raw JSON as an
artifact, and commits the refreshed README straight to `main`.
