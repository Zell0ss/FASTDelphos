# Classifier Internal/External Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the call classifier so a repo whose top-level package is a PEP 420 namespace package (no `__init__.py`) — the confirmed illumiows bug — gets its own calls correctly resolved as `resolved_internal`, instead of every one of them silently misclassifying as `resolved_external`.

**Architecture:** `build_symbol_inventory()`'s top-level discovery loop currently requires `__init__.py` to even attempt loading a directory via griffe — so a namespace package is never attempted, never ends up in `top_level_packages`, and its calls fall through to "external, with positive evidence." The fix broadens discovery to any top-level directory containing `.py` files (with or without `__init__.py`) in one single, unified pass that also picks up loose root-level modules in the same repo (illumiows has both at once). A name counts as internal the moment we *attempt* to load it, not only if griffe succeeds — a package with one syntax-broken file inside is still our code. Failed load attempts, previously silently swallowed, now surface as `tool_limitation` gaps with the concrete error. Classification order (inventory lookup first, external second, dynamic third) is already correct in `_classify_qualname`/`classify_call` and needs no change. A `--toppackages` CLI escape hatch and a first-report-line sanity check round out the fix for repos where even the broadened auto-detection still guesses wrong, and for making a `0 internal` result impossible to miss.

**Tech Stack:** Python 3.11, `griffe` (symbol inventory + PEP 420 namespace package loading), stdlib `ast`/`warnings`, `pytest`.

## Global Constraints

- Contract: `doc_proyecto/ESQUEMA_POC.md` is NOT touched — this plan changes the call classifier, not the graph schema.
- `top_level_packages` is derived from a single, unified discovery-and-attempt pass (broadened to include namespace packages) — not from a second, independent scan of what's already in the loaded inventory. This is a deliberate, discussed deviation from the spec's literal one-line pseudocode (`internal_top_levels = {qualname.split(".")[0] for qualname in inventory}`): that formula would have regressed an existing, deliberate test (`test_top_level_packages_recorded_even_if_load_fails`) by demoting a package with one broken file inside from "internal, opaque" to "external" — arguably worse than the bug being fixed. The spec's acceptance criteria are unaffected either way.
- A top-level name counts as internal as soon as it is *attempted* — never gated on griffe's load actually succeeding.
- Every `_try_load` failure must be recorded with its concrete error message and surfaced as a `tool_limitation` gap — never a silent `except Exception: pass`.
- Classification order (`_classify_qualname`: inventory lookup → top-level membership → dynamic) is already correct per spec item 2 and is NOT modified by this plan.
- `--toppackages NAME,NAME` is an override/escape valve for repos where auto-detection still gets it wrong — not the fix itself. Default behavior (no flag) is full auto-derivation. If the override disagrees with what auto-detection found, print a visible warning showing both sets; still honor the override.
- The first-report-line sanity check fires only when `resolved_internal == 0` **and** the inventory has at least one function — never on a genuinely empty/non-Python repo.
- No agora regression: recompiling agora before and after this plan must produce byte-identical `nodes`/`edges` (ids, hashes, props) in `graph.json` — agora's `backend/` package already had `__init__.py` and was already correctly discovered under the old code, so this is a true no-op for it.
- illumiows itself is not available in this dev environment. Its topology (namespace-package root + normal subpackages inside + loose root modules) is reproduced as a permanent synthetic fixture; final validation against the real repo is Josem's to run separately after merge.

---

### Task 1: Broaden top-level discovery to namespace packages, surface load failures

**Files:**
- Modify: `src/cc/extract/_calls_resolver.py:21-127` (the `SymbolInventory` dataclass and `build_symbol_inventory`)
- Test: `tests/test_calls_resolver.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: nothing new — `excluded_files` (already imported), `griffe`, `_walk_griffe_functions` (unchanged).
- Produces: `SymbolInventory.load_failures: list[tuple[str, str, str]]` — `(pkg_name, location, error)`. Consumed by Task 2. `build_symbol_inventory(repo_path, exclude_patterns=(), use_gitignore=True)` signature unchanged for now (Task 3 adds a 4th param).

- [ ] **Step 1: Write the failing tests**

In `tests/test_calls_resolver.py`, replace the existing `test_top_level_packages_recorded_even_if_load_fails` (currently lines 70-76) with:

```python
def test_top_level_packages_recorded_even_if_load_fails(tmp_path):
    repo = _make_repo(tmp_path)
    # SyntaxError in the package's own __init__.py — verified empirically:
    # this is what actually makes griffe.load raise (LoadingError). A
    # SyntaxError in a *nested* submodule (e.g. broken/oops.py with a valid
    # broken/__init__.py) does NOT raise — griffe silently loads the rest of
    # the package and just omits that one submodule, no exception at all.
    # That's a real, useful distinction: this test's actual point (a name
    # still counts as internal even when we can't fully load it) needs the
    # raising case to exercise the load_failures path below.
    _write(repo, "broken/__init__.py", "def f(:\n")
    inv = build_symbol_inventory(repo)
    assert "broken" in inv.top_level_packages  # attempted, not load-success-based
    assert "services" in inv.top_level_packages
    failures = {pkg: (location, error) for pkg, location, error in inv.load_failures}
    assert "broken" in failures
    assert str(repo / "broken") == failures["broken"][0]


