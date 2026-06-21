# syntax=docker/dockerfile:1
#
# graphlens CLI image — bundles the CLI and every language adapter together
# with the toolchains their resolvers drive, so a project can run the full
# analysis (Python/ty, TypeScript/Node, Go/gopls, Rust/rust-analyzer) in CI
# without installing anything else:
#
#   docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
#       analyze /workspace --output /workspace/graph.json
#
# The image is built from source, so it always matches the committed code
# (the Go and Rust adapters are not published to PyPI — this image is the
# supported way to get them).

FROM python:3.13-slim

ARG GO_VERSION=1.26.0
ARG GOPLS_VERSION=v0.22.0
ARG NODE_MAJOR=20

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
RUN curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs \
        | sh -s -- -y --profile minimal \
            --component rust-analyzer --component rust-src

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
        /opt/graphlens/packages/graphlens-link \
        "/opt/graphlens/packages/graphlens-cli[neo4j,mcp]" \
    && graphlens --help >/dev/null

# Projects to analyse are mounted here.
WORKDIR /workspace
ENTRYPOINT ["graphlens"]
CMD ["--help"]
