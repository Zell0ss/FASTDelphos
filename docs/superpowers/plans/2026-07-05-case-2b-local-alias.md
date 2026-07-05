# Case 2b — Local Alias to External Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the call classifier so a local variable assigned from an external import's attribute call (`client = anthropic.AsyncAnthropic(...)`, `match = re.search(...)`) is tracked as an alias to that external package — so later calls through it (`client.messages.stream(...)`, `match.group(1)`) classify as `external` instead of `dynamic`. Per the decided spec in `doc_proyecto/VISITOR.md` §"Caso 2b".

**Architecture:** A new pure function `build_local_alias_table` in `_calls_resolver.py` scans one function's assignments (reusing `classify_call` itself to classify each candidate RHS — no new classification logic, just a new place that calls the existing one) and returns `dict[local_name, external_package]` for names it trusts. The orchestrator (`calls.py`) computes this per function and merges it into that function's own copy of the import table before resolving its call sites — local aliases only ever apply within their own function's scope.

**Tech Stack:** `ast` (stdlib). No new dependencies.

## Global Constraints

- **Only trust `name = base.attr(...)` where `base` resolves to `external`.** If `base` resolves to `internal` (or to `dynamic`), do NOT create an alias for `name` — internal-base aliasing is explicitly a different, not-yet-decided risk profile (see `VISITOR.md`'s open finding on internal re-exports).
- **No last-wins.** If a name has more than one qualifying assignment in the same function scope and they disagree (different external package, or one qualifies and another doesn't), drop the name entirely — it falls to whatever it would normally resolve to (almost always `dynamic`, since it's not literally in the import table).
- **Scope: per-function only.** An alias computed for one function must never leak into another function's resolution. Nested/closure defs are folded into their enclosing named function already (existing convention) — the alias table follows the same folding.
- **Not a case 4.** This extends case 2's mechanism (attribute-on-import, now one indirection further) — it does not introduce a new resolution *strategy*, it reuses `classify_call` itself on the RHS.
- **Determinism:** iterating `ast.walk(fn_node)` in fixed AST order and building the alias dict from it is already deterministic — no unordered set/dict iteration should leak into which alias "wins" (there is no winning; disagreement means exclusion, not arbitration).

---

### Task 1: `build_local_alias_table` in `_calls_resolver.py`

**Files:**
- Modify: `src/cc/extract/_calls_resolver.py`
- Test: `tests/test_calls_resolver.py` (append)

**Interfaces:**
- Consumes: `classify_call`, `SymbolInventory` (already defined in this module).
- Produces: `build_local_alias_table(fn_node: ast.AST, import_table: dict[str, str], module_qname: str, class_qname: str | None, inventory: SymbolInventory) -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_calls_resolver.py`:

```python
from cc.extract._calls_resolver import build_local_alias_table


def _parse_fn(source: str) -> ast.AST:
    tree = ast.parse(source)
    return tree.body[0]


def test_alias_to_external_attribute_call_is_tracked():
    inv = _inventory_with(top_level=set())  # "anthropic" is not a repo package
    table = {"anthropic": "anthropic"}
    fn = _parse_fn(
        "def f():\n"
        "    client = anthropic.AsyncAnthropic()\n"
        "    return client\n"
    )
    aliases = build_local_alias_table(fn, table, "pkg.mod", None, inv)
    assert aliases == {"client": "anthropic"}


def test_alias_to_internal_base_is_not_tracked():
    inv = _inventory_with(top_level={"services"})
    table = {"helper_mod": "services.helper_mod"}
    fn = _parse_fn(
        "def f():\n"
        "    x = helper_mod.Thing()\n"
        "    return x\n"
    )
    aliases = build_local_alias_table(fn, table, "pkg.mod", None, inv)
    assert aliases == {}


def test_alias_dropped_on_disagreeing_reassignment():
    inv = _inventory_with(top_level=set())
    table = {"anthropic": "anthropic", "boto3": "boto3"}
    fn = _parse_fn(
        "def f(flag):\n"
        "    if flag:\n"
        "        client = anthropic.AsyncAnthropic()\n"
        "    else:\n"
        "        client = boto3.client('s3')\n"
        "    return client\n"
    )
    aliases = build_local_alias_table(fn, table, "pkg.mod", None, inv)
    assert aliases == {}


def test_alias_kept_when_reassigned_with_same_external_package():
    inv = _inventory_with(top_level=set())
    table = {"anthropic": "anthropic"}
    fn = _parse_fn(
        "def f(flag):\n"
        "    if flag:\n"
        "        client = anthropic.AsyncAnthropic(key='a')\n"
        "    else:\n"
        "        client = anthropic.AsyncAnthropic(key='b')\n"
        "    return client\n"
    )
    aliases = build_local_alias_table(fn, table, "pkg.mod", None, inv)
    assert aliases == {"client": "anthropic"}


def test_alias_dropped_when_mixed_with_non_qualifying_assignment():
    inv = _inventory_with(top_level=set())
    table = {"anthropic": "anthropic"}
    fn = _parse_fn(
        "def f(flag):\n"
        "    client = anthropic.AsyncAnthropic()\n"
        "    if flag:\n"
        "        client = None\n"
        "    return client\n"
    )
    aliases = build_local_alias_table(fn, table, "pkg.mod", None, inv)
    assert aliases == {}


def test_non_call_or_non_attribute_rhs_is_not_tracked():
    inv = _inventory_with(top_level=set())
    fn = _parse_fn(
        "def f():\n"
        "    x = 5\n"
        "    y = some_function()\n"
    )
    aliases = build_local_alias_table(fn, {}, "pkg.mod", None, inv)
    assert aliases == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calls_resolver.py -v -k alias`
Expected: FAIL — `ImportError: cannot import name 'build_local_alias_table'`

- [ ] **Step 3: Implement**

Append to `src/cc/extract/_calls_resolver.py`:

```python
def build_local_alias_table(
    fn_node: ast.AST,
    import_table: dict[str, str],
    module_qname: str,
    class_qname: str | None,
    inventory: SymbolInventory,
) -> dict[str, str]:
    """Track simple local aliases to external imports within one function scope.

    Only `name = base.attr(...)` assignments where `base` resolves — via the
    same classify_call used for every other call site — to an EXTERNAL
    package are trusted. If a name has more than one qualifying assignment
    in this scope and they disagree (different external package, or mixed
    with a non-qualifying assignment), the name is dropped entirely rather
    than guessing which one wins — no last-wins.
    """
    seen: dict[str, set[str]] = {}
    for node in ast.walk(fn_node):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id

        value = "\x00other"  # sentinel distinct from any real package name
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            resolution = classify_call(
                node.value, import_table=import_table, module_qname=module_qname,
                class_qname=class_qname, inventory=inventory,
            )
            if resolution.kind == "external":
                value = resolution.package

        seen.setdefault(name, set()).add(value)

    return {
        name: next(iter(values))
        for name, values in seen.items()
        if len(values) == 1 and next(iter(values)) != "\x00other"
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calls_resolver.py -v -k alias`
Expected: PASS (6 new tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/_calls_resolver.py tests/test_calls_resolver.py
git commit -m "feat: case 2b — track local aliases to external imports"
```

---

### Task 2: Module-level alias table + wire both into the `calls.py` orchestrator

**Revision note (post-review):** the original version of this task asked for a new fixture file inside the shared `tests/fixtures/calls_repo/` tree, "tested only via its own `per_file` entry, never via the `total` assertions." That's impossible — `extract_calls`'s `coverage["total"]` is a sum over every file `collect_py_files` finds in the repo passed in, so adding *any* file under `CALLS_REPO` necessarily changes `total`. This revision uses `tmp_path`-based tests instead (matching the style already used for the SQL f-string fix), so the shared fixture and its hand-verified totals are never touched.

**Also new in this revision:** the real motivating agora pattern (`client = anthropic.AsyncAnthropic(...)` in `llm.py`) turned out to be a **module-level** assignment, not a per-function one — the originally-scoped "per-function only" case 2b doesn't reach it at all. This task now also builds a module-level alias table (reusing `build_local_alias_table` itself — it already takes a generic `ast.AST` root and `_own_scope_assign_nodes` already generalizes to a `Module` node with no changes needed), with one added rule: **a function's own local (re)assignment of a name shadows the module-level alias for that name within that function** — even if the function's local assignment doesn't itself qualify as an alias (matches real Python scoping: a local binding always shadows a global one, whether or not the local binding is itself an "alias").

**Files:**
- Modify: `src/cc/extract/_calls_resolver.py` (one new small function)
- Modify: `src/cc/extract/calls.py`
- Test: `tests/test_calls_resolver.py` (append)
- Test: `tests/test_calls.py` (append, `tmp_path`-based — no shared fixture touched)

**Interfaces:**
- Consumes: `build_local_alias_table` from Task 1 (used twice: once per-module, once per-function — same function, different AST root).
- Produces: `local_assignment_targets(fn_node: ast.AST) -> set[str]` (new, in `_calls_resolver.py`).

- [ ] **Step 1: Write the failing test for `local_assignment_targets`**

Append to `tests/test_calls_resolver.py`:

```python
from cc.extract._calls_resolver import local_assignment_targets


def test_local_assignment_targets_finds_simple_name_targets():
    fn = _parse_fn(
        "def f():\n"
        "    client = None\n"
        "    x = 1\n"
        "    return client\n"
    )
    assert local_assignment_targets(fn) == {"client", "x"}


def test_local_assignment_targets_ignores_nested_def_scope():
    fn = _parse_fn(
        "def f():\n"
        "    def inner():\n"
        "        client = None\n"
        "    return 1\n"
    )
    assert local_assignment_targets(fn) == set()


def test_local_assignment_targets_empty_when_nothing_assigned():
    fn = _parse_fn(
        "def f():\n"
        "    return 1\n"
    )
    assert local_assignment_targets(fn) == set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calls_resolver.py -v -k local_assignment_targets`
Expected: FAIL — `ImportError: cannot import name 'local_assignment_targets'`

- [ ] **Step 3: Implement `local_assignment_targets`**

Append to `src/cc/extract/_calls_resolver.py`:

```python
def local_assignment_targets(fn_node: ast.AST) -> set[str]:
    """Names assigned via a simple `name = ...` anywhere in fn_node's own scope,
    regardless of whether the assignment qualifies as an external alias.

    Used so a function's own (possibly non-qualifying) rebinding of a name
    shadows any module-level alias for that same name — matching real Python
    scoping, where a local assignment always shadows an outer/global binding.
    """
    names: set[str] = set()
    for node in _own_scope_assign_nodes(fn_node):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            names.add(node.targets[0].id)
    return names
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calls_resolver.py -v -k local_assignment_targets`
Expected: PASS (3 new tests)

- [ ] **Step 5: Write the failing integration tests (tmp_path-based, no shared fixture touched)**

Append to `tests/test_calls.py`:

```python
def test_case_2b_function_scope_alias_resolves_as_external(tmp_path):
    (tmp_path / "mod.py").write_text(
        "import re\n\n\n"
        "def f(text):\n"
        "    match = re.search('x', text)\n"
        "    return match.group(1)\n",
        encoding="utf-8",
    )
    _, edges, _, coverage = extract_calls(tmp_path)
    froms = {e.from_ for e in edges}
    assert "function:mod.f" not in froms  # no internal edge — both calls are external
    per_file = coverage["per_file"]["mod.py"]
    assert per_file["resolved_external"] == 2  # re.search(...) and match.group(...)
    assert per_file["unresolved_dynamic"] == 0


def test_case_2b_module_scope_alias_resolves_as_external(tmp_path):
    (tmp_path / "mod.py").write_text(
        "import anthropic\n\n"
        "client = anthropic.AsyncAnthropic()\n\n\n"
        "def call_a(prompt):\n"
        "    return client.messages.stream(prompt)\n\n\n"
        "def call_b(prompt):\n"
        "    return client.messages.create(prompt)\n",
        encoding="utf-8",
    )
    _, edges, _, coverage = extract_calls(tmp_path)
    per_file = coverage["per_file"]["mod.py"]
    # anthropic.AsyncAnthropic() itself (module-level, not inside any function,
    # so not counted in any function's call_sites) + 2 function bodies each with
    # one external call through the module-level alias.
    assert per_file["resolved_external"] == 2
    assert per_file["unresolved_dynamic"] == 0


def test_case_2b_local_reassignment_shadows_module_alias(tmp_path):
    (tmp_path / "mod.py").write_text(
        "import anthropic\n\n"
        "client = anthropic.AsyncAnthropic()\n\n\n"
        "def uses_module_client(prompt):\n"
        "    return client.messages.stream(prompt)\n\n\n"
        "def uses_local_client(prompt, local_client):\n"
        "    client = local_client\n"
        "    return client.messages.stream(prompt)\n",
        encoding="utf-8",
    )
    _, edges, _, coverage = extract_calls(tmp_path)
    per_file = coverage["per_file"]["mod.py"]
    # uses_module_client's call resolves external via the module alias.
    # uses_local_client's call must NOT inherit the module alias — its own
    # `client = local_client` (non-qualifying: local_client isn't an import)
    # shadows it, so that call falls to dynamic instead of being wrongly
    # classified external.
    assert per_file["resolved_external"] == 1
    assert per_file["unresolved_dynamic"] == 1
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_calls.py -v -k case_2b`
Expected: FAIL — none of this resolution exists yet.

- [ ] **Step 7: Wire both alias tables into `calls.py`**

Add to the imports in `src/cc/extract/calls.py`:

```python
from cc.extract._calls_resolver import (
    build_import_table,
    build_local_alias_table,
    build_symbol_inventory,
    classify_call,
    local_assignment_targets,
)
```

Inside `extract_calls`, right after `import_table = build_import_table(tree, module_qname, is_package_init)` (still outside the per-function loop), add:

```python
        module_aliases = build_local_alias_table(
            tree, import_table, module_qname, None, inventory,
        )
```

Inside the per-function loop, right after computing `class_qname`, add:

```python
            local_aliases = build_local_alias_table(
                fn_node, import_table, module_qname, class_qname, inventory,
            )
            shadowed = local_assignment_targets(fn_node)
            effective_table = {
                **import_table,
                **{k: v for k, v in module_aliases.items() if k not in shadowed},
                **local_aliases,
            }
```

Then change the `classify_call(...)` invocation inside the call-site loop from using `import_table` to using `effective_table` (same as before — only the source of the merged table changed):

```python
                resolution = classify_call(
                    call, import_table=effective_table, module_qname=module_qname,
                    class_qname=class_qname, inventory=inventory,
                )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_calls.py tests/test_calls_resolver.py -v`
Expected: PASS — all tests, including the new ones. The pre-existing coverage-total assertions in `test_calls.py` that use the shared `CALLS_REPO` fixture (`total["functions"] == 10` etc.) must be completely unaffected — this task never touches `tests/fixtures/calls_repo/`. If any of those change, STOP: something is wrong, since nothing in this task should affect that fixture at all.

- [ ] **Step 9: Run the full suite**

Run: `pytest -q`
Expected: PASS, previous count plus 9 (3 `local_assignment_targets` tests + 3 `case_2b` integration tests + 3 already-passing from Task 1, i.e. net +6 new tests from this task on top of Task 1's prior total).

- [ ] **Step 10: Recompile agora and report the actual numbers**

Run: `python -m cc compile /data/agora --out /tmp/agora-verify-case2b` (scratch output dir) and note the printed `unresolved_dynamic` count. Compare it to the pre-case-2b baseline of 243. Report the exact before/after numbers in your report — do not assume a specific expected number; agora's real `client`/`match` sites should now resolve external, so the count should drop, but report what actually happens rather than forcing a specific value.

- [ ] **Step 11: Commit**

```bash
git add src/cc/extract/_calls_resolver.py src/cc/extract/calls.py tests/test_calls_resolver.py tests/test_calls.py
git commit -m "feat: case 2b — module-level alias table + wire both scopes into the calls orchestrator"
```

---

## Self-Review Notes

1. **Spec coverage:** guardrail 1 (external-only) → Task 1's `test_alias_to_internal_base_is_not_tracked` + the `resolution.kind == "external"` check being the only path that sets a real value. Guardrail 2 (no last-wins) → Task 1's 3 disagreement tests (different package, mixed with non-qualifying, and the "kept when truly identical" positive case proving it's not over-conservative). Per-function scope, no cross-function leakage → Task 2's per-`fn_node` computation of `effective_table`, never mutating the shared `import_table`.
2. **Placeholder scan:** none found.
3. **Type consistency:** `build_local_alias_table` signature identical between Task 1's definition and Task 2's call site (`fn_node, import_table, module_qname, class_qname, inventory`, positional).
