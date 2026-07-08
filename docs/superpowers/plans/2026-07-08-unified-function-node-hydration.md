# Unified Function-Node Hydration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the four independent, disagreeing ways `endpoints.py`/`calls.py`/`sql.py` build a `function`-type `Node` with one shared hydration point, so no two extractors can ever compute a different `line`/`hash` for the same real function again — closing the crash the final review of the previous SQL-hydration bugfix plan found (decorated functions made griffe's line and AST's line disagree, and the new `build_graph` identity assertion turned that silent disagreement into a hard crash).

**Architecture:** A new module, `src/cc/extract/_node_hydration.py`, exposes two functions: `node_from_ast_def` (pure — given an already-parsed AST def node, computes the canonical `line`/`hash`) and `hydrate_function_node` (griffe-backed — locates a qualname's file via the shared `SymbolInventory`, parses that file once per pipeline run via a shared cache, and delegates to `node_from_ast_def`). All four function-node call sites (`endpoints.py`'s handler, `calls.py`'s caller, `calls.py`'s callee, `sql.py`'s DB-toucher) call `hydrate_function_node` first and fall back to their own already-parsed local AST node via `node_from_ast_def` only when griffe can't resolve the qualname — so every emitter agrees on the same value whichever path succeeds. `pipeline.py` builds one shared `SymbolInventory` (already done by the prior plan) and now also one shared `ast_cache` dict, threading both into all three extractors that need them.

**Tech Stack:** Python 3.11 stdlib (`ast`), `griffe` (via the existing `SymbolInventory` from `src/cc/extract/_calls_resolver.py`).

## Convention Being Established (write this into ESQUEMA_POC.md — Task 1)

- `line` on a `function`/`endpoint` node is the bare `def`/`async def` keyword's own line — decorators excluded. This is for human navigation: clicking a node should land on the definition, not a decorator three lines above it.
- `hash` on a `function`/`endpoint` node covers the **decorator-inclusive span** — from the first decorator (if any) through the def's `end_lineno`. Decorators are part of the unit's meaning (`@router.post(...)` on a handler, a caching/auth decorator on a helper) — editing one must count as an edit to the node.
- Every function-node emitter in the pipeline computes both values through the same code path (`src/cc/extract/_node_hydration.py`) — no extractor may compute its own `line`/`hash` independently again.

## Global Constraints

- `build_graph`'s identity assertion (added by the prior plan, `src/cc/graph/build.py`) is **not modified** by this plan — it's the backstop this plan is *proving out*, not weakening. If it fires after this plan's changes, that's a real remaining bug, not something to loosen the check for.
- No extractor may regress coverage: every function-node emitter must keep working (via its local `node_from_ast_def`-based fallback) even when `hydrate_function_node` returns `None` (griffe can't resolve the qualname — e.g. a closure, or a symbol in a package griffe failed to load).
- `via` (the SQL call-site edge prop) is untouched by this plan — still `f"{file}:{node.lineno}"`, still lives only in edge `props`.
- `extract_endpoints(repo_path, exclude_patterns)`, `extract_sql(repo_path, exclude_patterns)`, `extract_calls(repo_path, exclude_patterns)` must keep working unchanged for every existing 2-positional-arg call site — the new `inventory`/`ast_cache` parameters are optional, defaulting to "build/use a fresh one internally."
- Reuse `node_hash(file, lineno, end_lineno)` from `src/cc/graph/hash_util.py` — never reimplement hashing. This plan changes *what span* gets passed to it, never how hashing itself works.
- The pre-existing `sql.py`/`endpoints.py` `_module_qualname` inconsistency with `__init__.py` files (documented separately in `BACKLOG.md` after this plan merges) is explicitly **out of scope** — do not fix it as part of this plan. A `hydrate_function_node` miss caused by that inconsistency must fall back gracefully (this plan's whole point), not be "fixed" by also touching qualname computation.
- Do not modify the shared `tests/fixtures/simple_api/` or `tests/fixtures/calls_repo/` fixture files — new scenarios needing a different repo layout use `tmp_path`-based inline fixtures, matching this project's established test style.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/cc/extract/_node_hydration.py` | New. `node_from_ast_def` (pure span computation) + `hydrate_function_node` (griffe-backed lookup + cached AST parse, delegates to `node_from_ast_def`). |
| `doc_proyecto/ESQUEMA_POC.md` | Modified: documents the `line`=def-line / `hash`=decorator-inclusive-span convention. |
| `src/cc/extract/sql.py` | Modified: DB-toucher function nodes hydrate via `hydrate_function_node`, falling back to `node_from_ast_def` using the enclosing def's own AST node (which `_find_enclosing_function` now returns directly, replacing its line-number-tuple return). |
| `src/cc/extract/calls.py` | Modified: both the "caller" node (every function visited) and the "callee" node (a resolved internal call target) hydrate via `hydrate_function_node`, with the caller path falling back to `node_from_ast_def` using its own already-parsed `fn_node`. |
| `src/cc/extract/endpoints.py` | Modified: the handler's function node hydrates via `hydrate_function_node`, falling back to `node_from_ast_def` using its own already-found `fn_node`; the endpoint node's own `line`/`hash` are derived from the (now shared) handler node's values, same as today's "same source span" intent. |
| `src/cc/pipeline.py` | Modified: builds one shared `ast_cache: dict[str, ast.Module \| None]` alongside the existing shared `inventory`, and threads both into `extract_endpoints`, `extract_sql`, `extract_calls`. |
| `tests/test_node_hydration.py` | New. Unit tests for both functions in the new module. |
| `tests/test_pipeline.py` | Extended: the "immortalized" regression fixture — one decorated function that is simultaneously a caller, a callee, and touches a table. |

---

### Task 1: `_node_hydration.py` + schema convention doc

**Files:**
- Create: `src/cc/extract/_node_hydration.py`
- Modify: `doc_proyecto/ESQUEMA_POC.md`
- Test: `tests/test_node_hydration.py`

**Interfaces:**
- Consumes: `SymbolInventory`, `FuncInfo` (`src/cc/extract/_calls_resolver.py`); `node_hash` (`src/cc/graph/hash_util.py`); `Node` (`src/cc/graph/schema.py`).
- Produces: `node_from_ast_def(def_node: ast.FunctionDef | ast.AsyncFunctionDef, file: str, qualname: str, kind: str, is_handler: bool = False) -> Node`; `hydrate_function_node(qualname: str, inventory: SymbolInventory, ast_cache: dict[str, ast.Module | None], is_handler: bool = False) -> Node | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_node_hydration.py
import ast

from cc.extract._calls_resolver import FuncInfo, SymbolInventory
from cc.extract._node_hydration import hydrate_function_node, node_from_ast_def
from cc.graph.hash_util import node_hash


def _parse_def(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(source)
    return next(
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def test_node_from_ast_def_line_excludes_decorators(tmp_path):
    f = tmp_path / "mod.py"
    source = (
        "@audit\n"
        "async def get_active_roster(cur):\n"
        "    return 1\n"
    )
    f.write_text(source, encoding="utf-8")
    def_node = _parse_def(source)
    node = node_from_ast_def(def_node, str(f), "mod.get_active_roster", "function")
    assert node.line == 2  # the `async def` line, not the decorator's line 1


def test_node_from_ast_def_hash_includes_decorators(tmp_path):
    f = tmp_path / "mod.py"
    source = (
        "@audit\n"
        "async def get_active_roster(cur):\n"
        "    return 1\n"
    )
    f.write_text(source, encoding="utf-8")
    def_node = _parse_def(source)
    node = node_from_ast_def(def_node, str(f), "mod.get_active_roster", "function")
    assert node.hash == node_hash(f, 1, 3)  # decorator (line 1) through end (line 3)


def test_node_from_ast_def_undecorated_span_starts_at_def_line(tmp_path):
    f = tmp_path / "mod.py"
    source = "def plain():\n    return 1\n"
    f.write_text(source, encoding="utf-8")
    def_node = _parse_def(source)
    node = node_from_ast_def(def_node, str(f), "mod.plain", "function")
    assert node.line == 1
    assert node.hash == node_hash(f, 1, 2)


def test_node_from_ast_def_sets_id_and_props(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("def f():\n    pass\n", encoding="utf-8")
    def_node = _parse_def("def f():\n    pass\n")
    node = node_from_ast_def(def_node, str(f), "mod.f", "method", is_handler=True)
    assert node.id == "function:mod.f"
    assert node.type == "function"
    assert node.inferred is False
    assert node.props == {"qualname": "mod.f", "kind": "method", "is_handler": True}


def test_hydrate_function_node_uses_griffe_location_and_ast_span(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "@audit\nasync def get_active_roster(cur):\n    return 1\n",
        encoding="utf-8",
    )
    inventory = SymbolInventory(
        functions={
            "mod.get_active_roster": FuncInfo(
                qualname="mod.get_active_roster",
                file=str(f),
                lineno=1,  # griffe's own (decorator-inclusive) lineno — irrelevant, we re-derive from AST
                endlineno=3,
                kind="function",
            )
        }
    )
    node = hydrate_function_node("mod.get_active_roster", inventory, {})
    assert node is not None
    assert node.line == 2
    assert node.hash == node_hash(f, 1, 3)


def test_hydrate_function_node_returns_none_when_not_in_inventory():
    node = hydrate_function_node("mod.missing", SymbolInventory(functions={}), {})
    assert node is None


def test_hydrate_function_node_returns_none_when_file_is_unknown():
    inventory = SymbolInventory(
        functions={
            "mod.f": FuncInfo(qualname="mod.f", file="unknown", lineno=1, endlineno=1, kind="function")
        }
    )
    node = hydrate_function_node("mod.f", inventory, {})
    assert node is None


def test_hydrate_function_node_caches_the_parsed_ast_per_file(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "def a():\n    return 1\n\n\ndef b():\n    return 2\n",
        encoding="utf-8",
    )
    inventory = SymbolInventory(
        functions={
            "mod.a": FuncInfo(qualname="mod.a", file=str(f), lineno=1, endlineno=2, kind="function"),
            "mod.b": FuncInfo(qualname="mod.b", file=str(f), lineno=5, endlineno=6, kind="function"),
        }
    )
    cache: dict = {}
    node_a = hydrate_function_node("mod.a", inventory, cache)
    node_b = hydrate_function_node("mod.b", inventory, cache)
    assert node_a is not None and node_a.line == 1
    assert node_b is not None and node_b.line == 5
    assert len(cache) == 1  # one file, parsed once, reused for both lookups
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_node_hydration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc.extract._node_hydration'`

- [ ] **Step 3: Implement**

```python
# src/cc/extract/_node_hydration.py
import ast
import pathlib

from cc.extract._calls_resolver import SymbolInventory
from cc.graph.hash_util import node_hash
from cc.graph.schema import Node

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def node_from_ast_def(
    def_node: ast.FunctionDef | ast.AsyncFunctionDef,
    file: str,
    qualname: str,
    kind: str,
    is_handler: bool = False,
) -> Node:
    """Canonical function-Node construction from an already-parsed AST def node.

    Convention (see doc_proyecto/ESQUEMA_POC.md): `line` is the bare
    `def`/`async def` line — decorators excluded, for human navigation.
    `hash` covers the decorator-inclusive span — decorators are part of the
    unit's meaning, so editing one must count as an edit to the node.
    """
    def_line = def_node.lineno
    span_start = def_node.decorator_list[0].lineno if def_node.decorator_list else def_node.lineno
    end_line = def_node.end_lineno or def_node.lineno

    return Node(
        id=f"function:{qualname}",
        type="function",
        file=file,
        line=def_line,
        hash=node_hash(file, span_start, end_line),
        inferred=False,
        props={"qualname": qualname, "kind": kind, "is_handler": is_handler},
    )


def _parse_cached(file: str, ast_cache: dict[str, ast.Module | None]) -> ast.Module | None:
    if file in ast_cache:
        return ast_cache[file]
    try:
        source = pathlib.Path(file).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=file)
    except (OSError, SyntaxError):
        ast_cache[file] = None
        return None
    ast_cache[file] = tree
    return tree


def hydrate_function_node(
    qualname: str,
    inventory: SymbolInventory,
    ast_cache: dict[str, ast.Module | None],
    is_handler: bool = False,
) -> Node | None:
    """Single source of truth for a function-type Node's file/line/hash.

    griffe (via `inventory`) locates which file `qualname` lives in; that
    file's own AST (parsed once, cached in `ast_cache` for the lifetime of
    a pipeline run) supplies the exact def/decorator lines, delegated to
    `node_from_ast_def` for the actual span computation — so this and any
    caller-side AST fallback always agree on the same math.

    Returns None if griffe has no entry for `qualname`, the file can't be
    (re-)parsed, or no matching def is found in it — callers own their own
    fallback for that case; this function never guesses.
    """
    info = inventory.functions.get(qualname)
    if info is None or info.file == "unknown":
        return None

    tree = _parse_cached(info.file, ast_cache)
    if tree is None:
        return None

    fn_name = qualname.rsplit(".", 1)[-1]
    match = None
    for node in ast.walk(tree):
        if (
            isinstance(node, _DEF_TYPES)
            and node.name == fn_name
            and (node.end_lineno or node.lineno) == info.endlineno
        ):
            match = node
            break
    if match is None:
        return None

    return node_from_ast_def(match, info.file, qualname, info.kind, is_handler=is_handler)
```

In `doc_proyecto/ESQUEMA_POC.md`, in the `## Nodos` section, right after the existing bullet list of `id`/`hash` (currently ending "...Gate de la Capa 3 futura." on the line starting `- `hash``), add a new bullet:

```markdown
- `line` (nodos `function`/`endpoint`) — línea del propio `def`/`async def`, sin decoradores (para que "ir al nodo" aterrice en la definición). `hash` cubre el tramo **con decoradores incluidos** — un decorador es parte del significado de la pieza (`@router.post(...)`, un decorador de caché/auth), así que editarlo cuenta como edición del nodo. Un único punto de hidratación (`src/cc/extract/_node_hydration.py`) aplica esta convención para los cuatro emisores de nodos `function` (endpoints, calls-caller, calls-callee, sql) — ningún extractor calcula su propio `line`/`hash` de forma independiente.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_node_hydration.py -v`
Expected: PASS (9 passed)

Run: `pytest -q`
Expected: full suite passes — this task adds a new, unused-so-far module, no existing extractor calls it yet.

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/_node_hydration.py tests/test_node_hydration.py doc_proyecto/ESQUEMA_POC.md
git commit -m "feat: add unified function-node hydration (griffe-backed, decorator-inclusive hash)"
```

---

### Task 2: Wire `sql.py` to the shared hydration point

**Files:**
- Modify: `src/cc/extract/sql.py`
- Test: `tests/test_sql.py`

**Interfaces:**
- Consumes: `hydrate_function_node`, `node_from_ast_def` (Task 1, `src/cc/extract/_node_hydration.py`).
- Produces: `extract_sql(repo_path, exclude_patterns=(), inventory=None, ast_cache=None) -> tuple[list[Node], list[Edge], list[tuple[str, int, str]]]` — same return shape, new fourth parameter optional.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_sql.py
from cc.extract._node_hydration import node_from_ast_def  # noqa: F401 (re-export sanity, used implicitly)


def test_decorated_db_function_gets_decorator_inclusive_hash(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "db.py").write_text(
        "def audit(fn):\n"
        "    return fn\n"
        "\n"
        "\n"
        "@audit\n"
        "async def create_message(conn, content):\n"
        "    await conn.execute(\n"
        "        'INSERT INTO messages (content) VALUES (%s)', (content,)\n"
        "    )\n",
        encoding="utf-8",
    )
    nodes, _, _ = extract_sql(repo)
    fn_node = next(n for n in nodes if n.id == "function:db.create_message")
    assert fn_node.line == 6  # the `async def` line, not the decorator (5) or the execute() call (7)
    from cc.graph.hash_util import node_hash

    assert fn_node.hash == node_hash(repo / "db.py", 5, 9)  # decorator (5) through end (9)


def test_sql_still_works_when_griffe_cannot_resolve_the_function(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "db.py").write_text(
        "async def create_message(conn, content):\n"
        "    await conn.execute(\n"
        "        'INSERT INTO messages (content) VALUES (%s)', (content,)\n"
        "    )\n",
        encoding="utf-8",
    )
    from cc.extract._calls_resolver import SymbolInventory

    empty_inventory = SymbolInventory(functions={})
    nodes, _, _ = extract_sql(repo, inventory=empty_inventory)
    fn_node = next(n for n in nodes if n.id == "function:db.create_message")
    assert fn_node.line == 1  # local AST fallback still finds the real def line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sql.py -v`
Expected: FAIL — `test_decorated_db_function_gets_decorator_inclusive_hash` fails because `sql.py` currently uses `inventory.functions.get(fn_qname)`'s griffe-reported `lineno` directly (decorator-inclusive per griffe's own convention) as the node's `line`, so `fn_node.line` would be `5`, not `6` — the assertion `fn_node.line == 6` fails. `test_sql_still_works_when_griffe_cannot_resolve_the_function` should already PASS today (it's the pre-existing AST fallback, unaffected by this task) — run it to confirm it doesn't regress once you implement.

- [ ] **Step 3: Implement**

Replace `_find_enclosing_function` in `src/cc/extract/sql.py` (currently returns a `(qualname, def_lineno, def_end_lineno)` 3-tuple) with a version returning the actual AST node instead of separate line numbers:

```python
def _find_enclosing_function(
    call_node: ast.Call,
    tree: ast.AST,
    module_qname: str,
) -> tuple[str, ast.FunctionDef | ast.AsyncFunctionDef | None]:
    """Return (module-qualified function name enclosing call_node, the AST
    def node itself) — or (module_qname, None) at module level, outside
    any function."""
    fn_defs: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_defs.append(node)

    call_line = call_node.lineno
    fn_defs.sort(key=lambda n: n.lineno, reverse=True)
    for fn_def in fn_defs:
        end = fn_def.end_lineno or fn_def.lineno
        if fn_def.lineno <= call_line <= end:
            return f"{module_qname}.{fn_def.name}", fn_def

    return module_qname, None
```

Add the import at the top of `src/cc/extract/sql.py`:

```python
from cc.extract._node_hydration import hydrate_function_node, node_from_ast_def
```

Change `extract_sql`'s signature to add the fourth parameter:

```python
def extract_sql(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    inventory: SymbolInventory | None = None,
    ast_cache: dict[str, ast.Module | None] | None = None,
) -> tuple[list[Node], list[Edge], list[tuple[str, int, str]]]:
    repo_path = pathlib.Path(repo_path)
    if inventory is None:
        inventory = build_symbol_inventory(repo_path, exclude_patterns)
    if ast_cache is None:
        ast_cache = {}
```
(replace the current two-line `inventory` default-build block with these four lines — same insertion point, right after `repo_path = pathlib.Path(repo_path)`)

Change `raw_edges`'s type annotation to carry the AST node instead of separate line numbers:

```python
    raw_edges: list[
        tuple[str, str, str, str, str, int, ast.FunctionDef | ast.AsyncFunctionDef | None]
    ] = []  # (fn_qname, table, op, via, edge_file, edge_lineno, enclosing_def_node)
```

Update the three call sites of `_find_enclosing_function` — they currently do `fn_qname, def_lineno, def_end_lineno = _find_enclosing_function(...)`. Change all three to:

```python
            fn_qname, enclosing_def = _find_enclosing_function(node, tree, module_qname)
```

Update both `raw_edges.append(...)` call sites (the dynamic-SQL branch and the static-SQL branch) to carry `enclosing_def` instead of the two line numbers:

```python
                raw_edges.append((fn_qname, tbl, op, via, str(file), node.lineno, enclosing_def))
```

and

```python
            for tbl in tables:
                raw_edges.append((fn_qname, tbl, op, via, str(file), node.lineno, enclosing_def))
```

Replace the "Build function nodes" loop with:

```python
    # Build function nodes for each unique enclosing function that touches the DB.
    # hydrate_function_node (griffe-backed) is the primary source; the enclosing
    # def's own AST node (already found by _find_enclosing_function, same file)
    # is the fallback when griffe can't resolve the qualname — both paths run
    # through node_from_ast_def, so they always agree on line/hash. The SQL
    # call site itself is NEVER used for the node's own identity — it lives
    # only in the edge's `via` prop, computed above.
    fn_nodes: dict[str, Node] = {}
    for fn_qname, tbl, op, via, edge_file, edge_lineno, enclosing_def in raw_edges:
        if tbl not in table_nodes:
            continue
        fn_id = f"function:{fn_qname}"
        if fn_id in fn_nodes:
            continue

        node = hydrate_function_node(fn_qname, inventory, ast_cache)
        if node is None and enclosing_def is not None:
            node = node_from_ast_def(enclosing_def, edge_file, fn_qname, "function")
        if node is None:
            # Rare: the SQL call sits at module level (no enclosing function)
            # and griffe has no entry either — fall back to the call site
            # itself, same as this function's pre-fix behavior.
            node = Node(
                id=fn_id,
                type="function",
                file=edge_file,
                line=edge_lineno,
                hash=node_hash(edge_file, edge_lineno, edge_lineno),
                inferred=False,
                props={"qualname": fn_qname, "kind": "function", "is_handler": False},
            )
        fn_nodes[fn_id] = node
```

Update the final "Build edges" loop's unpacking to match the new 7-tuple shape:

```python
    # Build edges
    edges: list[Edge] = []
    for fn_qname, tbl, op, via, _edge_file, _edge_lineno, _enclosing_def in raw_edges:
        if tbl not in table_nodes:
            continue
        edges.append(
            Edge(
                from_=f"function:{fn_qname}",
                to=f"table:{tbl}",
                type=op,
                inferred=False,
                props={"via": via},
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sql.py -v`
Expected: PASS (all tests, including every pre-existing one from the prior plan)

Run: `pytest -q`
Expected: full suite passes.

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/sql.py tests/test_sql.py
git commit -m "fix: route sql.py function-node hydration through the shared hydration point"
```

---

### Task 3: Wire `calls.py` (both caller and callee paths) to the shared hydration point

**Files:**
- Modify: `src/cc/extract/calls.py`
- Test: `tests/test_calls.py`

**Interfaces:**
- Consumes: `hydrate_function_node`, `node_from_ast_def` (Task 1).
- Produces: `extract_calls(repo_path, exclude_patterns=(), inventory=None, ast_cache=None) -> tuple[list[Node], list[Edge], list[tuple[str, str]], dict]` — same return shape, new fourth parameter optional.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_calls.py
def test_decorated_caller_gets_decorator_inclusive_hash(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "svc.py").write_text(
        "def audit(fn):\n"
        "    return fn\n"
        "\n"
        "\n"
        "@audit\n"
        "def do_work():\n"
        "    return helper()\n"
        "\n"
        "\n"
        "def helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    nodes, edges, _, _ = extract_calls(repo)
    fn_node = next(n for n in nodes if n.id == "function:backend.svc.do_work")
    assert fn_node.line == 6  # the `def` line, not the decorator (5)
    from cc.graph.hash_util import node_hash

    assert fn_node.hash == node_hash(
        repo / "backend" / "svc.py", 5, 7
    )  # decorator (5) through end (7)


def test_decorated_callee_gets_decorator_inclusive_hash(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "svc.py").write_text(
        "def audit(fn):\n"
        "    return fn\n"
        "\n"
        "\n"
        "def caller():\n"
        "    return callee()\n"
        "\n"
        "\n"
        "@audit\n"
        "def callee():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    nodes, edges, _, _ = extract_calls(repo)
    fn_node = next(n for n in nodes if n.id == "function:backend.svc.callee")
    assert fn_node.line == 10  # the `def` line, not the decorator (9)
    from cc.graph.hash_util import node_hash

    assert fn_node.hash == node_hash(repo / "backend" / "svc.py", 9, 11)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calls.py -v`
Expected: FAIL — both new tests fail because `calls.py` currently uses `fn_node.lineno`/`callee_info.lineno` (AST-bare-line for the caller path, griffe-decorator-line for the callee path) directly, not the decorator-inclusive-hash convention. Confirm the exact failure values reported match what's described above (caller path: wrong `hash`, right `line`; callee path: wrong `line`, since griffe's own lineno is decorator-inclusive today).

