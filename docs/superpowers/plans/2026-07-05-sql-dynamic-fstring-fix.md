# SQL Dynamic F-String Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `extract_sql.py` currently discards any DB-method call whose SQL argument isn't a plain string literal — including f-strings, even when the table name is trivially visible as static text. This silently drops real `writes`/`reads` edges (confirmed: 3 `UPDATE` statements in agora, invisible in the graph today). Per the decided spec in `doc_proyecto/VISITOR.md` §"Fix — extract_sql.py y SQL dinámico vía f-string".

**Architecture:** When the SQL argument is an `ast.JoinedStr`, search each of its `ast.Constant` fragments **individually** (never a concatenation across fragments — that could splice an unrelated identifier next to a keyword and fabricate a false table name) for a verb+table regex match. If found, emit the edge with empty columns (same treatment as the existing `SELECT *` case). If no verb+table is found in any single fragment, the SQL is genuinely runtime-bound — emit a `Gap` with `kind="unresolved_dynamic"`, `severity={"comprehension": "warning", "compliance": "error"}` (not `tool_limitation` — nothing failed; the data is genuinely not statically present).

**Tech Stack:** `ast` (stdlib), `re` (stdlib). No new dependencies.

## Global Constraints

- **Per-fragment matching only, never concatenated.** This is the one non-negotiable correctness rule — concatenating `ast.Constant` fragments before regex-matching can fabricate a false table name when a `FormattedValue` sits between the keyword and the real table name (e.g. `f"INSERT INTO {prefix}channels ..."` must NOT match "channels"). A false edge is worse than a gap.
- **Scope: `ast.JoinedStr` only.** No `.format()`, no string concatenation with `+` — no evidence either exists in agora. Do not add without new eval-driven evidence.
- **Gap kind is `unresolved_dynamic`, not `tool_limitation`**, for the "no verb/table found at all" fallback. Severity: `{"comprehension": "warning", "compliance": "error"}`.
- **`extract_sql()` signature becomes `(nodes, edges, dynamic_gaps)`** where `dynamic_gaps: list[tuple[str, int, str]]` = `(file, lineno, fn_qname)`. `pipeline.py` converts each into a `Gap`, mirroring exactly how it already converts `extract_calls`'s `excluded` list into `tool_limitation` gaps — same mechanism, not a new one.
- **Matched sites get the exact same treatment as the existing `SELECT *` precedent**: the edge is emitted, columns stay empty for that call site (no columns are guessed).

---

### Task 1: Detect dynamic-but-partially-static SQL in `extract_sql.py`

**Files:**
- Modify: `src/cc/extract/sql.py`
- Modify: `src/cc/pipeline.py`
- Test: `tests/test_sql.py` (append)
- Test: `tests/test_pipeline.py` (append)

**Interfaces:**
- Produces: `extract_sql(repo_path) -> tuple[list[Node], list[Edge], list[tuple[str, int, str]]]` (third element: `dynamic_gaps`).

- [ ] **Step 1: Fix the 6 pre-existing tests in `tests/test_sql.py` for the new 3-tuple signature**

`tests/test_sql.py` currently has NO `_write` helper and all 6 existing tests unpack `extract_sql(...)` as a 2-tuple (`nodes, edges = extract_sql(SIMPLE_API)` or `nodes, _ = ...` or `_, edges = ...`). Once Step 3/4 change the signature to a 3-tuple, every one of these breaks with `ValueError: not enough values to unpack`. Fix all 6 by adding a third element:

- `test_finds_messages_table`, `test_table_node_id`, `test_write_columns_from_insert`: change `nodes, _ = extract_sql(SIMPLE_API)` to `nodes, _, _ = extract_sql(SIMPLE_API)`
- `test_extracts_write_edge`, `test_extracts_read_edge`: change `nodes, edges = extract_sql(SIMPLE_API)` to `nodes, edges, _ = extract_sql(SIMPLE_API)`
- `test_via_contains_file_and_line`: change `_, edges = extract_sql(SIMPLE_API)` to `_, edges, _ = extract_sql(SIMPLE_API)`

Add these two lines at the top of `tests/test_sql.py` (it currently has no `pathlib` import and no fixture-writing helper):

```python
import pathlib

from cc.extract.sql import extract_sql
from tests.conftest import SIMPLE_API


def _write(root: pathlib.Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
```

