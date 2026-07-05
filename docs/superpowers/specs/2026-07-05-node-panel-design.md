# Node Panel Redesign — Design Spec

> Status: approved by Josem 2026-07-05. Ready for implementation plan.

## Goal

Replace the node detail panel's raw `<pre>{JSON}</pre>` dump with a human-readable view, so the eval questions (`ESQUEMA_POC.md §Test`) can be answered by reading the panel, not by mentally parsing JSON. Concretely: clicking `build_context` should tell you who calls it, which endpoints can reach it, and its `file:line` — without leaving the panel.

## Non-goals (deferred, not in this round)

- "Alcanza a" (forward reach summary — e.g. "toca 4 tablas, 9 funciones" for an endpoint). Explicitly deferred to a future round per the same eval-driven-scope discipline the rest of this project uses.
- A configurable hub-threshold input in the sidebar. The threshold is a code constant for now.
- Persisting hidden nodes across page reloads (localStorage). Hiding is in-memory, session-only; F5 resets it.
- Drill-down to actual source content at `file:line` (opening the file, syntax-highlighted). Only the path text is shown, selectable/copyable — the schema's `file`/`line` fields are the anchor for a future viewer, not built here.

## Components

### 1. Humanized props (replaces the raw JSON dump)

Per node `type`, render a small formatted block instead of `JSON.stringify(props)`:

- **`endpoint`**: method + path (as today's title already implies) + handler qualname, handler is a clickable link that navigates to that function node (reuse `goToSearchResult`-style navigation, not a new mechanism).
- **`function`**: qualname, plus "· es handler" suffix if `props.is_handler` is true.
- **`model`**: one line per `props.fields[]` entry — `name: type`.
- **`table`**: one line per `props.columns[]` entry. If `columns` is empty, show the gap message inline, styled like the existing `.gap-error`/`.gap-warning` classes — "⚠ columnas desconocidas — falta DDL" — not a silently empty list. (This mirrors the exact gap `detect_gaps` already produces for this case — same message text, so the panel and the gaps sidebar section never disagree.)

A "ver raw" toggle (small link/checkbox at the top of the panel body) reveals the original `<pre>{JSON}</pre>` view underneath — both audiences (using the tool vs. debugging the compiler) stay served, on a per-click basis, no mode global to the app.

### 2. `file:line`

Plain text, one line, selectable (a `<span>` or single-cell layout, not an `<input>` — no interaction beyond "select and copy" is needed since there's nowhere to navigate to yet).

### 3. Neighborhood, in sentences

Replace the current "Sale →" / "← Entra" raw edge lists with grouped, human sentences — one line per (edge type, direction) pair that has at least one edge, e.g.:

```
Llama a: build_context, _compress, get_db (+2 más)
Escribe en: messages (via db/queries/messages.py:16), channel_syntheses
Lee: channels
Llamada por: run_turn, run_synthesis, test_run_turn (+0 más)
```

The label ("Llama a:", "Llamada por:", "Escribe en:", "Lee:") is plain text — never clickable. Every individual target name after the colon is its own clickable `<span>` (comma-separated), navigating via the same one-hop-or-center mechanism `goToSearchResult` already implements (reuse, not reimplement) — a group label like "Llama a" has no single unambiguous target, only its members do. Cap each group's rendered names at 5, with a trailing non-clickable "(+N más)" when the group has more (avoids one hub-adjacent node turning the panel into an unreadable wall of names — this cap is about the panel's own readability, unrelated to the hub-badge threshold in §4). For `reads`/`writes` specifically, show the `via` (file:line of the SQL call site) next to each table name — this is eval question 1 ("¿dónde se escribe `cost_usd`?") answered directly in the panel of the writer function, not just the table.

Grouping key is `(edge.type, direction)` where direction is `in` (edge points at this node) or `out` (edge originates from this node) — four possible groups for `calls` (in/out) and independently `reads`/`writes`/`handles`/`uses_model` wherever they apply to the node's type. Omit any group with zero edges (no "Llamada por 0 funciones" noise).

### 4. Hub badge

A node is a hub if its **incoming**-edge count (in-degree, across all edge types) meets:

```js
const HUB_MIN_PERCENT = 0.15;   // 15%
const HUB_MIN_ABSOLUTE = 5;     // floor, so small repos don't spuriously mark hubs
const functionCount = GRAPH.nodes.filter(n => n.type === 'function').length;
const hubThreshold = Math.max(HUB_MIN_ABSOLUTE, Math.ceil(HUB_MIN_PERCENT * functionCount));
```

Computed once at load (deterministic given the JSON), stored per-node in a `Set` of hub IDs. Badge text: `⚠ hub — N llamantes` (N = the node's actual in-degree, not the threshold). Both constants live together at the top of the script's "Search index" region (or a new small "Hub detection" region next to it) — not scattered as magic numbers elsewhere.

### 5. Hide node

A checkbox in the panel ("ocultar este nodo") for any node, hub or not — hub-ness is only ever an annotation, hiding is an independent, always-available action. Checking it adds the node's ID to an in-memory `hiddenNodes` Set; `makeElems` filters any ID present in that set out of every subsequent render (map mode, subgraph mode, search navigation, dbltap-expand — all funnel through `makeElems`, so filtering there covers every view without touching each call site). No persistence: reloading the page (F5) clears `hiddenNodes` back to empty — this is the "undo" mechanism, not a "show all" button.

### 6. "Alcanzable desde" (reachable-from, reverse BFS)

For any non-endpoint node, compute which endpoints can reach it by walking **backward** (via `edgesTo`, i.e. "who points at this node") across **all** edge types transitively, collecting every `endpoint`-type node encountered. The walk does **not** continue backward through a node marked as a hub (§4) — reaching a hub stops that branch of the search, so "reachable from" doesn't degenerate into "every endpoint" just because everything eventually touches a shared low-level helper like `get_db`. The hub-stop rule applies only to intermediate nodes encountered *during* the walk; the node the panel is currently showing is always the walk's origin regardless of its own hub status (clicking a hub directly still computes its own reachable-from set).

Rendered as one line: `Alcanzable desde: POST /channels/{id}/synthesize, POST /channels/{id}/rounds` (each path clickable, same navigation mechanism as everywhere else), plus, only when at least one hub was actually hit and stopped the walk: `(cálculo excluye 1 hub: get_db)` — naming which hub(s), not just a count, so the exclusion is auditable rather than a silent cap.

## Testing

Same constraint as the search feature: no JS test runner in this project. `tests/test_render.py` gets the same *shape* of structural assertions already used there (new function names present in the emitted HTML). Real verification is via a headless-Chromium reproduction script during implementation (the same technique that caught the `display: ''` and flexbox `min-width` bugs in the search feature) — not part of the committed test suite, but required before considering any task done, given how many real bugs manual "run `node --check`" alone missed last round.

## Files touched

- `src/cc/render/template_src.html` — `showPanel` rewritten to build the humanized sections; new small functions for hub-set computation, reachable-from BFS, and hidden-node filtering wired into `makeElems`.
- `tests/test_render.py` — structural assertions for the new function names / panel markup.
