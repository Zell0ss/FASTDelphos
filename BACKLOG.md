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
