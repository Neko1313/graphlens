# Core Contracts Reference

All types below are imported from `graphlens` (the workspace-root core library).

## Imports

```python
from graphlens import (
    GraphLens,
    LanguageAdapter,
    Node,
    NodeKind,
    Relation,
    RelationKind,
)
from graphlens.contracts import DependencyFileParser, normalize_pkg_name
from graphlens.utils import make_node_id, Span
```

---

## LanguageAdapter

```python
# graphlens.contracts.adapter
class LanguageAdapter(ABC):
    @abstractmethod
    def language(self) -> str: ...
    # Returns language identifier: "python", "typescript", "rust", etc.

    @abstractmethod
    def can_handle(self, project_root: Path) -> bool: ...
    # Returns True if this adapter can analyze the project at project_root.
    # Typically delegates to is_{lang}_project().

    @abstractmethod
    def analyze(
        self, project_root: Path, files: list[Path] | None = None
    ) -> GraphLens: ...
    # Parses the project and returns a populated GraphLens.
    # If files=None, calls self.collect_files() internally.
    # Must NOT write to any file or external system.

    def file_extensions(self) -> set[str]: ...
    # Returns {".ts", ".tsx"} etc. Used by collect_files().

    def collect_files(self, project_root: Path) -> list[Path]: ...
    # Concrete default: walks root, filters by file_extensions(),
    # skips .venv, __pycache__, .git, dist, build, .eggs, node_modules.
```

---

## DependencyFileParser

```python
# graphlens.contracts.deps
class DependencyFileParser(ABC):
    @abstractmethod
    def can_parse(self, project_root: Path) -> bool: ...
    # Returns True if this parser's manifest file exists in project_root.

    @abstractmethod
    def parse(self, project_root: Path) -> frozenset[str]: ...
    # Returns normalized top-level package names declared as dependencies.
    # MUST return frozenset() on any error — never raise.


def normalize_pkg_name(name: str) -> str: ...
# Normalizes a distribution name for import-name comparison:
# - Strips version specifiers, extras, inline comments
# - Lowercases, replaces hyphens with underscores
# - Scoped npm names (@scope/pkg) kept as-is (lowercased)
# Examples:
#   "requests>=2.0 [security]" → "requests"
#   "scikit-learn"             → "scikit_learn"
#   "@types/node"              → "@types/node"
```

---

## GraphLens

```python
# graphlens.models.graph
class GraphLens:
    nodes: dict[str, Node]         # node_id → Node
    relations: list[Relation]

    def add_node(self, node: Node) -> None: ...
    # Raises DuplicateNodeError if node.id already in self.nodes.
    # Always guard with: if node_id not in graph.nodes

    def add_relation(self, relation: Relation) -> None: ...
    # Duplicate relations are allowed (no deduplication).

    def merge(self, other: GraphLens) -> None: ...
    # Merges another graph into this one in-place.
```

---

## Node

```python
# graphlens.models.nodes
@dataclass(frozen=True, slots=True)
class Node:
    id: str                          # 16-char hex, from make_node_id()
    kind: NodeKind                   # discriminator enum
    qualified_name: str              # dotted path: "mypackage.utils.MyClass"
    name: str                        # last segment: "MyClass"
    file_path: str | None = None     # relative path from project root
    span: Span | None = None         # 1-based source location
    metadata: dict[str, object] = field(default_factory=dict)
```

### NodeKind enum values

| Value | String | Usage |
|---|---|---|
| `NodeKind.PROJECT` | `"project"` | Root node per sub-project |
| `NodeKind.MODULE` | `"module"` | Package/namespace node |
| `NodeKind.FILE` | `"file"` | Source file |
| `NodeKind.CLASS` | `"class"` | Class, interface, struct |
| `NodeKind.FUNCTION` | `"function"` | Top-level function |
| `NodeKind.METHOD` | `"method"` | Function inside a class |
| `NodeKind.PARAMETER` | `"parameter"` | Function/method parameter |
| `NodeKind.IMPORT` | `"import"` | Import statement |
| `NodeKind.DEPENDENCY` | `"dependency"` | (reserved for DEPENDS_ON edges) |
| `NodeKind.SYMBOL` | `"symbol"` | Call target (unresolved local) |
| `NodeKind.EXTERNAL_SYMBOL` | `"external_symbol"` | stdlib/third_party/unknown target |

