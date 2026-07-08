# Fix SQL Function-Node Hydration (Stable Anchors Bug) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `src/cc/extract/sql.py` so a DB-touching function's graph `Node` carries its own `def` line and a whole-body `hash` — never the SQL call-site line — and make `graph/build.py` fail loudly instead of silently if two extractors ever disagree about a node's identity again.

**Architecture:** `sql.py`'s function-node hydration switches to the same griffe-backed `SymbolInventory` that `calls.py` already uses for its callee nodes (single source of truth), falling back to the enclosing `def`'s own AST span (which `sql.py` already computes internally but currently discards) only when griffe can't resolve the symbol. `pipeline.py` builds that inventory once and shares it with both extractors — fixing the bug and removing a redundant griffe load in one move. `graph/build.py` gains an equality assertion so a future regression fails a test immediately instead of silently picking whichever extractor ran first.

**Tech Stack:** Python 3.11 stdlib (`ast`), `griffe` (via the existing `SymbolInventory`/`build_symbol_inventory` in `src/cc/extract/_calls_resolver.py`).

## Global Constraints

- `via` (the edge prop pointing at the exact SQL call site, e.g. `f"{file}:{node.lineno}"`) is never touched by this plan — the fix is scoped entirely to the function **Node's** own `file`/`line`/`hash`.
- `sql.py` must never regress coverage: a DB-touching function whose qualname griffe's inventory can't resolve must still get a `function` Node (using the enclosing `def`'s own AST span as a fallback), not silently disappear or become a new gap.
- `extract_sql(repo_path, exclude_patterns)` and `extract_calls(repo_path, exclude_patterns)` must keep working unchanged for every existing 2-positional-arg call site and test — the new inventory-sharing parameter is optional and defaults to "build your own," preserving standalone testability.
- Reuse `node_hash(file, lineno, end_lineno)` from `src/cc/graph/hash_util.py` — never reimplement hashing.
- Do not modify the shared `tests/fixtures/simple_api/` or `tests/fixtures/calls_repo/` fixture files — other tests depend on their exact current content. New scenarios needing a different repo layout use `tmp_path`-based inline fixtures, matching the existing style already used in several `tests/test_pipeline.py` tests (e.g. `test_pipeline_reports_exclusions_when_patterns_given`).
- `graph/build.py`'s new invariant check compares `file`, `line`, and `hash` only — `props` differences between a richer node (e.g. an endpoint handler's `is_handler=True`) and a generic stub for the same id are expected and must NOT raise; only conflicting `file`/`line`/`hash` values are a real defect.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/cc/extract/sql.py` | Modified: `_find_enclosing_function` also returns the enclosing `def`'s own line span; `extract_sql` gains an optional shared `SymbolInventory` param and hydrates function nodes from it (with the AST span as fallback) instead of the SQL call-site line. |
| `src/cc/extract/calls.py` | Modified: `extract_calls` gains an optional shared `SymbolInventory` param, so `pipeline.py` can build one inventory and pass it to both extractors. |
| `src/cc/pipeline.py` | Modified: builds one `SymbolInventory` via `build_symbol_inventory()` and passes it into both `extract_sql` and `extract_calls`. |
| `src/cc/graph/build.py` | Modified: `build_graph` raises `ValueError` if the same node id is registered with a conflicting `file`/`line`/`hash`. |
| `tests/test_sql.py` | Extended: hydration-from-inventory tests, AST-fallback test, shared-inventory-agreement test. |
| `tests/test_graph.py` | Extended: conflicting-identity raises `ValueError`. |
| `tests/test_pipeline.py` | Extended: end-to-end regression test proving `function:db.create_message` gets its `def` line (1), not the call-site line (2), against the existing `SIMPLE_API` fixture. |

---

### Task 1: `sql.py` — hydrate function nodes from the shared `SymbolInventory`, not the SQL call-site line

**Files:**
- Modify: `src/cc/extract/sql.py`
- Test: `tests/test_sql.py`

**Interfaces:**
- Consumes: `SymbolInventory`, `FuncInfo` from `src/cc/extract/_calls_resolver.py` (`FuncInfo` has `.qualname`, `.file`, `.lineno`, `.endlineno`, `.kind`; `SymbolInventory.functions: dict[str, FuncInfo]`), `build_symbol_inventory(repo_path, exclude_patterns) -> SymbolInventory` (same module).
- Produces: `extract_sql(repo_path, exclude_patterns=(), inventory: SymbolInventory | None = None) -> tuple[list[Node], list[Edge], list[tuple[str, int, str]]]` — same return shape as today, new third parameter is optional and keyword-friendly.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_sql.py
import ast

from cc.extract._calls_resolver import FuncInfo, SymbolInventory
from cc.extract.sql import _find_enclosing_function, extract_sql


def test_find_enclosing_function_returns_def_span():
    source = (
        "async def get_message(conn, msg_id):\n"
        "    return await conn.fetchone('SELECT 1', (msg_id,))\n"
    )
    tree = ast.parse(source)
    call_node = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    qname, start, end = _find_enclosing_function(call_node, tree, "db")
    assert qname == "db.get_message"
    assert start == 1
    assert end == 2


def test_find_enclosing_function_module_level_returns_none_span():
    source = "CUR.execute('SELECT 1')\n"
    tree = ast.parse(source)
    call_node = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    qname, start, end = _find_enclosing_function(call_node, tree, "db")
    assert qname == "db"
    assert start is None
    assert end is None


def test_function_node_uses_def_line_from_inventory_not_call_site(tmp_path):
    repo = tmp_path / "repo"
    (repo / "db.py").write_text(
        "async def create_message(conn, content):\n"
        "    await conn.execute(\n"
        "        'INSERT INTO messages (content) VALUES (%s)', (content,)\n"
        "    )\n",
        encoding="utf-8",
    )
    inventory = SymbolInventory(
        functions={
            "db.create_message": FuncInfo(
                qualname="db.create_message",
                file=str(repo / "db.py"),
                lineno=1,
                endlineno=4,
                kind="function",
            )
        }
    )
    nodes, _, _ = extract_sql(repo, inventory=inventory)
    fn_node = next(n for n in nodes if n.id == "function:db.create_message")
    assert fn_node.line == 1  # the `async def` line, not line 2's execute() call
    assert fn_node.file == str(repo / "db.py")


def test_function_node_falls_back_to_ast_span_when_not_in_inventory(tmp_path):
    repo = tmp_path / "repo"
    (repo / "db.py").write_text(
        "async def create_message(conn, content):\n"
        "    await conn.execute(\n"
        "        'INSERT INTO messages (content) VALUES (%s)', (content,)\n"
        "    )\n",
        encoding="utf-8",
    )
    empty_inventory = SymbolInventory(functions={})
    nodes, _, _ = extract_sql(repo, inventory=empty_inventory)
    fn_node = next(n for n in nodes if n.id == "function:db.create_message")
    assert fn_node.line == 1  # AST fallback still finds the real def line
    assert fn_node.file == str(repo / "db.py")


def test_extract_sql_without_inventory_arg_still_works():
    # Backward compatibility: existing 2-positional-arg call sites (no inventory).
    nodes, edges, gaps = extract_sql(SIMPLE_API)
    assert any(n.type == "table" for n in nodes)
```

