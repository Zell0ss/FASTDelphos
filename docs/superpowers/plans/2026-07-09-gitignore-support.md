# .gitignore Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `cc compile` respect the target repo's own `.gitignore` (root + nested) by default, so files the repo's own authors have declared out-of-scope (build artifacts, generated code, vendored dirs without a `.venv`-style name) don't pollute the graph — without requiring the `git` binary or a `.git` directory, and without adding a second, parallel exclusion mechanism alongside the existing `--exclude`.

**Architecture:** A new `gitignore_excluded_files()` in `src/cc/extract/_collect.py` loads every `.gitignore` under the repo (root + nested), rewrites each nested file's patterns so they're anchored to the repo root using git's own anchoring rules (a pattern with a `/` in the middle is directory-relative; a bare name matches at any depth beneath its own `.gitignore`), and compiles them into one `pathspec.PathSpec` using the `gitwildmatch` pattern syntax (the same one used by `black`/`mkdocs`). The resulting file set feeds into the *same* `excluded_files()` union that `--exclude` already populates — one single exclusion set flows into `collect_py_files()` and every griffe-backed inventory (`_calls_resolver.py`, `models.py`), exactly mirroring how `--exclude` already works. Default ON; `--no-gitignore` disables the whole code path, reproducing today's output byte-for-byte.

**Tech Stack:** `pathspec>=0.12` (new dependency, `gitwildmatch` pattern factory). No `git` binary, no `.git` directory requirement — reads `.gitignore` files as plain text.

## Global Constraints

- New dependency: `pathspec` — added to `pyproject.toml`'s `dependencies`. No shelling out to `git`; the tool must work on a copied/exported repo with no `.git` directory at all.
- Load the target repo's own root `.gitignore` **+ nested ones** in subdirectories. **Never** the user's global gitignore, **never** `.git/info/exclude` — those don't travel with the source, and loading them would make output depend on the machine running the tool (breaks determinism).
- Gitignore-derived exclusions integrate into the **same single exclusion set** `--exclude` already populates (`excluded_files()` in `_collect.py`) — feeding the same `collect_py_files()` collection point and the same post-load griffe filter in `_calls_resolver.py`/`models.py`. No second, parallel exclusion path.
- **Declared, never silent:** the exclusion/coverage report breaks down by origin — existing `--exclude` pattern entries unchanged in shape, plus one new `{"pattern": "(.gitignore)", "count": N}` entry when gitignore matches anything. The render already handles arbitrary entries in `graph.exclusions` generically — no template change needed.
- **Default ON**; `--no-gitignore` flag disables it. When disabled, output must be byte-for-byte identical to the tool's pre-this-plan behavior — verified by a dedicated test.
- `.git/` is always excluded from file collection, with or without a `.gitignore` (already true via the existing `_SKIP_PARTS` set — must stay true).
- A repo with no `.gitignore` at all must compile with no warnings and no shape change beyond "nothing new gets excluded."

---

### Task 1: `pathspec` dependency + core gitignore pattern loading and matching

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/cc/extract/_collect.py`
- Test: `tests/test_collect.py`

**Interfaces:**
- Produces: `gitignore_excluded_files(repo_path: pathlib.Path, use_gitignore: bool = True) -> set[pathlib.Path]` — every `.py` file under `repo_path` matched by the repo's own (root + nested) `.gitignore` rules. Empty set when `use_gitignore=False` or when the repo has no `.gitignore` files at all. This is the only new public entry point Task 2 consumes.

- [ ] **Step 1: Add the dependency and install it**

In `pyproject.toml`, add `"pathspec>=0.12",` to the `dependencies` list (after `"httpx>=0.27",`):

```toml
dependencies = [
    "griffe>=0.47",
    "sqlglot>=25.0",
    "python-dotenv>=1.0",
    "anthropic>=0.40",
    "httpx>=0.27",
    "pathspec>=0.12",
]
```

Run: `pip install -e ".[dev]"`
Expected: installs cleanly, `python -c "import pathspec; print(pathspec.PathSpec)"` prints without error.

- [ ] **Step 2: Write the failing tests**

In `tests/test_collect.py`, add (the `_write(root, rel, content="")` helper already at the top of this file is reused as-is):

```python
from cc.extract._collect import (
    collect_py_files,
    excluded_files,
    exclusion_report,
    gitignore_excluded_files,
)


def test_gitignore_excluded_files_root_level_pattern(tmp_path):
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "build/generated.py", "x = 1\n")
    _write(tmp_path, "app.py", "x = 1\n")
    excluded = gitignore_excluded_files(tmp_path)
    assert excluded == {tmp_path / "build" / "generated.py"}


