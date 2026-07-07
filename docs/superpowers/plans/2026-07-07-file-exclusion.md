# Configurable File Exclusion (`--exclude`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repeatable `--exclude PATTERN` flag to `cc compile` that drops matching files (glob, relative to the repo root) from the entire graph — nodes, edges, gaps, coverage — before they're built, with the exclusion declared visibly in the report and the HTML. Default behavior (no `--exclude`) is byte-identical to today.

**Architecture:** Two independent exclusion layers. The existing hardcoded infra skip-list (`.venv`, `__pycache__`, etc.) is untouched. A new content layer, driven by `--exclude`, is computed once per pattern via `pathlib.Path.glob` and consumed at two points that must agree: (1) `collect_py_files`, already the single file-list source for `extract_endpoints`/`extract_sql`/`extract_calls`, and (2) the two griffe-backed extractors (`extract/models.py`, `extract/_calls_resolver.py`), which load whole packages from disk on their own and must have their *output* pruned by the same excluded-file set — otherwise a call from a non-excluded file into an excluded one resolves as a phantom internal edge to a node that was never created (a real bug found during design, not hypothetical: `extract_calls` hydrates callee nodes directly from griffe's symbol inventory, not from the filtered file walk, so an unpruned inventory means an "excluded" file's functions can still appear in the graph).

**Tech Stack:** `pathlib` glob (stdlib, already the project convention for package discovery). No new dependencies.

## Global Constraints

- **Two layers, not one.** Infra dirs (`.venv`, `__pycache__`, `.git`, `node_modules`, `.tox`, `dist`, `build`) stay fixed, non-configurable, and invisible in the exclusion report — they were never a per-repo decision. `--exclude` is a second, independent layer: default **empty**, every active pattern reported.
- **`exclude_patterns: tuple[str, ...] = ()`** is the parameter name and type used everywhere it's threaded (extractors, `pipeline.run`, resolver functions). Always defaults to `()` so every existing call site keeps working unchanged.
- **Patterns are glob, relative to the repo root**, expanded via `repo_path.glob(pattern)` — native `**` recursive support, no hand-rolled matching.
- **Only `.py` files count** toward exclusion sets and reported counts — a glob pattern may also match directories, those are not counted or tracked.
- **Griffe symmetry is mandatory, not optional.** `models.py` and `_calls_resolver.py` must prune their own walked trees by the same excluded-file set computed for `collect_py_files`, not just rely on `graph/build.py`'s dangling-edge report as a safety net.
- **No change to `esquema-grafo-poc.md` node/edge/gap schema.** The only schema addition is a new top-level `exclusions` field on `Graph`.
- **Determinism:** patterns are sorted before use; two runs with identical `exclude_patterns` produce identical output.

---

### Task 1: Foundational exclusion helpers in `_collect.py`

**Files:**
- Modify: `src/cc/extract/_collect.py`
- Test: `tests/test_collect.py` (new)

**Interfaces:**
- Produces:
  - `excluded_files(repo_path: pathlib.Path, exclude_patterns: tuple[str, ...] = ()) -> set[pathlib.Path]` — union of `.py` files matched by any pattern (absolute paths). Consumed directly by Task 2 (griffe pruning).
  - `exclusion_report(repo_path: pathlib.Path, exclude_patterns: tuple[str, ...] = ()) -> list[dict]` — `[{"pattern": str, "count": int}, ...]` sorted by pattern. Consumed by Task 4 (`pipeline.py`).
  - `collect_py_files(repo_path: pathlib.Path, exclude_patterns: tuple[str, ...] = ()) -> list[pathlib.Path]` — existing function, signature extended (default keeps it a no-op for every existing caller until Task 3 threads the new param through).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_collect.py`:

```python
import pathlib

from cc.extract._collect import collect_py_files, exclusion_report, excluded_files


