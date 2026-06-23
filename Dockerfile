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
# (the Go, Rust and PHP adapters are not published to PyPI — this image is
# the supported way to get them).

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

# --- PHP semantic resolvers (PHPantom default, phpactor alternative) ---------
# PHPantom (phpantom_lsp) is the default PhpantomResolver engine: a
# self-contained Rust language server — no PHP runtime needed — built here with
# the cargo from the rustup install above and dropped on the PATH. phpactor is
# kept as the alternative PhpactorResolver engine; it runs on PHP, so the CLI
# php runtime and the extensions it needs are installed alongside it. Composer
# is included so a project's `vendor/` tree can be populated, letting either
# resolver resolve third-party symbols precisely.
RUN . "$HOME/.cargo/env" \
    && cargo install phpantom_lsp --root /usr/local --locked \
    && phpantom_lsp --version
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        php-cli \
        php-mbstring \
        php-xml \
        php-tokenizer \
        unzip \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://getcomposer.org/installer | php -- \
        --install-dir=/usr/local/bin --filename=composer \
    && curl -fsSL \
        https://github.com/phpactor/phpactor/releases/latest/download/phpactor.phar \
        -o /usr/local/bin/phpactor \
    && chmod +x /usr/local/bin/phpactor \
    && phpactor --version

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
        /opt/graphlens/packages/graphlens-link \
        "/opt/graphlens/packages/graphlens-cli[neo4j,mcp]" \
    && graphlens --help >/dev/null

# Projects to analyse are mounted here.
WORKDIR /workspace
ENTRYPOINT ["graphlens"]
CMD ["--help"]
