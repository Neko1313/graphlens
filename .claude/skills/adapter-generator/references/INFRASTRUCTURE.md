# Infrastructure Reference

Patterns for linting, task running, CI, and coverage — derived from the existing `graphlens-python` setup.

---

## `ruff.toml`

Create `packages/graphlens-{lang}/ruff.toml`. Key: `[lint.per-file-ignores]` relaxes rules for tests so pytest code doesn't need production-grade typing/docs.

```toml
line-length = 79
target-version = "py310"
fix = true
unsafe-fixes = true

[lint]
ignore = [
    "D203",
    "D212",
    "S101",
    "RUF002",
    "ANN401",
    "RUF001",
    "RUF003",
    "S603",
    "RUF012",
    "S107",
]
select = [
    "A", "B", "F", "I", "Q", "ASYNC",
    "N", "W", "UP", "T20", "SIM", "ANN",
    "PL", "PT", "RET", "E", "S", "C4",
    "EM", "DTZ", "RUF", "TCH", "ARG",
    "DOC", "D",
]

[lint.per-file-ignores]
# Tests: relax annotations, docstrings, magic values, security, naming
"tests/**" = [
    "ANN", "D", "PLR2004", "ARG", "E501",
    "S", "N", "PLC", "PT011", "B008",
]
"**/tests/**" = [
    "ANN", "D", "PLR2004", "ARG", "E501",
    "S", "N", "PLC", "PT011", "B008",
]
```

---

## `pyproject.toml` — linting and bandit sections

Add to `packages/graphlens-{lang}/pyproject.toml`:

```toml
[dependency-groups]
lint = [
    "bandit>=1.9.3",
    "ruff>=0.15.0",
    "ty>=0.0.15",
]
test = [
    "pytest>=9.0.2",
    "pytest-cov>=7.0.0",
]

[tool.bandit]
exclude_dirs = ["tests"]
skips = ["B101"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.coverage.run]
source = ["graphlens", "graphlens_{lang}"]

[tool.coverage.report]
fail_under = 100
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "\\.\\.\\.",
]
```

---

## `Taskfile.yaml`

Create `packages/graphlens-{lang}/Taskfile.yaml`. Mirrors `packages/graphlens-python/Taskfile.yaml` exactly — only the task names differ.

```yaml
version: "3"

tasks:
  lint:
    desc: Run linters for {lang} package (use CI=true for reports)
    cmds:
      - |
        if [[ "{{.CI}}" == "true" ]]; then
          echo "Running {language} linters with CI reports..."
          mkdir -p {{.TASKFILE_DIR}}/reports
          uv run ruff check --output-format json --output-file {{.TASKFILE_DIR}}/reports/ruff.json {{.TASKFILE_DIR}}/src {{.TASKFILE_DIR}}/tests
          uv run bandit -c {{.TASKFILE_DIR}}/pyproject.toml -r {{.TASKFILE_DIR}}/src -o {{.TASKFILE_DIR}}/reports/bandit.json --format json
          uv run ty check --output-format github > {{.TASKFILE_DIR}}/reports/gl-code-quality-report.json
        else
          echo "Running {language} linters for development..."
          uv run ruff check --fix {{.TASKFILE_DIR}}/src {{.TASKFILE_DIR}}/tests
          uv run bandit -c {{.TASKFILE_DIR}}/pyproject.toml -r {{.TASKFILE_DIR}}/src
          uv run ty check
        fi

  test:
    desc: Run {lang} package tests (use CI=true for reports)
    cmds:
      - |
        if [[ "{{.CI}}" == "true" ]]; then
          echo "Running {language} tests with CI reports..."
          mkdir -p {{.TASKFILE_DIR}}/reports
          uv run pytest {{.TASKFILE_DIR}}/tests \
            --junitxml={{.TASKFILE_DIR}}/reports/junit.xml \
            --cov={{.TASKFILE_DIR}}/src \
            --cov-branch \
            --cov-report=xml:{{.TASKFILE_DIR}}/reports/coverage.xml \
            --cov-report=term-missing
        else
          echo "Running {language} tests for development..."
          uv run pytest {{.TASKFILE_DIR}}/tests --cov={{.TASKFILE_DIR}}/src --cov-report=term-missing:skip-covered
        fi
```

---