def _write(root: pathlib.Path, rel: str, content: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_collect_py_files_no_patterns_matches_current_behavior(tmp_path):
    _write(tmp_path, "app.py", "x = 1\n")
    _write(tmp_path, ".venv/lib/pkg.py", "x = 1\n")
    files = collect_py_files(tmp_path)
    assert files == [tmp_path / "app.py"]


def test_collect_py_files_excludes_matching_pattern(tmp_path):
    _write(tmp_path, "backend/app.py", "x = 1\n")
    _write(tmp_path, "backend/tests/test_app.py", "x = 1\n")
    files = collect_py_files(tmp_path, exclude_patterns=("backend/tests/**",))
    assert files == [tmp_path / "backend" / "app.py"]


def test_collect_py_files_pattern_matches_nested_files(tmp_path):
    _write(tmp_path, "backend/tests/unit/test_deep.py", "x = 1\n")
    files = collect_py_files(tmp_path, exclude_patterns=("backend/tests/**",))
    assert files == []


def test_collect_py_files_unmatched_pattern_excludes_nothing(tmp_path):
    _write(tmp_path, "backend/app.py", "x = 1\n")
    files = collect_py_files(tmp_path, exclude_patterns=("scripts/**",))
    assert files == [tmp_path / "backend" / "app.py"]


def test_excluded_files_returns_only_py_files(tmp_path):
    _write(tmp_path, "backend/tests/test_app.py", "x = 1\n")
    _write(tmp_path, "backend/tests/data.json", "{}")
    excluded = excluded_files(tmp_path, ("backend/tests/**",))
    assert excluded == {tmp_path / "backend" / "tests" / "test_app.py"}


def test_excluded_files_empty_when_no_patterns(tmp_path):
    _write(tmp_path, "backend/tests/test_app.py", "x = 1\n")
    assert excluded_files(tmp_path, ()) == set()


def test_exclusion_report_counts_per_pattern(tmp_path):
    _write(tmp_path, "backend/tests/a.py", "")
    _write(tmp_path, "backend/tests/b.py", "")
    _write(tmp_path, "scripts/one.py", "")
    report = exclusion_report(tmp_path, ("backend/tests/**", "scripts/**"))
    assert report == [
        {"pattern": "backend/tests/**", "count": 2},
        {"pattern": "scripts/**", "count": 1},
    ]


def test_exclusion_report_empty_when_no_patterns(tmp_path):
    assert exclusion_report(tmp_path, ()) == []


def test_exclusion_report_zero_count_for_unmatched_pattern(tmp_path):
    report = exclusion_report(tmp_path, ("nothing/here/**",))
    assert report == [{"pattern": "nothing/here/**", "count": 0}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_collect.py -v`
Expected: FAIL — `excluded_files` and `exclusion_report` don't exist yet; `collect_py_files` doesn't accept `exclude_patterns`.

- [ ] **Step 3: Implement**

Replace the full contents of `src/cc/extract/_collect.py`:

```python
import pathlib

_SKIP_PARTS = {".venv", "__pycache__", ".git", "node_modules", ".tox", "dist", "build"}


def excluded_files(
    repo_path: pathlib.Path, exclude_patterns: tuple[str, ...] = ()
) -> set[pathlib.Path]:
    """Expand each glob pattern (relative to repo_path) and return the union of
    .py files any pattern matches (absolute paths).

    Shared by collect_py_files (subtracts this set from the file list) and the
    griffe-backed extractors in models.py / _calls_resolver.py (prune the same
    files out of their symbol inventories), so every stage of the pipeline
    agrees on what "doesn't exist" means — no asymmetric resolution toward
    excluded code.
    """
    excluded: set[pathlib.Path] = set()
    for pattern in sorted(exclude_patterns):
        excluded.update(p for p in repo_path.glob(pattern) if p.suffix == ".py")
    return excluded


def exclusion_report(
    repo_path: pathlib.Path, exclude_patterns: tuple[str, ...] = ()
) -> list[dict]:
    """[{"pattern": str, "count": int}, ...] sorted by pattern — how many .py
    files each individual --exclude pattern matched, for the coverage report
    and the compiled graph's metadata."""
    report = []
    for pattern in sorted(exclude_patterns):
        count = sum(1 for p in repo_path.glob(pattern) if p.suffix == ".py")
        report.append({"pattern": pattern, "count": count})
    return report


def collect_py_files(
    repo_path: pathlib.Path, exclude_patterns: tuple[str, ...] = ()
) -> list[pathlib.Path]:
    """Return all .py files under repo_path, excluding non-source directories
    and anything matched by exclude_patterns (glob, relative to repo_path)."""
    excluded = excluded_files(repo_path, exclude_patterns)
    return sorted(
        f
        for f in repo_path.rglob("*.py")
        if not _SKIP_PARTS.intersection(f.parts) and f not in excluded
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_collect.py -v`
Expected: PASS — all 9 tests.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS, same count as before plus 9 (existing `collect_py_files` callers are unaffected — `exclude_patterns` defaults to `()`).

- [ ] **Step 6: Commit**

```bash
git add src/cc/extract/_collect.py tests/test_collect.py
git commit -m "feat: add excluded_files/exclusion_report helpers, extend collect_py_files with exclude_patterns"
```

---

### Task 2: Griffe symmetry — prune excluded files from the model and symbol inventories

**Files:**
- Modify: `src/cc/extract/models.py`
- Modify: `src/cc/extract/_calls_resolver.py`
- Test: `tests/test_models_ext.py` (append)
- Test: `tests/test_calls_resolver.py` (append)

**Interfaces:**
- Consumes: `excluded_files(repo_path, exclude_patterns) -> set[pathlib.Path]` (Task 1).
- Produces:
  - `_load_models(repo_path: pathlib.Path, exclude_patterns: tuple[str, ...] = ()) -> dict[str, griffe.Class]` (private, `models.py`) — used by Task 3.
  - `build_symbol_inventory(repo_path: str | pathlib.Path, exclude_patterns: tuple[str, ...] = ()) -> SymbolInventory` (`_calls_resolver.py`) — used by Task 3.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_calls_resolver.py`:

```python
def test_build_symbol_inventory_excludes_matching_files(tmp_path):
    repo = tmp_path / "repo"
    _write(repo, "backend/__init__.py", "")
    _write(repo, "backend/app.py", "def keep():\n    return 1\n")
    _write(repo, "backend/tests/__init__.py", "")
    _write(repo, "backend/tests/helpers.py", "def drop():\n    return 2\n")

    inv = build_symbol_inventory(repo, exclude_patterns=("backend/tests/**",))
    assert "backend.app.keep" in inv.functions
    assert "backend.tests.helpers.drop" not in inv.functions


def test_build_symbol_inventory_no_patterns_unaffected(tmp_path):
    repo = tmp_path / "repo"
    _write(repo, "backend/__init__.py", "")
    _write(repo, "backend/tests/__init__.py", "")
    _write(repo, "backend/tests/helpers.py", "def drop():\n    return 2\n")

    inv = build_symbol_inventory(repo)
    assert "backend.tests.helpers.drop" in inv.functions
```

Update the import line at the top of `tests/test_calls_resolver.py` from:

```python
from cc.extract._calls_resolver import build_symbol_inventory
```

to (unchanged — `build_symbol_inventory` is the only symbol this file imports from that module, no new import needed).

Append to `tests/test_models_ext.py`:

```python
def test_extract_models_excludes_matching_files(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "schemas.py").write_text(
        "from pydantic import BaseModel\n\n\n"
        "class Kept(BaseModel):\n    x: int\n",
        encoding="utf-8",
    )
    (repo / "backend" / "tests").mkdir()
    (repo / "backend" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "tests" / "schemas.py").write_text(
        "from pydantic import BaseModel\n\n\n"
        "class Dropped(BaseModel):\n    y: int\n",
        encoding="utf-8",
    )
    nodes, _ = extract_models(repo, [], exclude_patterns=("backend/tests/**",))
    names = {n.props["name"] for n in nodes}
    assert "Kept" in names
    assert "Dropped" not in names
```

Update the import line at the top of `tests/test_models_ext.py` from:

```python
from cc.extract.endpoints import extract_endpoints
from cc.extract.models import extract_models
from tests.conftest import SIMPLE_API
```

(unchanged — `extract_models` is already imported; no new import needed. This test doesn't use `SIMPLE_API` or `extract_endpoints`, it builds its own `tmp_path` fixture.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calls_resolver.py tests/test_models_ext.py -v -k "exclud"`
Expected: FAIL — `build_symbol_inventory` and `extract_models` don't accept `exclude_patterns` yet; `backend.tests.helpers.drop` / `Dropped` are found because nothing prunes them.

- [ ] **Step 3: Implement in `_calls_resolver.py`**

Add the import (after the existing `import griffe` line):

```python
from cc.extract._collect import excluded_files
```

Change `_walk_griffe_functions`'s signature and add the exclusion check, right after the existing `griffe.Alias` check:

```python
def _walk_griffe_functions(
    obj, inv: SymbolInventory, class_stack: list[str], excluded: set[pathlib.Path]
) -> None:
    if isinstance(obj, griffe.Alias):
        return
    if getattr(obj, "filepath", None) in excluded:
        return

    if isinstance(obj, griffe.Function):
        qname = obj.canonical_path
        kind = "method" if class_stack else "function"
        inv.functions[qname] = FuncInfo(
            qualname=qname,
            file=str(obj.filepath) if obj.filepath else "unknown",
            lineno=obj.lineno or 1,
            endlineno=obj.endlineno or (obj.lineno or 1),
            kind=kind,
        )
        if class_stack:
            inv.class_methods.setdefault(class_stack[-1], {})[obj.name] = qname
        return  # functions carry no nested defs worth walking into

    if isinstance(obj, griffe.Class):
        qname = obj.canonical_path
        bases = []
        for b in obj.bases or []:
            try:
                bases.append(b.canonical_path if hasattr(b, "canonical_path") else str(b))
            except Exception:
                bases.append(str(b))
        inv.class_bases[qname] = bases
        class_stack = class_stack + [qname]

    if hasattr(obj, "members"):
        for child in obj.members.values():
            _walk_griffe_functions(child, inv, class_stack, excluded)
```

Change `build_symbol_inventory`'s signature and the two `_try_load` call sites that invoke `_walk_griffe_functions`:

```python
def build_symbol_inventory(
    repo_path: str | pathlib.Path, exclude_patterns: tuple[str, ...] = ()
) -> SymbolInventory:
    """Load the repo's own top-level packages via griffe and collect every
    function/method qualname, class base-class relationship, and the set of
    top-level package names that belong to the repo (used later to tell
    "external" imports from "internal but unresolved" ones).
    """
    repo_path = pathlib.Path(repo_path)
    excluded = excluded_files(repo_path, exclude_patterns)
    inv = SymbolInventory()

    def _try_load(pkg_name: str, search_paths: list[pathlib.Path]) -> None:
        sys.path.insert(0, str(search_paths[0]))
        try:
            pkg = griffe.load(pkg_name, search_paths=search_paths)
            _walk_griffe_functions(pkg, inv, [], excluded)
        except Exception:
            pass
        finally:
            try:
                sys.path.remove(str(search_paths[0]))
            except ValueError:
                pass

    loaded_any = False
    for init in repo_path.glob("*/__init__.py"):
        if init.parent.name in _SKIP_DIRS:
            continue
        inv.top_level_packages.add(init.parent.name)
        _try_load(init.parent.name, [repo_path])
        loaded_any = True

    if not loaded_any and (repo_path / "__init__.py").exists():
        inv.top_level_packages.add(repo_path.name)
        _try_load(repo_path.name, [repo_path.parent])
    else:
        for py_file in repo_path.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            inv.top_level_packages.add(py_file.stem)
            _try_load(py_file.stem, [repo_path])

    return inv
```

(Only the signature, the new `excluded = excluded_files(...)` line, and the `_walk_griffe_functions(pkg, inv, [], excluded)` call change — the rest of the function body, including `top_level_packages` handling, is unchanged: package *existence* stays independent of content exclusion, so an excluded call site still correctly falls to `dynamic` rather than `external`.)

- [ ] **Step 4: Implement in `models.py`**

Add the import (after the existing `import griffe` line):

```python
from cc.extract._collect import excluded_files
```

Change `_load_models` and `_walk_griffe`:

```python
def _load_models(
    repo_path: pathlib.Path, exclude_patterns: tuple[str, ...] = ()
) -> dict[str, "griffe.Class"]:
    """Return short_name -> griffe.Class for all BaseModel subclasses under repo_path."""
    excluded = excluded_files(repo_path, exclude_patterns)
    found: dict[str, griffe.Class] = {}

    def _try_load(pkg_name: str, search_paths: list[pathlib.Path]) -> None:
        sys.path.insert(0, str(search_paths[0]))
        try:
            pkg = griffe.load(pkg_name, search_paths=search_paths)
            _walk_griffe(pkg, found, excluded)
        except Exception:
            pass
        finally:
            try:
                sys.path.remove(str(search_paths[0]))
            except ValueError:
                pass

    # Top-level sub-packages inside the repo (e.g. agora/backend/).
    # griffe.load on each recurses into sub-packages automatically.
    loaded_any = False
    for init in repo_path.glob("*/__init__.py"):
        if init.parent.name in _SKIP_DIRS:
            continue
        _try_load(init.parent.name, [repo_path])
        loaded_any = True

    # Fallback: the repo itself is the package (e.g. tests/fixtures/simple_api/).
    if not loaded_any and (repo_path / "__init__.py").exists():
        _try_load(repo_path.name, [repo_path.parent])

    return found


def _walk_griffe(
    obj: "griffe.Object", found: dict[str, "griffe.Class"], excluded: set[pathlib.Path]
) -> None:
    """Recursively walk griffe object tree, skipping unresolvable aliases and
    anything whose source file was excluded via --exclude (griffe loads the
    whole package from disk regardless — this prunes what survives into
    `found`, so the model inventory agrees with what collect_py_files sees)."""
    if isinstance(obj, griffe.Alias):
        # Skip aliases to external packages (e.g. fastapi.APIRouter)
        return
    if getattr(obj, "filepath", None) in excluded:
        return
    if isinstance(obj, griffe.Class):
        bases = []
        for b in obj.bases or []:
            try:
                bases.append(b.canonical_path if hasattr(b, "canonical_path") else str(b))
            except Exception:
                bases.append(str(b))
        if any("BaseModel" in b for b in bases):
            found[obj.name] = obj
    if hasattr(obj, "members"):
        for child in obj.members.values():
            _walk_griffe(child, found, excluded)
```

Change `extract_models`'s signature to accept and forward the new parameter:

```python
def extract_models(
    repo_path: str | pathlib.Path,
    handler_nodes: list[Node],
    exclude_patterns: tuple[str, ...] = (),
) -> tuple[list[Node], list[Edge]]:
    repo_path = pathlib.Path(repo_path)
    griffe_models = _load_models(repo_path, exclude_patterns)
```

(the rest of `extract_models`'s body is unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_calls_resolver.py tests/test_models_ext.py -v`
Expected: PASS, all tests in both files.

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: PASS, same count as Task 1's end plus 3 (2 in `test_calls_resolver.py`, 1 in `test_models_ext.py`).

- [ ] **Step 7: Commit**

```bash
git add src/cc/extract/models.py src/cc/extract/_calls_resolver.py tests/test_models_ext.py tests/test_calls_resolver.py
git commit -m "fix: prune excluded files from griffe-backed model and symbol inventories"
```

---

### Task 3: Thread `exclude_patterns` through the extractor orchestrators

**Files:**
- Modify: `src/cc/extract/calls.py`
- Modify: `src/cc/extract/endpoints.py`
- Modify: `src/cc/extract/sql.py`
- Test: `tests/test_calls.py` (append)
- Test: `tests/test_endpoints.py` (append)
- Test: `tests/test_sql.py` (append)

**Interfaces:**
- Consumes: `collect_py_files(repo_path, exclude_patterns)` (Task 1), `build_symbol_inventory(repo_path, exclude_patterns)` (Task 2).
- Produces:
  - `extract_calls(repo_path, exclude_patterns: tuple[str, ...] = ()) -> tuple[list[Node], list[Edge], list[tuple[str, str]], dict]` — used by Task 4.
  - `extract_endpoints(repo_path, exclude_patterns: tuple[str, ...] = ()) -> tuple[list[Node], list[Edge]]` — used by Task 4 and Task 5 (`cli.py`'s oracle call).
  - `extract_sql(repo_path, exclude_patterns: tuple[str, ...] = ()) -> tuple[list[Node], list[Edge], list[tuple[str, int, str]]]` — used by Task 4.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_calls.py` — this is the critical end-to-end proof that excluding a file makes calls into it fall to `unresolved_dynamic` instead of producing a phantom internal edge/node (the bug found during design: `extract_calls` hydrates callee nodes straight from the griffe inventory, so without Task 2's pruning an "excluded" file's functions could still appear in the graph if something calls into them):

```python
def test_call_into_excluded_file_falls_to_dynamic_not_phantom_node(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "backend" / "app.py").write_text(
        "from backend.tests.helpers import helper\n\n\n"
        "def use_it():\n    return helper()\n",
        encoding="utf-8",
    )
    (tmp_path / "backend" / "tests").mkdir()
    (tmp_path / "backend" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "backend" / "tests" / "helpers.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8"
    )

    nodes, edges, _, coverage = extract_calls(tmp_path, exclude_patterns=("backend/tests/**",))

    node_ids = {n.id for n in nodes}
    assert "function:backend.tests.helpers.helper" not in node_ids
    froms = {e.from_ for e in edges}
    assert "function:backend.app.use_it" not in froms
    per_file = coverage["per_file"]["backend/app.py"]
    assert per_file["unresolved_dynamic"] == 1
    assert per_file["resolved_internal"] == 0


def test_extract_calls_excluded_file_produces_no_own_nodes(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "backend" / "app.py").write_text(
        "def keep():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "backend" / "tests").mkdir()
    (tmp_path / "backend" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "backend" / "tests" / "test_app.py").write_text(
        "def drop():\n    return 2\n", encoding="utf-8"
    )
    nodes, _, _, coverage = extract_calls(tmp_path, exclude_patterns=("backend/tests/**",))
    node_ids = {n.id for n in nodes}
    assert "function:backend.app.keep" in node_ids
    assert "function:backend.tests.test_app.drop" not in node_ids
    assert "backend/tests/test_app.py" not in coverage["per_file"]
```

Append to `tests/test_endpoints.py`:

```python
def test_extract_endpoints_respects_exclude_patterns(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "backend" / "routes.py").write_text(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n\n"
        "@router.get('/kept')\n"
        "def kept():\n    return {}\n",
        encoding="utf-8",
    )
    (tmp_path / "backend" / "tests").mkdir()
    (tmp_path / "backend" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "backend" / "tests" / "fixtures.py").write_text(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n\n"
        "@router.get('/dropped')\n"
        "def dropped():\n    return {}\n",
        encoding="utf-8",
    )
    nodes, _ = extract_endpoints(tmp_path, exclude_patterns=("backend/tests/**",))
    paths = {n.props["path"] for n in nodes if n.type == "endpoint"}
    assert paths == {"/kept"}
```

Append to `tests/test_sql.py`:

```python
def test_extract_sql_respects_exclude_patterns(tmp_path):
    _write(tmp_path, "backend/db.py", (
        "async def get_kept(cur):\n    await cur.execute('SELECT * FROM kept_table')\n"
    ))
    _write(tmp_path, "backend/tests/db.py", (
        "async def get_dropped(cur):\n    await cur.execute('SELECT * FROM dropped_table')\n"
    ))
    nodes, _, _ = extract_sql(tmp_path, exclude_patterns=("backend/tests/**",))
    table_names = {n.props["name"] for n in nodes if n.type == "table"}
    assert "kept_table" in table_names
    assert "dropped_table" not in table_names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calls.py tests/test_endpoints.py tests/test_sql.py -v -k "exclud or phantom"`
Expected: FAIL — none of the three `extract_*` functions accept `exclude_patterns` yet (`TypeError: unexpected keyword argument`).

- [ ] **Step 3: Implement**

In `src/cc/extract/calls.py`, change `extract_calls`'s signature and its two `collect_py_files`/`build_symbol_inventory` call sites:

```python
def extract_calls(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
) -> tuple[list[Node], list[Edge], list[tuple[str, str]], dict]:
    """Return (function nodes, call edges, [(excluded_file, error_msg)], coverage).

    coverage = {"per_file": {rel_path: counts}, "total": counts} where
    counts = {"functions", "call_sites", "resolved_internal",
              "resolved_external", "unresolved_dynamic"}.
    """
    repo_path = pathlib.Path(repo_path)
    files = collect_py_files(repo_path, exclude_patterns)
    if not files:
        return [], [], [], {"per_file": {}, "total": _zero_counts()}

    inventory = build_symbol_inventory(repo_path, exclude_patterns)
```

(the rest of the function body is unchanged.)

In `src/cc/extract/endpoints.py`, change `extract_endpoints`'s signature and its `collect_py_files` call site:

```python
def extract_endpoints(
    repo_path: str | pathlib.Path, exclude_patterns: tuple[str, ...] = ()
) -> tuple[list[Node], list[Edge]]:
    repo_path = pathlib.Path(repo_path)
    nodes: list[Node] = []
    edges: list[Edge] = []

    for file in collect_py_files(repo_path, exclude_patterns):
```

(the rest of the function body is unchanged.)

In `src/cc/extract/sql.py`, change `extract_sql`'s signature and its `collect_py_files` call site:

```python
def extract_sql(
    repo_path: str | pathlib.Path, exclude_patterns: tuple[str, ...] = ()
) -> tuple[list[Node], list[Edge], list[tuple[str, int, str]]]:
    repo_path = pathlib.Path(repo_path)
    table_columns: dict[str, set[str]] = defaultdict(set)
    table_files: dict[str, tuple[str, int]] = {}
    raw_edges: list[
        tuple[str, str, str, str, str, int]
    ] = []
    dynamic_gaps: list[tuple[str, int, str]] = []

    for file in collect_py_files(repo_path, exclude_patterns):
```

(the rest of the function body is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calls.py tests/test_endpoints.py tests/test_sql.py -v`
Expected: PASS, all tests in all three files.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS, same count as Task 2's end plus 4 (2 in `test_calls.py`, 1 in `test_endpoints.py`, 1 in `test_sql.py`).

- [ ] **Step 6: Commit**

```bash
git add src/cc/extract/calls.py src/cc/extract/endpoints.py src/cc/extract/sql.py tests/test_calls.py tests/test_endpoints.py tests/test_sql.py
git commit -m "feat: thread exclude_patterns through extract_calls/extract_endpoints/extract_sql"
```

---

### Task 4: `Graph.exclusions` field and `pipeline.py` wiring

**Files:**
- Modify: `src/cc/graph/schema.py`
- Modify: `src/cc/pipeline.py`
- Test: `tests/test_schema.py` (append)
- Test: `tests/test_pipeline.py` (append)

**Interfaces:**
- Consumes: `exclusion_report(repo_path, exclude_patterns)` (Task 1), `extract_endpoints/extract_models/extract_sql/extract_calls(repo_path, exclude_patterns)` (Tasks 2–3).
- Produces: `run(repo_path, out_dir, exclude_patterns: tuple[str, ...] = ()) -> None` — used by Task 5 (`cli.py`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schema.py`:

```python
def test_graph_exclusions_defaults_to_empty_list():
    graph = Graph(nodes=[], edges=[], gaps=[])
    assert graph.exclusions == []


def test_graph_exclusions_can_be_set():
    graph = Graph(nodes=[], edges=[], gaps=[], exclusions=[{"pattern": "tests/**", "count": 3}])
    assert graph.exclusions == [{"pattern": "tests/**", "count": 3}]
```

Append to `tests/test_pipeline.py`:

```python
def test_run_without_exclude_arg_matches_default_empty_tuple():
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        run(SIMPLE_API, pathlib.Path(d1))
        run(SIMPLE_API, pathlib.Path(d2), exclude_patterns=())
        a = (pathlib.Path(d1) / "graph.json").read_text()
        b = (pathlib.Path(d2) / "graph.json").read_text()
        assert a == b


def test_pipeline_graph_json_has_empty_exclusions_by_default():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        assert data["exclusions"] == []


def test_pipeline_reports_exclusions_when_patterns_given(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "backend" / "tests").mkdir()
    (repo / "backend" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "tests" / "test_app.py").write_text(
        "def drop():\n    return 2\n", encoding="utf-8"
    )
    out = tmp_path / "out"
    run(repo, out, exclude_patterns=("backend/tests/**",))
    data = json.loads((out / "graph.json").read_text())
    assert data["exclusions"] == [{"pattern": "backend/tests/**", "count": 1}]


def test_excluded_run_keeps_surviving_node_ids_and_hashes_stable(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "backend" / "tests").mkdir()
    (repo / "backend" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "tests" / "test_app.py").write_text(
        "def drop():\n    return 2\n", encoding="utf-8"
    )

    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"
    run(repo, out_a)
    run(repo, out_b, exclude_patterns=("backend/tests/**",))

    nodes_a = {n["id"]: n for n in json.loads((out_a / "graph.json").read_text())["nodes"]}
    nodes_b = {n["id"]: n for n in json.loads((out_b / "graph.json").read_text())["nodes"]}

    assert "function:backend.tests.test_app.drop" in nodes_a
    assert "function:backend.tests.test_app.drop" not in nodes_b

    common_ids = set(nodes_a) & set(nodes_b)
    assert "function:backend.app.keep" in common_ids
    for node_id in common_ids:
        assert nodes_a[node_id]["hash"] == nodes_b[node_id]["hash"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schema.py tests/test_pipeline.py -v -k "exclusion"`
Expected: FAIL — `Graph` has no `exclusions` field; `run()` doesn't accept `exclude_patterns`.

- [ ] **Step 3: Implement**

In `src/cc/graph/schema.py`, add the field to `Graph`:

```python
@dataclass
class Graph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    exclusions: list[dict] = field(default_factory=list)
```

In `src/cc/pipeline.py`, change the imports and `run`'s signature/body:

```python
import pathlib

from cc.extract._collect import collect_py_files, exclusion_report
from cc.extract.calls import extract_calls
from cc.extract.endpoints import extract_endpoints
from cc.extract.models import extract_models
from cc.extract.sql import extract_sql
from cc.gaps import detect_gaps
from cc.graph.build import build_graph
from cc.graph.schema import Gap
from cc.render.emit import emit


def run(
    repo_path: str | pathlib.Path,
    out_dir: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
) -> None:
    repo_path = pathlib.Path(repo_path)
    out_dir = pathlib.Path(out_dir)

    ep_nodes, ep_edges = extract_endpoints(repo_path, exclude_patterns)
    handler_nodes = [n for n in ep_nodes if n.type == "function"]

    model_nodes, model_edges = extract_models(repo_path, handler_nodes, exclude_patterns)
    sql_nodes, sql_edges, sql_dynamic_gaps = extract_sql(repo_path, exclude_patterns)
    call_nodes, call_edges, call_excluded, call_coverage = extract_calls(
        repo_path, exclude_patterns
    )

    # Order matters: build_graph keeps the FIRST node registered per id. Handler
    # nodes (ep_nodes) and DB-touching nodes (sql_nodes) carry more specific
    # props (is_handler=True, etc.) than the generic function stub the call
    # visitor emits for the same id, so they must come first.
    all_nodes = ep_nodes + model_nodes + sql_nodes + call_nodes
    all_edges = ep_edges + model_edges + sql_edges + call_edges

    graph = build_graph(all_nodes, all_edges)
    graph.gaps = detect_gaps(graph)
    graph.exclusions = exclusion_report(repo_path, exclude_patterns)

    for filepath, error in call_excluded:
        rel = pathlib.Path(filepath).relative_to(repo_path)
        graph.gaps.append(
            Gap(
                kind="tool_limitation",
                where=f"{filepath}:0",
                node_id=None,
                missing=f"Call graph unavailable for `{rel}` — SyntaxError: {error}",
                suggested="Fix the syntax error so `ast.parse` can process the file.",
                severity={"comprehension": "warning", "compliance": "error"},
            )
        )

    for filepath, lineno, fn_qname in sql_dynamic_gaps:
        graph.gaps.append(
            Gap(
                kind="unresolved_dynamic",
                where=f"{filepath}:{lineno}",
                node_id=f"function:{fn_qname}",
                missing=f"SQL built dynamically (f-string) in `{fn_qname}` — "
                "table/operation could not be statically determined",
                suggested="Consider keeping the table name as literal text even if "
                "the rest of the query is dynamic, so lineage stays traceable.",
                severity={"comprehension": "warning", "compliance": "error"},
            )
        )

    if call_excluded:
        total_files = len(collect_py_files(repo_path, exclude_patterns))
        excluded_count = len(call_excluded)
        print(
            f"  call graph: {total_files - excluded_count}/{total_files} files analyzed"
            f" ({excluded_count} excluded — see gaps in output)"
        )
        for filepath, error in call_excluded:
            rel = pathlib.Path(filepath).relative_to(repo_path)
            print(f"    excluded: {rel} — {error}")

    total = call_coverage["total"]
    print(
        f"  call graph coverage: {total['resolved_internal']} internal, "
        f"{total['resolved_external']} external, "
        f"{total['unresolved_dynamic']} unresolved_dynamic "
        f"(of {total['call_sites']} call sites across {total['functions']} functions)"
    )

    emit(graph, out_dir)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schema.py tests/test_pipeline.py -v`
Expected: PASS, all tests in both files.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS, same count as Task 3's end plus 6 (2 in `test_schema.py`, 4 in `test_pipeline.py`).

- [ ] **Step 6: Commit**

```bash
git add src/cc/graph/schema.py src/cc/pipeline.py tests/test_schema.py tests/test_pipeline.py
git commit -m "feat: add Graph.exclusions and wire exclude_patterns through pipeline.run"
```

---

### Task 5: `--exclude` CLI flag

**Files:**
- Modify: `src/cc/cli.py`

**Interfaces:**
- Consumes: `run(repo_path, out_dir, exclude_patterns)` (Task 4), `extract_endpoints(repo_path, exclude_patterns)` (Task 3).

No new automated test — `cli.py` is a thin argparse wrapper with no existing dedicated test file in this project (verified manually per Step 3 below, matching how `--serve`/`--oracle`/`--port` were added previously without a `test_cli.py`).

- [ ] **Step 1: Implement**

In `src/cc/cli.py`, add the new argument after the existing `--port` argument:

```python
    comp.add_argument(
        "--exclude",
        action="append",
        metavar="PATTERN",
        help="Glob pattern (relative to the repo root) to exclude from the graph, "
        "e.g. --exclude 'backend/tests/**'. Repeatable.",
    )
```

Change the body of the `if args.cmd == "compile":` block:

```python
    if args.cmd == "compile":
        exclude_patterns = tuple(args.exclude or ())
        print(f"Compiling {args.repo} → {args.out} …")
        run(args.repo, args.out, exclude_patterns=exclude_patterns)
        print(f"Done. Open {args.out}/index.html")
        if args.oracle:
            import sys

            from cc.extract.endpoints import extract_endpoints
            from cc.oracle import compare_oracle

            ep_nodes, _ = extract_endpoints(args.repo, exclude_patterns)
            sys.path.insert(0, str(args.repo.parent))
            try:
                result = compare_oracle(args.repo, ep_nodes)
            finally:
                try:
                    sys.path.remove(str(args.repo.parent))
                except ValueError:
                    pass
            print(
                f"Route recovery: {result['static_count']}/{result['oracle_count']} "
                f"({result['recovery_rate']:.0%})"
            )
            if result.get("missing"):
                print("Missing from static:", result["missing"])
        if args.serve:
            from cc.serve import serve_directory

            serve_directory(args.out, args.port)
```

- [ ] **Step 2: Run the full suite**

Run: `pytest -q`
Expected: PASS, same count as Task 4's end (no new tests in this task).

- [ ] **Step 3: Manual verification**

Run:
```bash
source .venv/bin/activate
mkdir -p /tmp/exclude-cli-check/backend/tests
touch /tmp/exclude-cli-check/backend/__init__.py
echo 'def keep():
    return 1' > /tmp/exclude-cli-check/backend/app.py
touch /tmp/exclude-cli-check/backend/tests/__init__.py
echo 'def drop():
    return 2' > /tmp/exclude-cli-check/backend/tests/test_app.py

python -m cc compile /tmp/exclude-cli-check --out /tmp/exclude-cli-check-out --exclude 'backend/tests/**'
python3 -c "
import json
data = json.load(open('/tmp/exclude-cli-check-out/graph.json'))
print('exclusions:', data['exclusions'])
print('has drop node:', any('drop' in n['id'] for n in data['nodes']))
print('has keep node:', any('keep' in n['id'] for n in data['nodes']))
"
rm -rf /tmp/exclude-cli-check /tmp/exclude-cli-check-out
```
Expected: `exclusions: [{'pattern': 'backend/tests/**', 'count': 1}]`, `has drop node: False`, `has keep node: True`.

- [ ] **Step 4: Commit**

```bash
git add src/cc/cli.py
git commit -m "feat: add --exclude flag to cc compile"
```

---

### Task 6: Render — show the exclusion summary in the UI

**Files:**
- Modify: `src/cc/render/template_src.html`
- Test: `tests/test_render.py` (append)

**Interfaces:**
- Consumes: `GRAPH.exclusions` (JSON array, `[{"pattern": str, "count": int}, ...]`, embedded by `emit.py` unchanged — it already serializes the whole `Graph` dataclass).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_render.py`. First add a second graph builder next to `_minimal_graph()`:

```python
def _graph_with_exclusions():
    graph = _minimal_graph()
    graph.exclusions = [{"pattern": "backend/tests/**", "count": 3}]
    return graph


def test_html_shows_no_exclusion_summary_when_none():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        html = (pathlib.Path(d) / "index.html").read_text()
        assert "exclusión" not in html.lower()


def test_html_shows_exclusion_summary_when_present():
    with tempfile.TemporaryDirectory() as d:
        emit(_graph_with_exclusions(), pathlib.Path(d))
        html = (pathlib.Path(d) / "index.html").read_text()
        assert 'id="exclusions-info"' in html
        assert "backend/tests/**" in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_render.py -v -k exclusion`
Expected: FAIL — `#exclusions-info` doesn't exist; `_minimal_graph()`'s emitted HTML never mentions "exclusión" either way at this point, so the "no summary" test passes vacuously but the "present" test fails (no such div, no pattern text embedded beyond raw JSON).

- [ ] **Step 3: Implement**

In `src/cc/render/template_src.html`, add a CSS rule inside the existing `<style>` block, right after the `.panel-raw { margin-top: 8px; }` rule:

```css
    #exclusions-info { font-size: 10px; color: #778; margin-bottom: 6px; }
```

Add the container div in the sidebar, right after the title div and before `#search-wrap`:

```html
    <div style="font-size:13px;font-weight:bold;color:#aad;margin-bottom:4px">Comprehension Compiler</div>
    <div id="exclusions-info"></div>

    <div id="search-wrap">
```

Add the population logic in the `<script>` block, right before the existing `// ── Gaps ──` comment block:

```js
    // ── Exclusions ────────────────────────────────────────────────────────────
    if (GRAPH.exclusions && GRAPH.exclusions.length) {
      const totalExcluded = GRAPH.exclusions.reduce((sum, x) => sum + x.count, 0);
      const info = document.getElementById('exclusions-info');
      info.textContent =
        `compilado con ${GRAPH.exclusions.length} exclusión(es) — ${totalExcluded} ficheros fuera`;
      info.title = GRAPH.exclusions.map(x => `${x.pattern}: ${x.count}`).join('\n');
    }

    // ── Gaps ──────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_render.py -v`
Expected: PASS, all tests in the file.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS, same count as Task 5's end plus 2.

- [ ] **Step 6: Commit**

```bash
git add src/cc/render/template_src.html tests/test_render.py
git commit -m "feat: show active --exclude patterns in the render UI sidebar"
```

---

### Task 7: README documentation

**Files:**
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add exclusion documentation**

In `README.md`, add a new subsection right after the "Using the UI" section and before "Gaps — what the tool won't guess", titled `## Excluding files`:

```markdown
## Excluding files

Two layers, always. A fixed set — `.venv`, `__pycache__`, `.git`, `node_modules`, `.tox`, `dist`, `build` — is never walked, on every run, not configurable: it's vendor/tooling, never a candidate for "this repo's own source" in the first place.

On top of that, `--exclude PATTERN` (repeatable, glob relative to the repo root) drops your own content — most commonly a test suite that would otherwise pollute the call-graph coverage numbers with test-only helpers and mocks:

```bash
cc compile /path/to/repo --out ./output/repo --exclude 'backend/tests/**'
```

Default is no content exclusions — explicit over implicit, nothing is silently dropped unless you ask for it. Excluded files disappear from the graph entirely: no nodes, no edges, no gaps, and they don't count toward coverage. Anything a *non-excluded* file calls into that lives in an excluded file resolves as `unresolved_dynamic` rather than a broken reference — the tool never points at code that isn't there.

Active patterns and their matched-file counts are visible in the sidebar (top, under the title) and in `graph.json`'s `exclusions` field, so an exclusion is always declared, never silent.
```

- [ ] **Step 2: Verify by reading the rendered section**

Run: `sed -n '/## Excluding files/,/## Gaps/p' README.md`
Expected: the new section prints cleanly between "Using the UI" and "Gaps".

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document file exclusion (--exclude and the fixed infra skip-list)"
```

---

## Self-Review Notes

1. **Spec coverage:**
   - Two-layer exclusion (infra fixed / content configurable) → Task 1's `_collect.py` keeps `_SKIP_PARTS` untouched, adds the new layer alongside it.
   - Single exclusion set consumed by the whole pipeline, no asymmetry → Task 1 (`excluded_files`) + Task 2 (griffe pruning) + Task 3 (orchestrator threading) together.
   - Griffe symmetry (approach A) → Task 2, with the concrete phantom-node bug documented and tested in Task 3 Step 1's `test_call_into_excluded_file_falls_to_dynamic_not_phantom_node`.
   - Exclusion declared, never silent (report + counts) → Task 1's `exclusion_report`, Task 4's `Graph.exclusions` wiring, Task 6's UI summary.
   - Default: no exclusions → every new parameter defaults to `()`, tested explicitly in Task 4's `test_run_without_exclude_arg_matches_default_empty_tuple`.
   - Excluded files disappear entirely (no nodes/edges/gaps, out of coverage denominator) → Task 3's `test_extract_calls_excluded_file_produces_no_own_nodes` and the endpoints/sql equivalents.
   - Determinism → patterns sorted in `excluded_files`/`exclusion_report` (Task 1); `test_run_without_exclude_arg_matches_default_empty_tuple` exercises repeat-run stability implicitly since `SIMPLE_API` is static.
   - id/hash of surviving nodes unchanged → Task 4's `test_excluded_run_keeps_surviving_node_ids_and_hashes_stable`.
   - Acceptance criterion 2 (report + HTML declare patterns/counts) → Task 4 (JSON) + Task 6 (HTML).
   - README documentation of both layers → Task 7.
2. **Placeholder scan:** none found — every step has complete code or an exact command with expected output.
3. **Type consistency:** `exclude_patterns: tuple[str, ...] = ()` used identically as the parameter name and type across `_collect.py`, `_calls_resolver.py`, `models.py`, `calls.py`, `endpoints.py`, `sql.py`, `pipeline.py`, and `cli.py` (where it's constructed once via `tuple(args.exclude or ())` and passed through unchanged). `excluded_files`/`exclusion_report` signatures match between their Task 1 definition and every consumer in Tasks 2 and 4.
