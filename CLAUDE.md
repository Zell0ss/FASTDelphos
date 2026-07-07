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
  extract/{_collect.py, endpoints.py, models.py, calls.py, sql.py}
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

- **`missing_artifact`** — info isn't in the source (e.g. table referenced but no `CREATE TABLE`). Actionable: ask dev to add it. `comprehension: warning`, `compliance: error`.
- **`unresolved_dynamic`** — info exists at runtime (`Depends`, `getattr`, dispatch-by-dict). Mark `inferred=true`, don't ask. Not a gap.
- **`tool_limitation`** — info IS in the source, but the current tool can't parse it (e.g. pyan3 crashes on module-level initialization code). Signals partial coverage, not a repo defect. `comprehension: warning` (partial but still useful), `compliance: error` (an auditor cannot trace flows through a file with no call graph).

Gap fields: `kind`, `where` (file:line + node id), `missing` (human description), `suggested` (fillable stub), `severity.comprehension`, `severity.compliance`.

## Extractor Conventions

**File discovery** — always use `collect_py_files(repo_path)` from `extract/_collect.py`. Never use `rglob("*.py")` directly — it walks the target repo's `.venv` and pollutes the graph with vendor code. Excluded dirs: `.venv`, `__pycache__`, `.git`, `node_modules`, `tests`, `dist`, `build`.

**griffe package discovery** (`extract/models.py`) — use `glob("*/__init__.py")` (one level, not rglob) to find top-level packages. Call `griffe.load(pkg_name, search_paths=[repo_path])` — search root is the repo itself, not its parent. If no sub-packages exist, fall back to loading the repo directory itself as a package from `repo_path.parent`. Catch all exceptions from griffe per package (it can raise `KeyError`, `ImportError`, etc.).

**AST call visitor** (`extract/calls.py` + `extract/_calls_resolver.py`) — replaced pyan3 (GPL-2.0, 0 edges recovered in agora's `backend/services/`). `extract_calls()` returns `(nodes, edges, excluded, coverage)`. Resolution is griffe-inventory-backed and covers exactly 3 cases: direct name (module-local or imported), attribute-on-import (any depth), self/cls method via class hierarchy (MRO-aware, cross-module). Every call site lands in one of 3 buckets — `resolved_internal` (edge + hydrated `function` Nodes on both ends), `resolved_external` (aggregate count only — import positively rooted outside the repo's own top-level packages, never a gap), `unresolved_dynamic` (the default — "not knowing what a call is never classifies it as external"). `tool_limitation` gaps now come from `ast.parse` `SyntaxError`, not parser crashes — near-unreachable in practice. Do not add a 4th resolution case without checking `doc_proyecto/VISITOR.md` — the 3-case scope is deliberate.

**Oracle** (`oracle.py`) — only used with `--oracle` flag. Discovers top-level sub-packages (not just `repo_path.name`) to try `backend.main` etc. Adds target's `.venv` site-packages to `sys.path`. Does `os.chdir(repo_path)` so pydantic-settings finds `.env`. Uses `app.openapi()` (fully resolves sub-routers), not `app.routes`.

## Phase Roadmap

**Phase 1 (current):** FastAPI adapter, pure static, zero LLM. Target: agora.  
**Phase 2 option A:** LLM why-notes per node, hash-gated (regenerate only on drift). `inferred` content visually distinct in UI.  
**Phase 2 option B (alternative):** `generic` adapter — static analysis for non-FastAPI repos (Sebastian, claude-redditor). Interchangeable with 2A without touching Phase 1.

## Test Target: agora

- Stack: FastAPI + aiomysql + MariaDB (`tertulia_db`)
- SQL: raw queries, no ORM
- Known tables: `profiles`, `channels`, `channel_profiles`, `messages`, `summaries`, `channel_syntheses`
- **Oracle check:** `app.routes` / `app.openapi()` import is valid ONLY for agora (boots clean in dev). Use to measure static recovery rate vs. ground truth. This is a POC validator, NOT the production extraction path. Do not use in Corporate context.

## Phase 1 Acceptance Criteria

1. The 3 eval questions (`ESQUEMA_POC.md §Test`) answered faster by navigating the graph than by grep+read.
2. Every `table` without inferable columns appears as an actionable gap, not an empty node.
3. Route recovery rate reported (static vs. oracle). ✅ **18/18 (100%)** against agora, `cc compile /data/agora --oracle` (2026-07-07) — zero routes missing from static extraction.
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
