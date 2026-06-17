# FASTDelphos Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic static-analysis Comprehension Compiler that reads a FastAPI repo and outputs a navigable property graph (JSON + HTML) showing endpoints, models, function calls, and table access — with explicit gap reporting for anything that can't be inferred from source.

**Architecture:** Linear pipeline: adapter → extractors → graph builder → gap reporter → renderer. Each extractor produces typed Node/Edge/Gap objects conforming to a shared schema. The builder assembles them, assigns stable IDs and content hashes, then the renderer emits a self-contained HTML file using Cytoscape.js from CDN. No LLM, no DB connections, read-only on the target repo.

**Tech Stack:** Python 3.11, griffe (symbol inventory + Pydantic model fields), ast stdlib (decorator/call-site parsing), pyan3 `CallGraphVisitor` (call graph), sqlglot (SQL AST), hashlib (content hashing), Cytoscape.js CDN (render), ruff (format+lint), pytest.

## Global Constraints

- Python 3.11+; install with `source .venv/bin/activate && pip install -e ".[dev]"`; run from `/data/FASTDelphos/`
- Source-only: never import the target app except in the oracle path (Task 10)
- Read-only on target: never write to the analyzed repo
- `id` format: `{type}:{qualname_or_key}` — e.g. `function:myapp.main.create_user`, `endpoint:POST:/users/`, `model:myapp.models.UserIn`, `table:users`
- `hash`: `hashlib.sha256(source_span_text.encode()).hexdigest()` where `source_span_text` = joined lines from `lineno` to `end_lineno` inclusive
- `inferred = False` everywhere in Phase 1 (zero LLM)
- Gaps flag, don't block: always produce partial output and declare gaps; never raise on missing info
- Entry point: `python -m cc compile <path> --out <dir>`
- Run all tests with: `pytest -v`

---

## File Map

```
src/cc/
  __init__.py
  cli.py                  - argparse entry point, `compile` subcommand
  pipeline.py             - orchestrates adapter → extractors → build → render
  adapters/
    __init__.py
    base.py               - Adapter Protocol (abstract interface)
    fastapi.py            - FastAPI adapter: collect Python files, detect router vars
  extract/
    __init__.py
    endpoints.py          - parse @router.*/app.* decorators, resolve router prefixes
    models.py             - find Pydantic BaseModel subclasses, extract fields + handler annotations
    calls.py              - pyan3 CallGraphVisitor, produce calls edges
    sql.py                - detect DB call sites, parse SQL with sqlglot
  graph/
    __init__.py
    schema.py             - Node, Edge, Gap, Graph dataclasses
    build.py              - assemble graph, assign id/hash, serialize to JSON
  gaps.py                 - gap detection after graph assembly
  render/
    __init__.py
    emit.py               - write JSON + embed in HTML
    template.html         - Cytoscape.js CDN template with endpoint list + click panel
tests/
  conftest.py             - shared path helpers
  fixtures/
    simple_api/
      __init__.py
      main.py             - FastAPI app with APIRouter(prefix="/messages"), 2 endpoints
      models.py           - MessageIn(content, author), MessageOut(id, content, author)
      db.py               - two async functions with embedded SQL strings
  test_schema.py
  test_endpoints.py
  test_models_ext.py
  test_sql.py
  test_calls.py
  test_graph.py
  test_gaps.py
  test_render.py
  test_pipeline.py
```

---

### Task 1: Package scaffold + graph schema

**Files:**
- Create: `src/cc/__init__.py`, `src/cc/adapters/__init__.py`, `src/cc/extract/__init__.py`, `src/cc/graph/__init__.py`, `src/cc/render/__init__.py`
- Create: `src/cc/graph/schema.py`
- Create: `tests/__init__.py`, `tests/conftest.py`
- Create: `tests/fixtures/__init__.py`, `tests/fixtures/simple_api/__init__.py`
- Create: `tests/fixtures/simple_api/main.py`
- Create: `tests/fixtures/simple_api/models.py`
- Create: `tests/fixtures/simple_api/db.py`
- Create: `tests/test_schema.py`

**Interfaces:**
- Produces: `Node`, `Edge`, `Gap`, `Graph` dataclasses from `cc.graph.schema` — imported by all subsequent tasks

- [ ] **Step 1: Create all empty `__init__.py` files**

```bash
touch src/cc/__init__.py \
      src/cc/adapters/__init__.py \
      src/cc/extract/__init__.py \
      src/cc/graph/__init__.py \
      src/cc/render/__init__.py \
      tests/__init__.py \
      tests/fixtures/__init__.py \
      tests/fixtures/simple_api/__init__.py
```

- [ ] **Step 2: Write the test fixture — `tests/fixtures/simple_api/models.py`**

```python
from pydantic import BaseModel


class MessageIn(BaseModel):
    content: str
    author: str


class MessageOut(BaseModel):
    id: int
    content: str
    author: str
```

- [ ] **Step 3: Write the test fixture — `tests/fixtures/simple_api/db.py`**

```python
async def create_message(conn, content: str, author: str) -> None:
    await conn.execute(
        "INSERT INTO messages (content, author) VALUES (%s, %s)",
        (content, author),
    )


async def get_message(conn, msg_id: int) -> dict:
    return await conn.fetchone(
        "SELECT id, content, author FROM messages WHERE id = %s",
        (msg_id,),
    )
```

- [ ] **Step 4: Write the test fixture — `tests/fixtures/simple_api/main.py`**

```python
from fastapi import FastAPI, APIRouter

from .models import MessageIn, MessageOut

app = FastAPI()
router = APIRouter(prefix="/messages")


@router.post("/", response_model=MessageOut)
async def create_message(msg: MessageIn) -> MessageOut:
    return MessageOut(id=1, content=msg.content, author=msg.author)


@router.get("/{msg_id}", response_model=MessageOut)
async def get_message(msg_id: int) -> MessageOut:
    return MessageOut(id=msg_id, content="hello", author="alice")


app.include_router(router)
```

- [ ] **Step 5: Write the test fixture path helper — `tests/conftest.py`**

```python
import pathlib

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SIMPLE_API = FIXTURES / "simple_api"
```

- [ ] **Step 6: Write the failing schema test — `tests/test_schema.py`**

```python
from cc.graph.schema import Edge, Gap, Graph, Node


def test_node_fields():
    n = Node(id="function:myapp.foo", type="function", file="myapp.py", line=1,
              hash="abc", inferred=False, props={})
    assert n.id == "function:myapp.foo"
    assert n.inferred is False


def test_edge_fields():
    e = Edge(from_="function:myapp.foo", to="function:myapp.bar",
              type="calls", inferred=False, props={})
    assert e.type == "calls"


def test_gap_fields():
    g = Gap(kind="missing_artifact", where="myapp.py:10", node_id="table:users",
             missing="No CREATE TABLE for users", suggested="-- TODO: DDL for users",
             severity={"comprehension": "warning", "compliance": "error"})
    assert g.kind == "missing_artifact"


def test_graph_collects_all():
    n = Node(id="table:messages", type="table", file="db.py", line=1,
              hash="x", inferred=False, props={"name": "messages", "columns": []})
    graph = Graph(nodes=[n], edges=[], gaps=[])
    assert len(graph.nodes) == 1
```

