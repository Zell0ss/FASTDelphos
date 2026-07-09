# Backlog

Deferred work — deliberately not implemented, pending a scope decision. Full historical
context (why, what was tried, what was ruled out) lives in `doc_proyecto/VISITOR.md`;
this file is the short, scannable pointer.

## Call resolution: re-export through an internal facade module

**Status:** ⏳ not implemented, scope decision pending. Full trace in
`doc_proyecto/VISITOR.md` § "Hallazgos pendientes de decisión" (item 2).

**Symptom:** 8 call sites in agora (`logger.info(...)`, `logger.error(...)`, etc.)
resolve to `unresolved_dynamic` today, even though the target is knowable statically.

**Why the current resolver misses it:** the sites do `from backend.logger import logger`.
`backend.logger` is an **internal** module that itself does `from loguru import logger` —
a one-hop internal re-export facade. Case 2b (local alias to an external import) correctly
refuses to touch this, because its fence #1 requires the alias's base to resolve directly
to an *external* package — `backend.logger` resolves internal, so 2b stops there by design.
This isn't 2b's gap; it's a distinct, unimplemented resolution case.

**Root cause in the inventory:** `_walk_griffe_functions` discards `griffe.Alias` nodes at
module level with an immediate `return` — module-level re-exports are never indexed at all
today.

**What implementing it would take:**

1. Track `alias_targets: dict[canonical_path, target_path]` — the module-level aliases
   `_walk_griffe_functions` currently throws away, keyed by their canonical path, valued by
   `griffe.Alias.target_path` (confirmed via a spike to work **without** loading the
   external package — `target_path` is a plain string; `final_target` is the one that
   forces a load and blows up with `AliasResolutionError`).
2. Chase the chain with a **bounded depth** until it lands on something outside the
   alias inventory (i.e. a real symbol or an unindexed external name).