def test_gitignore_excluded_files_nested_pattern_is_scoped_to_its_own_subtree(tmp_path):
    _write(tmp_path, "backend/.gitignore", "generated.py\n")
    _write(tmp_path, "backend/generated.py", "x = 1\n")
    _write(tmp_path, "frontend/generated.py", "x = 1\n")  # same basename, different subtree
    excluded = gitignore_excluded_files(tmp_path)
    assert excluded == {tmp_path / "backend" / "generated.py"}


def test_gitignore_excluded_files_matches_at_any_depth_within_its_own_directory(tmp_path):
    _write(tmp_path, "backend/.gitignore", "generated.py\n")
    _write(tmp_path, "backend/sub/deep/generated.py", "x = 1\n")
    excluded = gitignore_excluded_files(tmp_path)
    assert excluded == {tmp_path / "backend" / "sub" / "deep" / "generated.py"}


def test_gitignore_excluded_files_supports_negation(tmp_path):
    _write(tmp_path, ".gitignore", "generated/*.py\n!generated/keep.py\n")
    _write(tmp_path, "generated/drop.py", "x = 1\n")
    _write(tmp_path, "generated/keep.py", "x = 1\n")
    excluded = gitignore_excluded_files(tmp_path)
    assert excluded == {tmp_path / "generated" / "drop.py"}


def test_gitignore_excluded_files_disabled_returns_empty(tmp_path):
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "build/generated.py", "x = 1\n")
    assert gitignore_excluded_files(tmp_path, use_gitignore=False) == set()


def test_gitignore_excluded_files_no_gitignore_present_returns_empty(tmp_path):
    _write(tmp_path, "app.py", "x = 1\n")
    assert gitignore_excluded_files(tmp_path) == set()


def test_gitignore_excluded_files_never_reads_gitignore_inside_dot_git(tmp_path):
    _write(tmp_path, ".git/.gitignore", "*.py\n")  # decoy — must never be treated as a source
    _write(tmp_path, "app.py", "x = 1\n")
    assert gitignore_excluded_files(tmp_path) == set()


def test_gitignore_excluded_files_is_deterministic_across_calls(tmp_path):
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "backend/.gitignore", "generated.py\n")
    _write(tmp_path, "build/generated.py", "x = 1\n")
    _write(tmp_path, "backend/generated.py", "x = 1\n")
    first = gitignore_excluded_files(tmp_path)
    second = gitignore_excluded_files(tmp_path)
    assert first == second
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_collect.py -v -k gitignore`
Expected: FAIL with `ImportError: cannot import name 'gitignore_excluded_files'`.

- [ ] **Step 4: Implement gitignore pattern loading and matching**

In `src/cc/extract/_collect.py`, add `import pathspec` to the imports at the top, and add these functions after `_glob_py_files` (before `excluded_files`):

```python
def _anchor_gitignore_pattern(raw_line: str, prefix: str) -> str | None:
    """Rewrite one raw .gitignore line so it's anchored to the repo root
    instead of to the directory its .gitignore file lives in (`prefix`,
    posix-style, relative to repo root; "" for the root .gitignore itself).

    Mirrors git's own anchoring rules (gitignore(5)): a pattern containing a
    "/" anywhere but the end is directory-relative already; a bare name (no
    "/") matches at any depth beneath its own .gitignore's directory.
    Returns None for blank lines and comments.
    """
    line = raw_line.rstrip("\n")
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if not prefix:
        return line

    negate = line.startswith("!")
    body = line[1:] if negate else line

    if body.startswith("/"):
        anchored = f"{prefix}{body}"
    elif "/" in body.rstrip("/"):
        anchored = f"{prefix}/{body}"
    else:
        anchored = f"{prefix}/**/{body}"

    return f"!{anchored}" if negate else anchored


def _gitignore_files(repo_path: pathlib.Path) -> list[pathlib.Path]:
    """Root + nested .gitignore files under repo_path, in deterministic
    order, excluding any living inside a skipped directory (.git, .venv,
    ...) — those aren't part of the repo's own source tree."""
    return sorted(
        p
        for p in repo_path.rglob(".gitignore")
        if not _SKIP_PARTS.intersection(p.relative_to(repo_path).parts[:-1])
    )