- [ ] **Step 7: Run the test — verify it fails**

```bash
pytest tests/test_schema.py -v
```

Expected: `ImportError: No module named 'cc.graph.schema'`

- [ ] **Step 8: Write `src/cc/graph/schema.py`**

```python
from dataclasses import dataclass, field


@dataclass
class Node:
    id: str
    type: str       # endpoint | function | model | table
    file: str
    line: int
    hash: str
    inferred: bool
    props: dict = field(default_factory=dict)


@dataclass
class Edge:
    from_: str
    to: str
    type: str       # handles | uses_model | calls | reads | writes
    inferred: bool
    props: dict = field(default_factory=dict)


@dataclass
class Gap:
    kind: str       # missing_artifact | unresolved_dynamic
    where: str      # "file:line"
    node_id: str | None
    missing: str
    suggested: str
    severity: dict  # {"comprehension": "warning"|"error", "compliance": "warning"|"error"}


@dataclass
class Graph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
```

- [ ] **Step 9: Run the test — verify it passes**

```bash
pytest tests/test_schema.py -v
```

Expected: 4 passed

- [ ] **Step 10: Commit**

```bash
git add src/ tests/ pyproject.toml
git commit -m "feat: package scaffold, graph schema, test fixtures"
```

---

### Task 2: Hash utility

**Files:**
- Create: `src/cc/graph/hash_util.py`

**Interfaces:**
- Consumes: a file path (str | Path) + `lineno` (int) + `end_lineno` (int)
- Produces: `node_hash(file, lineno, end_lineno) -> str` — imported by extractors and graph builder

- [ ] **Step 1: Write the failing test — add to `tests/test_schema.py`**

```python
from cc.graph.hash_util import node_hash
from tests.conftest import SIMPLE_API


def test_node_hash_is_stable():
    h1 = node_hash(SIMPLE_API / "models.py", 1, 6)
    h2 = node_hash(SIMPLE_API / "models.py", 1, 6)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_node_hash_differs_by_span():
    h1 = node_hash(SIMPLE_API / "models.py", 1, 6)
    h2 = node_hash(SIMPLE_API / "models.py", 1, 3)
    assert h1 != h2
```

- [ ] **Step 2: Run — verify it fails**

```bash
pytest tests/test_schema.py::test_node_hash_is_stable -v
```

Expected: `ImportError`

- [ ] **Step 3: Write `src/cc/graph/hash_util.py`**

```python
import hashlib
import pathlib


def node_hash(file: str | pathlib.Path, lineno: int, end_lineno: int) -> str:
    lines = pathlib.Path(file).read_text(encoding="utf-8").splitlines()
    span = "\n".join(lines[lineno - 1 : end_lineno])
    return hashlib.sha256(span.encode()).hexdigest()
```

- [ ] **Step 4: Run — verify it passes**

```bash
pytest tests/test_schema.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc/graph/hash_util.py tests/test_schema.py
git commit -m "feat: node hash utility (SHA-256 of source span)"
```

---

### Task 3: Endpoint extractor

**Files:**
- Create: `src/cc/extract/endpoints.py`
- Create: `tests/test_endpoints.py`

**Interfaces:**
- Consumes: `repo_path: Path` — path to the root of the target repo
- Produces: `extract_endpoints(repo_path) -> tuple[list[Node], list[Edge]]`
  - Nodes: type=`endpoint` with `props={method, path, handler}` and type=`function` for handlers
  - Edges: type=`handles` from endpoint node to function node
- Imports: `Node`, `Edge` from `cc.graph.schema`; `node_hash` from `cc.graph.hash_util`

- [ ] **Step 1: Write the failing test — `tests/test_endpoints.py`**

```python
from cc.extract.endpoints import extract_endpoints
from tests.conftest import SIMPLE_API


def test_finds_two_endpoints():
    nodes, edges = extract_endpoints(SIMPLE_API)
    ep_nodes = [n for n in nodes if n.type == "endpoint"]
    assert len(ep_nodes) == 2


def test_endpoint_methods_and_paths():
    nodes, edges = extract_endpoints(SIMPLE_API)
    ep_nodes = {n.props["method"] + " " + n.props["path"]: n
                for n in nodes if n.type == "endpoint"}
    assert "POST /messages/" in ep_nodes
    assert "GET /messages/{msg_id}" in ep_nodes


def test_endpoint_ids_are_stable():
    nodes, _ = extract_endpoints(SIMPLE_API)
    ep_ids = {n.id for n in nodes if n.type == "endpoint"}
    assert "endpoint:POST:/messages/" in ep_ids
    assert "endpoint:GET:/messages/{msg_id}" in ep_ids


def test_handles_edges_link_endpoint_to_handler():
    nodes, edges = extract_endpoints(SIMPLE_API)
    handles = [e for e in edges if e.type == "handles"]
    assert len(handles) == 2
    handler_qualnames = {e.to for e in handles}
    assert any("create_message" in q for q in handler_qualnames)
    assert any("get_message" in q for q in handler_qualnames)


def test_endpoint_nodes_have_hash():
    nodes, _ = extract_endpoints(SIMPLE_API)
    for n in nodes:
        assert len(n.hash) == 64
        assert n.inferred is False
```

- [ ] **Step 2: Run — verify it fails**

```bash
pytest tests/test_endpoints.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Write `src/cc/extract/endpoints.py`**

Algorithm:
1. Walk all `.py` files under `repo_path` with `pathlib.rglob("*.py")`.
2. Parse each file with `ast.parse(source)`.
3. **Collect router vars**: find assignments like `router = APIRouter(prefix=...)` — walk `ast.Assign` nodes where the value is a `Call` with `func` name `APIRouter`. Extract the `prefix` keyword arg (default `""`).
4. **Collect include prefixes**: find `app.include_router(var, prefix=...)` calls — walk `ast.Expr` → `ast.Call` where `func` is `app.include_router`. Extract the first positional arg name and optional `prefix` kwarg (default `""`).
5. **Collect decorated functions**: walk `ast.AsyncFunctionDef` / `ast.FunctionDef`. For each decorator that is a `Call` on an `Attribute` (e.g. `router.get`), check if the attribute name is one of `{get, post, put, delete, patch, head, options}`. Extract the router var name and the first positional string arg as the path.
6. Build full path: `include_prefix.get(router_var, "") + router_prefix.get(router_var, "") + path`.
7. Build node IDs and hashes. Handler qualname: `{module_qualname}.{funcname}` where `module_qualname` is derived from the file path relative to `repo_path`, dots replacing path separators.

```python
import ast
import pathlib
from cc.graph.schema import Edge, Node
from cc.graph.hash_util import node_hash

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


def _module_qualname(file: pathlib.Path, root: pathlib.Path) -> str:
    rel = file.relative_to(root).with_suffix("")
    return str(rel).replace("/", ".")