(replace the file's current 2-line header of just `from cc.extract.sql import extract_sql` / `from tests.conftest import SIMPLE_API` with the above 6 lines).

- [ ] **Step 1b: Write the new failing tests**

Append to `tests/test_sql.py`:

```python
def test_fstring_update_with_static_table_emits_writes_edge(tmp_path):
    _write(tmp_path, "db.py", (
        "async def update_channel(cur, channel_id, fields):\n"
        "    set_clause = ', '.join(f'{k} = %s' for k in fields)\n"
        "    values = list(fields.values()) + [channel_id]\n"
        "    await cur.execute(f\"UPDATE channels SET {set_clause} WHERE id = %s\", values)\n"
    ))
    nodes, edges, dynamic_gaps = extract_sql(tmp_path)
    write_edges = [e for e in edges if e.type == "writes" and e.to == "table:channels"]
    assert len(write_edges) == 1
    assert write_edges[0].props["via"] == f"{tmp_path / 'db.py'}:4"
    table_node = next(n for n in nodes if n.id == "table:channels")
    assert table_node.props["columns"] == []
    assert dynamic_gaps == []


def test_fstring_select_with_static_table_emits_reads_edge(tmp_path):
    _write(tmp_path, "db.py", (
        "async def get_messages(cur, condition):\n"
        "    await cur.execute(f\"SELECT * FROM messages WHERE {condition}\")\n"
    ))
    nodes, edges, dynamic_gaps = extract_sql(tmp_path)
    read_edges = [e for e in edges if e.type == "reads" and e.to == "table:messages"]
    assert len(read_edges) == 1
    assert dynamic_gaps == []


def test_fstring_dynamic_prefix_before_table_does_not_fabricate_edge(tmp_path):
    # The Frankenstein case: concatenating fragments would wrongly read "channels"
    # as the table name, when the real (unknowable) table is f"{prefix}channels".
    _write(tmp_path, "db.py", (
        "async def insert_dynamic(cur, prefix, values):\n"
        "    await cur.execute(f\"INSERT INTO {prefix}channels (a, b) VALUES (%s, %s)\", values)\n"
    ))
    nodes, edges, dynamic_gaps = extract_sql(tmp_path)
    assert edges == []
    assert not any(n.type == "table" for n in nodes)
    assert len(dynamic_gaps) == 1
    file, lineno, fn_qname = dynamic_gaps[0]
    assert lineno == 2
    assert fn_qname == "db.insert_dynamic"


def test_fully_dynamic_sql_with_no_static_verb_or_table_is_a_gap(tmp_path):
    _write(tmp_path, "db.py", (
        "async def run_query(cur, query_var, values):\n"
        "    await cur.execute(query_var, values)\n"
    ))
    nodes, edges, dynamic_gaps = extract_sql(tmp_path)
    assert edges == []
    assert len(dynamic_gaps) == 1
    file, lineno, fn_qname = dynamic_gaps[0]
    assert lineno == 2
    assert fn_qname == "db.run_query"
```

(the `_write` helper these tests call was already added to the top of the file in Step 1.)

Append to `tests/test_pipeline.py`:

```python
def test_pipeline_emits_unresolved_dynamic_gap_for_fully_dynamic_sql():
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "backend").mkdir(parents=True)
        (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "backend" / "db.py").write_text(
            "async def run_query(cur, query_var, values):\n"
            "    await cur.execute(query_var, values)\n",
            encoding="utf-8",
        )
        out = pathlib.Path(d) / "out"
        run(repo, out)
        data = json.loads((out / "graph.json").read_text())
        dyn_gaps = [g for g in data["gaps"] if g["kind"] == "unresolved_dynamic"]
        assert len(dyn_gaps) == 1
        assert dyn_gaps[0]["severity"] == {"comprehension": "warning", "compliance": "error"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sql.py tests/test_pipeline.py -v -k "fstring or fully_dynamic or unresolved_dynamic_gap"`
Expected: FAIL — `extract_sql` still returns a 2-tuple, and the f-string cases produce no edges at all today (silently dropped, no gap).

- [ ] **Step 3: Implement the fragment-scoped regex extraction**

In `src/cc/extract/sql.py`, add near the top (after the existing imports, before `_DB_METHODS`):

```python
import re
```

Add after `_str_const`:

```python
_SQL_VERB_PATTERNS = [
    (re.compile(r"\bUPDATE\s+([a-zA-Z_]\w*)", re.IGNORECASE), "writes"),
    (re.compile(r"\bINSERT\s+INTO\s+([a-zA-Z_]\w*)", re.IGNORECASE), "writes"),
    (re.compile(r"\bDELETE\s+FROM\s+([a-zA-Z_]\w*)", re.IGNORECASE), "writes"),
    (re.compile(r"\bFROM\s+([a-zA-Z_]\w*)", re.IGNORECASE), "reads"),
]


def _dynamic_sql_verb_table(node: ast.expr | None) -> tuple[str, str] | None:
    """Best-effort verb+table extraction from an f-string's STATIC fragments only.

    Only trusts a match found entirely within a single ast.Constant fragment —
    never a concatenation across a FormattedValue gap, which could splice an
    unrelated identifier next to a keyword and fabricate a false table name
    (e.g. f"INSERT INTO {prefix}channels ..." must NOT match "channels" — the
    real table name is dynamic and unknowable, so this must fall through to
    "no match" rather than guess).
    """
    if not isinstance(node, ast.JoinedStr):
        return None
    for value in node.values:
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for pattern, op in _SQL_VERB_PATTERNS:
            m = pattern.search(value.value)
            if m:
                return op, m.group(1)
    return None
```

- [ ] **Step 4: Wire the fallback into the main loop**

In `src/cc/extract/sql.py`, inside `extract_sql`, change:

```python
            sql = _str_const(node.args[0])
            if not sql:
                continue
```

to:

```python
            sql = _str_const(node.args[0])
            if not sql:
                dynamic = _dynamic_sql_verb_table(node.args[0])
                if dynamic is None:
                    fn_qname = _find_enclosing_function(node, tree, module_qname)
                    dynamic_gaps.append((str(file), node.lineno, fn_qname))
                    continue
                op, tbl = dynamic
                fn_qname = _find_enclosing_function(node, tree, module_qname)
                via = f"{file}:{node.lineno}"
                table_columns[tbl].update(())
                if tbl not in table_files:
                    table_files[tbl] = (str(file), node.lineno)
                raw_edges.append((fn_qname, tbl, op, via, str(file), node.lineno))
                continue
```

Add `dynamic_gaps: list[tuple[str, int, str]] = []` alongside the other accumulator declarations at the top of `extract_sql` (next to `table_columns`, `table_files`, `raw_edges`).

Change the function's return type annotation and final `return` statement:

```python
def extract_sql(
    repo_path: str | pathlib.Path,
) -> tuple[list[Node], list[Edge], list[tuple[str, int, str]]]:
```

```python
    return list(table_nodes.values()) + list(fn_nodes.values()), edges, dynamic_gaps
```

- [ ] **Step 5: Wire `pipeline.py` to the 3-tuple**

In `src/cc/pipeline.py`, change:

```python
    sql_nodes, sql_edges = extract_sql(repo_path)
```

to:

```python
    sql_nodes, sql_edges, sql_dynamic_gaps = extract_sql(repo_path)
```

And after the existing `for filepath, error in call_excluded:` gap-append loop (before the `if call_excluded:` print block, or right after — either position is fine as long as it runs before `emit(graph, out_dir)`), add:

```python
    for filepath, lineno, fn_qname in sql_dynamic_gaps:
        graph.gaps.append(Gap(
            kind="unresolved_dynamic",
            where=f"{filepath}:{lineno}",
            node_id=f"function:{fn_qname}",
            missing=f"SQL built dynamically (f-string) in `{fn_qname}` — "
                    "table/operation could not be statically determined",
            suggested="Consider keeping the table name as literal text even if "
                      "the rest of the query is dynamic, so lineage stays traceable.",
            severity={"comprehension": "warning", "compliance": "error"},
        ))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_sql.py tests/test_pipeline.py -v`
Expected: PASS — all tests in both files, including the 5 new ones.

- [ ] **Step 7: Run the full suite**

Run: `pytest -q`
Expected: PASS, same count as before plus 5.

- [ ] **Step 8: Recompile agora and confirm the 3 known sites are now visible**

Run: `python -m cc compile /data/agora --out /tmp/agora-verify-fstring-fix` (use any scratch output directory), then:

```bash
python3 -c "
import json
data = json.load(open('/tmp/agora-verify-fstring-fix/graph.json'))
writes = [e for e in data['edges'] if e['type']=='writes']
for e in writes:
    if 'channels' in e['to'] or 'profiles' in e['to']:
        print(e['from_'], '->', e['to'], e['props']['via'])
gaps = [g for g in data['gaps'] if g['kind']=='unresolved_dynamic']
print('unresolved_dynamic gaps:', len(gaps))
"
```
Expected: the 3 previously-invisible writes (`channels.py:33`, `channels.py:154`, `profiles.py:44`) now appear as `writes` edges with matching `via`, and `unresolved_dynamic` gaps is `0` for agora (all 3 known sites have a statically-visible table name — none of agora's real f-strings are the fully-dynamic case).

- [ ] **Step 9: Commit**

```bash
git add src/cc/extract/sql.py src/cc/pipeline.py tests/test_sql.py tests/test_pipeline.py
git commit -m "feat: extract_sql resolves table names from f-string SQL, emits unresolved_dynamic gap otherwise"
```

---

## Self-Review Notes

1. **Spec coverage:** per-fragment (not concatenated) matching → Task 1 Step 3 docstring + Step 1's Frankenstein test. `ast.JoinedStr`-only scope → `_dynamic_sql_verb_table`'s first `isinstance` check. `unresolved_dynamic` gap kind + severity → Step 5. 3-tuple signature → Steps 4-5. `SELECT *`-style empty-columns treatment for matched sites → Step 4's `table_columns[tbl].update(())`.
2. **Placeholder scan:** none found.
3. **Type consistency:** `extract_sql` signature `(nodes, edges, dynamic_gaps)` used identically in Task 1's own definition and in `pipeline.py`'s consumption.
