# Comprehension Compiler (FASTDelphos)

Reads a FastAPI repo, extracts its structure **deterministically** (no LLM, no guessing), and compiles it into a navigable property graph — rendered as a self-contained interactive HTML page. It's not a documentation generator: the point is to answer "where does X happen?" faster by clicking through the graph than by grepping and reading source.

![Comprehension Compiler UI — subgraph view with the node panel open](docs/assets/screenshot.png)

## Why

Onboarding a new codebase (or an auditor reviewing one) usually means grepping for a name, opening five files, and mentally rebuilding the call chain. This tool builds that chain once, as data, and gives you a UI to walk it: click an endpoint, see everything it reaches; click a table, see who writes to it and from which exact line.

Two rules shape everything it does:

- **Deterministic first.** Phase 1 (this repo, today) uses zero LLM calls — everything comes from parsing the source (`ast`, `griffe`, `sqlglot`). If a future phase adds LLM-generated content, it will be visually marked `inferred=true`, never mixed in silently.
- **Flag, don't guess.** When something can't be extracted from source (a table with no `CREATE TABLE` anywhere, a call resolved only at runtime via `Depends()`), the tool never invents an answer — it declares a **gap**, visible in the output, with a suggested fix where one makes sense.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cc compile /path/to/a/fastapi/repo --out ./output/myrepo
```

Open `./output/myrepo/index.html` in a browser — it's a single static file, no server required for local use.

**Working over SSH with no desktop** (e.g. a headless dev box, VS Code Remote-SSH from Windows/Mac): add `--serve` and it compiles, then serves the output over HTTP on `127.0.0.1` until you hit Ctrl+C — VS Code's automatic port forwarding picks it up the same way it does for a local dev server.

```bash
cc compile /path/to/repo --out ./output/myrepo --serve --port 8642
# → Sirviendo ./output/myrepo en http://localhost:8642 — Ctrl+C para parar
```

If the `.venv` from Quick start isn't already active in your shell (e.g. a fresh SSH session), activate it first or call the module directly — both are equivalent to the `cc` console script:

```bash
source .venv/bin/activate
python -m cc compile /path/to/repo --out ./output/myrepo --serve --port 8642
```

The output is a fully self-contained static bundle (Cytoscape vendored inline, no network calls) — if it's already compiled and you just want to view it, you don't need `cc`, the venv, or even this repo checked out. Any static file server works, e.g. stdlib `http.server` from inside the output directory:

```bash
cd ./output/myrepo
python3 -m http.server 8642 --bind 127.0.0.1
```

## What you get

A graph of four node types and five edge types — small enough vocabulary to hold in your head, big enough to answer real questions.

**Nodes:**

| Type | Color | What it is |
|---|---|---|
| `endpoint` | 🔵 blue | A FastAPI route — method, path, and its handler function |
| `function` | ⚪ grey | A function or method, `is_handler` marks whether it's directly wired to a route |
| `model` | 🟢 green | A Pydantic model — its `fields[]`, name and type each |
| `table` | 🟠 orange | A DB table inferred from `CREATE TABLE` / `INSERT` / single-table `SELECT` — its `columns[]` |

**Edges:**

| Type | Meaning |
|---|---|
| `handles` | endpoint → its handler function |
| `calls` | function → function it calls (resolved via `ast` + a griffe symbol inventory — see [`doc_proyecto/VISITOR.md`](doc_proyecto/VISITOR.md) for exactly which call shapes are resolved and why) |
| `uses_model` | endpoint → request/response model, direction-tagged (`in`/`out`) |
| `reads` / `writes` | function → table, with `via` pointing at the exact `file:line` of the SQL call site |

Every node/edge is `inferred: false` in this phase — everything you see was read from source, not guessed.

## Using the UI

- **Search box** (top of the sidebar) — substring, case-insensitive, matches node names *and* what's inside them: a model's field names, a table's column names. Searching `cost_usd` lands you on the table that has that column, not just on tables in general. Click a result to jump straight to it.
- **Node panel** (right side, click any node) — humanized props per node type, `file:line`, and its neighborhood as clickable sentences ("Llama a: ...", "Escrita por: ..."), each with `via` for DB edges. A "ver raw" toggle reveals the underlying JSON if you need it.
- **Hub badge** — a node whose in-degree crosses a relative threshold (15% of all functions, floor of 5) gets flagged `⚠ hub — N llamantes`, so a shared low-level helper (a DB connection getter, say) doesn't get treated like normal application logic.
- **"Alcanzable desde"** — for any non-endpoint node, which endpoints can reach it, computed by walking the call graph backward. The walk stops at hub nodes (see above) so this doesn't degenerate into "every endpoint reaches everything" just because everything eventually touches a shared helper.
- **Ocultar nodo** — hide a node from the current session's view (useful for noisy nodes while you explore); resets on page reload, nothing is persisted.
- **Subgrafo / Mapa completo** — start from one endpoint's reachable subgraph (the default), or see the whole compiled graph at once. Double-click any node to pull in its immediate neighbors.

## Excluding files

Two layers, always. A fixed set — `.venv`, `__pycache__`, `.git`, `node_modules`, `.tox`, `dist`, `build` — is never walked, on every run, not configurable: it's vendor/tooling, never a candidate for "this repo's own source" in the first place.

On top of that, `--exclude PATTERN` (repeatable, glob relative to the repo root) drops your own content — most commonly a test suite that would otherwise pollute the call-graph coverage numbers with test-only helpers and mocks:

```bash
cc compile /path/to/repo --out ./output/repo --exclude 'backend/tests/**'
```

Default is no content exclusions — explicit over implicit, nothing is silently dropped unless you ask for it. Excluded files disappear from the graph entirely: no nodes, no edges, no gaps, and they don't count toward coverage. Anything a *non-excluded* file calls into that lives in an excluded file resolves as `unresolved_dynamic` rather than a broken reference — the tool never points at code that isn't there.

Active patterns and their matched-file counts are visible in the sidebar (top, under the title) and in `graph.json`'s `exclusions` field, so an exclusion is always declared, never silent.

## Gaps — what the tool won't guess

When source doesn't have the answer, the tool says so instead of inventing one. Three kinds, each aimed at a different audience:

- **`missing_artifact`** — the information genuinely isn't in the source (a table referenced in SQL with no `CREATE TABLE` anywhere). Actionable: add the missing artifact.
- **`unresolved_dynamic`** — the information exists, but only at runtime (`Depends()`, `getattr`, dispatch by dict). Not a defect — the code works fine, static analysis just can't see through it. Not something you're asked to fix.
- **`tool_limitation`** — the information *is* in the source, but this version of the tool can't parse that particular shape. Transparency about coverage, not a claim about the repo.

Each gap carries a severity per audience — `comprehension` (is the graph still useful?) and `compliance` (can an auditor trust it?) — because the same gap can be a shrug for one and a blocker for the other.

## How it's built

```
adapter (fastapi) → extractors → graph build → gaps → render
```

- **Extractors** (`src/cc/extract/`) are independent and deterministic: `endpoints.py` (routes), `models.py` (Pydantic via `griffe`), `calls.py` (call graph via `ast` + `griffe`), `sql.py` (tables/columns via `sqlglot`).
- **Graph build** assigns each node a stable `id` (derived from qualname + path — survives body edits) and a content `hash` (changes on edit; the anchor for a future hash-gated LLM-annotation phase).
- **Gaps** are computed once the graph exists, per the taxonomy above.
- **Render** (`src/cc/render/`) emits a single self-contained HTML file — Cytoscape.js and its dagre layout extension are vendored inline, so the output has zero network dependencies and zero build step.

## Development

```bash
pytest                        # 127 tests, no JS runner (the render UI has no
                               # browser test harness — real verification during
                               # development is a throwaway Playwright script,
                               # not part of the committed suite)
