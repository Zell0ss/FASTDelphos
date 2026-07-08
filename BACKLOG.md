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
