# Endpoint Identity Fix (FLASS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `cc compile` from crashing when a real repo has two distinct handlers declaring the same `METHOD path` (e.g. two routers registered from different namespaces both exposing `GET /ecosystems/`), by making the handler's qualname part of the endpoint node's identity, and surfacing the underlying route ambiguity as a gap instead of silently merging or crashing.

**Architecture:** `endpoint` node ids grow a third segment — `endpoint:{METHOD}:{path}:{handler_qualname}` — so two different handlers can never collide on `id` even if `method`+`path` are identical. `graph/build.py`'s existing identity assertion stays untouched (it's still correct — it's the id that was insufficient, not the check). A new gap-detection pass groups all `endpoint` nodes in the final graph by `(method, path)` and emits one `unresolved_dynamic` gap per group with more than one member, since real disambiguation between them depends on router-registration order at runtime, not on anything visible per-handler in the source.

**Tech Stack:** Python stdlib only (`ast`, dataclasses already in use). No new dependencies.

## Global Constraints

- New endpoint `id` format: `endpoint:{METHOD}:{path}:{handler_qualname}` — exact, per spec §1a.
- `graph/build.py`'s node-identity assertion (raises `ValueError` on conflicting `file`/`line`/`hash` for the same `id`) must NOT be modified — it was doing its job correctly; the bug was in the `id`, not the check.
- Route ambiguity (two+ endpoint nodes sharing `(method, path)`) becomes a `kind="unresolved_dynamic"` gap, `severity={"comprehension": "warning", "compliance": "error"}` — flag, don't block, don't ask the dev to rewrite working code.
- The regression fixture for this bug must be synthetic (two toy routers, same subpath, different namespaces) — never real Corporate code.
- Every existing endpoint `id` in every repo this tool compiles (including agora) changes shape. This is an accepted, un-migrated side effect — hub marks in `localStorage` keyed by old endpoint ids go stale; no migration is being built for that.

---

### Task 1: Endpoint `id` includes the handler qualname

**Files:**
- Modify: `src/cc/extract/endpoints.py:119-121`
- Modify: `tests/test_endpoints.py:18-22`
- Modify: `doc_proyecto/ESQUEMA_POC.md:17-23`

**Interfaces:**
- Produces: `extract_endpoints()` now returns `endpoint` nodes whose `.id` is `f"endpoint:{method.upper()}:{full_path}:{handler_qname}"` (previously `f"endpoint:{method.upper()}:{full_path}"`). `handler_qname` is already computed at `endpoints.py:119` as `f"{module_qname}.{fn_node.name}"` — nothing new to derive, just reused in the id.

- [ ] **Step 1: Update the existing id-stability test to expect the new format**

In `tests/test_endpoints.py`, replace `test_endpoint_ids_are_stable`:

```python
def test_endpoint_ids_are_stable():
    nodes, _ = extract_endpoints(SIMPLE_API)
    ep_ids = {n.id for n in nodes if n.type == "endpoint"}
    assert "endpoint:POST:/messages/:main.create_message" in ep_ids
    assert "endpoint:GET:/messages/{msg_id}:main.get_message" in ep_ids
```