ruff check . && ruff format .
```

Design history lives in `doc_proyecto/` (the original schema contract, `ESQUEMA_POC.md`) and `docs/superpowers/` (specs and implementation plans for everything built after the initial POC, in chronological order — useful if you want to see *why* a given piece of resolution logic exists, not just what it does).

## Benchmarking against ground truth (`--oracle`)

The extractor never imports the repo it's analyzing — it only parses source (`ast`, `griffe`, `sqlglot`). That's non-negotiable for a real target, since importing untrusted app code can execute arbitrary module-level side effects and may require secrets/infra the tool has no business touching.

`--oracle` is a **narrow, opt-in exception for measuring the extractor itself**, not something you'd run against a client repo:

```bash
cc compile /path/to/repo --oracle
# → Route recovery: 18/18 (100%)
```

It boots the target app for real (`app.openapi()`, which resolves mounted sub-routers) and asks it directly what routes are registered at runtime — that's the ground truth. It then diffs that list against what the static extractor found, and prints the recovery rate plus any routes the static pass missed.

Use it only against a repo you're comfortable importing locally (it boots clean with no real secrets/infra — a throwaway dev copy or a test fixture, never a repo carrying production credentials). It's how this project validates its own coverage, not a feature for auditing someone else's code.

## Status

Phase 1 (this repo): FastAPI adapter, fully static, zero LLM, validated against a real multi-router FastAPI + MariaDB target — **18/18 routes recovered (100%)** via `--oracle`. Two directions considered for what comes next — not started, no timeline:

- LLM-generated why-notes per node, hash-gated so they only regenerate when the underlying code actually changes.
- A `generic` adapter — static analysis for non-FastAPI repos, interchangeable with the LLM option without touching anything in Phase 1.
