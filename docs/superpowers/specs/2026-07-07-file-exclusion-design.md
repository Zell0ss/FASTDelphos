# Configurable File Exclusion (`--exclude`) — Design Spec

> Status: approved by Josem 2026-07-07. Ready for implementation plan.

## Goal

Give `cc compile` a `--exclude PATTERN` flag (repeatable) so a repo's own non-target
content — most concretely `backend/tests/**` in agora — can be dropped from the graph
before it pollutes coverage numbers. This closes two open threads at once: the 15
import-local-a-función sites and the ~52 `unresolved_dynamic` sites that live in agora's
test suite, both of which currently sit in the `unresolved_dynamic` denominator even
though they're not application logic. This is expected compiler behavior for any real
(non-agora) target too — a coverage report always excludes something.

## Non-goals

- Config file (`pyproject.toml` / `cc.toml`) support — CLI-only for now. If `--exclude`
  ends up being typed identically on every run, the natural evolution is a config file;
  out of scope today.
- Exclusion by content or decorator — patterns are paths/globs only.
- Include-lists (`--only`) — no use case yet.
- Any change to the graph schema (`esquema-grafo-poc.md`) — this is input filtering, not
  a new node/edge/gap type.

## Two layers of exclusion (not one)

The existing hardcoded skip-list (`.venv`, `__pycache__`, `.git`, `node_modules`, `.tox`,
`dist`, `build`) is infrastructure/vendor code — it was never a per-repo decision and
stays exactly as it is today: fixed, non-configurable, invisible in the exclusion report
(it was never a candidate for "this repo's own source" in the first place).