def _load_gitignore_spec(repo_path: pathlib.Path) -> "pathspec.PathSpec | None":
    """Combine every .gitignore under repo_path (root + nested, each
    anchored to its own directory via _anchor_gitignore_pattern) into a
    single gitwildmatch PathSpec, or None if the repo has no .gitignore
    files at all."""
    patterns: list[str] = []
    for gi_file in _gitignore_files(repo_path):
        rel_dir = gi_file.parent.relative_to(repo_path)
        prefix = "" if str(rel_dir) == "." else rel_dir.as_posix()
        for raw_line in gi_file.read_text(encoding="utf-8").splitlines():
            pattern = _anchor_gitignore_pattern(raw_line, prefix)
            if pattern is not None:
                patterns.append(pattern)
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def gitignore_excluded_files(
    repo_path: pathlib.Path, use_gitignore: bool = True
) -> set[pathlib.Path]:
    """.py files under repo_path matched by the repo's own (root + nested)
    .gitignore rules — never the user's global gitignore or
    .git/info/exclude, so output stays identical across machines. Empty set
    when disabled or when the repo has no .gitignore at all."""
    if not use_gitignore:
        return set()
    spec = _load_gitignore_spec(repo_path)
    if spec is None:
        return set()
    return {
        f
        for f in repo_path.rglob("*.py")
        if not _SKIP_PARTS.intersection(f.parts)
        and spec.match_file(f.relative_to(repo_path).as_posix())
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_collect.py -v -k gitignore`
Expected: PASS, 8/8.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/cc/extract/_collect.py tests/test_collect.py
git commit -m "feat: load and match the target repo's own .gitignore (root + nested)

pathspec's gitwildmatch pattern factory does the actual matching; the
new code here only handles anchoring nested .gitignore patterns to the
repo root (git's own directory-relative-vs-any-depth rule) so a single
combined PathSpec behaves like git's real nested-gitignore precedence.
No git binary or .git directory required — reads .gitignore as text."
```

---

### Task 2: Wire into the single exclusion set + declared-by-origin coverage report

**Files:**
- Modify: `src/cc/extract/_collect.py`
- Test: `tests/test_collect.py`

**Interfaces:**
- Consumes: `gitignore_excluded_files()` from Task 1.
- Produces: `excluded_files(repo_path, exclude_patterns=(), use_gitignore=True) -> set[pathlib.Path]`, `exclusion_report(repo_path, exclude_patterns=(), use_gitignore=True) -> list[dict]`, `collect_py_files(repo_path, exclude_patterns=(), use_gitignore=True) -> list[pathlib.Path]` — all three gain a `use_gitignore: bool = True` parameter as their new third positional/keyword arg, defaulting to the same "gitignore is active" behavior everywhere it isn't explicitly turned off.

- [ ] **Step 1: Write the failing tests**

In `tests/test_collect.py`, add:

```python
def test_collect_py_files_excludes_gitignored_files_by_default(tmp_path):
    _write(tmp_path, "app.py", "x = 1\n")
    _write(tmp_path, ".gitignore", "generated.py\n")
    _write(tmp_path, "generated.py", "x = 1\n")
    files = collect_py_files(tmp_path)
    assert files == [tmp_path / "app.py"]


def test_collect_py_files_no_gitignore_flag_disables_gitignore_filtering(tmp_path):
    _write(tmp_path, "app.py", "x = 1\n")
    _write(tmp_path, ".gitignore", "generated.py\n")
    _write(tmp_path, "generated.py", "x = 1\n")
    files = collect_py_files(tmp_path, use_gitignore=False)
    assert files == [tmp_path / "app.py", tmp_path / "generated.py"]


def test_exclusion_report_adds_gitignore_origin_entry(tmp_path):
    _write(tmp_path, ".gitignore", "generated.py\n")
    _write(tmp_path, "generated.py", "x = 1\n")
    _write(tmp_path, "backend/tests/a.py", "")
    report = exclusion_report(tmp_path, ("backend/tests/**",))
    assert {"pattern": "backend/tests/**", "count": 1} in report
    assert {"pattern": "(.gitignore)", "count": 1} in report


def test_exclusion_report_no_gitignore_entry_when_repo_has_no_gitignore(tmp_path):
    _write(tmp_path, "backend/tests/a.py", "")
    report = exclusion_report(tmp_path, ("backend/tests/**",))
    assert report == [{"pattern": "backend/tests/**", "count": 1}]


def test_excluded_files_unions_exclude_patterns_and_gitignore(tmp_path):
    _write(tmp_path, ".gitignore", "generated.py\n")
    _write(tmp_path, "generated.py", "x = 1\n")
    _write(tmp_path, "backend/tests/a.py", "")
    excluded = excluded_files(tmp_path, ("backend/tests/**",))
    assert excluded == {tmp_path / "generated.py", tmp_path / "backend" / "tests" / "a.py"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_collect.py -v -k "excludes_gitignored or no_gitignore_flag or gitignore_origin or no_gitignore_entry or unions_exclude"`
Expected: FAIL — `TypeError: collect_py_files() got an unexpected keyword argument 'use_gitignore'` (and equivalent for the others).

- [ ] **Step 3: Wire `use_gitignore` through the three existing functions**

In `src/cc/extract/_collect.py`, replace `excluded_files`, `exclusion_report`, and `collect_py_files`:

```python
def excluded_files(
    repo_path: pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> set[pathlib.Path]:
    """Expand each glob pattern (relative to repo_path) and return the union of
    .py files any pattern matches, plus every .py file matched by the repo's
    own .gitignore rules (unless use_gitignore=False) — one single exclusion
    set regardless of origin.

    Shared by collect_py_files (subtracts this set from the file list) and the
    griffe-backed extractors in models.py / _calls_resolver.py (prune the same
    files out of their symbol inventories), so every stage of the pipeline
    agrees on what "doesn't exist" means — no asymmetric resolution toward
    excluded code.
    """
    excluded: set[pathlib.Path] = set()
    for pattern in sorted(exclude_patterns):
        excluded.update(_glob_py_files(repo_path, pattern))
    excluded.update(gitignore_excluded_files(repo_path, use_gitignore))
    return excluded


def exclusion_report(
    repo_path: pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> list[dict]:
    """[{"pattern": str, "count": int}, ...] — how many .py files each
    --exclude pattern matched, sorted by pattern, plus one aggregate
    "(.gitignore)" entry (only present when it matched at least one file) —
    for the coverage report and the compiled graph's metadata. Declared by
    origin, never silent."""
    report = []
    for pattern in sorted(exclude_patterns):
        count = len(_glob_py_files(repo_path, pattern))
        report.append({"pattern": pattern, "count": count})
    gitignore_count = len(gitignore_excluded_files(repo_path, use_gitignore))
    if gitignore_count:
        report.append({"pattern": "(.gitignore)", "count": gitignore_count})
    return report


def collect_py_files(
    repo_path: pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> list[pathlib.Path]:
    """Return all .py files under repo_path, excluding non-source directories
    and anything matched by exclude_patterns (glob, relative to repo_path) or
    by the repo's own .gitignore (unless use_gitignore=False)."""
    excluded = excluded_files(repo_path, exclude_patterns, use_gitignore)
    return sorted(
        f
        for f in repo_path.rglob("*.py")
        if not _SKIP_PARTS.intersection(f.parts) and f not in excluded
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_collect.py -v`
Expected: PASS, all tests in the file green (existing `--exclude`-only tests are unaffected — none of their fixture repos create a `.gitignore` file, so `gitignore_excluded_files` contributes nothing to them).

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/_collect.py tests/test_collect.py
git commit -m "feat: fold .gitignore matches into the single exclusion set

excluded_files()/exclusion_report()/collect_py_files() all gain a
use_gitignore=True parameter. Gitignore-matched files join the same
union --exclude already populates — no second, parallel exclusion
path. exclusion_report() adds one declared '(.gitignore)' entry when
it contributes anything; existing --exclude entries keep their exact
prior shape."
```

---

### Task 3: Thread `use_gitignore` through every extractor, `pipeline.run`, and the CLI

**Files:**
- Modify: `src/cc/extract/_calls_resolver.py:76-125` (`build_symbol_inventory`)
- Modify: `src/cc/extract/models.py:14-47,103-109` (`_load_models`, `extract_models`)
- Modify: `src/cc/extract/endpoints.py:72-91` (`extract_endpoints`)
- Modify: `src/cc/extract/sql.py:128-146` (`extract_sql`)
- Modify: `src/cc/extract/calls.py:51-69` (`extract_calls`)
- Modify: `src/cc/pipeline.py` (`run`)
- Modify: `src/cc/cli.py` (new `--no-gitignore` flag on `compile`)
- Test: `tests/test_calls_resolver.py`, `tests/test_calls.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `excluded_files`/`collect_py_files` from Task 2 (already accept `use_gitignore`).
- Produces: every function below gains a `use_gitignore: bool = True` parameter, threaded to its own `collect_py_files`/`excluded_files`/`build_symbol_inventory` calls: `build_symbol_inventory(repo_path, exclude_patterns=(), use_gitignore=True)`, `extract_models(repo_path, handler_nodes, exclude_patterns=(), use_gitignore=True)`, `extract_endpoints(repo_path, exclude_patterns=(), inventory=None, ast_cache=None, use_gitignore=True)`, `extract_sql(repo_path, exclude_patterns=(), inventory=None, ast_cache=None, use_gitignore=True)`, `extract_calls(repo_path, exclude_patterns=(), inventory=None, ast_cache=None, use_gitignore=True)`, `run(repo_path, out_dir, exclude_patterns=(), use_gitignore=True)`.

- [ ] **Step 1: Write the failing regression tests mirroring the existing `--exclude` symmetry tests**

In `tests/test_calls_resolver.py`, add (near `test_build_symbol_inventory_excludes_matching_files`):

```python
def test_build_symbol_inventory_excludes_gitignored_files(tmp_path):
    _write(tmp_path, "backend/__init__.py", "")
    _write(tmp_path, "backend/app.py", "def keep():\n    return 1\n")
    _write(tmp_path, ".gitignore", "backend/generated.py\n")
    _write(tmp_path, "backend/generated.py", "def drop():\n    return 2\n")

    inv = build_symbol_inventory(tmp_path)
    assert "backend.app.keep" in inv.functions
    assert "backend.generated.drop" not in inv.functions
```

In `tests/test_calls.py`, add (near `test_call_into_excluded_file_falls_to_dynamic_not_phantom_node`):

```python
def test_call_into_gitignored_file_falls_to_dynamic_not_phantom_node(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "backend" / "app.py").write_text(
        "from backend.generated import helper\n\n\ndef use_it():\n    return helper()\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text("backend/generated.py\n", encoding="utf-8")
    (tmp_path / "backend" / "generated.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8"
    )

    nodes, edges, _, coverage = extract_calls(tmp_path)

    node_ids = {n.id for n in nodes}
    assert "function:backend.generated.helper" not in node_ids
    froms = {e.from_ for e in edges}
    assert "function:backend.app.use_it" not in froms
    per_file = coverage["per_file"]["backend/app.py"]
    assert per_file["unresolved_dynamic"] == 1
    assert per_file["resolved_internal"] == 0
```

In `tests/test_pipeline.py`, add:

```python
def test_run_excludes_gitignored_file_by_default(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("backend/generated.py\n", encoding="utf-8")
    (repo / "backend" / "generated.py").write_text("def drop():\n    return 2\n", encoding="utf-8")
    out = tmp_path / "out"
    run(repo, out)
    data = json.loads((out / "graph.json").read_text())
    fn_ids = {n["id"] for n in data["nodes"] if n["type"] == "function"}
    assert "function:backend.app.keep" in fn_ids
    assert "function:backend.generated.drop" not in fn_ids


def test_run_no_gitignore_flag_reproduces_prior_behavior_byte_for_byte(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "backend" / "generated.py").write_text("def drop():\n    return 2\n", encoding="utf-8")

    out_before = tmp_path / "out_before"
    run(repo, out_before)  # no .gitignore exists yet — today's behavior

    (repo / ".gitignore").write_text("backend/generated.py\n", encoding="utf-8")
    out_after = tmp_path / "out_after"
    run(repo, out_after, use_gitignore=False)  # .gitignore now exists, but disabled

    before = (out_before / "graph.json").read_text()
    after = (out_after / "graph.json").read_text()
    assert before == after


def test_gitignore_excluded_run_keeps_surviving_node_ids_and_hashes_stable(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "backend" / "generated.py").write_text("def drop():\n    return 2\n", encoding="utf-8")

    out_a = tmp_path / "out_a"
    run(repo, out_a, use_gitignore=False)

    (repo / ".gitignore").write_text("backend/generated.py\n", encoding="utf-8")
    out_b = tmp_path / "out_b"
    run(repo, out_b)

    nodes_a = {n["id"]: n for n in json.loads((out_a / "graph.json").read_text())["nodes"]}
    nodes_b = {n["id"]: n for n in json.loads((out_b / "graph.json").read_text())["nodes"]}

    assert "function:backend.generated.drop" in nodes_a
    assert "function:backend.generated.drop" not in nodes_b

    common_ids = set(nodes_a) & set(nodes_b)
    assert "function:backend.app.keep" in common_ids
    for node_id in common_ids:
        assert nodes_a[node_id]["hash"] == nodes_b[node_id]["hash"]


def test_run_reports_gitignore_exclusion_in_graph_json(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("backend/generated.py\n", encoding="utf-8")
    (repo / "backend" / "generated.py").write_text("def drop():\n    return 2\n", encoding="utf-8")
    out = tmp_path / "out"
    run(repo, out)
    data = json.loads((out / "graph.json").read_text())
    assert {"pattern": "(.gitignore)", "count": 1} in data["exclusions"]


def test_run_is_deterministic_with_gitignore_active(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("backend/generated.py\n", encoding="utf-8")
    (repo / "backend" / "generated.py").write_text("def drop():\n    return 2\n", encoding="utf-8")

    out_1 = tmp_path / "out_1"
    out_2 = tmp_path / "out_2"
    run(repo, out_1)
    run(repo, out_2)
    assert (out_1 / "graph.json").read_text() == (out_2 / "graph.json").read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calls_resolver.py tests/test_calls.py tests/test_pipeline.py -v -k gitignore`
Expected: FAIL — either `TypeError: unexpected keyword argument 'use_gitignore'` (for `run`) or the gitignored file still shows up (since nothing threads `use_gitignore` into `collect_py_files`/`excluded_files` calls yet, so it defaults `True` inside those low-level functions but the higher-level functions never call `gitignore_excluded_files` at all — they still call `collect_py_files(repo_path, exclude_patterns)` positionally without ever exposing `use_gitignore`, so it's unreachable from `run()`).

- [ ] **Step 3: Thread `use_gitignore` through `_calls_resolver.py`**

In `src/cc/extract/_calls_resolver.py`, change the `build_symbol_inventory` signature and its one `excluded_files` call:

```python
def build_symbol_inventory(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> SymbolInventory:
    """Load the repo's own top-level packages via griffe and collect every
    function/method qualname, class base-class relationship, and the set of
    top-level package names that belong to the repo (used later to tell
    "external" imports from "internal but unresolved" ones).
    """
    repo_path = pathlib.Path(repo_path)
    excluded = excluded_files(repo_path, exclude_patterns, use_gitignore)
    inv = SymbolInventory()
```

(Only the `excluded = excluded_files(repo_path, exclude_patterns)` line changes — everything else in the function body is unchanged.)

- [ ] **Step 4: Thread `use_gitignore` through `models.py`**

In `src/cc/extract/models.py`, change `_load_models` and `extract_models`:

```python
def _load_models(
    repo_path: pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> dict[str, "griffe.Class"]:
    """Return short_name -> griffe.Class for all BaseModel subclasses under repo_path."""
    excluded = excluded_files(repo_path, exclude_patterns, use_gitignore)
    found: dict[str, griffe.Class] = {}
```

```python
def extract_models(
    repo_path: str | pathlib.Path,
    handler_nodes: list[Node],
    exclude_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> tuple[list[Node], list[Edge]]:
    repo_path = pathlib.Path(repo_path)
    griffe_models = _load_models(repo_path, exclude_patterns, use_gitignore)
```

(Only the signatures and the two calls shown change — the rest of both function bodies is unchanged.)

- [ ] **Step 5: Thread `use_gitignore` through `endpoints.py`, `sql.py`, `calls.py`**

In `src/cc/extract/endpoints.py`, change `extract_endpoints`'s signature and its `collect_py_files`/`build_symbol_inventory` calls:

```python
def extract_endpoints(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    inventory: SymbolInventory | None = None,
    ast_cache: dict[str, ast.Module | None] | None = None,
    use_gitignore: bool = True,
) -> tuple[list[Node], list[Edge]]:
    repo_path = pathlib.Path(repo_path)
    if inventory is None:
        inventory = build_symbol_inventory(repo_path, exclude_patterns, use_gitignore)
    if ast_cache is None:
        ast_cache = {}
    nodes: list[Node] = []
    edges: list[Edge] = []

    for file in collect_py_files(repo_path, exclude_patterns, use_gitignore):
```

In `src/cc/extract/sql.py`, change `extract_sql`'s signature and its two calls:

```python
def extract_sql(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    inventory: SymbolInventory | None = None,
    ast_cache: dict[str, ast.Module | None] | None = None,
    use_gitignore: bool = True,
) -> tuple[list[Node], list[Edge], list[tuple[str, int, str]]]:
    repo_path = pathlib.Path(repo_path)
    if inventory is None:
        inventory = build_symbol_inventory(repo_path, exclude_patterns, use_gitignore)
    if ast_cache is None:
        ast_cache = {}
    table_columns: dict[str, set[str]] = defaultdict(set)
    table_files: dict[str, tuple[str, int]] = {}
    raw_edges: list[
        tuple[str, str, str, str, str, int, ast.FunctionDef | ast.AsyncFunctionDef | None]
    ] = []
    dynamic_gaps: list[tuple[str, int, str]] = []

    for file in collect_py_files(repo_path, exclude_patterns, use_gitignore):
```

In `src/cc/extract/calls.py`, change `extract_calls`'s signature and its two calls:

```python
def extract_calls(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    inventory: "SymbolInventory | None" = None,
    ast_cache: dict[str, ast.Module | None] | None = None,
    use_gitignore: bool = True,
) -> tuple[list[Node], list[Edge], list[tuple[str, str]], dict]:
    """Return (function nodes, call edges, [(excluded_file, error_msg)], coverage).

    coverage = {"per_file": {rel_path: counts}, "total": counts} where
    counts = {"functions", "call_sites", "resolved_internal",
              "resolved_external", "unresolved_dynamic"}.
    """
    repo_path = pathlib.Path(repo_path)
    files = collect_py_files(repo_path, exclude_patterns, use_gitignore)
    if not files:
        return [], [], [], {"per_file": {}, "total": _zero_counts()}

    if inventory is None:
        inventory = build_symbol_inventory(repo_path, exclude_patterns, use_gitignore)
```

- [ ] **Step 6: Thread `use_gitignore` through `pipeline.py` and add the CLI flag**

In `src/cc/pipeline.py`, change `run`'s signature and every extractor call:

```python
def run(
    repo_path: str | pathlib.Path,
    out_dir: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> None:
    repo_path = pathlib.Path(repo_path)
    out_dir = pathlib.Path(out_dir)

    inventory = build_symbol_inventory(repo_path, exclude_patterns, use_gitignore)
    ast_cache: dict[str, ast.Module | None] = {}

    ep_nodes, ep_edges = extract_endpoints(
        repo_path, exclude_patterns, inventory=inventory, ast_cache=ast_cache,
        use_gitignore=use_gitignore,
    )
    handler_nodes = [n for n in ep_nodes if n.type == "function"]

    model_nodes, model_edges = extract_models(
        repo_path, handler_nodes, exclude_patterns, use_gitignore
    )
    sql_nodes, sql_edges, sql_dynamic_gaps = extract_sql(
        repo_path, exclude_patterns, inventory=inventory, ast_cache=ast_cache,
        use_gitignore=use_gitignore,
    )
    call_nodes, call_edges, call_excluded, call_coverage = extract_calls(
        repo_path, exclude_patterns, inventory=inventory, ast_cache=ast_cache,
        use_gitignore=use_gitignore,
    )
```

And the two remaining `collect_py_files`/`exclusion_report` calls further down in the same function:

```python
    graph.exclusions = exclusion_report(repo_path, exclude_patterns, use_gitignore)
```

```python
    if call_excluded:
        total_files = len(collect_py_files(repo_path, exclude_patterns, use_gitignore))
```

(Every other line in `run()` is unchanged — only these five call sites and the signature.)

In `src/cc/cli.py`, add a `--no-gitignore` flag to the `compile` subparser (after the existing `--exclude` argument):

```python
    comp.add_argument(
        "--no-gitignore",
        action="store_true",
        help="Do not respect the target repo's own .gitignore (root + nested). "
        "By default, gitignored files are excluded just like --exclude patterns.",
    )
```

And in `main()`'s `if args.cmd == "compile":` branch, thread it into `run()`:

```python
    if args.cmd == "compile":
        exclude_patterns = tuple(args.exclude or ())
        use_gitignore = not args.no_gitignore
        print(f"Compiling {args.repo} → {args.out} …")
        run(args.repo, args.out, exclude_patterns=exclude_patterns, use_gitignore=use_gitignore)
```

(The `--oracle` branch further down calls `extract_endpoints(args.repo, exclude_patterns)` positionally — leave it as-is; oracle mode is a narrow POC validator per `CLAUDE.md`, and this plan doesn't need to extend it. If a gitignore-affected repo is later run through `--oracle`, static and oracle counts would diverge by the gitignored routes — out of scope here, matching this plan's spec which only names `compile`.)

- [ ] **Step 7: Run all the tests written in this task to verify they pass**

Run: `pytest tests/test_calls_resolver.py tests/test_calls.py tests/test_pipeline.py -v -k gitignore`
Expected: PASS, all new tests green.

- [ ] **Step 8: Run the full test suite**

Run: `pytest -v`
Expected: PASS, all tests green (existing tests never create a `.gitignore` fixture file, so `gitignore_excluded_files` contributes nothing to any of them — no existing assertion changes).

- [ ] **Step 9: Manually verify against agora**

Run:
```bash
cc compile /data/agora --out /tmp/agora-gitignore-check
```
Expected:
- Compile succeeds.
- If agora has no `.gitignore`, `graph.json`'s `exclusions` list is unchanged from a pre-this-plan compile (no `(.gitignore)` entry appears) and endpoint count stays 18/18.
- If agora does have a `.gitignore`, inspect what it newly excludes and sanity-check it's reasonable (e.g. `__pycache__`-style patterns that `_SKIP_PARTS` already covered are harmless double-coverage; anything unexpected is worth a second look before treating this as done).
- Run `cc compile /data/agora --out /tmp/agora-gitignore-check-off --no-gitignore` and confirm its `graph.json` matches a compile from before this plan existed (if such an output is still around) or at minimum matches `/tmp/agora-endpoint-id-check` from the sibling identity-fix plan's Task 3 verification, since neither plan should interact — the only difference between the two should be the endpoint id format, not gitignore-driven exclusions.

- [ ] **Step 10: Commit**

```bash
git add src/cc/extract/_calls_resolver.py src/cc/extract/models.py \
        src/cc/extract/endpoints.py src/cc/extract/sql.py src/cc/extract/calls.py \
        src/cc/pipeline.py src/cc/cli.py \
        tests/test_calls_resolver.py tests/test_calls.py tests/test_pipeline.py
git commit -m "feat: respect target repo's .gitignore by default (--no-gitignore to disable)

use_gitignore=True threads through every extractor and the pipeline,
reusing the exact same excluded_files()/collect_py_files() union
--exclude already populates — griffe-backed inventories prune the same
files, so a call into a gitignored function falls to unresolved_dynamic
instead of a phantom node, mirroring --exclude's existing symmetry.
--no-gitignore reproduces prior output byte-for-byte."
```

---

## Self-Review Notes

- **Spec coverage:** Rule 1 (pathspec, no git binary) → Task 1. Rule 2 (root + nested, never global/`.git/info/exclude`) → Task 1's `_gitignore_files`/`_load_gitignore_spec`. Rule 3 (single exclusion set, no parallel path) → Task 2. Rule 4 (declared, never silent, per-origin breakdown) → Task 2's `exclusion_report`. Rule 5 (default ON, `--no-gitignore`) → Task 3. Rule 6 (`.git/` always excluded) → `_gitignore_files`'s `_SKIP_PARTS` filter (Task 1) plus the pre-existing `_SKIP_PARTS` filter in `collect_py_files`/`gitignore_excluded_files` itself. Acceptance criteria 1 (gitignored file → zero nodes/edges/gaps, griffe symmetry) → Task 3 Step 1's `test_build_symbol_inventory_excludes_gitignored_files` + `test_call_into_gitignored_file_falls_to_dynamic_not_phantom_node` + `test_run_excludes_gitignored_file_by_default`. Acceptance criteria 2 (per-origin breakdown) → Task 2's `test_exclusion_report_adds_gitignore_origin_entry` + Task 3's `test_run_reports_gitignore_exclusion_in_graph_json`. Acceptance criteria 3 (`--no-gitignore` byte-identical) → Task 3's `test_run_no_gitignore_flag_reproduces_prior_behavior_byte_for_byte`. Acceptance criteria 4 (determinism + survivor rule) → Task 1's `test_gitignore_excluded_files_is_deterministic_across_calls` + Task 3's `test_run_is_deterministic_with_gitignore_active` + `test_gitignore_excluded_run_keeps_surviving_node_ids_and_hashes_stable`. Acceptance criteria 5 (no `.gitignore` → no warnings) → Task 1's `test_gitignore_excluded_files_no_gitignore_present_returns_empty` + Task 2's `test_exclusion_report_no_gitignore_entry_when_repo_has_no_gitignore`.
- **Placeholder scan:** none found — every step has complete code.
- **Type consistency:** `use_gitignore: bool = True` is the exact same name and default across `gitignore_excluded_files`, `excluded_files`, `exclusion_report`, `collect_py_files`, `build_symbol_inventory`, `_load_models`, `extract_models`, `extract_endpoints`, `extract_sql`, `extract_calls`, and `run` — no renaming drift between tasks.