def test_namespace_package_without_init_is_discovered_and_internal(tmp_path):
    # Mirrors illumiows: a root-level namespace package (no __init__.py) with
    # normal subpackages inside, alongside loose standalone modules at the
    # repo root. Before this fix, "api" was invisible to discovery (which
    # required __init__.py) — its calls resolved as external "with positive
    # evidence" instead of internal, because no top-level name for it ever
    # existed to check against.
    repo = tmp_path / "repo"
    _write(repo, "api/routes/__init__.py", "")
    _write(
        repo,
        "api/routes/views.py",
        (
            "from api.routes import crud\n\n\n"
            "def delete_iplist_allregions(list_id):\n"
            "    return crud.delete_iplist(list_id)\n"
        ),
    )
    _write(repo, "api/routes/crud.py", "def delete_iplist(list_id):\n    return list_id\n")
    _write(repo, "asgi.py", "from api.routes import views\n")
    _write(repo, "conftest.py", "import pytest\n")

    inv = build_symbol_inventory(repo)

    assert "api" in inv.top_level_packages
    assert "asgi" in inv.top_level_packages
    assert "conftest" in inv.top_level_packages
    assert "api.routes.views.delete_iplist_allregions" in inv.functions
    assert "api.routes.crud.delete_iplist" in inv.functions
    assert not inv.load_failures
```

In `tests/test_pipeline.py`, add:

```python
def test_namespace_package_calls_resolve_as_internal():
    # End-to-end regression for the illumiows classifier bug: a namespace
    # package (no __init__.py) at repo root must produce a real `calls`
    # edge between its own functions, not get miscategorized as external.
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "api" / "routes").mkdir(parents=True)
        (repo / "api" / "routes" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "api" / "routes" / "views.py").write_text(
            "from api.routes import crud\n\n\n"
            "def delete_iplist_allregions(list_id):\n"
            "    return crud.delete_iplist(list_id)\n",
            encoding="utf-8",
        )
        (repo / "api" / "routes" / "crud.py").write_text(
            "def delete_iplist(list_id):\n    return list_id\n", encoding="utf-8"
        )
        (repo / "asgi.py").write_text("from api.routes import views\n", encoding="utf-8")
        out = pathlib.Path(d) / "out"

        run(repo, out)

        data = json.loads((out / "graph.json").read_text())
        call_edges = {(e["from_"], e["to"]) for e in data["edges"] if e["type"] == "calls"}
        assert (
            "function:api.routes.views.delete_iplist_allregions",
            "function:api.routes.crud.delete_iplist",
        ) in call_edges
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calls_resolver.py::test_top_level_packages_recorded_even_if_load_fails tests/test_calls_resolver.py::test_namespace_package_without_init_is_discovered_and_internal tests/test_pipeline.py::test_namespace_package_calls_resolve_as_internal -v`
Expected: first test FAILS on the new `load_failures` assertions (`AttributeError: 'SymbolInventory' object has no attribute 'load_failures'`); the other two FAIL with `"api" not in {...}` / empty `call_edges`.

- [ ] **Step 3: Implement the fix**

In `src/cc/extract/_calls_resolver.py`, replace the `SymbolInventory` dataclass (currently lines 30-35):

```python
@dataclass
class SymbolInventory:
    functions: dict[str, FuncInfo] = field(default_factory=dict)
    class_bases: dict[str, list[str]] = field(default_factory=dict)
    class_methods: dict[str, dict[str, str]] = field(default_factory=dict)
    top_level_packages: set[str] = field(default_factory=set)
    load_failures: list[tuple[str, str, str]] = field(default_factory=list)
    # (pkg_name, location, error) — a top-level package/module we attempted
    # to load via griffe but couldn't (e.g. a SyntaxError inside it). It
    # still counts as internal in top_level_packages — we know it's ours,
    # we just can't see inside it right now.
