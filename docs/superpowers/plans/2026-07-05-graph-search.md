# Graph Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a search box to the render UI (`src/cc/render/template_src.html`) that lets a user jump straight to a node, or a field/column *inside* a node, by substring match — per `docs/superpowers/specs/2026-07-05-graph-search-design.md`.

**Architecture:** All logic lives inline in `template_src.html`'s existing `<script>` block (no new files, no build step — the project's single-self-contained-HTML constraint). A flat `SEARCH_INDEX` array is built once at load from `GRAPH.nodes` (one entry per node, plus one per `model.fields[]`/`table.columns[]` entry). A live dropdown filters it on every keystroke (substring, case-insensitive, 2-char minimum). Clicking a result either centers+flashes the node if it's already on screen, or loads a fresh one-hop-both-directions neighborhood around it (same lookup tables the existing double-tap-expand already uses) — either way ending with the node's side panel open.

**Tech Stack:** Vanilla JS, Cytoscape.js (already vendored). No new dependencies. Node.js (already available in this environment) is used only as an ad-hoc syntax/logic sanity check during implementation — it is not part of the project's test suite (this project has no JS test runner; see Global Constraints).

## Global Constraints

- **No new files, no build step.** Everything goes inline into `src/cc/render/template_src.html`'s existing `<style>`/`<script>` blocks. `src/cc/render/emit.py` is not touched — it already interpolates `GRAPH`/the vendored JS files into this template unchanged.
- **Matching is substring, case-insensitive only.** No fuzzy matching, no scoring/ranking. Minimum 2 characters before searching (avoids a huge dropdown on 1 keystroke).
- **No result cap.** ~150 nodes plus their fields/columns is small enough to leave unbounded and scrollable for v1.
- **This project has no JS test framework.** The only automated tests for `template_src.html` are structural Python assertions in `tests/test_render.py` (does the emitted HTML contain certain strings) — matching the existing style in that file (`test_html_references_cytoscape`, `test_html_embeds_graph_json`). Do not introduce a new JS testing tool as part of this task.
- **Flash/highlight styling must be a Cytoscape style rule, not plain CSS** — Cytoscape renders nodes to a `<canvas>`, so a `.search-hit` class only has visual effect if it's registered in the `cy` instance's own `style: [...]` array (`cy.style().selector(...)` or the init array), not via a normal CSS class in the stylesheet.

---

### Task 1: Search index, dropdown UI, and navigation

**Files:**
- Modify: `src/cc/render/template_src.html`
- Test: `tests/test_render.py` (append)

**Interfaces:**
- Produces (all inline JS functions in the template, callable from the browser console for manual verification): `buildSearchIndex()`, `searchGraph(query)`, `goToSearchResult(entry)`, `renderSearchDropdown(matches, query)`, `closeSearchDropdown()`.

- [ ] **Step 1: Write the failing structural test**

Append to `tests/test_render.py`:

```python
def test_html_includes_search_ui():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        html = (pathlib.Path(d) / "index.html").read_text()
        assert 'id="search-input"' in html
        assert 'id="search-dropdown"' in html
        assert "function buildSearchIndex" in html
        assert "function searchGraph" in html
        assert "function goToSearchResult" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_render.py -v -k search`
Expected: FAIL — none of these strings exist in the template yet.

- [ ] **Step 3: Add CSS**