## `taskfile.dist.yaml` — workspace root updates

Three places to edit:

**1. Add include** (under `includes:`):
```yaml
includes:
  python:
    taskfile: packages/graphlens-python/Taskfile.yaml
  {lang}:                                            # ← add
    taskfile: packages/graphlens-{lang}/Taskfile.yaml
```

**2. Add to top-level `lint:` task** (under `deps:`):
```yaml
lint:
  desc: Run linters for core + all packages
  deps:
    - core:lint
    - python:lint
    - {lang}:lint    # ← add
```

**3. Add to top-level `tests:` task** (under `deps:`):
```yaml
tests:
  desc: Run tests for core + all packages
  deps:
    - core:test
    - python:test
    - {lang}:test    # ← add
```

**4. Update `release:bump`** — add the new package:
```yaml
release:bump:
  cmds:
    - uv version {{.VERSION}} --frozen
    - uv version {{.VERSION}} --package graphlens-python --frozen
    - uv version {{.VERSION}} --package graphlens-{lang} --frozen   # ← add
    - uv lock
```

**5. Update `release:commit`** — stage the new `pyproject.toml`:
```yaml
release:commit:
  cmds:
    - |
      git add pyproject.toml \
              packages/graphlens-python/pyproject.toml \
              packages/graphlens-{lang}/pyproject.toml \    # ← add
              uv.lock \
              CHANGELOG.md
```

---

## GitHub CI workflow

Create `.github/workflows/ci-{lang}.yml`. Follows `ci-python.yml` exactly.

```yaml
name: "CI: {language}"

on:
  pull_request:
    branches: ["*"]
    paths:
      - "packages/graphlens-{lang}/**"
      - "ci-{lang}.yml"
      - "pyproject.toml"
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  lint:
    runs-on: ubuntu-latest
    defaults:
      run:
        shell: bash
    steps:
      - uses: actions/checkout@v6

      - uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true

      - uses: arduino/setup-task@v2
        with:
          repo-token: ${{ secrets.GITHUB_TOKEN }}

      - name: Install dependencies
        run: task install

      - name: Lint
        run: task {lang}:lint CI=true

  test:
    runs-on: ubuntu-latest
    defaults:
      run:
        shell: bash
    steps:
      - uses: actions/checkout@v6

      - uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true

      - uses: arduino/setup-task@v2
        with:
          repo-token: ${{ secrets.GITHUB_TOKEN }}

      - name: Install dependencies
        run: task install

      - name: Test
        run: task {lang}:test CI=true

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v5
        with:
          token: ${{ secrets.CODECOV_TOKEN }}
          files: packages/graphlens-{lang}/reports/coverage.xml
          flags: {lang}
          fail_ci_if_error: true

      - name: Upload test analytics to Codecov
        if: always()
        uses: codecov/codecov-action@v5
        with:
          token: ${{ secrets.CODECOV_TOKEN }}
          files: packages/graphlens-{lang}/reports/junit.xml
          flags: {lang}
          report_type: test_results
          fail_ci_if_error: true

      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: {lang}-test-results
          path: packages/graphlens-{lang}/reports/
```

---

## `codecov.yml` — add new flag

Edit the existing `codecov.yml` at the workspace root. Add one entry to `flag_management.individual_flags`:

```yaml
flag_management:
  default_rules:
    carryforward: true
  individual_flags:
    - name: core
      paths:
        - src/
    - name: python
      paths:
        - packages/graphlens-python/src/
    - name: {lang}         # ← add this block
      paths:
        - packages/graphlens-{lang}/src/
```

The `carryforward: true` default means the flag carries its last known value on PRs that don't touch that package, so coverage reports stay green for unrelated changes.

---

## Checklist summary

When adding a new adapter, verify these files exist or are updated:

| File | Action |
|---|---|
| `packages/graphlens-{lang}/ruff.toml` | create |
| `packages/graphlens-{lang}/Taskfile.yaml` | create |
| `packages/graphlens-{lang}/pyproject.toml` | create (linting + test deps + bandit config) |
| `taskfile.dist.yaml` | update (include + lint/test deps + release steps) |
| `.github/workflows/ci-{lang}.yml` | create |
| `codecov.yml` | update (add flag) |
| `pyproject.toml` (workspace root) | update (optional-dep + workspace members if explicit) |