(`tests/fixtures/simple_api/main.py` defines `create_message` and `get_message` directly in `main.py`, and `extract_endpoints(SIMPLE_API)` is called with `repo_path=SIMPLE_API`, so `_module_qualname` resolves the module to `"main"` — hence `main.create_message` / `main.get_message`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_endpoints.py::test_endpoint_ids_are_stable -v`
Expected: FAIL — old ids (`endpoint:POST:/messages/`, no handler suffix) don't match the new assertions.

- [ ] **Step 3: Change the id construction**

In `src/cc/extract/endpoints.py`, change line 120:

```python
                ep_id = f"endpoint:{method.upper()}:{full_path}"
```

to:

```python
                ep_id = f"endpoint:{method.upper()}:{full_path}:{handler_qname}"
```

- [ ] **Step 4: Document the new id format in the schema doc**

In `doc_proyecto/ESQUEMA_POC.md`, immediately after the existing `line`/`hash` convention bullet (the one ending "...ningún extractor calcula su propio `line`/`hash` de forma independiente."), add a new bullet:

```markdown
- **Excepción de `id` para `endpoint`:** dos handlers distintos pueden declarar el mismo `method`+`path` (routers registrados desde namespaces distintos) — el qualname del handler entra en el `id` (`endpoint:{METHOD}:{path}:{handler_qualname}`) para que la identidad sea inequívoca incluso en ese caso. La ambigüedad de ruta en sí (dos handlers "compitiendo" por el mismo `method`+`path` aparente) se reporta como gap `unresolved_dynamic` — ver Huecos.
```

- [ ] **Step 5: Run the full test suite to verify the fix and check for unrelated breakage**

Run: `pytest -v`
Expected: PASS, 262+ tests (the id-format assertion in `test_pipeline.py::test_pipeline_finds_endpoint_node` only checks substrings `"POST"` and `"messages"` inside the id, so it's unaffected by the added suffix; every other `"endpoint:..."` reference in the test suite is a manually-constructed `Node` fixture, not the real extractor's output, so it's also unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/cc/extract/endpoints.py tests/test_endpoints.py doc_proyecto/ESQUEMA_POC.md
git commit -m "fix: include handler qualname in endpoint node id

Two handlers registered from different router namespaces can declare
the same METHOD+path (e.g. two routers both exposing GET /ecosystems/).
Method+path alone was insufficient identity — it collided in
graph/build.py's identity assertion and crashed the whole compile.
The handler's own qualname, already computed for the handles edge, now
disambiguates the id."
```

---

### Task 2: Ambiguous-route detection gap

**Files:**
- Modify: `src/cc/gaps.py`
- Modify: `tests/test_gaps.py`

**Interfaces:**
- Consumes: `Node` with `type == "endpoint"` and `props == {"method": str, "path": str, "handler": str}` (already produced by `extract_endpoints`, Task 1).
- Produces: `detect_gaps(graph)` now also returns one `Gap` per group of 2+ endpoint nodes sharing `(method, path)`, in addition to its existing `missing_artifact` table-column gaps.

- [ ] **Step 1: Write the failing tests**

In `tests/test_gaps.py`, add (keep the existing two tests as-is, add these after them):

```python
def test_two_endpoints_same_method_and_path_emit_ambiguity_gap():
    nodes = [
        Node(
            id="endpoint:GET:/ecosystems/:pkg_a.routes.list_ecosystems",
            type="endpoint",
            file="pkg_a/routes.py",
            line=10,
            hash="a" * 64,
            inferred=False,
            props={
                "method": "GET",
                "path": "/ecosystems/",
                "handler": "pkg_a.routes.list_ecosystems",
            },
        ),
        Node(
            id="endpoint:GET:/ecosystems/:pkg_b.routes.list_ecosystems",
            type="endpoint",
            file="pkg_b/routes.py",
            line=20,
            hash="b" * 64,
            inferred=False,
            props={
                "method": "GET",
                "path": "/ecosystems/",
                "handler": "pkg_b.routes.list_ecosystems",
            },
        ),
    ]
    graph = Graph(nodes=nodes, edges=[], gaps=[])
    gaps = detect_gaps(graph)
    ambiguous = [g for g in gaps if g.kind == "unresolved_dynamic"]
    assert len(ambiguous) == 1
    gap = ambiguous[0]
    assert gap.missing == (
        "ruta ambigua: 2 handlers declaran GET /ecosystems/; "
        "la desambiguación vive en el registro de routers"
    )
    assert gap.where == "pkg_a/routes.py:10; pkg_b/routes.py:20"
    assert gap.severity == {"comprehension": "warning", "compliance": "error"}


def test_single_endpoint_for_a_route_has_no_ambiguity_gap():
    nodes = [
        Node(
            id="endpoint:GET:/x:pkg.routes.handler",
            type="endpoint",
            file="pkg/routes.py",
            line=1,
            hash="a" * 64,
            inferred=False,
            props={"method": "GET", "path": "/x", "handler": "pkg.routes.handler"},
        ),
    ]
    graph = Graph(nodes=nodes, edges=[], gaps=[])
    gaps = detect_gaps(graph)
    assert not [g for g in gaps if g.kind == "unresolved_dynamic"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gaps.py -v`
Expected: `test_two_endpoints_same_method_and_path_emit_ambiguity_gap` FAILS (`detect_gaps` today only inspects `table` nodes, so `ambiguous` is empty); `test_single_endpoint_for_a_route_has_no_ambiguity_gap` passes trivially (nothing to implement yet, but keep it — it's the negative case that must stay true after Step 3).

- [ ] **Step 3: Implement ambiguous-route detection**

Replace the full contents of `src/cc/gaps.py`:

```python
from cc.graph.schema import Gap, Graph, Node


def _detect_ambiguous_endpoints(graph: Graph) -> list[Gap]:
    """Group endpoint nodes by (method, path) — the apparent route a runtime
    caller would use. A group with more than one member means the tool found
    multiple handlers that could apparently answer the same route; real
    disambiguation lives in the router-registration order, which is
    runtime-bound, not something a dev should be asked to change. Flag it,
    don't guess which one wins."""
    groups: dict[tuple[str, str], list[Node]] = {}
    for node in graph.nodes:
        if node.type != "endpoint":
            continue
        key = (node.props["method"], node.props["path"])
        groups.setdefault(key, []).append(node)

    gaps: list[Gap] = []
    for (method, path), nodes in sorted(groups.items()):
        if len(nodes) < 2:
            continue
        locations = sorted(f"{n.file}:{n.line}" for n in nodes)
        gaps.append(
            Gap(
                kind="unresolved_dynamic",
                where="; ".join(locations),
                node_id=None,
                missing=f"ruta ambigua: {len(nodes)} handlers declaran {method} {path}; "
                "la desambiguación vive en el registro de routers",
                suggested="",
                severity={"comprehension": "warning", "compliance": "error"},
            )
        )
    return gaps


def detect_gaps(graph: Graph) -> list[Gap]:
    gaps: list[Gap] = []
    for node in graph.nodes:
        if node.type != "table":
            continue
        if not node.props.get("columns"):
            gaps.append(
                Gap(
                    kind="missing_artifact",
                    where=f"{node.file}:{node.line}",
                    node_id=node.id,
                    missing=f"No columns inferred for table `{node.props['name']}`"
                    " — no CREATE TABLE, INSERT, or single-table SELECT found",
                    suggested=f"-- TODO: add DDL for `{node.props['name']}`, "
                    f"e.g. CREATE TABLE {node.props['name']} (id INT, ...)",
                    severity={"comprehension": "warning", "compliance": "error"},
                )
            )
    gaps.extend(_detect_ambiguous_endpoints(graph))
    return gaps
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gaps.py -v`
Expected: PASS, 4/4.

- [ ] **Step 5: Commit**

```bash
git add src/cc/gaps.py tests/test_gaps.py
git commit -m "feat: emit unresolved_dynamic gap for ambiguous METHOD+path routes

Two endpoint nodes now surviving the identity fix (previously a crash)
share a real signal worth surfacing: an auditor can't tell which
handler actually answers the route without reading the router
registration order. Flag it, don't guess."
```

---

### Task 3: Regression fixture, agora validation, and backlog entry

**Files:**
- Modify: `tests/test_pipeline.py`
- Modify: `BACKLOG.md`

**Interfaces:**
- Consumes: `run()` from `src/cc/pipeline.py` (unchanged signature), the id format from Task 1, the gap from Task 2.

- [ ] **Step 1: Write the FLASS regression fixture test**

In `tests/test_pipeline.py`, add:

```python
def test_two_routers_same_path_different_namespace_compiles_with_ambiguity_gap():
    # Regression fixture for the real-world crash this plan fixes: two
    # routers registered from different namespaces both declare
    # `GET /ecosystems/`. Before the endpoint-id fix (Task 1) this crashed
    # the whole compile via graph/build.py's identity assertion — same id,
    # different file/line/hash. Synthetic fixture — never real Corporate code.
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "team_a").mkdir(parents=True)
        (repo / "team_a" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "team_a" / "routes.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n\n"
            '@router.get("/ecosystems/")\n'
            "def list_ecosystems():\n    return []\n",
            encoding="utf-8",
        )
        (repo / "team_b").mkdir(parents=True)
        (repo / "team_b" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "team_b" / "routes.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n\n"
            '@router.get("/ecosystems/")\n'
            "def list_ecosystems():\n    return []\n",
            encoding="utf-8",
        )
        out = pathlib.Path(d) / "out"
        run(repo, out)  # must not raise — this is the crash this plan fixes

        data = json.loads((out / "graph.json").read_text())
        ep_nodes = [n for n in data["nodes"] if n["type"] == "endpoint"]
        assert len(ep_nodes) == 2
        assert len({n["id"] for n in ep_nodes}) == 2  # distinct ids despite identical method+path

        ambiguous = [
            g
            for g in data["gaps"]
            if g["kind"] == "unresolved_dynamic" and "ruta ambigua" in g["missing"]
        ]
        assert len(ambiguous) == 1
        assert ambiguous[0]["severity"] == {"comprehension": "warning", "compliance": "error"}
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `pytest tests/test_pipeline.py::test_two_routers_same_path_different_namespace_compiles_with_ambiguity_gap -v`
Expected: PASS. This is a regression/integration test, not new-feature TDD — Tasks 1 and 2 already implemented the underlying fix; this test proves they combine correctly end-to-end through the real pipeline (not just the unit-level `extract_endpoints`/`detect_gaps` tests) and stands as the permanent fixture for the exact bug class that motivated this plan.

- [ ] **Step 3: Add the named backlog entry (explicitly deferred, not implemented in this plan)**

Append to `BACKLOG.md`:

```markdown

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
```

- [ ] **Step 4: Run the full test suite**

Run: `pytest -v`
Expected: PASS, all tests green.

- [ ] **Step 5: Manually verify against agora**

Run:
```bash
cc compile /data/agora --out /tmp/agora-endpoint-id-check
```
Expected:
- Compile succeeds, no crash.
- Console output (or `graph.json`'s endpoint node count) shows 18 endpoint nodes — same count as the existing `18/18` oracle baseline in `CLAUDE.md`'s Phase 1 Acceptance Criteria.
- No new `unresolved_dynamic` "ruta ambigua" gaps in `graph.json` (agora has no duplicate routes).
- Spot-check a few endpoint ids in `graph.json` to confirm the only change vs. a prior compile is the new `:{handler_qualname}` suffix — function/model/table node ids are untouched.

- [ ] **Step 6: Commit**

```bash
git add tests/test_pipeline.py BACKLOG.md
git commit -m "test: add FLASS regression fixture for ambiguous-route endpoint ids

Synthetic two-router-same-path fixture proves the id fix (Task 1) and
the ambiguity gap (Task 2) combine correctly through the real pipeline
without crashing. Also records the deferred include_router-chain
resolution work this surfaced, as its own backlog entry."
```

---

## Self-Review Notes

- **Spec coverage:** §1a → Task 1. §1b → Task 2. §1c (accepted id churn, no migration) → documented in Global Constraints, no code change needed. Acceptance criteria 1 (FLASS repro, synthetic fixture) → Task 3 Steps 1-2. Acceptance criteria 2 (agora 18/18, zero new ambiguity gaps, diff scoped to endpoint ids) → Task 3 Step 5. Acceptance criteria 3 (assert untouched, test proves it) → already covered by existing `tests/test_graph.py::test_build_raises_on_conflicting_node_identity`, unmodified by this plan — confirmed by Step 4's full-suite run in Task 3.
- **Placeholder scan:** none found — every step has complete code.
- **Type consistency:** `Gap.node_id: str | None` — `_detect_ambiguous_endpoints` passes `None` (no single node owns a multi-node gap), consistent with the existing `node_id=None` precedent in `pipeline.py` for `tool_limitation` call-graph gaps.