```

Replace `build_symbol_inventory` (currently lines 76-127) with:

```python
def build_symbol_inventory(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> SymbolInventory:
    """Discover the repo's own top-level packages/modules and load every one
    of them via griffe, collecting every function/method qualname, class
    base-class relationship, and the set of top-level names that belong to
    the repo (used later to tell "external" imports from "internal but
    unresolved" ones).

    A top-level name counts as internal as soon as we ATTEMPT to load it —
    not only if griffe succeeds. A package with one syntax-broken file
    inside is still our code; calls into it should resolve as
    unresolved_dynamic ("ours, but opaque"), never external ("someone
    else's"). Failed attempts are recorded in `load_failures` so the caller
    can surface them as gaps instead of silently losing the information.

    Discovery does not require `__init__.py` — a PEP 420 namespace package
    (a directory with .py files but no `__init__.py`) is a perfectly valid
    top-level package and must be attempted like any other. Requiring
    `__init__.py` was the root cause of a real repo compiling with zero
    internal calls resolved: every call into its namespace-package root
    fell through to "external, with positive evidence" for lack of any
    top-level name to check it against.
    """
    repo_path = pathlib.Path(repo_path)
    excluded = excluded_files(repo_path, exclude_patterns, use_gitignore)
    inv = SymbolInventory()

    def _try_load(
        pkg_name: str, search_paths: list[pathlib.Path], location: pathlib.Path
    ) -> None:
        sys.path.insert(0, str(search_paths[0]))
        try:
            pkg = griffe.load(pkg_name, search_paths=search_paths)
            _walk_griffe_functions(pkg, inv, [], excluded)
        except Exception as exc:
            inv.load_failures.append((pkg_name, str(location), str(exc)))
        finally:
            try:
                sys.path.remove(str(search_paths[0]))
            except ValueError:
                pass

    found_any = False
    for entry in sorted(repo_path.iterdir()):
        if entry.name in _SKIP_DIRS:
            continue
        if entry.is_dir():
            if not any(entry.rglob("*.py")):
                continue
            inv.top_level_packages.add(entry.name)
            _try_load(entry.name, [repo_path], entry)
            found_any = True
        elif entry.suffix == ".py" and entry.name != "__init__.py":
            inv.top_level_packages.add(entry.stem)
            _try_load(entry.stem, [repo_path], entry)
            found_any = True

    if not found_any and (repo_path / "__init__.py").exists():
        inv.top_level_packages.add(repo_path.name)
        _try_load(repo_path.name, [repo_path.parent], repo_path)

    return inv
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calls_resolver.py -v && pytest tests/test_pipeline.py::test_namespace_package_calls_resolve_as_internal -v`
Expected: PASS, all of them (including every pre-existing `build_symbol_inventory` test in the file — re-run the whole file, not just the new tests, since the discovery loop was rewritten wholesale).

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS, no regressions elsewhere (nothing outside `_calls_resolver.py` changed yet).

- [ ] **Step 6: Commit**

```bash
git add src/cc/extract/_calls_resolver.py tests/test_calls_resolver.py tests/test_pipeline.py
git commit -m "fix: discover namespace packages (no __init__.py) as valid top-level code

build_symbol_inventory required __init__.py to even attempt loading a
top-level directory via griffe. A PEP 420 namespace package has none —
so it was never attempted, never entered top_level_packages, and every
call into it fell through to resolved_external 'with positive
evidence.' Confirmed root cause of a real repo (illumiows) compiling
with 0 internal calls resolved across 2456 call sites.

Fix: one unified discovery pass, broadened to any top-level directory
containing .py files, that also picks up loose root-level modules in
the same pass (a repo can have both at once). A name counts as
internal the moment it's attempted, not only on load success — a
package with one broken file inside is still our code. Failed
attempts are now recorded (load_failures) instead of silently
swallowed; Task 2 turns them into gaps."
```

---

### Task 2: Surface load failures as `tool_limitation` gaps

**Files:**
- Modify: `src/cc/pipeline.py:16-56`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `SymbolInventory.load_failures: list[tuple[str, str, str]]` from Task 1.
- Produces: nothing new consumed by later tasks.

- [ ] **Step 1: Write the failing test**

In `tests/test_pipeline.py`, add:

```python
def test_package_load_failure_surfaces_as_tool_limitation_gap():
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "broken").mkdir(parents=True)
        # SyntaxError in __init__.py itself, not a nested submodule — see
        # the note on test_top_level_packages_recorded_even_if_load_fails
        # (Task 1) for why that distinction is what actually makes
        # griffe.load raise instead of silently tolerating it.
        (repo / "broken" / "__init__.py").write_text("def f(:\n", encoding="utf-8")
        out = pathlib.Path(d) / "out"

        run(repo, out)

        data = json.loads((out / "graph.json").read_text())
        load_gaps = [
            g
            for g in data["gaps"]
            if g["kind"] == "tool_limitation" and "griffe" in g["missing"]
        ]
        assert len(load_gaps) == 1
        assert "broken" in load_gaps[0]["missing"]
        assert load_gaps[0]["severity"] == {"comprehension": "warning", "compliance": "error"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline.py::test_package_load_failure_surfaces_as_tool_limitation_gap -v`
Expected: FAIL — `load_gaps` is empty (the failure is currently recorded in `inventory.load_failures` but nothing reads it).

- [ ] **Step 3: Implement**

In `src/cc/pipeline.py`, after the existing `for filepath, lineno, fn_qname in sql_dynamic_gaps:` block (currently lines 71-83), insert a new block:

```python
    for pkg_name, location, error in inventory.load_failures:
        graph.gaps.append(
            Gap(
                kind="tool_limitation",
                where=f"{location}:0",
                node_id=None,
                missing=f"Package `{pkg_name}` could not be loaded by griffe — {error}",
                suggested="Fix the error so griffe can introspect this package; until "
                "then, calls into it resolve as unresolved_dynamic instead of a real edge.",
                severity={"comprehension": "warning", "compliance": "error"},
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS, all of them.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/cc/pipeline.py tests/test_pipeline.py
git commit -m "feat: surface griffe package-load failures as tool_limitation gaps

Previously swallowed by a bare except Exception: pass inside
build_symbol_inventory. Now recorded with their concrete error and
reported the same way call_excluded and sql_dynamic_gaps already are
— never silent, matching the project's own gap philosophy."
```

---

### Task 3: `--toppackages` CLI override

**Files:**
- Modify: `src/cc/extract/_calls_resolver.py` (the `build_symbol_inventory` signature and body, from Task 1)
- Modify: `src/cc/pipeline.py:16-25`
- Modify: `src/cc/cli.py:41-53,77-81`
- Test: `tests/test_calls_resolver.py`

**Interfaces:**
- Consumes: `build_symbol_inventory` from Task 1.
- Produces: `build_symbol_inventory(..., override_top_levels: frozenset[str] | None = None)`. `run(..., top_packages_override: frozenset[str] | None = None)`. `cc compile --toppackages a,b`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_calls_resolver.py`, add `_classify_qualname` to the existing import block (currently lines 81-89):

```python
from cc.extract._calls_resolver import (
    FuncInfo,
    Resolution,
    SymbolInventory,
    _classify_qualname,
    build_import_table,
    classify_call,
    flatten_attribute,
    resolve_method_in_hierarchy,
)
```

Then add:

```python
def test_override_top_levels_replaces_detected_set(tmp_path):
    repo = _make_repo(tmp_path)
    inv = build_symbol_inventory(repo, override_top_levels=frozenset({"services"}))
    assert inv.top_level_packages == {"services"}


def test_override_top_levels_warns_on_divergence(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    build_symbol_inventory(repo, override_top_levels=frozenset({"totally_different"}))
    captured = capsys.readouterr()
    assert "diverges from auto-detection" in captured.out
    assert "totally_different" in captured.out
    assert "services" in captured.out


def test_override_top_levels_no_warning_when_matching(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    build_symbol_inventory(repo, override_top_levels=frozenset({"services"}))
    captured = capsys.readouterr()
    assert "diverges" not in captured.out


def test_override_top_levels_affects_classification(tmp_path):
    repo = _make_repo(tmp_path)
    inv = build_symbol_inventory(repo, override_top_levels=frozenset({"mystery"}))
    resolution = _classify_qualname("mystery.something", inv)
    assert resolution.kind == "dynamic"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calls_resolver.py::test_override_top_levels_replaces_detected_set tests/test_calls_resolver.py::test_override_top_levels_warns_on_divergence tests/test_calls_resolver.py::test_override_top_levels_no_warning_when_matching tests/test_calls_resolver.py::test_override_top_levels_affects_classification -v`
Expected: FAIL with `TypeError: build_symbol_inventory() got an unexpected keyword argument 'override_top_levels'`.

- [ ] **Step 3: Implement — `_calls_resolver.py`**

Change the `build_symbol_inventory` signature (from Task 1) to:

```python
def build_symbol_inventory(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
    override_top_levels: frozenset[str] | None = None,
) -> SymbolInventory:
```

Append a paragraph to the docstring (after the existing final paragraph):

```python
    `override_top_levels`, when given, is an escape hatch for repos where
    auto-detection still gets it wrong — it replaces the detected
    `top_level_packages` outright. If it disagrees with what was detected,
    a warning is printed showing both sets, so the mismatch is never
    silent even though the override wins.
    """
```

And, right before the final `return inv` (after the `found_any`/root-package block from Task 1), insert:

```python
    if override_top_levels is not None:
        detected = frozenset(inv.top_level_packages)
        if detected != frozenset(override_top_levels):
            print(
                "  ⚠ --toppackages diverges from auto-detection: "
                f"override={sorted(override_top_levels)}, detected={sorted(detected)}"
            )
        inv.top_level_packages = set(override_top_levels)

    return inv
```

- [ ] **Step 4: Implement — `pipeline.py`**

Change the `run` signature (currently lines 16-21) to:

```python
def run(
    repo_path: str | pathlib.Path,
    out_dir: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
    top_packages_override: frozenset[str] | None = None,
) -> None:
```

Change the `build_symbol_inventory` call (currently line 25) to:

```python
    inventory = build_symbol_inventory(
        repo_path, exclude_patterns, use_gitignore, override_top_levels=top_packages_override
    )
```

- [ ] **Step 5: Implement — `cli.py`**

After the existing `--no-gitignore` argument (currently lines 48-53), add:

```python
    comp.add_argument(
        "--toppackages",
        metavar="PKG,PKG,...",
        help="Comma-separated override for the repo's own top-level package "
        "names, used to classify calls as internal vs. external. Use for "
        "repos where auto-detection still gets it wrong. A warning is "
        "printed if this diverges from what auto-detection found.",
    )
```

In the `if args.cmd == "compile":` block (currently lines 77-81), change:

```python
    if args.cmd == "compile":
        exclude_patterns = tuple(args.exclude or ())
        use_gitignore = not args.no_gitignore
        print(f"Compiling {args.repo} → {args.out} …")
        run(args.repo, args.out, exclude_patterns=exclude_patterns, use_gitignore=use_gitignore)
```

to:

```python
    if args.cmd == "compile":
        exclude_patterns = tuple(args.exclude or ())
        use_gitignore = not args.no_gitignore
        top_packages_override = (
            frozenset(p.strip() for p in args.toppackages.split(",") if p.strip())
            if args.toppackages
            else None
        )
        print(f"Compiling {args.repo} → {args.out} …")
        run(
            args.repo,
            args.out,
            exclude_patterns=exclude_patterns,
            use_gitignore=use_gitignore,
            top_packages_override=top_packages_override,
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_calls_resolver.py -v`
Expected: PASS, all of them.

- [ ] **Step 7: Run the full suite**

Run: `pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add src/cc/extract/_calls_resolver.py src/cc/pipeline.py src/cc/cli.py tests/test_calls_resolver.py
git commit -m "feat: add --toppackages override for repos auto-detection gets wrong

Escape valve, not the fix — default behavior (no flag) is still full
auto-derivation from Task 1. A user pointing the tool at an unfamiliar
repo (the Corporate case) won't know its top-level packages and shouldn't
need to; --toppackages is for the rare case where even the broadened
discovery guesses wrong. Divergence from auto-detection always prints
both sets rather than silently overriding."
```

---

### Task 4: First-line sanity check on 0 resolved_internal

**Files:**
- Modify: `src/cc/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `inventory.functions`, `inventory.top_level_packages`, `call_coverage["total"]["resolved_internal"]` — all already computed in `run()`.
- Produces: nothing consumed by later tasks — this is the last report-shaping change.

- [ ] **Step 1: Write the failing test**

In `tests/test_pipeline.py`, add:

```python
def test_zero_internal_calls_prints_sanity_warning(capsys):
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "lonely").mkdir(parents=True)
        (repo / "lonely" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "lonely" / "mod.py").write_text(
            "import os\n\n\ndef f():\n    return os.getcwd()\n", encoding="utf-8"
        )
        out = pathlib.Path(d) / "out"

        run(repo, out)

        captured = capsys.readouterr()
        assert "0 llamadas internas resueltas" in captured.out
        assert "lonely" in captured.out


def test_nonzero_internal_calls_does_not_print_sanity_warning(capsys):
    # NOTE: deliberately not using the SIMPLE_API fixture here — its
    # handlers never actually call db.py's functions, so it genuinely has
    # 0 resolved_internal today (verified independently of this plan's
    # fix) and would make this test assert the wrong thing. This fixture
    # has a real cross-file call (main.compute -> helpers.double).
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "app").mkdir(parents=True)
        (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "app" / "helpers.py").write_text(
            "def double(x):\n    return x * 2\n", encoding="utf-8"
        )
        (repo / "app" / "main.py").write_text(
            "from app.helpers import double\n\n\n"
            "def compute(x):\n    return double(x)\n",
            encoding="utf-8",
        )
        out = pathlib.Path(d) / "out"

        run(repo, out)

        captured = capsys.readouterr()
        assert "llamadas internas resueltas" not in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline.py::test_zero_internal_calls_prints_sanity_warning tests/test_pipeline.py::test_nonzero_internal_calls_does_not_print_sanity_warning -v`
Expected: first FAILS (no such text printed yet); second PASSES already (nothing to assert against yet, vacuously true) — confirms the first test is the real one driving this task.

- [ ] **Step 3: Implement**

In `src/cc/pipeline.py`, move `total = call_coverage["total"]` up so the check can run first, and add the warning. Replace the tail of `run()` (currently everything from `if call_excluded:` through the final `print(...)` block, lines 85-107) with:

```python
    total = call_coverage["total"]

    if total["resolved_internal"] == 0 and inventory.functions:
        print(
            f"⚠ 0 llamadas internas resueltas con {len(inventory.functions)} "
            "funciones inventariadas.\n"
            f"  Internos derivados del inventario: "
            f"{{{', '.join(sorted(inventory.top_level_packages))}}}\n"
            "  Posible mismatch de descubrimiento/topología — revisar antes "
            "de fiarse del grafo."
        )

    if call_excluded:
        total_files = len(collect_py_files(repo_path, exclude_patterns, use_gitignore))
        excluded_count = len(call_excluded)
        print(
            f"  call graph: {total_files - excluded_count}/{total_files} files analyzed"
            f" ({excluded_count} excluded — see gaps in output)"
        )
        for filepath, error in call_excluded:
            rel = pathlib.Path(filepath).relative_to(repo_path)
            print(f"    excluded: {rel} — {error}")

    print(
        "  top-level packages detected: "
        f"{', '.join(sorted(inventory.top_level_packages)) or '(none)'}"
    )

    print(
        f"  call graph coverage: {total['resolved_internal']} internal, "
        f"{total['resolved_external']} external, "
        f"{total['unresolved_dynamic']} unresolved_dynamic "
        f"(of {total['call_sites']} call sites across {total['functions']} functions)"
    )

    emit(graph, out_dir)
```

(The `for pkg_name, location, error in inventory.load_failures:` gap-emission block from Task 2 and everything above `if call_excluded:` is unchanged — only the tail from `if call_excluded:` onward is being replaced here, to move `total`'s computation earlier and insert the new warning before it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS, all of them.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/cc/pipeline.py tests/test_pipeline.py
git commit -m "feat: warn on the report's first line when 0 internal calls resolve

An hour of manual diagnosis (this plan's own origin story) becomes ten
seconds of reading the compile output. Fires only when the inventory
actually found functions — never on a genuinely empty/non-Python repo."
```

---

### Task 5: Suppress target SyntaxWarning noise; share one AST parse per file

**Files:**
- Modify: `src/cc/extract/_node_hydration.py`
- Modify: `src/cc/extract/endpoints.py:87-92`
- Modify: `src/cc/extract/calls.py:81-87`
- Modify: `src/cc/extract/sql.py:147-152`
- Test: `tests/test_node_hydration.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `parse_module_cached(file: pathlib.Path, ast_cache: dict[str, ast.Module | None]) -> ast.Module` in `_node_hydration.py`, raising `SyntaxError` (never swallowing it) so each existing call site keeps its own error handling unchanged. Used by `endpoints.py`, `calls.py`, `sql.py`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_node_hydration.py`, add (adjust the import line at the top of the file to include `parse_module_cached` alongside whatever is already imported from `cc.extract._node_hydration`):

```python
import warnings

from cc.extract._node_hydration import parse_module_cached


def test_parse_module_cached_reuses_prior_successful_parse(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def a():\n    pass\n", encoding="utf-8")
    ast_cache: dict = {}
    first = parse_module_cached(f, ast_cache)
    second = parse_module_cached(f, ast_cache)
    assert first is second  # identical object -> genuinely reused, not re-parsed


def test_parse_module_cached_raises_syntax_error_uncached(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("def f(:\n", encoding="utf-8")
    ast_cache: dict = {}
    import pytest

    with pytest.raises(SyntaxError):
        parse_module_cached(f, ast_cache)
    assert f not in ast_cache and str(f) not in ast_cache


def test_parse_module_cached_suppresses_parse_warnings(tmp_path):
    # An unescaped regex string (real illumiows case) triggers a warning at
    # parse time — SyntaxWarning on Python 3.12+, DeprecationWarning on
    # 3.11 (verified empirically: same underlying issue, different category
    # depending on interpreter version). Suppress broadly rather than pin
    # to one category, so this doesn't silently stop working on whichever
    # Python actually runs the tool.
    f = tmp_path / "regex.py"
    f.write_text(r'import re' + "\n" + r"re.compile('\d+')" + "\n", encoding="utf-8")
    ast_cache: dict = {}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        parse_module_cached(f, ast_cache)  # must not raise despite the invalid escape
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_node_hydration.py -v -k parse_module_cached`
Expected: FAIL with `ImportError: cannot import name 'parse_module_cached'`.

- [ ] **Step 3: Implement — `_node_hydration.py`**

Add `warnings` to the imports at the top of the file (currently `import ast` / `import pathlib`):

```python
import ast
import pathlib
import warnings
```

Add, right after the existing `_parse_cached` function:

```python
def parse_module_cached(
    file: pathlib.Path, ast_cache: dict[str, ast.Module | None]
) -> ast.Module:
    """Parse `file`, reusing a prior successful parse from `ast_cache` if present.

    Used by the three main per-file driving loops (endpoints.py, calls.py,
    sql.py), which previously each called `ast.parse` independently —
    3 passes per file. They share this cache (and `_parse_cached`'s own
    on-demand hydration lookups share it too, transparently, since both
    key on the same `str(file)`).

    Raises SyntaxError exactly like ast.parse — callers keep their own
    try/except, since some (calls.py) need the actual exception message
    for their own gap reporting. Only successful parses are cached; a file
    that fails to parse is simply reparsed (and re-raises) on each pass —
    rare in practice, and safer than caching a failure without its message.

    Suppresses warnings raised while parsing (e.g. an unescaped regex
    string — SyntaxWarning on Python 3.12+, DeprecationWarning on 3.11,
    same underlying issue) — the target repo's own code quality is not
    this tool's output to show off. Suppressed broadly, not pinned to one
    category, so this doesn't depend on which Python version runs the tool.
    """
    key = str(file)
    cached = ast_cache.get(key)
    if cached is not None:
        return cached
    source = file.read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tree = ast.parse(source, filename=key)
    ast_cache[key] = tree
    return tree
```

Update `_parse_cached` to also suppress parse-time warnings broadly (for consistency — it does its own separate `ast.parse` call for on-demand hydration lookups). Change:

```python
def _parse_cached(file: str, ast_cache: dict[str, ast.Module | None]) -> ast.Module | None:
    if file in ast_cache:
        return ast_cache[file]
    try:
        source = pathlib.Path(file).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=file)
    except (OSError, SyntaxError, UnicodeDecodeError):
        ast_cache[file] = None
        return None
    ast_cache[file] = tree
    return tree
```

to:

```python
def _parse_cached(file: str, ast_cache: dict[str, ast.Module | None]) -> ast.Module | None:
    if file in ast_cache:
        return ast_cache[file]
    try:
        source = pathlib.Path(file).read_text(encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source, filename=file)
    except (OSError, SyntaxError, UnicodeDecodeError):
        ast_cache[file] = None
        return None
    ast_cache[file] = tree
    return tree
```

- [ ] **Step 4: Implement — `endpoints.py`**

Add the import (alongside the existing `from cc.extract._node_hydration import hydrate_function_node, node_from_ast_def`):

```python
from cc.extract._node_hydration import hydrate_function_node, node_from_ast_def, parse_module_cached
```

Replace the per-file parse (currently lines 87-92):

```python
    for file in collect_py_files(repo_path, exclude_patterns, use_gitignore):
        source = file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(file))
        except SyntaxError:
            continue
```

with:

```python
    for file in collect_py_files(repo_path, exclude_patterns, use_gitignore):
        try:
            tree = parse_module_cached(file, ast_cache)
        except SyntaxError:
            continue
```

- [ ] **Step 5: Implement — `calls.py`**

Add the import (alongside the existing `from cc.extract._node_hydration import hydrate_function_node, node_from_ast_def`):

```python
from cc.extract._node_hydration import hydrate_function_node, node_from_ast_def, parse_module_cached
```

Replace the per-file parse (currently lines 81-87):

```python
    for file in files:
        source = file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(file))
        except SyntaxError as exc:
            excluded.append((str(file), str(exc)))
            continue
```

with:

```python
    for file in files:
        try:
            tree = parse_module_cached(file, ast_cache)
        except SyntaxError as exc:
            excluded.append((str(file), str(exc)))
            continue
```

- [ ] **Step 6: Implement — `sql.py`**

Add the import (alongside the existing `from cc.extract._node_hydration import hydrate_function_node, node_from_ast_def`):

```python
from cc.extract._node_hydration import hydrate_function_node, node_from_ast_def, parse_module_cached
```

Replace the per-file parse (currently lines 147-152):

```python
    for file in collect_py_files(repo_path, exclude_patterns, use_gitignore):
        source = file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(file))
        except SyntaxError:
            continue
```

with:

```python
    for file in collect_py_files(repo_path, exclude_patterns, use_gitignore):
        try:
            tree = parse_module_cached(file, ast_cache)
        except SyntaxError:
            continue
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_node_hydration.py tests/test_endpoints.py tests/test_calls.py tests/test_sql.py -v`
Expected: PASS, all of them.

- [ ] **Step 8: Run the full suite**

Run: `pytest -q`
Expected: PASS, no regressions. Pay particular attention to any test asserting exact `SyntaxError` message text from `calls.py`'s `excluded` list (e.g. via `tool_limitation` gaps) — `parse_module_cached` re-raises the real exception unchanged, so messages must be byte-identical to before.

- [ ] **Step 9: Commit**

```bash
git add src/cc/extract/_node_hydration.py src/cc/extract/endpoints.py src/cc/extract/calls.py src/cc/extract/sql.py tests/test_node_hydration.py
git commit -m "perf: parse each file's AST once per compile, not once per extractor

endpoints.py, calls.py, and sql.py each called ast.parse independently
on every file — 3 parses per file per compile. They now share the
same ast_cache pipeline.py already threads through all three (used
until now only by hydrate_function_node's on-demand lookups).
Also suppresses warnings raised while parsing target code (e.g. an
unescaped regex string) — that's the target repo's code quality, not
this tool's output to show off."
```

---

### Task 6: Full-suite verification and manual agora regression check

**Files:**
- None modified — verification only.

**Interfaces:**
- Consumes: everything from Tasks 1-5.

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: PASS, all tests green.

- [ ] **Step 2: Manually verify agora is a true no-op**

Run, from the repo root, using the `.venv`:

```bash
git stash
python -m cc compile /data/agora --out /tmp/agora-classifier-before 2>&1 | tail -5
git stash pop
python -m cc compile /data/agora --out /tmp/agora-classifier-after 2>&1 | tail -5
python3 -c "
import json
before = json.load(open('/tmp/agora-classifier-before/graph.json'))
after = json.load(open('/tmp/agora-classifier-after/graph.json'))
bn = {n['id']: (n['hash'], n['props']) for n in before['nodes']}
an = {n['id']: (n['hash'], n['props']) for n in after['nodes']}
be = {(e['from_'], e['to'], e['type']) for e in before['edges']}
ae = {(e['from_'], e['to'], e['type']) for e in after['edges']}
print('node id set diff:', set(bn) ^ set(an))
print('node hash/props diff:', {k for k in bn if k in an and bn[k] != an[k]})
print('edge set diff:', be ^ ae)
"
```

Expected: all three diffs print as empty (`set()`). If `git stash` has nothing to stash (all 5 tasks already committed), instead compare against the last commit before Task 1 via `git worktree` or `git show <pre-task-1-sha>:...` — the point is a clean before/after diff on the *unmodified* agora repo, not a new fixture.

- [ ] **Step 3: Confirm the sanity-check warning does NOT fire on agora or illumiows-shaped repos**

The `test_nonzero_internal_calls_does_not_print_sanity_warning` test (Task 4) already covers this for the in-repo fixture; visually confirm the same by checking the Step 2 `after` compile's console output contains no `⚠ 0 llamadas internas resueltas` line.

- [ ] **Step 4: Clean up temporary compile output**

```bash
rm -rf /tmp/agora-classifier-before /tmp/agora-classifier-after
```

- [ ] **Step 5: Note the illumiows validation gap for Josem**

No commit needed for this step — it's a message to relay, not a file change: illumiows itself isn't available in this dev environment, so acceptance criteria 1-2 (illumiows recompiles with `resolved_internal` in the hundreds, and the `views.deleteIPList → crud.delete_iplist_allregions` edge is navigable) are covered here only via the synthetic namespace-package fixture (Task 1). Confirming them against the real repo is a `cc compile /path/to/illumiows` run for Josem to do separately.

---

## Self-Review Notes

- **Spec coverage:**
  - §1 (derive internal top-levels, eliminate parallel discovery) → Task 1, implemented as the discussed hybrid (single unified discovery-and-attempt pass, broadened to namespace packages; "internal" = attempted, not load-succeeded) rather than the literal inventory-derived formula, to avoid regressing `test_top_level_packages_recorded_even_if_load_fails`. Load failures promoted from a silently-swallowed exception to a first-class `load_failures` field.
  - §2 (inventory-first classification order) → confirmed already correct in `_classify_qualname`/`classify_call` (no code change); documented in Global Constraints.
  - §3 (`--toppackages` override + divergence warning) → Task 3.
  - §4 (first-line sanity check) → Task 4.
  - §5 (SyntaxWarning suppression + shared AST cache) → Task 5.
  - Fixture requirement (namespace-package root + normal subpackages + loose root modules) → Task 1, both at the `build_symbol_inventory` unit level and the `run()` pipeline level.
  - Acceptance criterion 1-2 (illumiows itself) → not directly runnable in this environment; covered by the Task 1 fixture; flagged explicitly in Task 6 Step 5 for Josem to confirm against the real repo.
  - Acceptance criterion 3 (agora zero regression) → Task 6 Step 2.
  - Acceptance criterion 4 (fixture green) → Task 1.
  - Acceptance criterion 5 (sanity check fires/doesn't fire correctly) → Task 4's two tests, reconfirmed visually in Task 6 Step 3.
  - New requirement from mid-plan discussion (load failures → `tool_limitation` gap, extend the existing test) → Task 1 Step 1 (test extension) + Task 2 (gap emission).
- **Empirically verified before writing this plan (not assumed):** `griffe.load()` only raises when the SyntaxError is in the top-level entry file itself (`__init__.py` for a package, or the module file for a standalone module) — a SyntaxError in a *nested* submodule is silently tolerated (griffe just omits that one submodule from the loaded tree, no exception, no warning). The pre-existing test's original fixture (`broken/__init__.py` empty + `broken/oops.py` broken) never actually exercised the `except Exception` branch it was named for — its assertions happened to pass anyway since they only checked directory-based `top_level_packages` membership, set unconditionally before the try/except in the old code. Task 1's rewritten fixture moves the SyntaxError into `broken/__init__.py` itself so `load_failures` (and Task 2's gap) are genuinely exercised. Also verified: the "unescaped regex" parse-time warning is `SyntaxWarning` on Python 3.12+ but `DeprecationWarning` on 3.11 (this dev environment) for the same underlying cause — Task 5 suppresses broadly (all categories) rather than pinning to `SyntaxWarning`, so the fix doesn't silently stop working depending on which Python runs the tool.
- **Placeholder scan:** none found — every step has complete code, exact commands, and expected output.
- **Type consistency:** `SymbolInventory.load_failures: list[tuple[str, str, str]]` — `(pkg_name, location, error)`, all `str`, populated in Task 1's `_try_load`, consumed unchanged in Task 2's gap loop. `build_symbol_inventory`'s new `override_top_levels: frozenset[str] | None` (Task 3) matches `run`'s new `top_packages_override: frozenset[str] | None` (Task 3) matches `cli.py`'s constructed `frozenset` from `--toppackages` (Task 3) — same type end to end. `parse_module_cached(file: pathlib.Path, ast_cache: dict[str, ast.Module | None]) -> ast.Module` (Task 5) matches the `ast_cache` type already declared in `pipeline.py:26` and already threaded as a parameter through `extract_endpoints`/`extract_sql`/`extract_calls`.