Add the missing import at the top of `tests/test_sql.py` if `SIMPLE_API` isn't already imported there (check the existing top of the file — it already imports `from tests.conftest import SIMPLE_API`, so only the new `ast`, `FuncInfo`, `SymbolInventory`, `_find_enclosing_function` imports need adding).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sql.py -v`
Expected: FAIL — `test_find_enclosing_function_returns_def_span` and `test_find_enclosing_function_module_level_returns_none_span` fail with `ValueError: too many values to unpack (expected 3)` (current `_find_enclosing_function` returns a plain string); the two new hydration tests fail with `TypeError: extract_sql() got an unexpected keyword argument 'inventory'`.

- [ ] **Step 3: Implement**

In `src/cc/extract/sql.py`, add this import near the top (alongside the existing imports):

```python
from cc.extract._calls_resolver import SymbolInventory, build_symbol_inventory
```

Replace `_find_enclosing_function` (currently lines 103-127) with:

```python
def _find_enclosing_function(
    call_node: ast.Call,
    tree: ast.AST,
    module_qname: str,
) -> tuple[str, int | None, int | None]:
    """Return (module-qualified function name enclosing call_node, the def's
    own lineno, the def's own end_lineno).

    Falls back to (module_qname, None, None) when the call site sits at
    module level, outside any function — there is no def span to report.
    """
    fn_defs: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            fn_defs.append((node.lineno, end, node.name))

    call_line = call_node.lineno

    # Sort by start_line descending so innermost (latest start) is checked first
    fn_defs.sort(key=lambda x: x[0], reverse=True)
    for start, end, name in fn_defs:
        if start <= call_line <= end:
            return f"{module_qname}.{name}", start, end

    return module_qname, None, None
```