In `src/cc/render/template_src.html`, inside the existing `<style>` block, find this block (it's the last rule before `</style>`):

```css
    #expand-hint {
      position: absolute; bottom: 8px; left: 50%; transform: translateX(-50%);
      font-size: 10px; color: #445; pointer-events: none; display: none;
      background: #1a1a2ecc; padding: 2px 8px; border-radius: 3px;
    }
  </style>
```

Replace it with (adds the new rules, keeps the closing `</style>` at the end):

```css
    #expand-hint {
      position: absolute; bottom: 8px; left: 50%; transform: translateX(-50%);
      font-size: 10px; color: #445; pointer-events: none; display: none;
      background: #1a1a2ecc; padding: 2px 8px; border-radius: 3px;
    }

    #search-wrap { position: relative; margin-bottom: 10px; }
    #search-input {
      width: 100%; padding: 5px 8px; background: #12122a; border: 1px solid #333;
      color: #e0e0e0; font-family: monospace; font-size: 12px; border-radius: 3px;
      box-sizing: border-box;
    }
    #search-input:focus { outline: none; border-color: #7755aa; }
    #search-dropdown {
      position: absolute; top: 100%; left: 0; right: 0; z-index: 10;
      background: #1a1a2e; border: 1px solid #333; border-top: none;
      max-height: 260px; overflow-y: auto; display: none;
    }
    .search-row {
      display: flex; align-items: center; gap: 6px; padding: 4px 8px;
      font-size: 11px; cursor: pointer; border-bottom: 1px solid #252545;
    }
    .search-row:hover { background: #252545; }
    .search-row.no-results { cursor: default; color: #556; font-style: italic; }
    .search-row.no-results:hover { background: transparent; }
    .search-kind {
      font-size: 9px; padding: 1px 4px; border-radius: 3px; flex-shrink: 0;
      background: #333; color: #99a;
    }
    .search-kind.field, .search-kind.column { background: #2a3a2a; color: #7c7; }
  </style>
```

- [ ] **Step 4: Add the HTML markup**

In `src/cc/render/template_src.html`, find:

```html
  <div id="sidebar">
    <div style="font-size:13px;font-weight:bold;color:#aad;margin-bottom:4px">Comprehension Compiler</div>

    <div class="section-title">Endpoints</div>
```

Replace it with:

```html
  <div id="sidebar">
    <div style="font-size:13px;font-weight:bold;color:#aad;margin-bottom:4px">Comprehension Compiler</div>

    <div id="search-wrap">
      <input type="text" id="search-input" placeholder="Buscar nodo, tabla, columna, campo…" autocomplete="off">
      <div id="search-dropdown"></div>
    </div>

    <div class="section-title">Endpoints</div>
```

- [ ] **Step 5: Add the Cytoscape `.search-hit` style rule**

In `src/cc/render/template_src.html`, find:

```js
        { selector: 'node.root-node', style: { 'border-width': 3, 'border-color': '#7755aa', width: 40, height: 40 } },
```

Add immediately after it:

```js
        { selector: 'node.root-node', style: { 'border-width': 3, 'border-color': '#7755aa', width: 40, height: 40 } },
        { selector: 'node.search-hit', style: { 'border-width': 4, 'border-color': '#ffdd44' } },
```

- [ ] **Step 6: Add `buildSearchIndex()` and `searchGraph()`**

In `src/cc/render/template_src.html`, find the end of the "Fast lookup" block:

```js
    const edgesFrom = {}, edgesTo = {};
    for (const e of GRAPH.edges) {
      (edgesFrom[e.from_] = edgesFrom[e.from_] || []).push(e);
      (edgesTo[e.to]      = edgesTo[e.to]      || []).push(e);
    }
```

Add immediately after it:

```js

    // ── Search index ─────────────────────────────────────────────────────────
    function buildSearchIndex() {
      const index = [];
      for (const n of GRAPH.nodes) {
        const label = short(n.id);
        index.push({ matchText: label, kind: 'node', nodeId: n.id, label });

        if (n.type === 'model' && Array.isArray(n.props.fields)) {
          for (const f of n.props.fields) {
            index.push({
              matchText: f.name, kind: 'field', nodeId: n.id,
              label: `campo ${f.name} en ${label}`,
            });
          }
        }
        if (n.type === 'table' && Array.isArray(n.props.columns)) {
          for (const col of n.props.columns) {
            index.push({
              matchText: col, kind: 'column', nodeId: n.id,
              label: `columna ${col} en ${label}`,
            });
          }
        }
      }
      return index;
    }

    const SEARCH_INDEX = buildSearchIndex();

    function searchGraph(query) {
      const q = query.trim().toLowerCase();
      if (q.length < 2) return [];
      return SEARCH_INDEX.filter(e => e.matchText.toLowerCase().includes(q));
    }
```

Note: `short()` is defined later in this same file (`function short(id) { ... }`) — this works because `function` declarations are hoisted in JavaScript; `buildSearchIndex()` is only *called* after the whole script has parsed, by which point `short` exists.

- [ ] **Step 7: Add `goToSearchResult()`**

In `src/cc/render/template_src.html`, find the end of the `loadEndpoint` function:

```js
      hidePlaceholder();
      document.getElementById('expand-hint').style.display = '';
    }

    // ── Mode switch ───────────────────────────────────────────────────────────
```

Replace it with:

```js
      hidePlaceholder();
      document.getElementById('expand-hint').style.display = '';
    }

    // ── Search navigation ─────────────────────────────────────────────────────
    function flashNode(ele) {
      ele.addClass('search-hit');
      setTimeout(() => ele.removeClass('search-hit'), 600);
    }

    function goToSearchResult(entry) {
      const nodeId   = entry.nodeId;
      const existing = cy.getElementById(nodeId);

      if (existing.length) {
        cy.center(existing);
        flashNode(existing);
        showPanel(existing.data());
        return;
      }

      const oneHop = new Set([nodeId]);
      for (const e of (edgesFrom[nodeId] || [])) oneHop.add(e.to);
      for (const e of (edgesTo[nodeId]   || [])) oneHop.add(e.from_);

      currentMode = 'subgraph';
      currentRoot = nodeId;
      if (activeEpBtn) { activeEpBtn.classList.remove('active'); activeEpBtn = null; }
      document.getElementById('btn-subgraph').classList.add('active');
      document.getElementById('btn-map').classList.remove('active');

      cy.elements().remove();
      cy.add(makeElems(oneHop, nodeId));
      restoreEdgeLabels();
      applyLayout(false);
      cy.fit(cy.elements(), 40);
      hidePlaceholder();
      document.getElementById('expand-hint').style.display = '';

      const added = cy.getElementById(nodeId);
      flashNode(added);
      showPanel(added.data());
    }

    // ── Mode switch ───────────────────────────────────────────────────────────
```

- [ ] **Step 8: Add dropdown rendering + event wiring**

In `src/cc/render/template_src.html`, find the end of the "Endpoint list" block:

```js
    for (const n of GRAPH.nodes.filter(n => n.type === 'endpoint')) {
      const btn = document.createElement('button');
      btn.className = 'ep-btn';
      btn.innerHTML = `<span class="badge ${n.props.method}">${n.props.method}</span>${n.props.path}`;
      btn.onclick = () => loadEndpoint(n.id, btn);
      epList.appendChild(btn);
    }
```

Add immediately after it:

```js

    // ── Search UI wiring ──────────────────────────────────────────────────────
    function renderSearchDropdown(matches, query) {
      const dd = document.getElementById('search-dropdown');
      if (!query || query.trim().length < 2) { dd.style.display = 'none'; dd.innerHTML = ''; return; }

      if (!matches.length) {
        dd.innerHTML = '<div class="search-row no-results">sin resultados</div>';
        dd.style.display = '';
        return;
      }

      const kindLabel = { node: 'nodo', field: 'campo', column: 'columna' };
      dd.innerHTML = matches.map((m, i) =>
        `<div class="search-row" data-idx="${i}">` +
        `<span class="search-kind ${m.kind}">${kindLabel[m.kind]}</span>` +
        `<span>${m.label}</span></div>`
      ).join('');
      dd.style.display = '';

      dd.querySelectorAll('.search-row[data-idx]').forEach(row => {
        row.onclick = () => {
          goToSearchResult(matches[Number(row.dataset.idx)]);
          document.getElementById('search-input').value = '';
          closeSearchDropdown();
        };
      });
    }

    function closeSearchDropdown() {
      const dd = document.getElementById('search-dropdown');
      dd.style.display = 'none';
      dd.innerHTML = '';
    }

    document.getElementById('search-input').addEventListener('input', evt => {
      renderSearchDropdown(searchGraph(evt.target.value), evt.target.value);
    });

    document.getElementById('search-input').addEventListener('keydown', evt => {
      if (evt.key === 'Escape') {
        evt.target.value = '';
        closeSearchDropdown();
      }
    });

    document.addEventListener('click', evt => {
      if (!document.getElementById('search-wrap').contains(evt.target)) closeSearchDropdown();
    });
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/test_render.py -v`
Expected: PASS — all tests in the file, including the new one.

- [ ] **Step 10: Run the full suite**

Run: `pytest -q`
Expected: PASS, previous count plus 1.

- [ ] **Step 11: Syntax-check the emitted JS with Node**

This project has no JS test runner, but Node (already available in this environment) can at least parse-check the emitted script for syntax errors (typos, mismatched braces) without executing it — `node --check` only parses, it does not run the code, so references to browser-only globals (`document`, `cytoscape`) are fine.

```bash
python -m cc compile /data/agora --out /tmp/search-verify
python3 -c "
import re
html = open('/tmp/search-verify/index.html').read()
script = re.search(r'<script>\s*const GRAPH.*?</script>', html, re.DOTALL).group(0)
script = script.replace('<script>', '').replace('</script>', '')
open('/tmp/search-verify/_extracted.js', 'w').write(script)
"
node --check /tmp/search-verify/_extracted.js
```
Expected: no output from `node --check` (silence means valid syntax). If it reports a `SyntaxError`, fix the code — do not skip this check.

- [ ] **Step 12: Sanity-check the pure logic functions against real agora data**

Still using Node, load the actual compiled `graph.json` and exercise `buildSearchIndex`/`searchGraph` directly (no DOM needed for these two functions):

```bash
node -e "
const fs = require('fs');
const GRAPH = JSON.parse(fs.readFileSync('/tmp/search-verify/graph.json', 'utf8'));
function short(id) { return id.split(':').slice(1).join(':'); }
function buildSearchIndex() {
  const index = [];
  for (const n of GRAPH.nodes) {
    const label = short(n.id);
    index.push({ matchText: label, kind: 'node', nodeId: n.id, label });
    if (n.type === 'model' && Array.isArray(n.props.fields)) {
      for (const f of n.props.fields) index.push({ matchText: f.name, kind: 'field', nodeId: n.id, label: \`campo \${f.name} en \${label}\` });
    }
    if (n.type === 'table' && Array.isArray(n.props.columns)) {
      for (const col of n.props.columns) index.push({ matchText: col, kind: 'column', nodeId: n.id, label: \`columna \${col} en \${label}\` });
    }
  }
  return index;
}
const SEARCH_INDEX = buildSearchIndex();
function searchGraph(query) {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return [];
  return SEARCH_INDEX.filter(e => e.matchText.toLowerCase().includes(q));
}
console.log('total index entries:', SEARCH_INDEX.length);
console.log(JSON.stringify(searchGraph('cost_usd'), null, 2));
"
```
Expected: `searchGraph('cost_usd')` returns at least one entry with `kind: "column"` and `nodeId: "table:messages"` (this is the eval question 1 case — `cost_usd` must resolve here). If it returns `[]`, STOP — either `cost_usd` isn't in agora's compiled `table:messages.columns` (check `graph.json` directly) or the index-building logic has a bug; do not proceed to commit until this specific case resolves correctly.

- [ ] **Step 13: Commit**

```bash
git add src/cc/render/template_src.html tests/test_render.py
git commit -m "feat: add search box to render UI (nodes, model fields, table columns)"
```

---

## Self-Review Notes

1. **Spec coverage:** index over nodes+fields+columns → Step 6. Substring/case-insensitive/2-char-minimum → `searchGraph`. Live dropdown, grouped display, no-results row → Step 8. Escape clears+closes, click-outside closes → Step 8's event listeners. Navigate: center+flash if on-screen, else one-hop-both-directions fresh load → Step 7 (`goToSearchResult`). Panel opens on landing → both branches of `goToSearchResult` end in `showPanel(...)`. Cytoscape-style (not CSS-class) flash → Step 5.
2. **Placeholder scan:** none found.
3. **Type consistency:** `entry` shape (`{matchText, kind, nodeId, label}`) is identical between `buildSearchIndex` (Step 6), `renderSearchDropdown`'s row click handler (Step 8), and `goToSearchResult`'s parameter usage (Step 7) — `entry.nodeId` is the only field `goToSearchResult` reads, matching what `buildSearchIndex` always sets.