`--exclude` adds a second, independent layer: user-declared content exclusions, default
**empty** (explicit-over-implicit — no magic `tests/` default; whoever wants an exclusion
asks for it and it's recorded). Both layers apply at the same point in the pipeline, but
only the second layer is reported.

## Current-state finding that shapes this design

`collect_py_files` (`src/cc/extract/_collect.py`) is already the single source of the
file list for `extract_endpoints`, `extract_sql`, and `extract_calls` — rule 1's "one set
consumed by the whole pipeline" is trivially satisfiable for those three.

`models.py` (Pydantic model discovery) and `_calls_resolver.py`
(`build_symbol_inventory`, used to resolve call targets) do **not** consume
`collect_py_files` at all — each calls `griffe.load(pkg_name, ...)` independently, and
griffe recurses through the whole package on disk by itself. Their local `_SKIP_DIRS`
only gates which *top-level* `*/__init__.py` packages get loaded; a nested dir like
`backend/tests/` isn't top-level, so today it's indexed by griffe regardless. Left
unaddressed, `--exclude 'backend/tests/**'` would stop the call visitor / endpoint /
SQL extractors from emitting nodes for excluded files, while griffe's inventory would
keep offering their functions as resolvable call targets — a call from a non-excluded
file into an excluded one would resolve to a node that no longer exists in the graph
(a dangling edge, caught by `graph/build.py`'s existing dangling-edge report, but a real
asymmetry, not a hypothetical one rule 1 is guarding against preemptively).

**Decision:** close this for real (not just rely on the dangling-edge safety net).
Griffe still loads the whole package from disk (unavoidable, that's how its loader
works) — but its *output* gets pruned before being folded into the model dict / symbol
inventory.

## Design

**1. Pattern matching.** `--exclude PATTERN`, repeatable, glob patterns relative to the
repo root. Each pattern is expanded via `repo_path.glob(pattern)` (stdlib `pathlib`,
already the project's convention — `models.py` already does `glob("*/__init__.py")`) —
native `**` support, no new dependency, no hand-rolled fnmatch translation. The union of
all patterns' matches is the excluded-files set (absolute paths). Patterns are sorted
before being applied (determinism, matches the "sorted output" convention already used
by `collect_py_files`).

**2. `collect_py_files` gains an `exclude_patterns: tuple[str, ...] = ()` parameter.**
It keeps applying the fixed infra skip-list exactly as today, then additionally drops
anything in the glob-expanded excluded set. A sibling helper (or an extra return value)
reports `{pattern: count}` — the number of `.py` files each individual pattern matched —
for the coverage report. `extract_endpoints`, `extract_sql`, `extract_calls`, and
`pipeline.py`'s own file count all thread `exclude_patterns` through to
`collect_py_files`.

**3. Griffe symmetry (models.py + `_calls_resolver.py`).** Both call sites compute the
same excluded-absolute-paths set (via the same glob expansion as step 1 — not a second
independent implementation) and pass it into their griffe tree walk
(`_walk_griffe` / `_walk_griffe_functions`). When visiting a `griffe.Function` or
`griffe.Class`, compare `obj.filepath` against the excluded set; skip adding it to the
model dict / symbol inventory (and, for classes, don't descend into excluded members)
when it matches. Griffe still loads the full package tree from disk — this prunes what
survives into the data structures the rest of the pipeline reads.

**4. Reporting.** `pipeline.py` collects the `{pattern: count}` breakdown from step 2 and
threads it into the compiled graph's top-level metadata (e.g. `exclusions: [{pattern,
count}]`, empty list when `--exclude` wasn't passed). The render template shows a single
visible line when non-empty (footer or header — e.g. *"compilado con 2 exclusiones — 8
ficheros fuera"*) and nothing at all when the list is empty, so the default (no
`--exclude`) output is visually unchanged.

**5. Nodes that survive keep identical ids/hashes.** Exclusion happens upstream of node
construction; `id`/`hash` are derived only from a node's own qualname/path/content, never
from what else exists in the graph, so nothing here should change them. Verified
explicitly (not just argued) by a test that diffs the common node ids between a run with
and without `--exclude` and asserts they're byte-identical.

## Testing

- No `--exclude` passed → output byte-identical to today (regression-zero check, mirrors
  criterion 3 below).
- A fixture with a nested (non-top-level) helper dir excluded via a `**` pattern →
  zero nodes/edges from that dir, and — the case that actually exercises the fix in this
  spec — a call site from a *non-excluded* file that would otherwise resolve into the
  excluded dir now correctly falls to `unresolved_dynamic` instead of producing a
  dangling `calls` edge.
- Determinism: two runs with the same `--exclude` flags produce identical output.
- The id/hash-stability diff test from item 5 above.
- Coverage report / graph metadata correctly lists each active pattern with its matched
  file count.

## Acceptance criteria (carried over from Josem's original spec)

1. `cc compile <agora> --exclude 'backend/tests/**'` → the 15 import-local-a-función
   sites and the ~52 test-only `unresolved_dynamic` sites disappear from the report;
   global coverage rises accordingly and the denominator is clean.
2. The report and the HTML declare the active patterns and their excluded-file counts.
3. Without `--exclude`, output is byte-identical to today (zero regression).
4. Two runs with the same flags → identical output.
5. No surviving node changes `id` or `hash` relative to a run with no exclusions
   (verifiable by diffing the ids in common).

## Files touched

- `src/cc/extract/_collect.py` — `collect_py_files` gains `exclude_patterns`; new
  helper for the glob-expansion + per-pattern count logic (shared by step 3 below, not
  duplicated).
- `src/cc/extract/models.py` — `_walk_griffe` filters by `obj.filepath` against the
  excluded set.
- `src/cc/extract/_calls_resolver.py` — `_walk_griffe_functions` filters the same way.
- `src/cc/extract/calls.py`, `src/cc/extract/endpoints.py`, `src/cc/extract/sql.py` —
  thread `exclude_patterns` through to `collect_py_files`.
- `src/cc/pipeline.py` — accepts `exclude_patterns`, assembles the `{pattern: count}`
  breakdown into graph metadata, computes total file count with exclusions applied.
- `src/cc/cli.py` — new repeatable `--exclude` argument.
- `src/cc/render/template_src.html` — small addition to show the exclusion summary line
  when non-empty.
- `tests/test_collect.py` (new or extended), `tests/test_calls.py`,
  `tests/test_calls_resolver.py`, `tests/test_pipeline.py`, `tests/test_render.py` — per
  Testing section above.
- `README.md` — documents the fixed infra skip-list (already true today, currently
  undocumented) and `--exclude` usage, once implemented and merged.