3. At that landing point, apply the same rule used everywhere else in the resolver: is
   the top-level package in `top_level_packages` (the repo's own)? → internal/dynamic.
   Otherwise → external. Not a new rule — the existing positive-evidence rule, one hop
   further out.

**Open edge case:** shadowing — what if the module reassigns the re-exported name after
the import? Not analyzed yet.

**Why it's not just 2b extended:** letting internal-resolving bases participate changes
the risk profile from "reclassify external vs. dynamic" (2b, already fenced and shipped)
to "expand how much of the internal call graph gets resolved through indirection" — a
different, wider-blast-radius decision that hasn't been made.

**Relevance beyond agora:** internal re-export facades (a `utils.py` that re-exports half
the stdlib, a `logger.py` wrapping a third-party logger) are a common pattern in corporate
repos — this is very likely to recur in a Corporate-style target, not an agora-specific quirk.

## `_module_qualname` inconsistency for `__init__.py`-defined functions

**Status:** ⏳ not implemented, deliberately deferred — confirmed real, low blast radius so
far. Found 2026-07-08 while fixing the SQL function-node hydration bug (see
`docs/superpowers/plans/2026-07-08-fix-sql-node-hydration.md` and
`docs/superpowers/plans/2026-07-08-unified-function-node-hydration.md`).

**Symptom:** a function defined directly inside an `__init__.py` file gets a **different
qualname** — and therefore a different node `id` — depending on which extractor discovers
it: `src/cc/extract/calls.py`'s `_module_qualname` strips a trailing `.__init__` from the
module path (`pkg/__init__.py` → `pkg`, matching Python's real import semantics and
griffe's own `canonical_path`); `src/cc/extract/sql.py`'s and
`src/cc/extract/endpoints.py`'s own `_module_qualname` helpers do **not** (`pkg/__init__.py`
→ `pkg.__init__`).

**Why it doesn't crash today:** `graph/build.py`'s node-identity assertion (added by the
SQL-hydration-bugfix plan) only fires when two extractors register the **same** id with
conflicting `file`/`line`/`hash`. Two *different* ids for the same real function never
collide in the merge dict, so the assertion can't catch this — the practical effect is a
silent duplicate/orphan node (e.g. a DB-touching function defined in an `__init__.py`
would show up as `function:pkg.__init__.foo` from `sql.py` but `function:pkg.foo` from
`calls.py`, if it's also called from elsewhere), not a crash.

**Why it's deferred, not fixed:** confirmed during the SQL-hydration-bugfix plan's review
(`.superpowers/sdd/progress.md`, "SQL function-node hydration bugfix" section) as real but
explicitly out of scope for that plan (which targeted the call-site-vs-def-line hash bug
specifically). No test fixture currently defines a DB-touching function, a called function,
or a route handler directly inside an `__init__.py` — every fixture's `__init__.py` is
empty — so nothing today silently exercises or normalizes the wrong behavior.

**What fixing it would take:** unify all three `_module_qualname` implementations into one
shared helper (matching `calls.py`'s existing `__init__`-stripping behavior, which already
matches griffe's own `canonical_path` convention) — analogous to how the four
function-node-hydration call sites were unified onto `src/cc/extract/_node_hydration.py`.

## Static resolution of the `include_router` chain

**Status:** ⏳ not implemented, deliberately deferred — scope decision pending.

**Symptom / opportunity:** today the tool doesn't reconstruct the router-registration
tree (`app.include_router(router, prefix=...)`, including nested router-of-routers)
beyond extracting each router's own literal `prefix` to compute `full_path`. There's no
derived `wired: true/false` per endpoint — a router declared in the code but never
actually included from the real entrypoint (`main.py`) is reported identically to a live
one today.

**What implementing it would take:**
1. Static resolution of the full `include_router` chain — literal prefixes, including
   nested inclusion (a router that itself includes another router).
2. Derive `wired: true/false` per endpoint: `false` when a router is declared but never
   included from the entrypoint — rendered distinctly (dimmed / "not registered" badge).
3. Extend the synthetic fixture
   `test_two_routers_same_path_different_namespace_compiles_with_ambiguity_gap`
   (`tests/test_pipeline.py`, plan `docs/superpowers/plans/2026-07-09-endpoint-identity-fix.md`)
   with: a literal prefix passed to `include_router`, a nested prefix, and a prefix that
   comes from a non-literal variable — this last case is a gap (`unresolved_dynamic`),
   not something to guess.

**Relevance:** found while fixing endpoint identity
(`docs/superpowers/plans/2026-07-09-endpoint-identity-fix.md`) — explicitly out of scope
for that plan, which resolves the identity collision when the apparent route matches, not
the deeper question of whether a router is actually wired into the app at all.

## Top-level package discovery ignores exclude_patterns/.gitignore

**Status:** ⏳ not implemented, low urgency — no known repo where this changes real output.

**Symptom:** `build_symbol_inventory`'s discovery loop (`src/cc/extract/_calls_resolver.py`)
decides whether a top-level directory counts as internal via an unfiltered
`any(entry.rglob("*.py"))` check, and unconditionally adds the directory's name to
`top_level_packages` — both independent of `exclude_patterns`/`.gitignore`. Loading
(`_walk_griffe_functions`'s per-child `excluded` check) *does* respect exclusions. So a
top-level directory whose only `.py` files are all excluded (vendored/gitignored) can still
land in `top_level_packages`, contributing zero functions to the inventory but still
flipping any `dirname.*` call from `resolved_external` to `unresolved_dynamic`.

Observed in practice (harmlessly) against agora: `frontend/` newly appears in the
"top-level packages detected" report line after the namespace-package discovery fix
(`docs/superpowers/plans/2026-07-09-classifier-internal-external-fix.md`, Task 1), because
`frontend/node_modules/flatted/python/flatted.py` exists — but that file (and everything
importable from it) is excluded, so `graph.json` is byte-identical either way for agora
specifically (verified in that plan's Task 6).

**What implementing it would take:** gate the directory `.py`-existence check (and the
subsequent `top_level_packages.add`) through the same `excluded_files`/`collect_py_files`
filtering that loading already uses, so discovery and loading agree on what counts as
first-party source for a given repo's exclude configuration.

**Relevance:** found during the final whole-branch review of
`docs/superpowers/plans/2026-07-09-classifier-internal-external-fix.md` — no test or real
target currently exercises a case where this actually changes classification output, so
deferred rather than fixed speculatively.