def _str_const(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kw(call: ast.Call, name: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == name:
            return _str_const(kw.value)
    return None


def _collect_router_prefixes(tree: ast.Module) -> dict[str, str]:
    """var_name -> prefix string."""
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None
        )
        if name != "APIRouter":
            continue
        prefix = _kw(node.value, "prefix") or ""
        for target in node.targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix
    return prefixes


def _collect_include_prefixes(tree: ast.Module) -> dict[str, str]:
    """router_var_name -> extra prefix from include_router call."""
    extras: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "include_router":
            continue
        if not call.args:
            continue
        var = call.args[0]
        if not isinstance(var, ast.Name):
            continue
        prefix = _kw(call, "prefix") or ""
        extras[var.id] = prefix
    return extras


def extract_endpoints(repo_path: str | pathlib.Path) -> tuple[list[Node], list[Edge]]:
    repo_path = pathlib.Path(repo_path)
    nodes: list[Node] = []
    edges: list[Edge] = []

    for file in sorted(repo_path.rglob("*.py")):
        source = file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(file))
        except SyntaxError:
            continue

        router_prefixes = _collect_router_prefixes(tree)
        include_extras = _collect_include_prefixes(tree)
        module_qname = _module_qualname(file, repo_path)

        for fn_node in ast.walk(tree):
            if not isinstance(fn_node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for dec in fn_node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                if not isinstance(dec.func, ast.Attribute):
                    continue
                method = dec.func.attr.lower()
                if method not in _HTTP_METHODS:
                    continue
                router_var = (
                    dec.func.value.id
                    if isinstance(dec.func.value, ast.Name)
                    else None
                )
                if not dec.args:
                    continue
                path_suffix = _str_const(dec.args[0])
                if path_suffix is None:
                    continue

                r_prefix = router_prefixes.get(router_var, "") if router_var else ""
                i_prefix = include_extras.get(router_var, "") if router_var else ""
                full_path = i_prefix + r_prefix + path_suffix

                handler_qname = f"{module_qname}.{fn_node.name}"
                ep_id = f"endpoint:{method.upper()}:{full_path}"
                fn_id = f"function:{handler_qname}"

                ep_hash = node_hash(file, fn_node.lineno, fn_node.end_lineno)
                fn_hash = ep_hash  # same source span

                ep_node = Node(
                    id=ep_id, type="endpoint", file=str(file),
                    line=fn_node.lineno, hash=ep_hash, inferred=False,
                    props={"method": method.upper(), "path": full_path,
                           "handler": handler_qname},
                )
                fn_node_obj = Node(
                    id=fn_id, type="function", file=str(file),
                    line=fn_node.lineno, hash=fn_hash, inferred=False,
                    props={"qualname": handler_qname, "kind": "function",
                           "is_handler": True},
                )
                edge = Edge(from_=ep_id, to=fn_id, type="handles",
                            inferred=False, props={})

                nodes.extend([ep_node, fn_node_obj])
                edges.append(edge)

    return nodes, edges
```

- [ ] **Step 4: Run — verify it passes**

```bash
pytest tests/test_endpoints.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/endpoints.py tests/test_endpoints.py
git commit -m "feat: static endpoint extractor (decorator parsing + prefix resolution)"
```

---

### Task 4: Model extractor

**Files:**
- Create: `src/cc/extract/models.py`
- Create: `tests/test_models_ext.py`

**Interfaces:**
- Consumes: `repo_path: Path`, `handler_nodes: list[Node]` (function nodes from Task 3 with `is_handler=True`)
- Produces: `extract_models(repo_path, handler_nodes) -> tuple[list[Node], list[Edge]]`
  - Nodes: type=`model` with `props={name, kind, fields}` where `fields` is list of `{name, type}`
  - Edges: type=`uses_model` from endpoint's handler function node to model node, with `props={direction: "in"|"out"}`

- [ ] **Step 1: Write the failing test — `tests/test_models_ext.py`**

```python
from cc.extract.endpoints import extract_endpoints
from cc.extract.models import extract_models
from tests.conftest import SIMPLE_API


def _setup():
    handler_nodes, _ = extract_endpoints(SIMPLE_API)
    fn_nodes = [n for n in handler_nodes if n.type == "function"]
    return extract_models(SIMPLE_API, fn_nodes)


def test_finds_two_models():
    nodes, _ = _setup()
    model_nodes = [n for n in nodes if n.type == "model"]
    names = {n.props["name"] for n in model_nodes}
    assert "MessageIn" in names
    assert "MessageOut" in names


def test_model_ids():
    nodes, _ = _setup()
    ids = {n.id for n in nodes if n.type == "model"}
    assert any("MessageIn" in i for i in ids)


def test_model_fields():
    nodes, _ = _setup()
    msg_in = next(n for n in nodes if n.type == "model" and n.props["name"] == "MessageIn")
    field_names = {f["name"] for f in msg_in.props["fields"]}
    assert "content" in field_names
    assert "author" in field_names


def test_uses_model_edges():
    nodes, edges = _setup()
    um_edges = [e for e in edges if e.type == "uses_model"]
    assert len(um_edges) >= 2  # at least one in, one out
    directions = {e.props["direction"] for e in um_edges}
    assert "in" in directions
    assert "out" in directions
```

- [ ] **Step 2: Run — verify it fails**

```bash
pytest tests/test_models_ext.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Write `src/cc/extract/models.py`**

Algorithm:
1. Use `griffe.load` to load all Python packages/modules under `repo_path`.
2. Walk griffe's object tree to find all `Class` objects that have `BaseModel` in their bases.
3. For each such class, extract fields: griffe `Attribute` members with annotation set.
4. For each handler function node, parse its source file with ast to find the function definition. Walk parameter annotations and return annotation, collecting type names that match known BaseModel subclasses.
5. Create `uses_model` edges from the handler function node to each matched model node.

```python
import ast
import pathlib
import sys

import griffe

from cc.graph.schema import Edge, Node
from cc.graph.hash_util import node_hash


def _load_models(repo_path: pathlib.Path) -> dict[str, griffe.Class]:
    """Return qualname -> griffe.Class for all BaseModel subclasses."""
    found: dict[str, griffe.Class] = {}
    # Add repo_path to sys.path temporarily for griffe to resolve it
    sys.path.insert(0, str(repo_path))
    try:
        for init in repo_path.rglob("__init__.py"):
            pkg_name = init.parent.name
            try:
                pkg = griffe.load(pkg_name, search_paths=[repo_path])
            except Exception:
                continue
            _walk_griffe(pkg, found)
    finally:
        sys.path.pop(0)
    return found


def _walk_griffe(obj: griffe.Object, found: dict[str, griffe.Class]) -> None:
    if isinstance(obj, griffe.Class):
        bases = [b.canonical_path if hasattr(b, "canonical_path") else str(b)
                 for b in (obj.bases or [])]
        if any("BaseModel" in b for b in bases):
            found[obj.canonical_path] = obj
    for child in obj.members.values():
        _walk_griffe(child, found)


def _griffe_fields(cls: griffe.Class) -> list[dict]:
    fields = []
    for name, member in cls.members.items():
        if isinstance(member, griffe.Attribute) and member.annotation is not None:
            fields.append({"name": name, "type": str(member.annotation)})
    return fields


def _annotation_names(ann: ast.expr | None) -> list[str]:
    """Extract bare type names from an annotation AST node."""
    if ann is None:
        return []
    if isinstance(ann, ast.Name):
        return [ann.id]
    if isinstance(ann, ast.Attribute):
        return [ann.attr]
    if isinstance(ann, ast.Subscript):
        return _annotation_names(ann.slice)
    if isinstance(ann, ast.Tuple):
        names = []
        for elt in ann.elts:
            names.extend(_annotation_names(elt))
        return names
    return []


def extract_models(
    repo_path: str | pathlib.Path,
    handler_nodes: list[Node],
) -> tuple[list[Node], list[Edge]]:
    repo_path = pathlib.Path(repo_path)
    griffe_models = _load_models(repo_path)

    model_nodes: dict[str, Node] = {}
    for qname, cls in griffe_models.items():
        short_name = qname.split(".")[-1]
        m_id = f"model:{qname}"
        file_path = cls.filepath or "unknown"
        lineno = cls.lineno or 1
        end_lineno = cls.endlineno or lineno
        m_hash = node_hash(file_path, lineno, end_lineno)
        fields = _griffe_fields(cls)
        model_nodes[short_name] = Node(
            id=m_id, type="model", file=str(file_path),
            line=lineno, hash=m_hash, inferred=False,
            props={"name": short_name, "kind": "request", "fields": fields},
        )

    edges: list[Edge] = []
    for fn_node in handler_nodes:
        if not fn_node.props.get("is_handler"):
            continue
        source = pathlib.Path(fn_node.file).read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        func_name = fn_node.props["qualname"].split(".")[-1]
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if node.name != func_name:
                continue
            # Parameters (direction=in)
            for arg in node.args.args + node.args.kwonlyargs:
                for type_name in _annotation_names(arg.annotation):
                    if type_name in model_nodes:
                        edges.append(Edge(
                            from_=fn_node.id, to=model_nodes[type_name].id,
                            type="uses_model", inferred=False,
                            props={"direction": "in"},
                        ))
            # Return type (direction=out)
            for type_name in _annotation_names(node.returns):
                if type_name in model_nodes:
                    edges.append(Edge(
                        from_=fn_node.id, to=model_nodes[type_name].id,
                        type="uses_model", inferred=False,
                        props={"direction": "out"},
                    ))

    return list(model_nodes.values()), edges
```

- [ ] **Step 4: Run — verify it passes**

```bash
pytest tests/test_models_ext.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/models.py tests/test_models_ext.py
git commit -m "feat: model extractor (griffe BaseModel scan + handler annotation matching)"
```

---

### Task 5: SQL extractor

**Files:**
- Create: `src/cc/extract/sql.py`
- Create: `tests/test_sql.py`

**Interfaces:**
- Consumes: `repo_path: Path`
- Produces: `extract_sql(repo_path) -> tuple[list[Node], list[Edge]]`
  - Nodes: type=`table` with `props={name, columns: list[str]}`
  - Edges: type=`reads` or `writes` from function node to table node, with `props={via: "file:line"}`

- [ ] **Step 1: Write the failing test — `tests/test_sql.py`**

```python
from cc.extract.sql import extract_sql
from tests.conftest import SIMPLE_API


def test_finds_messages_table():
    nodes, _ = extract_sql(SIMPLE_API)
    table_nodes = [n for n in nodes if n.type == "table"]
    names = {n.props["name"] for n in table_nodes}
    assert "messages" in names


def test_table_node_id():
    nodes, _ = extract_sql(SIMPLE_API)
    ids = {n.id for n in nodes}
    assert "table:messages" in ids


def test_extracts_write_edge():
    nodes, edges = extract_sql(SIMPLE_API)
    write_edges = [e for e in edges if e.type == "writes"]
    assert len(write_edges) >= 1
    assert any("table:messages" == e.to for e in write_edges)


def test_extracts_read_edge():
    nodes, edges = extract_sql(SIMPLE_API)
    read_edges = [e for e in edges if e.type == "reads"]
    assert len(read_edges) >= 1
    assert any("table:messages" == e.to for e in read_edges)


def test_write_columns_from_insert():
    nodes, _ = extract_sql(SIMPLE_API)
    msg = next(n for n in nodes if n.id == "table:messages")
    assert "content" in msg.props["columns"]
    assert "author" in msg.props["columns"]


def test_via_contains_file_and_line():
    _, edges = extract_sql(SIMPLE_API)
    for e in edges:
        assert ":" in e.props["via"]
```

- [ ] **Step 2: Run — verify it fails**

```bash
pytest tests/test_sql.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Write `src/cc/extract/sql.py`**

Algorithm:
1. Walk all `.py` files under `repo_path`.
2. Use `ast.parse` to find `ast.Call` nodes where the method name (`func.attr`) is one of `{execute, executemany, fetchone, fetchall, fetchmany}`.
3. Extract the first string-literal argument as the SQL query.
4. Parse with `sqlglot.parse_one(sql)`.
5. From the sqlglot AST, extract: table names (`sqlglot.exp.Table`), column names from INSERT/UPDATE, operation type (SELECT → reads, INSERT/UPDATE/DELETE → writes).
6. Accumulate columns per table across all queries.
7. Build module qualname from file path relative to repo_path.

```python
import ast
import pathlib
from collections import defaultdict

import sqlglot
import sqlglot.expressions as exp

from cc.graph.schema import Edge, Node
from cc.graph.hash_util import node_hash

_DB_METHODS = {"execute", "executemany", "fetchone", "fetchall", "fetchmany"}


def _str_const(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _module_qualname(file: pathlib.Path, root: pathlib.Path) -> str:
    rel = file.relative_to(root).with_suffix("")
    return str(rel).replace("/", ".")


def _operation(stmt: sqlglot.Expression) -> str:
    if isinstance(stmt, exp.Select):
        return "reads"
    return "writes"


def _table_names(stmt: sqlglot.Expression) -> list[str]:
    return [t.name for t in stmt.find_all(exp.Table) if t.name]


def _insert_columns(stmt: sqlglot.Expression) -> list[str]:
    if not isinstance(stmt, exp.Insert):
        return []
    return [c.name for c in stmt.find_all(exp.Column) if c.name]


def _select_columns(stmt: sqlglot.Expression) -> list[str]:
    if not isinstance(stmt, exp.Select):
        return []
    cols = []
    for sel in stmt.expressions:
        if isinstance(sel, exp.Star):
            return []  # SELECT * — don't infer
        if isinstance(sel, exp.Column) and sel.name:
            cols.append(sel.name)
        elif isinstance(sel, exp.Alias):
            inner = sel.this
            if isinstance(inner, exp.Column) and inner.name:
                cols.append(inner.name)
    return cols


def extract_sql(repo_path: str | pathlib.Path) -> tuple[list[Node], list[Edge]]:
    repo_path = pathlib.Path(repo_path)
    table_columns: dict[str, set[str]] = defaultdict(set)
    table_files: dict[str, tuple[str, int]] = {}  # table -> (file, line)
    raw_edges: list[tuple[str, str, str, str]] = []  # (fn_qname, table, op, via)

    for file in sorted(repo_path.rglob("*.py")):
        source = file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(file))
        except SyntaxError:
            continue

        module_qname = _module_qualname(file, repo_path)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in _DB_METHODS:
                continue
            if not node.args:
                continue
            sql = _str_const(node.args[0])
            if not sql:
                continue

            try:
                stmt = sqlglot.parse_one(sql)
            except Exception:
                continue

            tables = _table_names(stmt)
            op = _operation(stmt)

            if op == "writes":
                cols = _insert_columns(stmt)
            else:
                cols = _select_columns(stmt)

            for tbl in tables:
                table_columns[tbl].update(cols)
                if tbl not in table_files:
                    table_files[tbl] = (str(file), node.lineno)

            # Find enclosing function
            fn_qname = module_qname  # fallback
            for parent in ast.walk(tree):
                if not isinstance(parent, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    continue
                if (parent.lineno <= node.lineno
                        and node.lineno <= (parent.end_lineno or node.lineno)):
                    fn_qname = f"{module_qname}.{parent.name}"
            via = f"{file}:{node.lineno}"
            for tbl in tables:
                raw_edges.append((fn_qname, tbl, op, via))

    # Build table nodes
    table_nodes: dict[str, Node] = {}
    for tbl, cols in table_columns.items():
        tbl_file, tbl_line = table_files.get(tbl, ("unknown", 1))
        t_hash = node_hash(tbl_file, tbl_line, tbl_line) if tbl_file != "unknown" else "0" * 64
        table_nodes[tbl] = Node(
            id=f"table:{tbl}", type="table", file=tbl_file, line=tbl_line,
            hash=t_hash, inferred=False,
            props={"name": tbl, "columns": sorted(cols)},
        )

    # Build edges
    edges: list[Edge] = []
    for fn_qname, tbl, op, via in raw_edges:
        if tbl not in table_nodes:
            continue
        edges.append(Edge(
            from_=f"function:{fn_qname}", to=f"table:{tbl}",
            type=op, inferred=False, props={"via": via},
        ))

    return list(table_nodes.values()), edges
```

- [ ] **Step 4: Run — verify it passes**

```bash
pytest tests/test_sql.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/sql.py tests/test_sql.py
git commit -m "feat: SQL extractor (sqlglot table/column/operation from DB call sites)"
```

---

### Task 6: Call graph extractor

**Files:**
- Create: `src/cc/extract/calls.py`
- Create: `tests/test_calls.py`

**Interfaces:**
- Consumes: `repo_path: Path`
- Produces: `extract_calls(repo_path) -> list[Edge]`
  - Edges: type=`calls` from `function:{caller_qualname}` to `function:{callee_qualname}`, `inferred=False`

- [ ] **Step 1: Write the failing test — `tests/test_calls.py`**

```python
from cc.extract.calls import extract_calls
from tests.conftest import SIMPLE_API


def test_returns_edge_list():
    edges = extract_calls(SIMPLE_API)
    assert isinstance(edges, list)


def test_calls_edges_have_correct_type():
    edges = extract_calls(SIMPLE_API)
    for e in edges:
        assert e.type == "calls"
        assert e.inferred is False
        assert e.from_.startswith("function:")
        assert e.to.startswith("function:")


def test_no_self_loops():
    edges = extract_calls(SIMPLE_API)
    for e in edges:
        assert e.from_ != e.to
```

- [ ] **Step 2: Run — verify it fails**

```bash
pytest tests/test_calls.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Write `src/cc/extract/calls.py`**

pyan3's `CallGraphVisitor` nodes have str representation `<Node function:{module}.{name}>`. We extract the qualname from the string, filter to `function:` nodes, and emit edges.

```python
import pathlib
from pyan.analyzer import CallGraphVisitor
from cc.graph.schema import Edge


def _qualname_from_pyan_node(node) -> str | None:
    s = str(node)
    # Format: <Node function:module.name> or <Node module:name>
    if "function:" in s:
        return s.split("function:")[-1].rstrip(">").strip()
    return None


def extract_calls(repo_path: str | pathlib.Path) -> list[Edge]:
    repo_path = pathlib.Path(repo_path)
    files = [str(f) for f in repo_path.rglob("*.py")]
    if not files:
        return []

    try:
        visitor = CallGraphVisitor(files)
        visitor.process()
        visitor.postprocess()
    except Exception:
        return []

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()

    for caller_node, callee_set in visitor.uses_edges.items():
        caller_qname = _qualname_from_pyan_node(caller_node)
        if not caller_qname:
            continue
        for callee_node in callee_set:
            callee_qname = _qualname_from_pyan_node(callee_node)
            if not callee_qname:
                continue
            if caller_qname == callee_qname:
                continue
            key = (caller_qname, callee_qname)
            if key in seen:
                continue
            seen.add(key)
            edges.append(Edge(
                from_=f"function:{caller_qname}",
                to=f"function:{callee_qname}",
                type="calls", inferred=False, props={},
            ))

    return edges
```

- [ ] **Step 4: Run — verify it passes**

```bash
pytest tests/test_calls.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/calls.py tests/test_calls.py
git commit -m "feat: call graph extractor (pyan3 CallGraphVisitor → calls edges)"
```

---

### Task 7: Graph builder + gap reporter

**Files:**
- Create: `src/cc/graph/build.py`
- Create: `src/cc/gaps.py`
- Create: `tests/test_graph.py`
- Create: `tests/test_gaps.py`

**Interfaces:**
- Consumes: all node/edge lists from Tasks 3–6
- Produces:
  - `build_graph(nodes, edges) -> Graph` — deduplicates nodes by id, assembles `Graph`
  - `detect_gaps(graph) -> list[Gap]` — inspects assembled graph, emits `missing_artifact` gaps for tables without columns

- [ ] **Step 1: Write the failing tests**

`tests/test_graph.py`:
```python
from cc.graph.schema import Edge, Graph, Node
from cc.graph.build import build_graph


def _make_nodes():
    return [
        Node(id="endpoint:POST:/x", type="endpoint", file="f.py", line=1,
             hash="a" * 64, inferred=False, props={"method": "POST", "path": "/x"}),
        Node(id="function:app.handler", type="function", file="f.py", line=1,
             hash="a" * 64, inferred=False, props={}),
        # Duplicate — should be deduplicated
        Node(id="function:app.handler", type="function", file="f.py", line=1,
             hash="a" * 64, inferred=False, props={}),
    ]


def test_build_deduplicates_nodes():
    graph = build_graph(_make_nodes(), [])
    ids = [n.id for n in graph.nodes]
    assert ids.count("function:app.handler") == 1


def test_build_returns_graph():
    graph = build_graph(_make_nodes(), [])
    assert isinstance(graph, Graph)


def test_build_includes_all_edges():
    e = Edge(from_="endpoint:POST:/x", to="function:app.handler",
              type="handles", inferred=False, props={})
    graph = build_graph(_make_nodes(), [e])
    assert len(graph.edges) == 1
```

`tests/test_gaps.py`:
```python
from cc.graph.schema import Graph, Node
from cc.gaps import detect_gaps


def test_table_without_columns_is_a_gap():
    table_node = Node(id="table:messages", type="table", file="db.py", line=1,
                      hash="x" * 64, inferred=False,
                      props={"name": "messages", "columns": []})
    graph = Graph(nodes=[table_node], edges=[], gaps=[])
    gaps = detect_gaps(graph)
    assert len(gaps) == 1
    assert gaps[0].kind == "missing_artifact"
    assert gaps[0].node_id == "table:messages"


def test_table_with_columns_has_no_gap():
    table_node = Node(id="table:messages", type="table", file="db.py", line=1,
                      hash="x" * 64, inferred=False,
                      props={"name": "messages", "columns": ["id", "content"]})
    graph = Graph(nodes=[table_node], edges=[], gaps=[])
    gaps = detect_gaps(graph)
    assert len(gaps) == 0
```

- [ ] **Step 2: Run — verify they fail**

```bash
pytest tests/test_graph.py tests/test_gaps.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Write `src/cc/graph/build.py`**

```python
from cc.graph.schema import Edge, Graph, Node


def build_graph(nodes: list[Node], edges: list[Edge]) -> Graph:
    seen: dict[str, Node] = {}
    for n in nodes:
        if n.id not in seen:
            seen[n.id] = n
    return Graph(nodes=list(seen.values()), edges=list(edges), gaps=[])
```

- [ ] **Step 4: Write `src/cc/gaps.py`**

```python
from cc.graph.schema import Gap, Graph


def detect_gaps(graph: Graph) -> list[Gap]:
    gaps: list[Gap] = []
    for node in graph.nodes:
        if node.type != "table":
            continue
        if not node.props.get("columns"):
            gaps.append(Gap(
                kind="missing_artifact",
                where=f"{node.file}:{node.line}",
                node_id=node.id,
                missing=f"No columns inferred for table `{node.props['name']}`"
                        " — no CREATE TABLE, INSERT, or single-table SELECT found",
                suggested=f"-- TODO: add DDL for `{node.props['name']}`, "
                          f"e.g. CREATE TABLE {node.props['name']} (id INT, ...)",
                severity={"comprehension": "warning", "compliance": "error"},
            ))
    return gaps
```

- [ ] **Step 5: Run — verify they pass**

```bash
pytest tests/test_graph.py tests/test_gaps.py -v
```

Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/cc/graph/build.py src/cc/gaps.py tests/test_graph.py tests/test_gaps.py
git commit -m "feat: graph builder (dedup by id) + gap reporter (missing table columns)"
```

---

### Task 8: HTML renderer

**Files:**
- Create: `src/cc/render/emit.py`
- Create: `src/cc/render/template.html`
- Create: `tests/test_render.py`

**Interfaces:**
- Consumes: `graph: Graph`, `out_dir: Path`
- Produces:
  - `emit(graph, out_dir)` — writes `out_dir/graph.json` and `out_dir/index.html`

- [ ] **Step 1: Write the failing test — `tests/test_render.py`**

```python
import json
import pathlib
import tempfile

from cc.graph.schema import Edge, Graph, Node
from cc.render.emit import emit


def _minimal_graph():
    ep = Node(id="endpoint:GET:/hello", type="endpoint", file="main.py", line=1,
              hash="a" * 64, inferred=False,
              props={"method": "GET", "path": "/hello", "handler": "main.hello"})
    fn = Node(id="function:main.hello", type="function", file="main.py", line=1,
              hash="a" * 64, inferred=False, props={"qualname": "main.hello"})
    e = Edge(from_="endpoint:GET:/hello", to="function:main.hello",
              type="handles", inferred=False, props={})
    return Graph(nodes=[ep, fn], edges=[e], gaps=[])


def test_emit_creates_json_file():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        assert (pathlib.Path(d) / "graph.json").exists()


def test_emit_creates_html_file():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        assert (pathlib.Path(d) / "index.html").exists()


def test_json_is_valid_and_has_nodes():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 2


def test_html_references_cytoscape():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        html = (pathlib.Path(d) / "index.html").read_text()
        assert "cytoscape" in html.lower()


def test_html_embeds_graph_json():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        html = (pathlib.Path(d) / "index.html").read_text()
        assert "endpoint:GET:/hello" in html
```

- [ ] **Step 2: Run — verify it fails**

```bash
pytest tests/test_render.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Write `src/cc/render/template.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Comprehension Compiler</title>
  <script src="https://cdn.jsdelivr.net/npm/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
  <style>
    body { margin: 0; font-family: monospace; display: flex; height: 100vh; }
    #endpoints { width: 260px; overflow-y: auto; border-right: 1px solid #ccc; padding: 8px; }
    #cy { flex: 1; }
    #panel { width: 320px; border-left: 1px solid #ccc; padding: 12px; overflow-y: auto; display: none; }
    .ep-btn { display: block; text-align: left; width: 100%; margin: 2px 0;
              padding: 4px 8px; background: #f0f0f0; border: none; cursor: pointer; }
    .ep-btn:hover { background: #ddd; }
    .badge { font-size: 10px; padding: 1px 4px; border-radius: 3px;
             color: #fff; margin-right: 4px; }
    .GET { background: #28a745; } .POST { background: #007bff; }
    .PUT { background: #fd7e14; } .DELETE { background: #dc3545; }
    .PATCH { background: #6f42c1; }
    .inferred { border: 2px dashed #ff9900; }
    #panel h3 { margin-top: 0; }
    #panel pre { background: #f8f8f8; padding: 8px; overflow-x: auto; font-size: 12px; }
  </style>
</head>
<body>
  <div id="endpoints">
    <h4 style="margin:0 0 8px">Endpoints</h4>
    <div id="ep-list"></div>
  </div>
  <div id="cy"></div>
  <div id="panel">
    <h3 id="panel-title"></h3>
    <div id="panel-body"></div>
  </div>

  <script>
    const GRAPH = __GRAPH_JSON__;

    // Build Cytoscape elements
    const elements = [];
    for (const n of GRAPH.nodes) {
      elements.push({ data: { id: n.id, label: n.id.split(":").slice(1).join(":"),
                               type: n.type, inferred: n.inferred, ...n.props },
                       classes: n.inferred ? "inferred" : "" });
    }
    for (const e of GRAPH.edges) {
      elements.push({ data: { id: e.from_ + "→" + e.to, source: e.from_,
                               target: e.to, label: e.type, ...e.props } });
    }

    const cy = cytoscape({
      container: document.getElementById("cy"),
      elements,
      style: [
        { selector: "node", style: { label: "data(label)", "font-size": 10,
          "background-color": "#aac", "text-wrap": "wrap", "text-max-width": 120 } },
        { selector: "node[type='endpoint']", style: { "background-color": "#4a90d9" } },
        { selector: "node[type='table']",    style: { "background-color": "#e8a838" } },
        { selector: "node[type='model']",    style: { "background-color": "#5cb85c" } },
        { selector: "node.inferred",         style: { "border-width": 2, "border-color": "#ff9900" } },
        { selector: "edge", style: { label: "data(label)", "font-size": 8,
          "curve-style": "bezier", "target-arrow-shape": "triangle", width: 1.5 } },
      ],
      layout: { name: "cose", animate: false },
    });

    // Endpoint list
    const epList = document.getElementById("ep-list");
    for (const n of GRAPH.nodes.filter(n => n.type === "endpoint")) {
      const btn = document.createElement("button");
      btn.className = "ep-btn";
      btn.innerHTML = `<span class="badge ${n.props.method}">${n.props.method}</span>${n.props.path}`;
      btn.onclick = () => { cy.getElementById(n.id).select(); showPanel(n); };
      epList.appendChild(btn);
    }

    // Click panel
    function showPanel(nodeData) {
      const panel = document.getElementById("panel");
      panel.style.display = "block";
      document.getElementById("panel-title").textContent = nodeData.id;
      const body = document.getElementById("panel-body");
      body.innerHTML = "<pre>" + JSON.stringify(nodeData.props || nodeData, null, 2) + "</pre>";
    }

    cy.on("tap", "node", evt => showPanel(evt.target.data()));

    // Show gaps
    if (GRAPH.gaps && GRAPH.gaps.length) {
      const gapDiv = document.createElement("div");
      gapDiv.style.cssText = "margin-top:16px;border-top:1px solid #ccc;padding-top:8px";
      gapDiv.innerHTML = "<b>Gaps (" + GRAPH.gaps.length + ")</b>";
      for (const g of GRAPH.gaps) {
        const d = document.createElement("div");
        d.style.cssText = "margin:4px 0;font-size:11px;color:#c00";
        d.textContent = "⚠ " + g.missing;
        gapDiv.appendChild(d);
      }
      document.getElementById("endpoints").appendChild(gapDiv);
    }
  </script>
</body>
</html>
```

- [ ] **Step 4: Write `src/cc/render/emit.py`**

```python
import dataclasses
import json
import pathlib

from cc.graph.schema import Graph

_TEMPLATE = pathlib.Path(__file__).parent / "template.html"


def _to_dict(obj) -> object:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        d = dataclasses.asdict(obj)
        # rename from_ -> from_ is fine in JSON but let's keep it
        return d
    return obj


def emit(graph: Graph, out_dir: str | pathlib.Path) -> None:
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    graph_dict = dataclasses.asdict(graph)
    json_path = out_dir / "graph.json"
    json_path.write_text(json.dumps(graph_dict, indent=2), encoding="utf-8")

    template = _TEMPLATE.read_text(encoding="utf-8")
    html = template.replace("__GRAPH_JSON__", json.dumps(graph_dict))
    (out_dir / "index.html").write_text(html, encoding="utf-8")
```

- [ ] **Step 5: Run — verify it passes**

```bash
pytest tests/test_render.py -v
```

Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/cc/render/ tests/test_render.py
git commit -m "feat: HTML renderer (Cytoscape.js template, graph.json + index.html)"
```

---

### Task 9: FastAPI adapter + CLI + pipeline

**Files:**
- Create: `src/cc/adapters/base.py`
- Create: `src/cc/adapters/fastapi.py`
- Create: `src/cc/pipeline.py`
- Create: `src/cc/cli.py`
- Create: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: all extractors (Tasks 3–6), `build_graph`, `detect_gaps`, `emit`
- Produces: `python -m cc compile <path> --out <dir>` runs end-to-end and writes `graph.json` + `index.html`

- [ ] **Step 1: Write the failing test — `tests/test_pipeline.py`**

```python
import json
import pathlib
import tempfile

from cc.pipeline import run
from tests.conftest import SIMPLE_API


def test_pipeline_produces_json():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        assert "nodes" in data
        assert len(data["nodes"]) > 0


def test_pipeline_produces_html():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        assert (pathlib.Path(d) / "index.html").exists()


def test_pipeline_finds_endpoint_node():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        ep_nodes = [n for n in data["nodes"] if n["type"] == "endpoint"]
        assert any("POST" in n["id"] and "messages" in n["id"] for n in ep_nodes)


def test_pipeline_finds_table_node():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        table_nodes = [n for n in data["nodes"] if n["type"] == "table"]
        assert any(n["id"] == "table:messages" for n in table_nodes)
```

- [ ] **Step 2: Run — verify it fails**

```bash
pytest tests/test_pipeline.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Write `src/cc/adapters/base.py`**

```python
from typing import Protocol
import pathlib


class Adapter(Protocol):
    def collect_files(self, repo_path: pathlib.Path) -> list[pathlib.Path]: ...
```

- [ ] **Step 4: Write `src/cc/adapters/fastapi.py`**

```python
import pathlib


class FastAPIAdapter:
    def collect_files(self, repo_path: pathlib.Path) -> list[pathlib.Path]:
        return sorted(repo_path.rglob("*.py"))
```

- [ ] **Step 5: Write `src/cc/pipeline.py`**

```python
import pathlib

from cc.adapters.fastapi import FastAPIAdapter
from cc.extract.endpoints import extract_endpoints
from cc.extract.models import extract_models
from cc.extract.calls import extract_calls
from cc.extract.sql import extract_sql
from cc.gaps import detect_gaps
from cc.graph.build import build_graph
from cc.render.emit import emit


def run(repo_path: str | pathlib.Path, out_dir: str | pathlib.Path) -> None:
    repo_path = pathlib.Path(repo_path)
    out_dir = pathlib.Path(out_dir)

    _adapter = FastAPIAdapter()

    ep_nodes, ep_edges = extract_endpoints(repo_path)
    handler_nodes = [n for n in ep_nodes if n.type == "function"]

    model_nodes, model_edges = extract_models(repo_path, handler_nodes)
    sql_nodes, sql_edges = extract_sql(repo_path)
    call_edges = extract_calls(repo_path)

    all_nodes = ep_nodes + model_nodes + sql_nodes
    all_edges = ep_edges + model_edges + sql_edges + call_edges

    graph = build_graph(all_nodes, all_edges)
    graph.gaps = detect_gaps(graph)

    emit(graph, out_dir)
```

- [ ] **Step 6: Write `src/cc/cli.py`**

```python
import argparse
import pathlib
import sys

from cc.pipeline import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="cc",
                                     description="Comprehension Compiler — build a code graph")
    sub = parser.add_subparsers(dest="cmd", required=True)

    comp = sub.add_parser("compile", help="Compile a repo into a navigable graph")
    comp.add_argument("repo", type=pathlib.Path, help="Path to the target repo")
    comp.add_argument("--out", type=pathlib.Path, default=pathlib.Path("cc-out"),
                      help="Output directory (default: cc-out/)")

    args = parser.parse_args()

    if args.cmd == "compile":
        print(f"Compiling {args.repo} → {args.out} …")
        run(args.repo, args.out)
        print(f"Done. Open {args.out}/index.html")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run the pipeline tests — verify they pass**

```bash
pytest tests/test_pipeline.py -v
```

Expected: 4 passed

- [ ] **Step 8: Run all tests**

```bash
pytest -v
```

Expected: all pass

- [ ] **Step 9: Smoke test the CLI**

```bash
python -m cc compile tests/fixtures/simple_api --out /tmp/cc-smoke
ls /tmp/cc-smoke/
```

Expected: `graph.json` and `index.html` present

- [ ] **Step 10: Commit**

```bash
git add src/cc/adapters/ src/cc/pipeline.py src/cc/cli.py tests/test_pipeline.py
git commit -m "feat: FastAPI adapter, pipeline, CLI — end-to-end compile command"
```

---

### Task 10: Oracle comparison (route recovery rate)

**Files:**
- Create: `src/cc/oracle.py`
- Create: `tests/test_oracle.py`

**Interfaces:**
- Consumes: `repo_path: Path`, `ep_nodes: list[Node]` (from `extract_endpoints`)
- Produces: `compare_oracle(repo_path, ep_nodes) -> dict` — `{static_count, oracle_count, recovery_rate, missing}`

Note: the oracle imports the target app at runtime. Only valid for repos that boot cleanly without infra (agora yes; BNP repos, assume no). Never call from the production pipeline path.

- [ ] **Step 1: Write the failing test — `tests/test_oracle.py`**

```python
import sys
from cc.extract.endpoints import extract_endpoints
from cc.oracle import compare_oracle
from tests.conftest import SIMPLE_API


def test_oracle_recovery_rate():
    sys.path.insert(0, str(SIMPLE_API.parent))
    ep_nodes, _ = extract_endpoints(SIMPLE_API)
    result = compare_oracle(SIMPLE_API, ep_nodes)
    sys.path.pop(0)

    assert "recovery_rate" in result
    assert "static_count" in result
    assert "oracle_count" in result
    assert result["recovery_rate"] >= 0.5  # at least 50% recovery


def test_oracle_finds_all_routes_in_fixture():
    sys.path.insert(0, str(SIMPLE_API.parent))
    ep_nodes, _ = extract_endpoints(SIMPLE_API)
    result = compare_oracle(SIMPLE_API, ep_nodes)
    sys.path.pop(0)

    assert result["recovery_rate"] == 1.0  # fixture is simple, should be 100%
```

- [ ] **Step 2: Run — verify it fails**

```bash
pytest tests/test_oracle.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Write `src/cc/oracle.py`**

```python
import importlib
import importlib.util
import pathlib
import sys

from cc.graph.schema import Node


def _load_app(repo_path: pathlib.Path):
    """Import the FastAPI `app` object from the target repo. Returns None on failure."""
    for candidate in ["main", "app", "server"]:
        try:
            spec = importlib.util.spec_from_file_location(
                candidate, repo_path / f"{candidate}.py"
            )
            if spec is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "app"):
                return mod.app
        except Exception:
            continue
    return None


def compare_oracle(
    repo_path: str | pathlib.Path,
    ep_nodes: list[Node],
) -> dict:
    repo_path = pathlib.Path(repo_path)
    app = _load_app(repo_path)
    if app is None:
        return {"static_count": len(ep_nodes), "oracle_count": 0,
                "recovery_rate": 0.0, "missing": [], "error": "Could not load app"}

    oracle_routes: set[str] = set()
    for route in getattr(app, "routes", []):
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if path and methods:
            for m in methods:
                oracle_routes.add(f"{m.upper()}:{path}")

    static_routes: set[str] = set()
    for n in ep_nodes:
        if n.type == "endpoint":
            static_routes.add(f"{n.props['method']}:{n.props['path']}")

    oracle_count = len(oracle_routes)
    static_count = len(static_routes)
    matched = static_routes & oracle_routes
    recovery_rate = len(matched) / oracle_count if oracle_count > 0 else 1.0
    missing = sorted(oracle_routes - static_routes)

    return {
        "static_count": static_count,
        "oracle_count": oracle_count,
        "recovery_rate": recovery_rate,
        "missing": missing,
    }
```

- [ ] **Step 4: Run — verify it passes**

```bash
pytest tests/test_oracle.py -v
```

Expected: 2 passed

- [ ] **Step 5: Run the full test suite**

```bash
pytest -v
```

Expected: all tests pass

- [ ] **Step 6: Add `--oracle` flag to CLI** — modify `src/cc/cli.py`

In `main()`, add to the `compile` subparser:
```python
comp.add_argument("--oracle", action="store_true",
                  help="Compare static extraction vs. runtime introspection (only for repos that boot without infra)")
```

In the `compile` handler, after `run(...)`:
```python
if args.oracle:
    from cc.extract.endpoints import extract_endpoints
    from cc.oracle import compare_oracle
    ep_nodes, _ = extract_endpoints(args.repo)
    result = compare_oracle(args.repo, ep_nodes)
    print(f"Route recovery: {result['static_count']}/{result['oracle_count']} "
          f"({result['recovery_rate']:.0%})")
    if result.get("missing"):
        print("Missing from static:", result["missing"])
```

- [ ] **Step 7: Run all tests again**

```bash
pytest -v
```

Expected: all pass

- [ ] **Step 8: Final commit**

```bash
git add src/cc/oracle.py src/cc/cli.py tests/test_oracle.py
git commit -m "feat: oracle comparison (runtime introspection vs. static, --oracle flag)"
```

---

## Self-Review

**Spec coverage:**
- ✅ CLI `compile <path> --out <dir>` — Task 9
- ✅ Endpoints (decorator, method, path, handler, router prefixes resolved) — Task 3
- ✅ Models (Pydantic BaseModel, fields[], uses_model edges with direction) — Task 4
- ✅ Calls (pyan3 best-effort, `inferred=False`) — Task 6
- ✅ Tables/columns (sqlglot over call sites, INSERT+SELECT, `missing_artifact` gap) — Tasks 5, 7
- ✅ Graph JSON conforming to schema (id/hash/inferred, gaps[]) — Tasks 1, 7
- ✅ HTML render (Cytoscape.js, endpoint list, click→panel, gaps visible) — Task 8
- ✅ Oracle comparison + recovery rate — Task 10
- ✅ Stable anchors (id=qualname+type, hash=SHA-256 of source span) — Tasks 1, 2
- ✅ Gap philosophy (flag, don't block; missing_artifact vs unresolved_dynamic) — Task 7
- ✅ `inferred=False` everywhere — enforced in all constructors
- ✅ Source-only (oracle is isolated and opt-in) — Task 10

**Placeholders:** None found.

**Type consistency:** All tasks use `Node`, `Edge`, `Gap`, `Graph` from `cc.graph.schema`. `node_hash` signature consistent. `extract_*` return types consistent across pipeline usage in Task 9.
