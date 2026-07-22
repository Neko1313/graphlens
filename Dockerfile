# syntax=docker/dockerfile:1
#
# graphlens CLI image — bundles the CLI and every language adapter together
# with the toolchains their resolvers drive, so a project can run the full
# analysis (Python/ty, TypeScript/Node, Go/gopls, Rust/rust-analyzer,
# PHP/Intelephense, C#/scip-dotnet+csharp-ls) in CI without installing
# anything else:
#
#   docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
#       analyze /workspace --output /workspace/graph.json
#
# The image is built from source, so it always matches the committed code
# (the Go, Rust, PHP and C# adapters are not published to PyPI — this image
# is the supported way to get them).

FROM python:3.13-slim

ARG GO_VERSION=1.26.0
ARG GOPLS_VERSION=v0.22.0
ARG NODE_MAJOR=20
ARG INTELEPHENSE_VERSION=1.18.5
ARG DOTNET_CHANNEL=10.0
ARG CSHARP_LS_VERSION=0.25.0
ARG SCIP_DOTNET_VERSION=0.2.14

ENV DEBIAN_FRONTEND=noninteractive \
    GOPATH=/root/go \
    PATH="/root/.cargo/bin:/usr/local/go/bin:/root/go/bin:${PATH}"

# --- Base OS deps -----------------------------------------------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

# --- Node.js (TypeScript Compiler-API resolver) -----------------------------
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# --- Go toolchain + gopls (Go semantic resolver) ----------------------------
RUN ARCH="$(dpkg --print-architecture)" \
    && curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${ARCH}.tar.gz" \
        -o /tmp/go.tar.gz \
    && tar -C /usr/local -xzf /tmp/go.tar.gz \
    && rm /tmp/go.tar.gz \
    && go install "golang.org/x/tools/gopls@${GOPLS_VERSION}" \
    && rm -rf /root/.cache/go-build

# --- Rust toolchain + rust-analyzer (Rust semantic resolver) ----------------
# A project's rust-toolchain.toml can pin a non-default toolchain; the
# rust-analyzer component is per-toolchain, so the pinned toolchain needs its
# own copy or the resolver falls back to the (possibly mismatched) default.
# RUST_PINNED_TOOLCHAINS lists toolchains pinned by benchmarked projects
# (e.g. astral-sh/ruff pins 1.96); extend it when adding such a project.
ARG RUST_PINNED_TOOLCHAINS="1.96"
RUN curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs \
        | sh -s -- -y --profile minimal --default-toolchain stable \
    && rustup component add rust-analyzer rust-src \
    && for tc in ${RUST_PINNED_TOOLCHAINS}; do \
           rustup toolchain install "$tc" --profile minimal \
           && rustup component add rust-analyzer rust-src --toolchain "$tc"; \
       done

# --- PHP semantic resolver (Intelephense) -----------------------------------
# Intelephense is the IntelephenseResolver engine: a Node.js language server
# (installed via npm, using the Node.js toolchain set up above for the
# TypeScript resolver) — no PHP runtime needed for the resolver itself.
# Composer (with the minimal php runtime it needs) is included only so a
# project's `vendor/` tree can be populated, letting Intelephense resolve
# third-party symbols precisely.
# The CLI only understands transport flags (--stdio/--node-ipc/--socket=/
# --pipe=), no --version/--help — passing either crashes it — so the smoke
# test checks the npm install instead of invoking the binary.
RUN npm install -g "intelephense@${INTELEPHENSE_VERSION}" \
    && npm ls -g intelephense --depth=0
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        php-cli \
        php-mbstring \
        php-xml \
        unzip \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://getcomposer.org/installer | php -- \
        --install-dir=/usr/local/bin --filename=composer \
    && composer --version

# --- .NET SDK + scip-dotnet + csharp-ls (C# semantic resolvers) -------------
# scip-dotnet is the CsharpScipResolver engine (the adapter's default): a
# Roslyn-based batch SCIP indexer, run once per analysis to write a static
# index that every query then reads back offline — see _resolver.py's module
# docstring for why that beats a live LSP server on large solutions. csharp-ls
# is the CsharpLspResolver engine (an explicit alternative, not the default):
# a Roslyn-based LSP server for live queries against a running workspace.
# Both are .NET global tools, so Roslyn needs the .NET SDK to load a
# compilation from source (installed via the official dotnet-install.sh — no
# apt repo needed). csharp-ls 0.25 targets net10.0, so channel 10.0.
# libicu + tzdata are mandatory: this slim base ships neither, and .NET aborts
# (SIGABRT) the moment any `dotnet` command touches globalization
# (CultureInfo / TimeZoneInfo during DateTime.Now) without ICU present.
# csharp-ls is an LSP server that reads stdin, so invoking it (no
# --version/--help) would hang the build — the smoke test runs `dotnet
# --version` (fails fast if globalization is broken) then lists both
# installed tools. Both land in /root/.dotnet/tools (on PATH below), where
# shutil.which("scip-dotnet") / shutil.which("csharp-ls") find them.
ENV DOTNET_ROOT=/usr/local/dotnet \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    PATH="/usr/local/dotnet:/root/.dotnet/tools:${PATH}"
RUN apt-get update \
    && apt-get install -y --no-install-recommends libicu-dev tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && chmod +x /tmp/dotnet-install.sh \
    && /tmp/dotnet-install.sh --channel "${DOTNET_CHANNEL}" \
        --install-dir "${DOTNET_ROOT}" \
    && rm /tmp/dotnet-install.sh \
    && dotnet --version \
    && dotnet tool install --global scip-dotnet --version "${SCIP_DOTNET_VERSION}" \
    && dotnet tool install --global csharp-ls --version "${CSHARP_LS_VERSION}" \
    && dotnet tool list --global | grep -q scip-dotnet \
    && dotnet tool list --global | grep -q csharp-ls

# --- uv (installer) ---------------------------------------------------------
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# --- graphlens (core + every adapter + CLI, installed from source) ----------
COPY . /opt/graphlens
RUN uv pip install --system --no-cache \
        /opt/graphlens \
        /opt/graphlens/packages/graphlens-python \
        /opt/graphlens/packages/graphlens-typescript \
        /opt/graphlens/packages/graphlens-go \
        /opt/graphlens/packages/graphlens-rust \
        /opt/graphlens/packages/graphlens-php \
        /opt/graphlens/packages/graphlens-csharp \
        /opt/graphlens/packages/graphlens-link \
        "/opt/graphlens/packages/graphlens-cli[neo4j,mcp]" \
    && graphlens --help >/dev/null

# Projects to analyse are mounted here.
WORKDIR /workspace
ENTRYPOINT ["graphlens"]
CMD ["--help"]
