# Node Panel Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the node panel's raw JSON dump with a humanized view (props by type, `file:line`, sentence-grouped clickable neighborhood, hub badge, hide-node toggle, reverse-BFS "reachable from") per `docs/superpowers/specs/2026-07-05-node-panel-design.md`.

**Architecture:** Everything is a rewrite of `showPanel` plus a handful of new pure-logic functions in `src/cc/render/template_src.html`'s existing inline script (same single-file constraint as the search feature). Navigation from any new clickable element reuses `goToSearchResult({nodeId})` unchanged — that function only ever reads `entry.nodeId`, so no new navigation code is needed anywhere in this plan.

**Tech Stack:** Vanilla JS, Cytoscape.js (already vendored). No new dependencies. Node.js + Playwright (already installed in this environment from the search-feature work) are used as ad-hoc, non-committed verification during implementation — this project still has no JS test runner (see Global Constraints).

## Global Constraints

- **No new files, no build step.** Everything goes inline into `src/cc/render/template_src.html`. `src/cc/render/emit.py` is not touched.
- **`goToSearchResult({nodeId})` is the only navigation mechanism.** It already ignores every `entry` field except `.nodeId` — do not write a second navigation function. Any clickable target in the new panel calls it via `goToSearchResult({ nodeId: theTargetId })`.
- **This project has no JS test framework.** `tests/test_render.py` gets the same structural-assertion style already used there. Real verification is a Playwright script run during implementation (not committed) — required before considering a step done, per the two real bugs (`style.display`, flexbox `min-width`) that plain `node --check` missed in the previous round.
- **Hub threshold is a code constant, not a UI control**, per the spec: `HUB_MIN_PERCENT = 0.15`, `HUB_MIN_ABSOLUTE = 5`, threshold = `Math.max(HUB_MIN_ABSOLUTE, Math.ceil(HUB_MIN_PERCENT * functionCount))`.
- **Hidden nodes are in-memory only** (a `Set`, module-level `let`/`const` in the script) — no `localStorage`. Reloading the page is the only "undo."
- **Neighborhood group labels are never clickable; only individual target names are**, capped at 5 per group with a non-clickable `(+N más)` suffix beyond that.
- **The "reachable from" BFS walks backward (`edgesTo`) across all edge types** and does not expand past any node in `HUB_IDS` — the origin node itself is exempt from this stop-rule (it's always explored from, regardless of its own hub status).

---

### Task 1: Rewrite the node panel

**Files:**
- Modify: `src/cc/render/template_src.html`
- Test: `tests/test_render.py` (append)

**Interfaces:**
- Produces (inline JS functions, callable from the browser console for manual verification): `renderPanelBody(data)`, `renderProps(data)`, `renderNeighborhood(nodeId)`, `reachableFromEndpoints(targetId)`, `toggleHideNode(nodeId, hide)`, `togglePanelRaw(show)`.
- Consumes: `goToSearchResult({nodeId})` (existing, unchanged), `nodeById`, `edgesFrom`, `edgesTo`, `short(id)` (all existing, unchanged).

- [ ] **Step 1: Write the failing structural test**

Append to `tests/test_render.py`:

```python
def test_html_includes_node_panel_features():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        html = (pathlib.Path(d) / "index.html").read_text()
        assert "function renderPanelBody" in html
        assert "function renderProps" in html
        assert "function renderNeighborhood" in html
        assert "function reachableFromEndpoints" in html
        assert "function toggleHideNode" in html
        assert "function togglePanelRaw" in html
        assert "HUB_MIN_PERCENT" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_render.py -v -k panel_features`
Expected: FAIL — none of these function names exist in the template yet.

- [ ] **Step 3: Add CSS**

In `src/cc/render/template_src.html`, find this block (the last CSS added by the search feature):

```css
    .search-kind.field, .search-kind.column { background: #2a3a2a; color: #7c7; }
  </style>
```

Replace it with:

```css
    .search-kind.field, .search-kind.column { background: #2a3a2a; color: #7c7; }

    .panel-link { color: #7fa8d9; cursor: pointer; text-decoration: underline dotted; }
    .panel-link:hover { color: #a8c8f0; }
    .hub-badge {
      display: inline-block; font-size: 10px; padding: 2px 6px; border-radius: 3px;
      background: #4a2010; color: #ff9944; margin-bottom: 6px;
    }
    .panel-toggle { display: block; font-size: 10px; color: #778; margin: 2px 0; cursor: pointer; }
    .panel-props { font-size: 11px; margin: 6px 0; }
    .panel-fileline { font-size: 10px; color: #8899bb; margin: 6px 0; user-select: text; }
    .neighbor-line { font-size: 11px; margin: 4px 0; }
    .neighbor-line b { color: #8899bb; font-weight: normal; }
    .neighbor-line .more { color: #556; font-size: 10px; }
    .neighbor-line .via { font-size: 9px; color: #667; margin-left: 2px; }
    .panel-raw { margin-top: 8px; }
  </style>
```

Now find these now-obsolete rules (they styled the old "Sale →"/"← Entra" edge list, which this task removes) and delete them entirely:

```css
    .panel-edges { margin-top: 8px; font-size: 11px; }
    .panel-edges .title { color: #8899bb; margin-bottom: 3px; }
    .panel-edges .edge-line { margin: 2px 0; font-size: 10px; }
    .panel-edges .via { font-size: 9px; color: #556; margin-left: 12px; }
```

- [ ] **Step 4: Include `file`/`line` in the Cytoscape element data**

`makeElems` currently spreads `n.props` into the element's `data` but not the Node's own top-level `file`/`line` fields — the panel needs them. In `src/cc/render/template_src.html`, find:

```js
        elems.push({
          data: { id: n.id, label: n.id.split(':').slice(1).join(':'), type: n.type, inferred: n.inferred, ...n.props },
          classes: [n.inferred ? 'inferred' : '', id === rootId ? 'root-node' : ''].join(' ').trim(),
        });
```

Replace it with:

```js
        elems.push({
          data: {
            id: n.id, label: n.id.split(':').slice(1).join(':'), type: n.type,
            inferred: n.inferred, file: n.file, line: n.line, ...n.props,
          },
          classes: [n.inferred ? 'inferred' : '', id === rootId ? 'root-node' : ''].join(' ').trim(),
        });
```

- [ ] **Step 5: Add hub detection and the hidden-nodes set**

In `src/cc/render/template_src.html`, find the end of the search index block:

```js
    const SEARCH_INDEX = buildSearchIndex();

    function searchGraph(query) {
      const q = query.trim().toLowerCase();
      if (q.length < 2) return [];
      return SEARCH_INDEX.filter(e => e.matchText.toLowerCase().includes(q));
    }
```

Add immediately after it:

```js

    // ── Hub detection ────────────────────────────────────────────────────────
    const HUB_MIN_PERCENT  = 0.15;
    const HUB_MIN_ABSOLUTE = 5;
    const functionCount = GRAPH.nodes.filter(n => n.type === 'function').length;
    const hubThreshold = Math.max(HUB_MIN_ABSOLUTE, Math.ceil(HUB_MIN_PERCENT * functionCount));
    const HUB_IDS = new Set(
      GRAPH.nodes.filter(n => (edgesTo[n.id] || []).length >= hubThreshold).map(n => n.id)
    );

    // ── Hidden nodes (in-memory only — F5 is the undo) ──────────────────────────
    const hiddenNodes = new Set();
```

- [ ] **Step 6: Filter hidden nodes out of every render**

In `src/cc/render/template_src.html`, find `makeElems`:

```js
    function makeElems(nodeIds, rootId) {
      const elems = [];
      for (const id of nodeIds) {
        const n = nodeById[id];
        if (!n) continue;
```

Replace it with:

```js
    function makeElems(nodeIds, rootId) {
      const elems = [];
      for (const id of nodeIds) {
        if (hiddenNodes.has(id)) continue;
        const n = nodeById[id];
        if (!n) continue;
```

And find the edge-building loop right below it:

```js
      for (const e of GRAPH.edges) {
        if (nodeIds.has(e.from_) && nodeIds.has(e.to))
          elems.push({ data: { id: e.from_ + '→' + e.to, source: e.from_, target: e.to, label: e.type, ...e.props } });
      }
```

Replace it with:

```js
      for (const e of GRAPH.edges) {
        if (nodeIds.has(e.from_) && nodeIds.has(e.to) &&
            !hiddenNodes.has(e.from_) && !hiddenNodes.has(e.to))
          elems.push({ data: { id: e.from_ + '→' + e.to, source: e.from_, target: e.to, label: e.type, ...e.props } });
      }
```

- [ ] **Step 7: Add `reachableFromEndpoints`**

In `src/cc/render/template_src.html`, find the existing `reachableFrom` (forward BFS, used by `loadEndpoint`):

```js
    // ── BFS ───────────────────────────────────────────────────────────────────
    function reachableFrom(startId) {
      const visited = new Set([startId]);
      const queue   = [startId];
      while (queue.length) {
        const curr = queue.shift();
        for (const e of (edgesFrom[curr] || [])) {
          if (!visited.has(e.to)) { visited.add(e.to); queue.push(e.to); }
        }
      }
      return visited;
    }
```

Add immediately after it:

```js

    // ── Reverse BFS: which endpoints can reach this node, stopping at hubs ──────
    function reachableFromEndpoints(targetId) {
      const visited     = new Set([targetId]);
      const queue        = [targetId];
      const endpointIds  = new Set();
      const stoppedHubs  = new Set();

      while (queue.length) {
        const curr = queue.shift();
        for (const e of (edgesTo[curr] || [])) {
          const prev = e.from_;
          if (visited.has(prev)) continue;
          visited.add(prev);

          const prevNode = nodeById[prev];
          if (prevNode && prevNode.type === 'endpoint') endpointIds.add(prev);

          if (HUB_IDS.has(prev)) { stoppedHubs.add(prev); continue; }
          queue.push(prev);
        }
      }
      return { endpointIds: [...endpointIds], stoppedHubs: [...stoppedHubs] };
    }
```

- [ ] **Step 8: Rewrite `showPanel` and its supporting render functions**

In `src/cc/render/template_src.html`, find the entire current `showPanel` function and the `short` helper right after it:

```js
    function showPanel(data) {
      document.getElementById('panel').style.display = 'block';
      cy.resize();
      document.getElementById('panel-title').textContent = data.id;

      const props = { ...data };
      delete props.id; delete props.label; delete props.type; delete props.inferred;

      const outEdges = GRAPH.edges.filter(e => e.from_ === data.id);
      const inEdges  = GRAPH.edges.filter(e => e.to   === data.id);

      let html = '<pre>' + JSON.stringify(props, null, 2) + '</pre>';

      if (outEdges.length) {
        html += '<div class="panel-edges"><div class="title">Sale →</div>';
        for (const e of outEdges) {
          const color = EDGE_COLORS[e.type] || '#aaa';
          html += `<div class="edge-line"><span style="color:${color}">${e.type}</span> → ${short(e.to)}`;
          if (e.props && e.props.via) html += `<div class="via">via ${e.props.via}</div>`;
          html += '</div>';
        }
        html += '</div>';
      }
      if (inEdges.length) {
        html += '<div class="panel-edges"><div class="title">← Entra</div>';
        for (const e of inEdges) {
          const color = EDGE_COLORS[e.type] || '#aaa';
          html += `<div class="edge-line">${short(e.from_)} <span style="color:${color}">${e.type}</span> →</div>`;
        }
        html += '</div>';
      }

      document.getElementById('panel-body').innerHTML = html;
    }

    function short(id) { return id.split(':').slice(1).join(':'); }
```

Replace it with:

```js
    function showPanel(data) {
      document.getElementById('panel').style.display = 'block';
      cy.resize();
      document.getElementById('panel-title').textContent = data.id;
      document.getElementById('panel-body').innerHTML = renderPanelBody(data);
    }

    function short(id) { return id.split(':').slice(1).join(':'); }

    function renderProps(data) {
      if (data.type === 'endpoint') {
        return (
          `<div>${data.method} ${data.path}</div>` +
          `<div>handler: <span class="panel-link" data-nav="function:${data.handler}">${data.handler}</span></div>`
        );
      }
      if (data.type === 'function') {
        return `<div>${data.qualname}${data.is_handler ? ' · es handler' : ''}</div>`;
      }
      if (data.type === 'model') {
        const fields = (data.fields || []);
        if (!fields.length) return '<div style="color:#556">(sin campos)</div>';
        return fields.map(f => `<div>${f.name}: ${f.type}</div>`).join('');
      }
      if (data.type === 'table') {
        const columns = data.columns || [];
        if (!columns.length) {
          return '<div class="gap-item gap-warning">⚠ columnas desconocidas — falta DDL</div>';
        }
        return columns.map(c => `<div>${c}</div>`).join('');
      }
      return '';
    }

    const NEIGHBOR_LABELS = {
      'calls:out':      'Llama a',
      'calls:in':       'Llamada por',
      'handles:out':    'Maneja',
      'handles:in':     'Manejado por',
      'uses_model:out': 'Usa modelo',
      'reads:out':      'Lee',
      'writes:out':     'Escribe en',
      'reads:in':       'Leída por',
      'writes:in':      'Escrita por',
    };

    function renderNeighborGroupLine(label, items) {
      const CAP   = 5;
      const shown = items.slice(0, CAP);
      const extra = items.length - shown.length;
      const parts = shown.map(it => {
        const via = it.via ? ` <span class="via">via ${it.via}</span>` : '';
        return `<span class="panel-link" data-nav="${it.targetId}">${short(it.targetId)}</span>${via}`;
      });
      let line = `<div class="neighbor-line"><b>${label}:</b> ${parts.join(', ')}`;
      if (extra > 0) line += ` <span class="more">(+${extra} más)</span>`;
      line += '</div>';
      return line;
    }

    function renderNeighborhood(nodeId) {
      const outByType = {}, inByType = {};
      for (const e of (edgesFrom[nodeId] || [])) {
        (outByType[e.type] = outByType[e.type] || []).push({ targetId: e.to, via: e.props && e.props.via });
      }
      for (const e of (edgesTo[nodeId] || [])) {
        (inByType[e.type] = inByType[e.type] || []).push({ targetId: e.from_, via: e.props && e.props.via });
      }

      let html = '';
      for (const [type, items] of Object.entries(outByType)) {
        const label = NEIGHBOR_LABELS[`${type}:out`];
        if (label) html += renderNeighborGroupLine(label, items);
      }
      for (const [type, items] of Object.entries(inByType)) {
        const label = NEIGHBOR_LABELS[`${type}:in`];
        if (label) html += renderNeighborGroupLine(label, items);
      }
      return html;
    }

    function renderReachableFrom(data) {
      if (data.type === 'endpoint') return '';
      const { endpointIds, stoppedHubs } = reachableFromEndpoints(data.id);
      if (!endpointIds.length) return '';

      const links = endpointIds.map(id => {
        const ep = nodeById[id];
        const label = ep ? `${ep.props.method} ${ep.props.path}` : short(id);
        return `<span class="panel-link" data-nav="${id}">${label}</span>`;
      }).join(', ');

      let hubNote = '';
      if (stoppedHubs.length) {
        const names = stoppedHubs.map(id => short(id)).join(', ');
        const plural = stoppedHubs.length > 1 ? 's' : '';
        hubNote = ` <span class="via">(cálculo excluye ${stoppedHubs.length} hub${plural}: ${names})</span>`;
      }
      return `<div class="neighbor-line"><b>Alcanzable desde:</b> ${links}${hubNote}</div>`;
    }

    function renderPanelBody(data) {
      const hubBadge = HUB_IDS.has(data.id)
        ? `<span class="hub-badge">⚠ hub — ${(edgesTo[data.id] || []).length} llamantes</span>`
        : '';

      const props = { ...data };
      delete props.id; delete props.label; delete props.type; delete props.inferred;
      const rawJson = JSON.stringify(props, null, 2);

      return (
        hubBadge +
        `<label class="panel-toggle"><input type="checkbox" onchange="togglePanelRaw(this.checked)"> ver raw</label>` +
        `<label class="panel-toggle"><input type="checkbox" onchange="toggleHideNode('${data.id}', this.checked)"> ocultar este nodo</label>` +
        `<div class="panel-props">${renderProps(data)}</div>` +
        `<div class="panel-fileline">${data.file || ''}:${data.line || ''}</div>` +
        renderNeighborhood(data.id) +
        renderReachableFrom(data) +
        `<pre class="panel-raw" style="display:none">${rawJson}</pre>`
      );
    }

    function togglePanelRaw(show) {
      const pre = document.querySelector('#panel-body .panel-raw');
      if (pre) pre.style.display = show ? 'block' : 'none';
    }

    function toggleHideNode(nodeId, hide) {
      if (!hide) return;
      hiddenNodes.add(nodeId);
      cy.getElementById(nodeId).remove();
      document.getElementById('panel').style.display = 'none';
    }

    document.getElementById('panel-body').addEventListener('click', evt => {
      const link = evt.target.closest('[data-nav]');
      if (!link) return;
      goToSearchResult({ nodeId: link.dataset.nav });
    });
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/test_render.py -v`
Expected: PASS — all tests, including the new one.

- [ ] **Step 10: Run the full suite**

Run: `pytest -q`
Expected: PASS, previous count plus 1.

- [ ] **Step 11: Verify against real agora data with a headless browser**

Compile agora and drive the actual UI with Playwright (same technique that caught the two display bugs in the search-feature round — write this as a throwaway script, not a committed test):

```bash
python -m cc compile /data/agora --out /tmp/panel-verify
```

```js
// /tmp/pw_panel_verify.js
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1258, height: 900 } });
  const errors = [];
  page.on('pageerror', err => errors.push(err.message));

  await page.goto('file:///tmp/panel-verify/index.html');
  await page.fill('#search-input', 'build_context');
  await page.waitForTimeout(200);
  await page.click('#search-dropdown .search-row[data-idx="0"]');
  await page.waitForTimeout(400);

  const panelBody = await page.$eval('#panel-body', el => el.innerHTML);
  const panelVisible = await page.$eval('#panel', el => getComputedStyle(el).display !== 'none');

  console.log('errors:', errors.join('; ') || '(none)');
  console.log('panel visible:', panelVisible);
  console.log('has "Llamada por":', panelBody.includes('Llamada por'));
  console.log('has "Alcanzable desde":', panelBody.includes('Alcanzable desde'));
  console.log('has file:line:', /panel-fileline">[^<]+:\d+/.test(panelBody));

  // Click a "Llamada por" link and confirm navigation actually happens
  await page.click('#panel-body .panel-link');
  await page.waitForTimeout(300);
  const newTitle = await page.$eval('#panel-title', el => el.textContent);
  console.log('title after clicking a neighbor link (should differ from build_context):', newTitle);

  await browser.close();
})();
```

```bash
node /tmp/pw_panel_verify.js
```

Expected: no errors, panel visible, "Llamada por" and file:line both present for `build_context` (it's called from `run_turn`, per the earlier eval-question-3 trace this whole project has been validating against). If "Alcanzable desde" is missing, check whether `build_context` is reachable from an endpoint at all in the compiled graph before assuming a bug — trace it by hand in `graph.json` first, same discipline as every other unverified-count claim this session has corrected.

Additionally verify the hub badge and hide-node manually against a node known to have many callers (e.g. `get_db` if it appears — check `graph.json` for its in-degree first to know whether it should actually cross the threshold before asserting the badge should appear):

```bash
node -e "
const fs = require('fs');
const g = JSON.parse(fs.readFileSync('/tmp/panel-verify/graph.json'));
const indeg = {};
for (const e of g.edges) indeg[e.to] = (indeg[e.to]||0)+1;
const functionCount = g.nodes.filter(n => n.type === 'function').length;
const threshold = Math.max(5, Math.ceil(0.15 * functionCount));
console.log('functionCount:', functionCount, 'threshold:', threshold);
const hubs = Object.entries(indeg).filter(([id,c]) => c >= threshold);
console.log('hubs:', JSON.stringify(hubs));
"
```

If any hubs are found, repeat the Playwright script's search/click steps targeting one of those IDs and confirm `panelBody.includes('hub-badge')` (or check for the `⚠ hub` text) is true, and that `renderReachableFrom` on a node reachable only through that hub either omits it or notes the exclusion correctly.

- [ ] **Step 12: Commit**

```bash
git add src/cc/render/template_src.html tests/test_render.py
git commit -m "feat: humanized node panel — props by type, sentence neighborhood, hub badge, hide node, reachable-from"
```

---

## Self-Review Notes

1. **Spec coverage:** humanized props per type + gap warning → `renderProps`. `file:line` → `renderPanelBody`'s `.panel-fileline` line (needs Step 4's `makeElems` change to carry `file`/`line` at all). Sentence neighborhood, capped at 5, group labels non-clickable → `renderNeighborGroupLine`/`renderNeighborhood`. Hub badge with relative threshold → Step 5 + `renderPanelBody`'s `hubBadge`. Hide node, in-memory, F5-only-undo → `toggleHideNode` + Step 6's `makeElems` filtering. Reachable-from, reverse BFS, stops at hubs, origin exempt → `reachableFromEndpoints`. Raw toggle, additive not replacing → `togglePanelRaw`.
2. **Placeholder scan:** none found.
3. **Type consistency:** `reachableFromEndpoints` returns `{endpointIds, stoppedHubs}` — used identically in `renderReachableFrom`. `renderNeighborGroupLine(label, items)` where `items` is `{targetId, via}[]` — matches how both `outByType`/`inByType` populate it in `renderNeighborhood`. Every clickable element uses `data-nav="<id>"` + the single delegated click listener added in Step 8 — no per-element `onclick` needed, avoiding the inline-handler pattern used elsewhere in this file for anything panel-body-specific.