- [ ] **Step 3: Implement**

Add the import at the top of `src/cc/extract/calls.py`:

```python
from cc.extract._node_hydration import hydrate_function_node, node_from_ast_def
```

Change `extract_calls`'s signature to add the fourth parameter:

```python
def extract_calls(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    inventory: "SymbolInventory | None" = None,
    ast_cache: dict[str, ast.Module | None] | None = None,
) -> tuple[list[Node], list[Edge], list[tuple[str, str]], dict]:
```

Right after the existing `if inventory is None: inventory = build_symbol_inventory(...)` block, add:

```python
    if ast_cache is None:
        ast_cache = {}
```

Replace the caller-node-building block (currently `caller_id = f"function:{fn_qualname}"` through the `nodes.setdefault(caller_id, Node(...))` call) with:

```python
            caller_id = f"function:{fn_qualname}"
            caller_kind = "method" if class_stack else "function"
            caller_node = hydrate_function_node(fn_qualname, inventory, ast_cache)
            if caller_node is None:
                caller_node = node_from_ast_def(fn_node, str(file), fn_qualname, caller_kind)
            nodes.setdefault(caller_id, caller_node)
```

Replace the callee-node-building block (currently the `callee_info = inventory.functions[callee_qname]` / `if callee_info.file == "unknown": continue` / manual `Node(...)` construction) with:

