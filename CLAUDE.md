# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**Comprehension Compiler** (FASTDelphos) — reads a repo (FastAPI in Phase 1), extracts its structure deterministically, and compiles it into a navigable property graph. Not a documentation generator. Output: interactive HTML + Cytoscape.js visualization.

## Non-Negotiable Principles

- **Source-only, zero infra.** Reads source files only. Never connects to DB, secrets, or services. Never imports the target app in production (oracle check is the only exception — see §Test Target).
- **Read-only on target.** Never writes to the analyzed repo. Output goes to its own `--out` dir.
- **Deterministic first; LLM rationed and always marked.** Phase 1 = zero LLM. Every LLM-generated field carries `inferred=true`.
- **Compiler behavior on gaps.** What can't be extracted from source is NOT guessed — it's declared as a gap with a suggested fix. Flag, don't block.

## Stable Anchors — Expensive to Change

These three fields are the cache contract. Treat them as immutable API:

- `id` — stable identity derived from `qualname + path` (e.g. `function:agora.services.synthesis.build_context`). Does NOT change when body is edited.
- `hash` — content fingerprint of the source span. Changes on edit. Gates Phase 2 LLM re-generation.
- `inferred` — `false` for deterministic extraction, `true` for LLM output.

## Pipeline

```
adapter (fastapi) → extractors → graph build → gaps → render
```

- `adapter` — knows what counts as an entry point. Phase 1: FastAPI (route = endpoint).
- `extractors` — `endpoints.py`, `models.py`, `calls.py`, `sql.py` → nodes and edges per `ESQUEMA_POC.md`.
- `graph build` — assembles nodes+edges, assigns stable `id` and content `hash`.
- `gaps` — anything non-inferable → legibility report with `suggested` stubs. Never silently invents.
- `render` — HTML + Cytoscape.js (CDN, no build step). Click node → panel with source span, props, edges.

## Project Layout

```
src/cc/
  cli.py
  pipeline.py
  adapters/{base.py, fastapi.py, generic.py (stub)}
  extract/{endpoints.py, models.py, calls.py, sql.py}
  graph/{schema.py, build.py}
  gaps.py
  render/{template.html, emit.py}
tests/
pyproject.toml
```

## Stack Decisions

| Component | Library |
|-----------|---------|
| Symbol inventory + signatures | `griffe` |
| Decorators + type annotations | `ast` (stdlib) |
| Call graph (best-effort) | `pyan3` |
| SQL parsing | `sqlglot` |
| Content hashing | `hashlib` (stdlib) |
| Graph render | Cytoscape.js (CDN) |
| Format + lint | `ruff` |
| Logging | LogCentral — `get_logger("fastdelphos")` |

## Gap Types — Do Not Conflate

- **`missing_artifact`** — info isn't in the source (e.g. table referenced but no `CREATE TABLE`). Actionable: ask dev to add it. Real gap.
- **`unresolved_dynamic`** — info exists at runtime (`Depends`, `getattr`, dispatch-by-dict). Mark `inferred=true`, don't ask. Not a gap.

Gap fields: `kind`, `where` (file:line + node id), `missing` (human description), `suggested` (fillable stub), `severity.comprehension`, `severity.compliance`.

Same gap is `warning` for comprehension and `error` for compliance — a linaje with holes doesn't work for an auditor, but partial comprehension is still useful.

## Phase Roadmap

**Phase 1 (current):** FastAPI adapter, pure static, zero LLM. Target: agora.  
**Phase 2 option A:** LLM why-notes per node, hash-gated (regenerate only on drift). `inferred` content visually distinct in UI.  
**Phase 2 option B (alternative):** `generic` adapter — static analysis for non-FastAPI repos (Sebastian, claude-redditor). Interchangeable with 2A without touching Phase 1.

## Test Target: agora

- Stack: FastAPI + aiomysql + MariaDB (`tertulia_db`)
- SQL: raw queries, no ORM
- Known tables: `profiles`, `channels`, `channel_profiles`, `messages`, `summaries`, `channel_syntheses`
- **Oracle check:** `app.routes` / `app.openapi()` import is valid ONLY for agora (boots clean in dev). Use to measure static recovery rate vs. ground truth. This is a POC validator, NOT the production extraction path. Do not use in BNP context.

## Phase 1 Acceptance Criteria

1. The 3 eval questions (`ESQUEMA_POC.md §Test`) answered faster by navigating the graph than by grep+read.
2. Every `table` without inferable columns appears as an actionable gap, not an empty node.
3. Route recovery rate reported (static vs. oracle).
4. Call graph recoveries validated by Josem against agora.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run
python -m cc compile <path-to-repo> --out <output-dir>

# Test
pytest

# Lint + format
ruff check . && ruff format .
```

## Adding New Node or Edge Types

1. Update `ESQUEMA_POC.md` first — it is the authoritative schema contract.
2. Add the extractor in `src/cc/extract/`.
3. Update `graph/schema.py` for validation.
4. Add a gap declaration if the new type can have unresolvable cases.
5. Add a test fixture for a known case in `tests/`.