---

## Relation

```python
# graphlens.models.relations
@dataclass(frozen=True, slots=True)
class Relation:
    source_id: str
    target_id: str
    kind: RelationKind
    metadata: dict[str, object] = field(default_factory=dict)
```

### RelationKind enum values

| Value | String | Usage |
|---|---|---|
| `RelationKind.CONTAINS` | `"contains"` | PROJECT→MODULE, MODULE→MODULE, MODULE→FILE |
| `RelationKind.DECLARES` | `"declares"` | FILE→CLASS/FUNCTION/IMPORT, CLASS→METHOD, FUNCTION→PARAMETER |
| `RelationKind.IMPORTS` | `"imports"` | FILE→MODULE or FILE→EXTERNAL_SYMBOL |
| `RelationKind.CALLS` | `"calls"` | FUNCTION/METHOD→SYMBOL |
| `RelationKind.REFERENCES` | `"references"` | (available for general references) |
| `RelationKind.DEPENDS_ON` | `"depends_on"` | PROJECT→DEPENDENCY |
| `RelationKind.RESOLVES_TO` | `"resolves_to"` | IMPORT→MODULE or IMPORT→EXTERNAL_SYMBOL |
| `RelationKind.INHERITS_FROM` | `"inherits_from"` | CLASS→EXTERNAL_SYMBOL |

---

## make_node_id

```python
# graphlens.utils.ids
def make_node_id(project_name: str, qualified_name: str, kind: str) -> str:
    """SHA-256[:16] of '{project_name}::{kind}::{qualified_name}'."""
```

Usage:
```python
node_id = make_node_id(project_name, qname, NodeKind.CLASS.value)
# kind must be NodeKind.<X>.value (the string), not the enum member
```

Same inputs always produce the same ID — enabling incremental updates.

---

## Span

```python
# graphlens.utils.span
@dataclass(frozen=True, slots=True)
class Span:
    start_line: int   # 1-based
    start_col: int    # 1-based
    end_line: int     # 1-based
    end_col: int      # 1-based
```

Conversion from tree-sitter (0-based) to Span (1-based):
```python
def _make_span(node: TSNode | None) -> Span | None:
    if node is None:
        return None
    try:
        sr, sc = node.start_point
        er, ec = node.end_point
        return Span(
            start_line=sr + 1,
            start_col=sc + 1,
            end_line=er + 1,
            end_col=ec + 1,
        )
    except Exception:
        return None
```

---

## ImportClassifier

```python
# lives in graphlens_{lang}._visitor (not in core)
@dataclass
class ImportClassifier:
    stdlib: frozenset[str] = field(default_factory=frozenset)
    third_party: frozenset[str] = field(default_factory=frozenset)
    internal: frozenset[str] = field(default_factory=frozenset)

    def classify(self, top_level: str) -> str:
        # Precedence: stdlib > internal > third_party > "unknown"
        if top_level in self.stdlib:
            return "stdlib"
        if top_level in self.internal:
            return "internal"
        if top_level in self.third_party:
            return "third_party"
        return "unknown"
```

`origin` values for `metadata["origin"]`:

| Value | Meaning |
|---|---|
| `"stdlib"` | Language standard library |
| `"internal"` | Module in the same project |
| `"third_party"` | Listed in a dependency manifest |
| `"unknown"` | None of the above |

Relative imports must always be classified as `"internal"` regardless of classifier.

---

## Graph hierarchy

```
PROJECT
  └─(CONTAINS)─ MODULE (top-level)
                  └─(CONTAINS)─ MODULE (nested)
                                  └─(CONTAINS)─ FILE
                                                  └─(DECLARES)─ CLASS
                                                                  └─(DECLARES)─ METHOD
                                                  └─(DECLARES)─ FUNCTION
                                                  └─(DECLARES)─ IMPORT ─(RESOLVES_TO)─ MODULE (internal)
                                                                        └─(RESOLVES_TO)─ EXTERNAL_SYMBOL
FUNCTION/METHOD ─(CALLS)─ SYMBOL
CLASS ─(INHERITS_FROM)─ EXTERNAL_SYMBOL
FILE ─(IMPORTS)─ MODULE or EXTERNAL_SYMBOL
```
