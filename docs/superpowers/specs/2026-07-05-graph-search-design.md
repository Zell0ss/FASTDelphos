# Graph Search — Design Spec

> Status: approved by Josem 2026-07-05. Ready for implementation plan.

## Goal

Give the render UI (`src/cc/render/template_src.html`) a search box so Josem can visually validate the compiled graph against the eval questions in `ESQUEMA_POC.md §Test` (e.g. "¿dónde se escribe `cost_usd`?") without grepping source — the tool's own stated success bar ("navegar el grafo responde más rápido que grep + leer"). Today the only entry points into the graph are the endpoint list and manual click/expand; there is no way to jump straight to a node, or to a field/column *inside* a node, by name.

## Non-goals

- Fuzzy matching, ranking/scoring, or typo tolerance — substring, case-insensitive is the whole matching strategy. At ~150 nodes the problem is "does a door exist to X", not relevance.
- Server-side or build-time indexing — the index is built once in the browser from the `GRAPH` object already embedded in the page.
- Persisting search state across reloads, keyboard shortcuts to focus the box, or any settings/preferences.

## Architecture

Everything lives in `template_src.html`'s existing inline `<script>` block, next to the other UI logic (`loadEndpoint`, `showPanel`, etc.) — no new files, no build step, consistent with the project's "single self-contained HTML" constraint (CLAUDE.md: `--out` produces one `index.html`, no CDN, no external JS).

Three pieces:

1. **`buildSearchIndex()`** — runs once at page load, over `GRAPH.nodes`. Produces a flat array of entries:
   ```js
   { matchText: string, kind: 'node'|'field'|'column', nodeId: string, label: string }
   ```
   - One `kind: 'node'` entry per node: `matchText` = the node's short label (same string `makeElems` already uses: `id.split(':').slice(1).join(':')`), so a search for "synthesize" matches the same text the user already sees on-canvas.
   - One `kind: 'field'` entry per `model.props.fields[].name` (props: `{name, type}`).
   - One `kind: 'column'` entry per `table.props.columns[]` (props: a bare string, no `type`).
   - `label` is the pre-formatted display string for the dropdown row, e.g. `` `columna cost_usd en table:messages` `` or `` `campo channel_id en model:ChannelIn` `` for fields, or just the short node label for `kind: 'node'` rows.

2. **`searchGraph(query)`** — substring, case-insensitive match of `query` against every entry's `matchText`. Requires `query.length >= 2` (returns `[]` below that, to avoid a huge dropdown on 1 keystroke). No cap on match count for v1 — ~150 nodes plus their fields/columns is small enough that even a broad query (e.g. "id") stays scrollable; revisit only if real usage shows this is wrong.

3. **`goToSearchResult(entry)`** — the navigation behavior (see below), plus opening the entry's node in the side panel via the existing `showPanel`.

## Search UI

- A text `<input>` at the very top of `#sidebar`, above the "Endpoints" section title — the first thing in the sidebar, since it's the fastest path to anything in the graph, not just endpoints.
- A dropdown `<div>` positioned directly under the input, hidden when empty or query < 2 chars. Re-rendered on every `input` event (no debounce needed at this scale).
- Each dropdown row shows `entry.label`, with a small type tag (`node`/`campo`/`columna`) styled like the existing `.badge`/`.node-dot` conventions already in the sheet, so it reads consistently with the rest of the sidebar.
- `Escape` clears and closes the dropdown. Clicking outside the input+dropdown also closes it (a single global `document` click listener, matching how nothing else in this file currently needs one — first of its kind, keep it minimal).
- Clicking a row calls `goToSearchResult(entry)` and closes the dropdown.

## Navigation behavior (`goToSearchResult`)

Given a target `nodeId` (the entry's node, not a synthetic "field" or "column" node — those don't exist in the schema, they're properties on the table/model node):

1. **If `nodeId` is already present in the currently-rendered Cytoscape elements** (true for any node when in Mapa completo, or for any node already pulled into the active subgraph): don't reload. `cy.center(cy.getElementById(nodeId))`, briefly flash the node (add a CSS class for ~600ms, e.g. `.search-hit` with a bright outline, then remove it), and call `showPanel` for it.
2. **Otherwise** (Subgrafo mode with a different/no endpoint loaded, and the target isn't in the current view): build a fresh neighborhood exactly like the existing double-tap-expand does, but as a **replace**, not an incremental add — one hop in both directions from `nodeId` (reuse `edgesFrom`/`edgesTo`, the same lookup tables `dbltap` already uses). Set `currentRoot = nodeId` (marks it `root-node` styled, and re-enables the "double-click to expand" hint, since `currentRoot` already drives that elsewhere). Clear any active endpoint button highlight (this view isn't tied to an endpoint anymore) — mirror what `setMode('map')` already does for clearing `activeEpBtn`. Apply the dagre layout, `cy.fit()`, `showPanel`.

Either branch ends with the panel open on the target node — landing "answered", not just "found": for a column hit, the panel already shows the table's `via`-annotated `writes`/`reads` edges (existing `showPanel` behavior, untouched), which is the click-through the eval question needs.

## Edge cases

- Query matches zero entries → dropdown shows a single disabled-looking row, `sin resultados`, not an empty box (avoids "is this broken or just empty" ambiguity).
- A table with `columns: []` (the `missing_artifact` gap case) contributes zero `column` entries — nothing to search for, correctly absent rather than fabricated.
- Multiple entries can point at the same `nodeId` (a table with 5 matching columns for a broad query like "id") — each is its own dropdown row (so "which column matched" stays visible), all resolving to the same navigation target.

## Testing

This project's render tests (`tests/test_render.py`) are structural only (file exists, embeds JSON, references cytoscape) — there's no headless-browser/JS execution test harness, and this spec doesn't introduce one (out of scope: the point of this feature is Josem's own manual visual validation, which is the actual test). Add the same *shape* of structural test already used in that file: confirm the emitted `index.html` contains the search input element and the new JS function names, so a future refactor that accidentally drops the feature fails fast even without a browser.

## Files touched

- `src/cc/render/template_src.html` — sidebar markup (search input + dropdown container), CSS (dropdown box, row, type tag, `.search-hit` flash), and the three JS pieces above, wired into the existing `cy`/`GRAPH`/`showPanel`/`edgesFrom`/`edgesTo` machinery already in the file.
- `tests/test_render.py` — one or two structural assertions (see Testing).
- `src/cc/render/emit.py` — not touched; it already just interpolates `GRAPH` and the two vendored JS files into `template_src.html` unchanged.