Change `extract_sql`'s signature (currently line 130-132) to:

```python
def extract_sql(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    inventory: SymbolInventory | None = None,
) -> tuple[list[Node], list[Edge], list[tuple[str, int, str]]]:
    repo_path = pathlib.Path(repo_path)
    if inventory is None:
        inventory = build_symbol_inventory(repo_path, exclude_patterns)
```
(insert these two new lines right after the existing `repo_path = pathlib.Path(repo_path)` line, before `table_columns: dict[str, set[str]] = defaultdict(set)`)

`raw_edges`'s type annotation (currently line 136-138) and every place it's appended to must carry the new def-span fields. Change the annotation to:

```python
    raw_edges: list[
        tuple[str, str, str, str, str, int, int | None, int | None]
    ] = []  # (fn_qname, table, op, via, edge_file, edge_lineno, def_lineno, def_end_lineno)
```

Update the two call sites of `_find_enclosing_function` (currently lines 163 and 167, both inside the `for node in ast.walk(tree):` loop) — both currently do `fn_qname = _find_enclosing_function(node, tree, module_qname)`. Change both to:

```python
            fn_qname, def_lineno, def_end_lineno = _find_enclosing_function(node, tree, module_qname)
```

And update the two `raw_edges.append(...)` call sites to carry the two new values. The dynamic-SQL branch (currently line 172):

```python
                raw_edges.append(
                    (fn_qname, tbl, op, via, str(file), node.lineno, def_lineno, def_end_lineno)
                )
```

The static-SQL branch (currently lines 197-198, inside `for tbl in tables:`):

```python
            for tbl in tables:
                raw_edges.append(
                    (fn_qname, tbl, op, via, str(file), node.lineno, def_lineno, def_end_lineno)
                )
```

Finally, replace the "Build function nodes" loop (currently lines 218-233) with:

```python
    # Build function nodes for each unique enclosing function that touches the DB.
    # Identity (file/line/hash) comes from the griffe-backed inventory whenever
    # the qualname resolves there (single source of truth, matching calls.py's
    # own callee hydration) — the enclosing def's own AST span is only a
    # fallback for when griffe can't resolve the symbol. The SQL call site
    # itself (edge_file/edge_lineno) is NEVER used for the node's own identity
    # — it already lives in the edge's `via` prop, computed above.
    fn_nodes: dict[str, Node] = {}
    for (
        fn_qname,
        tbl,
        op,
        via,
        edge_file,
        edge_lineno,
        def_lineno,
        def_end_lineno,
    ) in raw_edges:
        if tbl not in table_nodes:
            continue
        fn_id = f"function:{fn_qname}"
        if fn_id in fn_nodes:
            continue

        info = inventory.functions.get(fn_qname)
        if info is not None and info.file != "unknown":
            node_file, node_line, node_end = info.file, info.lineno, info.endlineno
        elif def_lineno is not None and def_end_lineno is not None:
            node_file, node_line, node_end = edge_file, def_lineno, def_end_lineno
        else:
            # Rare: the SQL call sits at module level (no enclosing function)
            # and griffe has no entry either — fall back to the call site
            # itself, same as this function's pre-fix behavior.
            node_file, node_line, node_end = edge_file, edge_lineno, edge_lineno

        fn_nodes[fn_id] = Node(
            id=fn_id,
            type="function",
            file=node_file,
            line=node_line,
            hash=node_hash(node_file, node_line, node_end),
            inferred=False,
            props={"qualname": fn_qname, "kind": "function", "is_handler": False},
        )
```

The final "Build edges" loop (currently lines 236-248) unpacks `raw_edges` too — update its unpacking to match the new 8-tuple shape, ignoring the four new/renamed fields it doesn't need:

```python
    # Build edges
    edges: list[Edge] = []
    for fn_qname, tbl, op, via, _edge_file, _edge_lineno, _def_lineno, _def_end_lineno in raw_edges:
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
Expected: PASS (all tests, including every pre-existing `test_sql.py` test — none of their assertions touch function-node `line`/`hash`, only table nodes and edges, so they're unaffected)

Run: `pytest -q`
Expected: full suite passes — no regressions in `test_calls.py`, `test_pipeline.py`, etc. (this task doesn't change `calls.py` or `pipeline.py` yet, so `extract_sql`'s new third parameter defaulting to `None` means every existing caller is unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/sql.py tests/test_sql.py
git commit -m "fix: hydrate SQL-touching function nodes from griffe inventory, not the call-site line"
```

---

### Task 2: Share one `SymbolInventory` between `calls.py` and `sql.py` via `pipeline.py`

**Files:**
- Modify: `src/cc/extract/calls.py`
- Modify: `src/cc/pipeline.py`
- Test: `tests/test_calls.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `SymbolInventory`, `build_symbol_inventory` (`src/cc/extract/_calls_resolver.py`); `extract_sql(repo_path, exclude_patterns=(), inventory=None)` (Task 1).
- Produces: `extract_calls(repo_path, exclude_patterns=(), inventory: SymbolInventory | None = None) -> tuple[list[Node], list[Edge], list[tuple[str, str]], dict]` — same return shape as today, new third parameter optional.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_calls.py
from cc.extract._calls_resolver import build_symbol_inventory
from cc.extract.calls import extract_calls


def test_extract_calls_accepts_a_prebuilt_inventory():
    inventory = build_symbol_inventory(CALLS_REPO)
    nodes, edges, excluded, coverage = extract_calls(CALLS_REPO, inventory=inventory)
    assert len(nodes) > 0
    assert coverage["total"]["functions"] > 0


def test_extract_calls_without_inventory_arg_still_works():
    # Backward compatibility: existing 2-positional-arg call sites (no inventory).
    nodes, edges, excluded, coverage = extract_calls(CALLS_REPO)
    assert len(nodes) > 0
```