```python
                    callee_node = hydrate_function_node(callee_qname, inventory, ast_cache)
                    if callee_node is None:
                        # griffe couldn't locate this symbol's source (namespace
                        # package, compiled stub, ...) — we can't hydrate a real
                        # Node for it. Skip rather than crash; the call was
                        # still structurally resolved, so it stays counted
                        # above, it just can't be rendered as an edge.
                        continue
                    callee_id = f"function:{callee_qname}"
                    nodes.setdefault(callee_id, callee_node)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calls.py -v`
Expected: PASS (all tests)

Run: `pytest -q`
Expected: full suite passes.

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/calls.py tests/test_calls.py
git commit -m "fix: route calls.py caller/callee function-node hydration through the shared hydration point"
```

---

### Task 4: Wire `endpoints.py` + `pipeline.py` to the shared hydration point

**Files:**
- Modify: `src/cc/extract/endpoints.py`
- Modify: `src/cc/pipeline.py`
- Test: `tests/test_endpoints.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `hydrate_function_node`, `node_from_ast_def` (Task 1); `build_symbol_inventory`, `SymbolInventory` (`src/cc/extract/_calls_resolver.py`).
- Produces: `extract_endpoints(repo_path, exclude_patterns=(), inventory=None, ast_cache=None) -> tuple[list[Node], list[Edge]]` — same return shape, two new optional parameters (this function had neither before).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_endpoints.py — check the existing top of the file for
# how `extract_endpoints` is already imported/called; match that style.
def test_decorated_handler_gets_decorator_inclusive_hash(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "api.py").write_text(
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter()\n"
        "\n"
        "\n"
        "def audit(fn):\n"
        "    return fn\n"
        "\n"
        "\n"
        "@audit\n"
        '@router.get("/x")\n'
        "def handler():\n"
        "    return {}\n",
        encoding="utf-8",
    )
    nodes, edges = extract_endpoints(repo)
    fn_node = next(n for n in nodes if n.type == "function")
    ep_node = next(n for n in nodes if n.type == "endpoint")
    assert fn_node.line == 12  # the `def` line, not either decorator (10 or 11)
    from cc.graph.hash_util import node_hash

    expected_hash = node_hash(repo / "backend" / "api.py", 10, 13)  # both decorators through end
    assert fn_node.hash == expected_hash
    assert ep_node.line == fn_node.line  # endpoint and handler still share the same span
    assert ep_node.hash == fn_node.hash