(check the existing top of `tests/test_calls.py` for how `CALLS_REPO` is already imported — reuse that import, don't re-add it if present)

```python
# append to tests/test_pipeline.py
def test_pipeline_builds_inventory_once_and_shares_it(tmp_path, monkeypatch):
    # A DB-touching function that's ALSO called by another function — this is
    # exactly the scenario where sql.py and calls.py must agree on identity.
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "db.py").write_text(
        "async def get_active_roster(cur, channel_id):\n"
        "    await cur.execute('SELECT * FROM channels WHERE id = %s', (channel_id,))\n",
        encoding="utf-8",
    )
    (repo / "backend" / "service.py").write_text(
        "from .db import get_active_roster\n"
        "\n"
        "async def run_turn(cur, channel_id):\n"
        "    return await get_active_roster(cur, channel_id)\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    run(repo, out)
    data = json.loads((out / "graph.json").read_text())
    fn_node = next(
        n for n in data["nodes"] if n["id"] == "function:backend.db.get_active_roster"
    )
    assert fn_node["line"] == 1  # the `async def` line, not the execute() call's line 2
```

(check the existing top of `tests/test_pipeline.py` for `json`/`pathlib`/`run` imports — already present, reuse them)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calls.py -v`
Expected: FAIL — `test_extract_calls_accepts_a_prebuilt_inventory` fails with `TypeError: extract_calls() got an unexpected keyword argument 'inventory'`

Run: `pytest tests/test_pipeline.py::test_pipeline_builds_inventory_once_and_shares_it -v`
Expected: FAIL — `fn_node["line"] == 1` assertion fails, actual value is `2` (reproduces the original bug end-to-end, since `pipeline.py` doesn't pass a shared inventory into `extract_sql` yet)

- [ ] **Step 3: Implement**

In `src/cc/extract/calls.py`, change `extract_calls`'s signature (currently lines 50-53) to:

```python
def extract_calls(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    inventory: "SymbolInventory | None" = None,
) -> tuple[list[Node], list[Edge], list[tuple[str, str]], dict]:
```

Add `SymbolInventory` to the existing import from `cc.extract._calls_resolver` (currently lines 4-10):

```python
from cc.extract._calls_resolver import (
    SymbolInventory,
    build_import_table,
    build_local_alias_table,
    build_symbol_inventory,
    classify_call,
    local_assignment_targets,
)
```

Change the line that currently unconditionally builds the inventory (`inventory = build_symbol_inventory(repo_path, exclude_patterns)`, currently line 65) to:

```python
    if inventory is None:
        inventory = build_symbol_inventory(repo_path, exclude_patterns)
```

In `src/cc/pipeline.py`, add the import:

```python
from cc.extract._calls_resolver import build_symbol_inventory
```

Then change the extraction block (currently lines 22-29) to build the inventory once and pass it to both extractors:

```python
    inventory = build_symbol_inventory(repo_path, exclude_patterns)

    ep_nodes, ep_edges = extract_endpoints(repo_path, exclude_patterns)
    handler_nodes = [n for n in ep_nodes if n.type == "function"]

    model_nodes, model_edges = extract_models(repo_path, handler_nodes, exclude_patterns)
    sql_nodes, sql_edges, sql_dynamic_gaps = extract_sql(
        repo_path, exclude_patterns, inventory=inventory
    )
    call_nodes, call_edges, call_excluded, call_coverage = extract_calls(
        repo_path, exclude_patterns, inventory=inventory
    )
```

Update the stale comment right below (currently lines 31-34, above `all_nodes = ep_nodes + model_nodes + sql_nodes + call_nodes`) — it currently claims ordering is why the right `line`/`hash` win, which is no longer the mechanism after this plan. Replace it with:

```python
    # Order still matters for which extractor's `props` win a given id (e.g.
    # an endpoint handler's is_handler=True vs. the call visitor's generic
    # stub) — but file/line/hash correctness no longer depends on it: sql.py
    # and calls.py both hydrate from the same shared `inventory` now, and
    # graph/build.py raises if two sources ever disagree on identity again.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calls.py tests/test_pipeline.py -v`
Expected: PASS (all tests)

Run: `pytest -q`
Expected: full suite passes.

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/calls.py src/cc/pipeline.py tests/test_calls.py tests/test_pipeline.py
git commit -m "fix: share one SymbolInventory between calls.py and sql.py via pipeline.py"
```

---

### Task 3: `graph/build.py` — raise on conflicting node identity instead of silently picking the first

**Files:**
- Modify: `src/cc/graph/build.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Produces: `build_graph(nodes: list[Node], edges: list[Edge]) -> Graph` — same signature, now raises `ValueError` for a conflicting duplicate id instead of silently keeping the first.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_graph.py
import pytest


def test_build_raises_on_conflicting_node_identity():
    nodes = [
        Node(
            id="function:app.handler",
            type="function",
            file="f.py",
            line=1,
            hash="a" * 64,
            inferred=False,
            props={},
        ),
        Node(
            id="function:app.handler",
            type="function",
            file="f.py",
            line=5,  # conflicting line for the same id
            hash="b" * 64,  # conflicting hash for the same id
            inferred=False,
            props={},
        ),
    ]
    with pytest.raises(ValueError, match="function:app.handler"):
        build_graph(nodes, [])


def test_build_allows_duplicate_with_matching_identity_but_different_props():
    # Different props (e.g. is_handler) for the same id/file/line/hash is fine —
    # only file/line/hash conflicts are a real defect.
    nodes = [
        Node(
            id="function:app.handler",
            type="function",
            file="f.py",
            line=1,
            hash="a" * 64,
            inferred=False,
            props={"is_handler": True},
        ),
        Node(
            id="function:app.handler",
            type="function",
            file="f.py",
            line=1,
            hash="a" * 64,
            inferred=False,
            props={"is_handler": False},
        ),
    ]
    graph = build_graph(nodes, [])
    assert len(graph.nodes) == 1
    assert graph.nodes[0].props == {"is_handler": True}  # first registration wins props
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_graph.py -v`
Expected: `test_build_raises_on_conflicting_node_identity` FAILS — no `ValueError` is raised today (the conflicting second node is silently dropped by the current dict-based `if n.id not in seen` check). `test_build_allows_duplicate_with_matching_identity_but_different_props` passes already (documents existing behavior, included for regression safety once the raise is added).

- [ ] **Step 3: Implement**

Replace `build_graph` (currently the whole file, lines 1-26) with:

```python
from cc.graph.schema import Edge, Graph, Node


def build_graph(nodes: list[Node], edges: list[Edge]) -> Graph:
    seen: dict[str, Node] = {}
    for n in nodes:
        if n.id in seen:
            existing = seen[n.id]
            if (existing.file, existing.line, existing.hash) != (n.file, n.line, n.hash):
                raise ValueError(
                    f"Conflicting node identity for id={n.id!r}: "
                    f"first registered as file={existing.file!r} line={existing.line} "
                    f"hash={existing.hash!r}, later registered as file={n.file!r} "
                    f"line={n.line} hash={n.hash!r}"
                )
            continue
        seen[n.id] = n

    node_ids = set(seen)
    valid_edges: list[Edge] = []
    dropped: list[Edge] = []
    for e in edges:
        if e.from_ in node_ids and e.to in node_ids:
            valid_edges.append(e)
        else:
            dropped.append(e)

    if dropped:
        print(f"  graph build: {len(dropped)} edge(s) dropped — endpoint node missing:")
        for e in dropped:
            missing = [x for x in (e.from_, e.to) if x not in node_ids]
            print(f"    {e.type}: {e.from_} -> {e.to} (missing: {', '.join(missing)})")

    return Graph(nodes=list(seen.values()), edges=valid_edges, gaps=[])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_graph.py -v`
Expected: PASS (all tests, including the two new ones and every pre-existing test — `test_build_deduplicates_nodes`'s fixture uses byte-identical duplicates, which still merge fine under the new check)

Run: `pytest -q`
Expected: full suite passes — this is the task most likely to surface a latent conflict elsewhere in the codebase; if any existing fixture/test now raises unexpectedly, that's a real pre-existing identity conflict this plan is meant to catch, not a false positive to suppress. Investigate and report rather than loosening the check.

- [ ] **Step 5: Commit**

```bash
git add src/cc/graph/build.py tests/test_graph.py
git commit -m "fix: raise on conflicting node identity in graph build instead of silently picking the first"
```

---

### Task 4: End-to-end regression test against the original bug report

**Files:**
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `run` (`src/cc/pipeline.py`), `SIMPLE_API` (`tests/conftest.py`), `node_hash` (`src/cc/graph/hash_util.py`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_pipeline.py
from cc.graph.hash_util import node_hash


def test_db_function_node_uses_def_line_not_call_site_line():
    # Original bug report: tests/fixtures/simple_api/db.py's create_message
    # is defined at line 1, but before this fix the compiled graph reported
    # line 2 (the `await conn.execute(...)` call site inside it).
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        fn_node = next(n for n in data["nodes"] if n["id"] == "function:db.create_message")
        assert fn_node["line"] == 1
        expected_hash = node_hash(SIMPLE_API / "db.py", 1, 5)
        assert fn_node["hash"] == expected_hash
```

(`tempfile`, `pathlib`, `json`, `run`, `SIMPLE_API` are already imported at the top of `tests/test_pipeline.py` — no new imports needed besides `node_hash`)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline.py::test_db_function_node_uses_def_line_not_call_site_line -v`
Expected: if Tasks 1-3 are already merged (this task runs last), this test should already PASS — it's the confirming regression test, not new functionality. Run it to make sure it genuinely exercises the fix: temporarily check what it would have reported before this plan (optional sanity check, not required to proceed) by confirming `fn_node["line"] == 1` — if Task 1-3 are correctly implemented this assertion holds.

- [ ] **Step 3: No implementation needed — this task is pure verification**

If the test in Step 2 already passes, there is nothing to implement — Tasks 1-3 already fixed the behavior this test checks. If it fails, that means Tasks 1-3 have a gap; do not weaken this test to make it pass — go back and fix the gap in the relevant task's code.

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: full suite passes, confirming zero regressions across the whole plan.

- [ ] **Step 5: Commit**

```bash
git add tests/test_pipeline.py
git commit -m "test: add end-to-end regression test for SQL function-node hydration fix"
```