```

(If `tests/test_endpoints.py` doesn't exist yet, check for an equivalent existing test file for `extract_endpoints` — e.g. `tests/test_endpoints.py` almost certainly already exists given `endpoints.py` is a Phase-1 extractor; append to whichever file already covers it, matching its existing import style.)

```python
# append to tests/test_pipeline.py
def test_pipeline_shares_ast_cache_across_all_three_extractors(tmp_path):
    # Not a behavior test — a wiring smoke test: the pipeline must not crash
    # when endpoints.py also needs inventory/ast_cache now.
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "api.py").write_text(
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter()\n"
        "\n"
        "\n"
        '@router.get("/x")\n'
        "def handler():\n"
        "    return {}\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    run(repo, out)
    data = json.loads((out / "graph.json").read_text())
    assert any(n["type"] == "endpoint" for n in data["nodes"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_endpoints.py -v` (or the equivalent existing file)
Expected: FAIL — `test_decorated_handler_gets_decorator_inclusive_hash` fails because `endpoints.py` currently computes `ep_hash`/`fn_hash` from `fn_node.lineno`/`fn_node.end_lineno` directly (bare span, no decorators, and no distinction between the two decorators here).

Run: `pytest tests/test_pipeline.py::test_pipeline_shares_ast_cache_across_all_three_extractors -v`
Expected: PASS already (this one is a smoke test that should keep passing before and after — it only becomes meaningful once `pipeline.py` is changed in Step 3; run it now mainly to confirm the fixture itself is valid).

- [ ] **Step 3: Implement**

In `src/cc/extract/endpoints.py`, add these imports at the top:

```python
from cc.extract._calls_resolver import SymbolInventory, build_symbol_inventory
from cc.extract._node_hydration import hydrate_function_node, node_from_ast_def
```

Change `extract_endpoints`'s signature:

```python
def extract_endpoints(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    inventory: SymbolInventory | None = None,
    ast_cache: dict[str, ast.Module | None] | None = None,
) -> tuple[list[Node], list[Edge]]:
    repo_path = pathlib.Path(repo_path)
    if inventory is None:
        inventory = build_symbol_inventory(repo_path, exclude_patterns)
    if ast_cache is None:
        ast_cache = {}
    nodes: list[Node] = []
    edges: list[Edge] = []
```
(replace the current `repo_path = pathlib.Path(repo_path)` / `nodes: list[Node] = []` / `edges: list[Edge] = []` block with this — same two list initializations, plus the new inventory/ast_cache setup in between)

Replace the node-construction block (currently `handler_qname = ...` through `edges.append(edge)`) with:

```python
                handler_qname = f"{module_qname}.{fn_node.name}"
                ep_id = f"endpoint:{method.upper()}:{full_path}"
                fn_id = f"function:{handler_qname}"

                fn_node_obj = hydrate_function_node(handler_qname, inventory, ast_cache, is_handler=True)
                if fn_node_obj is None:
                    fn_node_obj = node_from_ast_def(
                        fn_node, str(file), handler_qname, "function", is_handler=True
                    )

                ep_node = Node(
                    id=ep_id,
                    type="endpoint",
                    file=str(file),
                    line=fn_node_obj.line,
                    hash=fn_node_obj.hash,
                    inferred=False,
                    props={"method": method.upper(), "path": full_path, "handler": handler_qname},
                )
                edge = Edge(from_=ep_id, to=fn_id, type="handles", inferred=False, props={})

                nodes.extend([ep_node, fn_node_obj])
                edges.append(edge)
```

In `src/cc/pipeline.py`, add `import ast` to the top imports (alongside the existing `import pathlib`).

Change the extraction block to build and share `ast_cache` alongside the already-shared `inventory`:

```python
    inventory = build_symbol_inventory(repo_path, exclude_patterns)
    ast_cache: dict[str, ast.Module | None] = {}

    ep_nodes, ep_edges = extract_endpoints(
        repo_path, exclude_patterns, inventory=inventory, ast_cache=ast_cache
    )
    handler_nodes = [n for n in ep_nodes if n.type == "function"]

    model_nodes, model_edges = extract_models(repo_path, handler_nodes, exclude_patterns)
    sql_nodes, sql_edges, sql_dynamic_gaps = extract_sql(
        repo_path, exclude_patterns, inventory=inventory, ast_cache=ast_cache
    )
    call_nodes, call_edges, call_excluded, call_coverage = extract_calls(
        repo_path, exclude_patterns, inventory=inventory, ast_cache=ast_cache
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_endpoints.py tests/test_pipeline.py -v` (adjust the first path if endpoints tests live elsewhere)
Expected: PASS (all tests)

Run: `pytest -q`
Expected: full suite passes.

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/endpoints.py src/cc/pipeline.py tests/test_endpoints.py tests/test_pipeline.py
git commit -m "fix: route endpoints.py handler hydration through the shared hydration point; share ast_cache in pipeline"
```

---

### Task 5: The immortalized regression fixture — decorated caller + callee + table-toucher, in one function

**Files:**
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `run` (`src/cc/pipeline.py`), `node_hash` (`src/cc/graph/hash_util.py`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_pipeline.py
def test_decorated_function_that_is_caller_callee_and_table_toucher(tmp_path):
    # The exact case that surfaced this whole plan: a decorated function that
    # is simultaneously (a) a caller, (b) a callee, and (c) a DB-toucher —
    # exercised by all four function-node emitters at once. Before this
    # plan, endpoints.py/calls.py's caller path (AST, decorator-excluded
    # line) and sql.py/calls.py's callee path (griffe, decorator-inclusive
    # line) disagreed on this function's identity, and graph/build.py's
    # identity assertion turned that disagreement into a hard crash.
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "db.py").write_text(
        "def audit(fn):\n"
        "    return fn\n"
        "\n"
        "\n"
        "@audit\n"
        "async def get_active_roster(cur, channel_id):\n"
        "    rows = await cur.execute(\n"
        "        'SELECT * FROM channels WHERE id = %s', (channel_id,)\n"
        "    )\n"
        "    return format_roster(rows)\n"
        "\n"
        "\n"
        "def format_roster(rows):\n"
        "    return list(rows)\n"
        "\n"
        "\n"
        "async def run_turn(cur, channel_id):\n"
        "    return await get_active_roster(cur, channel_id)\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    run(repo, out)  # must not raise — this is the crash this plan fixes

    data = json.loads((out / "graph.json").read_text())
    matches = [n for n in data["nodes"] if n["id"] == "function:backend.db.get_active_roster"]
    assert len(matches) == 1  # exactly one node — no silent duplicate/conflict either
    fn_node = matches[0]
    assert fn_node["line"] == 6  # the `async def` line, not the decorator (5)
    from cc.graph.hash_util import node_hash

    assert fn_node["hash"] == node_hash(
        repo / "backend" / "db.py", 5, 10
    )  # decorator (5) through end (10, the `return format_roster(rows)` line)

    edge_types = {(e["from_"], e["to"], e["type"]) for e in data["edges"]}
    assert ("function:backend.db.run_turn", "function:backend.db.get_active_roster", "calls") in edge_types
    assert (
        "function:backend.db.get_active_roster",
        "function:backend.db.format_roster",
        "calls",
    ) in edge_types
    assert (
        "function:backend.db.get_active_roster",
        "table:channels",
        "reads",
    ) in edge_types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline.py::test_decorated_function_that_is_caller_callee_and_table_toucher -v`
Expected: if Tasks 1-4 are already merged (this task runs last), this test should already PASS — it's the confirming, "immortalized" regression test proving the whole plan holds together, not new functionality. Run it to confirm it genuinely exercises the fix.

- [ ] **Step 3: No implementation needed — this task is pure verification**

If the test in Step 2 already passes, Tasks 1-4 already fixed the behavior this test checks. If it fails, that means an earlier task has a gap — do not weaken this test to make it pass; go back and fix the gap in the relevant task's code.

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: full suite passes, confirming zero regressions across the whole plan.

- [ ] **Step 5: Commit**

```bash
git add tests/test_pipeline.py
git commit -m "test: immortalize the decorated caller+callee+table-toucher regression case"
```
