# Phase 2 Step 2 — notes.json Overlay + Regeneration Gate + `cc annotate` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `cc annotate <output-dir>` command that generates LLM why-notes for a compiled graph's endpoints and orchestrator functions, storing them in a separate `notes.json` overlay that regenerates only what changed.

**Architecture:** Six small, independently-testable modules under `src/cc/llm/` (source span extraction, graph-neighborhood serialization, prompt construction, notes overlay I/O + gate, role-based scope selection) composed by one orchestration function in `src/cc/annotate.py`, wired into `cli.py` as a new `annotate` subcommand. `graph.json` and the `compile` pipeline are never touched — this whole step only *reads* `graph.json` and *writes* a new `notes.json` file next to it.

**Tech Stack:** Python 3.11 stdlib (`ast`, `json`, `datetime`, `pathlib`), the `LLMClient` Protocol + `AnthropicClient` from Phase 2 Step 1.

## Global Constraints

- `graph.json` is byte-identical whether or not Phase 2 is used — nothing in this plan modifies `src/cc/graph/schema.py`, `src/cc/pipeline.py`, or `src/cc/render/emit.py`.
- `notes.json` lives at `<out_dir>/notes.json`, separate from `graph.json`, one entry per annotated node id: `{"text": str, "hash": str, "prompt_version": int, "model": str, "generated_at": str}`.
- Regeneration gate (spec §4): regenerate iff `force` is `True`, OR no existing entry, OR `existing["hash"] != current_node_hash`, OR `existing["prompt_version"] != PROMPT_VERSION`. `model` is recorded but never gates.
- Scope (spec §5): default batch = every `endpoint` node, plus every `function` node with `>=threshold` outgoing `calls` edges OR `>=threshold` distinct tables touched (`reads`+`writes` edges, deduped). `threshold` defaults to 2, configurable via `CC_LLM_ORCHESTRATOR_THRESHOLD`, never hardcoded past that one default.
- Automated tests use a hand-written fake implementing the `LLMClient` Protocol (`generate(self, system, user) -> str`) — **never call the real Anthropic API from the test suite.**
- Per-node failures (`LLMGenerationError`, or a `ValueError` from source-span extraction) are logged into the report and skip that node — they never abort the batch (spec §1: "una nota que falla no aborta el batch").
- `notes.json` is written after every successful generation, not only at the end of the batch, so a long `--all` run doesn't lose prior progress on an interruption.
- API keys are never touched directly in this plan — `run_annotate` receives an already-constructed `LLMClient` and a plain `model_name: str`, never the config's `api_key`.
- Node ids referenced in this plan (`"function:..."`, `"endpoint:..."`, `"table:..."`) follow the existing `id` format already produced by the extractors — this plan does not change how ids are built.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/cc/llm/source_span.py` | Given a source file + a line number, find the enclosing `def`/`async def`/`class` and return its exact source text. |
| `src/cc/llm/neighborhood.py` | Given the loaded graph (dict) and a node id, serialize its callers/callees/tables/reachable-endpoints as plain text for the prompt. |
| `src/cc/llm/prompt.py` | `PROMPT_VERSION` constant + system/user prompt builders implementing the anti-paraphrase rules (spec §6). |
| `src/cc/llm/notes.py` | Load/save `notes.json` + the regeneration-gate predicate (spec §4). |
| `src/cc/llm/scope.py` | Role-based target selection (spec §5) + the `CC_LLM_ORCHESTRATOR_THRESHOLD` config field. |
| `src/cc/annotate.py` | `run_annotate(...)` — orchestrates the five modules above into one batch run + report. |
| `src/cc/cli.py` | Modified: new `annotate` subcommand wiring CLI args → `load_config()` → provider dispatch → `run_annotate()`. |

---

### Task 1: Source span extraction

**Files:**
- Create: `src/cc/llm/source_span.py`
- Test: `tests/test_llm_source_span.py`

**Interfaces:**
- Produces: `get_source_span(file: str, line: int) -> str` — raises `ValueError` if no function/class definition starts at `line`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_source_span.py
import pytest

from cc.llm.source_span import get_source_span


def test_extracts_a_simple_function_body(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "x = 1\n"
        "\n"
        "def greet(name):\n"
        "    return f'hello {name}'\n"
        "\n"
        "y = 2\n",
        encoding="utf-8",
    )
    span = get_source_span(str(f), 3)
    assert span == "def greet(name):\n    return f'hello {name}'"


def test_extracts_an_async_function_body(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "async def fetch(x):\n"
        "    return await x()\n",
        encoding="utf-8",
    )
    span = get_source_span(str(f), 1)
    assert span == "async def fetch(x):\n    return await x()"


def test_extracts_a_method_by_its_own_lineno(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "class Service:\n"
        "    def run(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    span = get_source_span(str(f), 2)
    assert span == "    def run(self):\n        return 1"


def test_no_definition_at_line_raises_value_error(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("x = 1\ny = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mod.py:1"):
        get_source_span(str(f), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_source_span.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc.llm.source_span'`

- [ ] **Step 3: Implement**

```python
# src/cc/llm/source_span.py
import ast
import pathlib

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def get_source_span(file: str, line: int) -> str:
    """Return the exact source text of the def/class statement starting at `line`.

    `line` is 1-based and must match a node's own `.lineno` (decorators, if
    any, are excluded — Python's ast reports FunctionDef.lineno as the `def`
    keyword's line, not the decorator's, matching how node hashes are
    already computed in graph/hash_util.py).
    """
    path = pathlib.Path(file)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    match = None
    for node in ast.walk(tree):
        if isinstance(node, _DEF_TYPES) and node.lineno == line:
            match = node
            break

    if match is None:
        raise ValueError(f"No function/class definition found at {file}:{line}")

    lines = source.splitlines()
    return "\n".join(lines[line - 1 : match.end_lineno])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_source_span.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/cc/llm/source_span.py tests/test_llm_source_span.py
git commit -m "feat: add source span extractor for LLM annotation"
```

---

### Task 2: Graph neighborhood serialization

**Files:**
- Create: `src/cc/llm/neighborhood.py`
- Test: `tests/test_llm_neighborhood.py`

**Interfaces:**
- Consumes: a graph dict shaped like `json.loads(graph.json text)` — `{"nodes": [{"id", "type", "file", "line", "hash", "inferred", "props"}, ...], "edges": [{"from_", "to", "type", "inferred", "props"}, ...]}` (this is exactly `dataclasses.asdict(Graph)`, see `src/cc/render/emit.py:17`).
- Produces: `serialize_neighborhood(graph: dict, node_id: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_neighborhood.py
from cc.llm.neighborhood import serialize_neighborhood


def _node(id_, type_, props=None):
    return {"id": id_, "type": type_, "file": "f.py", "line": 1, "hash": "h", "inferred": False, "props": props or {}}


def _edge(from_, to, type_):
    return {"from_": from_, "to": to, "type": type_, "inferred": False, "props": {}}


def test_lists_callers_and_callees():
    graph = {
        "nodes": [
            _node("function:a", "function"),
            _node("function:b", "function"),
            _node("function:c", "function"),
        ],
        "edges": [
            _edge("function:a", "function:b", "calls"),
            _edge("function:b", "function:c", "calls"),
        ],
    }
    text = serialize_neighborhood(graph, "function:b")
    assert "Quién lo llama: function:a" in text
    assert "A qué llama: function:c" in text


def test_no_callers_or_callees_says_so():
    graph = {"nodes": [_node("function:a", "function")], "edges": []}
    text = serialize_neighborhood(graph, "function:a")
    assert "Quién lo llama: nadie" in text
    assert "A qué llama: nada" in text


def test_tables_include_columns():
    graph = {
        "nodes": [
            _node("function:a", "function"),
            _node("table:t", "table", props={"name": "t", "columns": ["id", "name"]}),
        ],
        "edges": [_edge("function:a", "table:t", "reads")],
    }
    text = serialize_neighborhood(graph, "function:a")
    assert "Tablas que lee: t(id, name)" in text
    assert "Tablas que escribe: ninguna" in text


def test_reachable_endpoints_via_backward_bfs():
    graph = {
        "nodes": [
            _node("endpoint:GET:/x", "endpoint"),
            _node("function:handler", "function"),
            _node("function:target", "function"),
        ],
        "edges": [
            _edge("function:handler", "function:target", "calls"),
        ],
    }
    # endpoint "handles" its handler via a `handles` edge, per ESQUEMA_POC.md
    graph["edges"].append(_edge("endpoint:GET:/x", "function:handler", "handles"))
    text = serialize_neighborhood(graph, "function:target")
    assert "Alcanzable desde estos endpoints: endpoint:GET:/x" in text


def test_reachability_stops_at_hub_nodes():
    # 5 distinct callers into "function:hub" makes it a hub (HUB_MIN_ABSOLUTE=5),
    # so the walk must not continue past it even though an endpoint calls it.
    nodes = [_node("function:hub", "function"), _node("function:target", "function")]
    edges = [_edge("function:hub", "function:target", "calls")]
    for i in range(5):
        caller_id = f"function:caller{i}"
        nodes.append(_node(caller_id, "function"))
        edges.append(_edge(caller_id, "function:hub", "calls"))
    nodes.append(_node("endpoint:GET:/x", "endpoint"))
    edges.append(_edge("endpoint:GET:/x", "function:caller0", "calls"))
    graph = {"nodes": nodes, "edges": edges}

    text = serialize_neighborhood(graph, "function:target")
    assert "Alcanzable desde estos endpoints: ninguno directamente" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_neighborhood.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc.llm.neighborhood'`

- [ ] **Step 3: Implement**

```python
# src/cc/llm/neighborhood.py
import math

_HUB_MIN_PERCENT = 0.15
_HUB_MIN_ABSOLUTE = 5


def _hub_ids(nodes: list[dict], edges: list[dict]) -> set[str]:
    """Mirror the render's hub detection (template_src.html) so the prompt's
    reachability description matches what a human sees in the UI panel."""
    function_count = sum(1 for n in nodes if n["type"] == "function")
    threshold = max(_HUB_MIN_ABSOLUTE, math.ceil(_HUB_MIN_PERCENT * function_count))
    in_degree: dict[str, int] = {}
    for e in edges:
        in_degree[e["to"]] = in_degree.get(e["to"], 0) + 1
    return {n["id"] for n in nodes if in_degree.get(n["id"], 0) >= threshold}


def _reachable_endpoints(nodes: list[dict], edges: list[dict], target_id: str) -> list[str]:
    by_id = {n["id"]: n for n in nodes}
    edges_to: dict[str, list[dict]] = {}
    for e in edges:
        edges_to.setdefault(e["to"], []).append(e)
    hub_ids = _hub_ids(nodes, edges)

    visited = {target_id}
    queue = [target_id]
    endpoint_ids: list[str] = []
    while queue:
        curr = queue.pop(0)
        for e in edges_to.get(curr, []):
            prev = e["from_"]
            if prev in visited:
                continue
            visited.add(prev)
            prev_node = by_id.get(prev)
            if prev_node and prev_node["type"] == "endpoint":
                endpoint_ids.append(prev)
            if prev in hub_ids:
                continue
            queue.append(prev)
    return endpoint_ids


def _table_line(label: str, table_ids: list[str], by_id: dict[str, dict]) -> str:
    if not table_ids:
        return f"{label}: ninguna"
    parts = []
    for tid in table_ids:
        t = by_id.get(tid)
        props = t["props"] if t else {}
        name = props.get("name", tid)
        cols = props.get("columns", [])
        parts.append(f"{name}({', '.join(cols)})" if cols else name)
    return f"{label}: " + ", ".join(parts)


def serialize_neighborhood(graph: dict, node_id: str) -> str:
    """Plain-text serialization of a node's graph neighborhood, for the LLM
    user prompt (spec §6 point 2). Deliberately mirrors the same adjacency
    the render's node panel already shows a human."""
    nodes = graph["nodes"]
    edges = graph["edges"]
    by_id = {n["id"]: n for n in nodes}

    callers = [e["from_"] for e in edges if e["type"] == "calls" and e["to"] == node_id]
    callees = [e["to"] for e in edges if e["type"] == "calls" and e["from_"] == node_id]
    reads = [e["to"] for e in edges if e["type"] == "reads" and e["from_"] == node_id]
    writes = [e["to"] for e in edges if e["type"] == "writes" and e["from_"] == node_id]
    endpoint_ids = _reachable_endpoints(nodes, edges, node_id)

    lines = [
        "Quién lo llama: " + (", ".join(callers) if callers else "nadie (dentro del grafo)"),
        "A qué llama: " + (", ".join(callees) if callees else "nada (dentro del grafo)"),
        _table_line("Tablas que lee", reads, by_id),
        _table_line("Tablas que escribe", writes, by_id),
        "Alcanzable desde estos endpoints: "
        + (", ".join(endpoint_ids) if endpoint_ids else "ninguno directamente"),
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_neighborhood.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/cc/llm/neighborhood.py tests/test_llm_neighborhood.py
git commit -m "feat: add graph neighborhood serializer for LLM prompts"
```

---

### Task 3: Prompt builder (anti-paraphrase, PROMPT_VERSION)

**Files:**
- Create: `src/cc/llm/prompt.py`
- Test: `tests/test_llm_prompt.py`

**Interfaces:**
- Produces: `PROMPT_VERSION: int` (starts at `1`), `build_system_prompt(extra_instructions: str | None) -> str`, `build_user_prompt(source_span: str, neighborhood_text: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_prompt.py
from cc.llm.prompt import PROMPT_VERSION, build_system_prompt, build_user_prompt


def test_prompt_version_starts_at_one():
    assert PROMPT_VERSION == 1


def test_system_prompt_forbids_paraphrasing_and_line_by_line_description():
    system = build_system_prompt(None)
    assert "PROHIBIDO" in system
    assert "línea a línea" in system


def test_system_prompt_appends_extra_instructions_when_present():
    system = build_system_prompt("Sé aún más breve.")
    assert system.endswith("Sé aún más breve.")


def test_system_prompt_without_extra_instructions_has_no_trailing_junk():
    system = build_system_prompt(None)
    assert system.strip() == system


def test_user_prompt_includes_source_span_and_neighborhood_verbatim():
    user = build_user_prompt("def f():\n    pass", "Quién lo llama: nadie")
    assert "def f():\n    pass" in user
    assert "Quién lo llama: nadie" in user
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_prompt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc.llm.prompt'`

- [ ] **Step 3: Implement**

```python
# src/cc/llm/prompt.py
PROMPT_VERSION = 1

_SYSTEM_PROMPT = """Eres un asistente de comprensión de código. Se te da el código fuente real de una pieza (función o endpoint) y su vecindario en un grafo de llamadas: quién la llama, a qué llama, qué tablas toca (con columnas) y desde qué endpoints es alcanzable.

Responde en español, en UN SOLO PÁRRAFO de máximo unas 80 palabras, explicando SOLO lo que el código no dice por sí mismo:
- por qué existe esta pieza como unidad separada,
- qué papel juega en los flujos que la atraviesan,
- qué se rompería o cambiaría si no existiera,
- si aplica, qué decisión de diseño revela (caché, transaccionalidad, ordenación, idempotencia…).

Tienes PROHIBIDO:
- re-describir lo que hace el código línea a línea,
- repetir el nombre de la función como si fuera una explicación,
- listar sus llamadas o las tablas que toca — eso ya lo muestra el grafo al lado.

Si no hay nada no-obvio que decir, responde con una frase corta reconociendo que el rol de la pieza es sencillo y se explica por su código y vecindario — nunca parafrasees para rellenar espacio."""


def build_system_prompt(extra_instructions: str | None) -> str:
    if extra_instructions:
        return _SYSTEM_PROMPT + "\n\n" + extra_instructions
    return _SYSTEM_PROMPT


def build_user_prompt(source_span: str, neighborhood_text: str) -> str:
    return (
        "Código fuente:\n"
        f"```python\n{source_span}\n```\n\n"
        "Vecindario en el grafo:\n"
        f"{neighborhood_text}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_prompt.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/cc/llm/prompt.py tests/test_llm_prompt.py
git commit -m "feat: add anti-paraphrase prompt builder (PROMPT_VERSION=1)"
```

---

### Task 4: notes.json overlay I/O + regeneration gate

**Files:**
- Create: `src/cc/llm/notes.py`
- Test: `tests/test_llm_notes.py`

**Interfaces:**
- Produces: `load_notes(path: pathlib.Path) -> dict`, `save_notes(path: pathlib.Path, notes: dict) -> None`, `needs_regeneration(existing: dict | None, current_hash: str, prompt_version: int, force: bool) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_notes.py
import json

from cc.llm.notes import load_notes, needs_regeneration, save_notes


def test_load_notes_missing_file_returns_empty_dict(tmp_path):
    assert load_notes(tmp_path / "notes.json") == {}


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "notes.json"
    notes = {"function:a": {"text": "x", "hash": "h1", "prompt_version": 1, "model": "m", "generated_at": "t"}}
    save_notes(path, notes)
    assert load_notes(path) == notes
    assert json.loads(path.read_text(encoding="utf-8")) == notes


def test_needs_regeneration_when_no_existing_entry():
    assert needs_regeneration(None, "h1", 1, force=False) is True


def test_needs_regeneration_when_hash_differs():
    existing = {"hash": "old", "prompt_version": 1}
    assert needs_regeneration(existing, "new", 1, force=False) is True


def test_needs_regeneration_when_prompt_version_differs():
    existing = {"hash": "h1", "prompt_version": 1}
    assert needs_regeneration(existing, "h1", 2, force=False) is True


def test_needs_regeneration_false_when_everything_matches():
    existing = {"hash": "h1", "prompt_version": 1}
    assert needs_regeneration(existing, "h1", 1, force=False) is False


def test_needs_regeneration_true_when_forced_even_if_matching():
    existing = {"hash": "h1", "prompt_version": 1}
    assert needs_regeneration(existing, "h1", 1, force=True) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_notes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc.llm.notes'`

- [ ] **Step 3: Implement**

```python
# src/cc/llm/notes.py
import json
import pathlib


def load_notes(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_notes(path: pathlib.Path, notes: dict) -> None:
    path.write_text(json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8")


def needs_regeneration(
    existing: dict | None, current_hash: str, prompt_version: int, force: bool
) -> bool:
    """Spec §4: regenerate iff forced, missing, hash drift, or a prompt_version bump."""
    if force:
        return True
    if existing is None:
        return True
    if existing.get("hash") != current_hash:
        return True
    if existing.get("prompt_version") != prompt_version:
        return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_notes.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/cc/llm/notes.py tests/test_llm_notes.py
git commit -m "feat: add notes.json overlay I/O and regeneration gate"
```

---

### Task 5: Role-based scope selection + configurable threshold

**Files:**
- Create: `src/cc/llm/scope.py`
- Modify: `src/cc/llm/config.py`
- Test: `tests/test_llm_scope.py`
- Test: `tests/test_llm_config.py` (extend)

**Interfaces:**
- Consumes: same graph dict shape as Task 2.
- Produces: `select_annotation_targets(graph: dict, threshold: int) -> list[str]`; `LLMConfig.orchestrator_threshold: int` (new field, default `2`, from `CC_LLM_ORCHESTRATOR_THRESHOLD`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_scope.py
from cc.llm.scope import select_annotation_targets


def _node(id_, type_):
    return {"id": id_, "type": type_, "file": "f.py", "line": 1, "hash": "h", "inferred": False, "props": {}}


def _edge(from_, to, type_):
    return {"from_": from_, "to": to, "type": type_, "inferred": False, "props": {}}


def test_all_endpoints_are_always_selected():
    graph = {
        "nodes": [_node("endpoint:GET:/x", "endpoint"), _node("function:leaf", "function")],
        "edges": [],
    }
    targets = select_annotation_targets(graph, threshold=2)
    assert "endpoint:GET:/x" in targets
    assert "function:leaf" not in targets


def test_function_selected_when_calls_out_meets_threshold():
    graph = {
        "nodes": [
            _node("function:orchestrator", "function"),
            _node("function:a", "function"),
            _node("function:b", "function"),
        ],
        "edges": [
            _edge("function:orchestrator", "function:a", "calls"),
            _edge("function:orchestrator", "function:b", "calls"),
        ],
    }
    targets = select_annotation_targets(graph, threshold=2)
    assert "function:orchestrator" in targets


def test_function_selected_when_tables_touched_meets_threshold():
    graph = {
        "nodes": [
            _node("function:writer", "function"),
            _node("table:t1", "table"),
            _node("table:t2", "table"),
        ],
        "edges": [
            _edge("function:writer", "table:t1", "reads"),
            _edge("function:writer", "table:t2", "writes"),
        ],
    }
    targets = select_annotation_targets(graph, threshold=2)
    assert "function:writer" in targets


def test_function_below_threshold_is_excluded():
    graph = {
        "nodes": [_node("function:leaf", "function"), _node("function:only_one", "function")],
        "edges": [_edge("function:leaf", "function:only_one", "calls")],
    }
    targets = select_annotation_targets(graph, threshold=2)
    assert "function:leaf" not in targets


def test_threshold_is_configurable_not_hardcoded():
    graph = {
        "nodes": [_node("function:a", "function"), _node("function:b", "function")],
        "edges": [_edge("function:a", "function:b", "calls")],
    }
    assert "function:a" in select_annotation_targets(graph, threshold=1)
    assert "function:a" not in select_annotation_targets(graph, threshold=2)
```

Extend `tests/test_llm_config.py` with:

```python
def test_orchestrator_threshold_defaults_to_two():
    config = load_config({"CC_LLM_PROVIDER": "anthropic", "CC_LLM_API_KEY": "k"})
    assert config.orchestrator_threshold == 2


def test_orchestrator_threshold_reads_from_env():
    config = load_config(
        {"CC_LLM_PROVIDER": "anthropic", "CC_LLM_API_KEY": "k", "CC_LLM_ORCHESTRATOR_THRESHOLD": "3"}
    )
    assert config.orchestrator_threshold == 3


def test_orchestrator_threshold_must_be_a_positive_integer():
    with pytest.raises(LLMConfigError, match="CC_LLM_ORCHESTRATOR_THRESHOLD"):
        load_config(
            {"CC_LLM_PROVIDER": "anthropic", "CC_LLM_API_KEY": "k", "CC_LLM_ORCHESTRATOR_THRESHOLD": "0"}
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_scope.py tests/test_llm_config.py -v`
Expected: FAIL — `test_llm_scope.py` with `ModuleNotFoundError: No module named 'cc.llm.scope'`; the three new `test_llm_config.py` tests FAIL with `TypeError: LLMConfig.__init__() got an unexpected keyword argument` or `AttributeError: 'LLMConfig' object has no attribute 'orchestrator_threshold'`.

- [ ] **Step 3: Implement**

```python
# src/cc/llm/scope.py
def select_annotation_targets(graph: dict, threshold: int) -> list[str]:
    """Spec §5: default batch scope. Every endpoint, plus every function that
    orchestrates — >=threshold outgoing calls, or >=threshold distinct tables
    touched (reads+writes deduped)."""
    nodes = graph["nodes"]
    edges = graph["edges"]

    calls_out: dict[str, set[str]] = {}
    tables_touched: dict[str, set[str]] = {}
    for e in edges:
        if e["type"] == "calls":
            calls_out.setdefault(e["from_"], set()).add(e["to"])
        elif e["type"] in ("reads", "writes"):
            tables_touched.setdefault(e["from_"], set()).add(e["to"])

    targets = []
    for n in nodes:
        if n["type"] == "endpoint":
            targets.append(n["id"])
        elif n["type"] == "function":
            out_count = len(calls_out.get(n["id"], ()))
            table_count = len(tables_touched.get(n["id"], ()))
            if out_count >= threshold or table_count >= threshold:
                targets.append(n["id"])
    return targets
```

Modify `src/cc/llm/config.py`:

1. Add `_DEFAULT_ORCHESTRATOR_THRESHOLD = 2` next to `_DEFAULT_MAX_TOKENS = 500` (line 8).
2. Add `orchestrator_threshold: int` as the last field of the `LLMConfig` dataclass (after `extra_instructions`).
3. In `load_config`, after the `max_tokens` block (currently ending at line 79) and before the `base_url` line (line 81), insert:

```python
    raw_threshold = values.get("CC_LLM_ORCHESTRATOR_THRESHOLD", "").strip()
    if not raw_threshold:
        orchestrator_threshold = _DEFAULT_ORCHESTRATOR_THRESHOLD
    else:
        try:
            orchestrator_threshold = int(raw_threshold)
        except ValueError as exc:
            raise LLMConfigError("CC_LLM_ORCHESTRATOR_THRESHOLD must be an integer") from exc
        if orchestrator_threshold <= 0:
            raise LLMConfigError("CC_LLM_ORCHESTRATOR_THRESHOLD must be a positive integer")
```

4. Add `orchestrator_threshold=orchestrator_threshold,` to the final `return LLMConfig(...)` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_scope.py tests/test_llm_config.py -v`
Expected: PASS (all tests, including the 3 new config tests)

Run: `pytest -q`
Expected: no regressions in the rest of the suite (existing `test_llm_config.py` tests must still pass unmodified — `orchestrator_threshold` has a default, so no existing call site breaks).

- [ ] **Step 5: Commit**

```bash
git add src/cc/llm/scope.py src/cc/llm/config.py tests/test_llm_scope.py tests/test_llm_config.py
git commit -m "feat: add role-based annotation scope with configurable threshold"
```

---

### Task 6: `cc annotate` orchestration + CLI wiring

**Files:**
- Create: `src/cc/annotate.py`
- Modify: `src/cc/cli.py`
- Test: `tests/test_annotate.py`
- Test: `tests/test_cli_annotate.py`

**Interfaces:**
- Consumes: `LLMClient` Protocol (`src/cc/llm/client.py`), `LLMGenerationError`, `get_source_span` (Task 1), `serialize_neighborhood` (Task 2), `PROMPT_VERSION`/`build_system_prompt`/`build_user_prompt` (Task 3), `load_notes`/`save_notes`/`needs_regeneration` (Task 4), `select_annotation_targets` (Task 5), `load_config`/`LLMConfigError` (Step 1), `AnthropicClient` (Step 1).
- Produces: `run_annotate(out_dir: pathlib.Path, client: LLMClient, model_name: str, extra_instructions: str | None = None, node_id: str | None = None, all_nodes: bool = False, force: bool = False, threshold: int = 2) -> dict` returning `{"generated": int, "cached": int, "failed": int, "failed_ids": list[str]}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_annotate.py
import json

import pytest

from cc.annotate import run_annotate
from cc.llm.client import LLMGenerationError
from cc.llm.prompt import PROMPT_VERSION


class FakeLLMClient:
    def __init__(self, responses=None, fail_on=()):
        self.responses = responses or {}
        self.fail_on = set(fail_on)
        self.calls = []

    def generate(self, system, user):
        self.calls.append((system, user))
        return "nota generada"


class FailingLLMClient:
    def __init__(self, fail_ids):
        self.fail_ids = set(fail_ids)
        self.call_count = 0

    def generate(self, system, user):
        self.call_count += 1
        raise LLMGenerationError("Anthropic generation failed: SimulatedError")


def _write_graph(out_dir):
    graph = {
        "nodes": [
            {
                "id": "endpoint:GET:/x",
                "type": "endpoint",
                "file": str(out_dir / "src.py"),
                "line": 1,
                "hash": "hash-a",
                "inferred": False,
                "props": {"method": "GET", "path": "/x", "handler": "mod.handler"},
            }
        ],
        "edges": [],
        "gaps": [],
        "exclusions": [],
    }
    (out_dir / "src.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    (out_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    return graph


def test_first_run_generates_and_second_run_is_fully_cached(tmp_path):
    _write_graph(tmp_path)
    client = FakeLLMClient()

    report1 = run_annotate(tmp_path, client, model_name="m")
    assert report1 == {"generated": 1, "cached": 0, "failed": 0, "failed_ids": []}
    assert len(client.calls) == 1

    report2 = run_annotate(tmp_path, client, model_name="m")
    assert report2 == {"generated": 0, "cached": 1, "failed": 0, "failed_ids": []}
    assert len(client.calls) == 1  # no new call


def test_hash_drift_regenerates_only_that_node(tmp_path):
    graph = _write_graph(tmp_path)
    client = FakeLLMClient()
    run_annotate(tmp_path, client, model_name="m")

    graph["nodes"][0]["hash"] = "hash-b"
    (tmp_path / "graph.json").write_text(json.dumps(graph), encoding="utf-8")

    report = run_annotate(tmp_path, client, model_name="m")
    assert report == {"generated": 1, "cached": 0, "failed": 0, "failed_ids": []}
    assert len(client.calls) == 2

    notes = json.loads((tmp_path / "notes.json").read_text(encoding="utf-8"))
    assert notes["endpoint:GET:/x"]["hash"] == "hash-b"


def test_prompt_version_bump_forces_full_regeneration(tmp_path):
    _write_graph(tmp_path)
    notes = {
        "endpoint:GET:/x": {
            "text": "vieja",
            "hash": "hash-a",
            "prompt_version": PROMPT_VERSION - 1,
            "model": "m",
            "generated_at": "t",
        }
    }
    (tmp_path / "notes.json").write_text(json.dumps(notes), encoding="utf-8")
    client = FakeLLMClient()

    report = run_annotate(tmp_path, client, model_name="m")
    assert report == {"generated": 1, "cached": 0, "failed": 0, "failed_ids": []}


def test_force_regenerates_even_when_everything_matches(tmp_path):
    _write_graph(tmp_path)
    client = FakeLLMClient()
    run_annotate(tmp_path, client, model_name="m")

    report = run_annotate(tmp_path, client, model_name="m", force=True)
    assert report == {"generated": 1, "cached": 0, "failed": 0, "failed_ids": []}
    assert len(client.calls) == 2


def test_node_id_targets_only_that_node(tmp_path):
    _write_graph(tmp_path)
    client = FakeLLMClient()
    report = run_annotate(tmp_path, client, model_name="m", node_id="endpoint:GET:/x")
    assert report == {"generated": 1, "cached": 0, "failed": 0, "failed_ids": []}


def test_failing_node_is_reported_and_does_not_raise(tmp_path):
    _write_graph(tmp_path)
    client = FailingLLMClient(fail_ids={"endpoint:GET:/x"})
    report = run_annotate(tmp_path, client, model_name="m")
    assert report == {"generated": 0, "cached": 0, "failed": 1, "failed_ids": ["endpoint:GET:/x"]}
    assert (tmp_path / "notes.json").exists() is False or json.loads(
        (tmp_path / "notes.json").read_text(encoding="utf-8")
    ) == {}


def test_notes_json_records_model_and_prompt_version(tmp_path):
    _write_graph(tmp_path)
    client = FakeLLMClient()
    run_annotate(tmp_path, client, model_name="claude-haiku-4-5")
    notes = json.loads((tmp_path / "notes.json").read_text(encoding="utf-8"))
    entry = notes["endpoint:GET:/x"]
    assert entry["model"] == "claude-haiku-4-5"
    assert entry["prompt_version"] == PROMPT_VERSION
    assert entry["text"] == "nota generada"
```

```python
# tests/test_cli_annotate.py
import json

from cc.cli import main


def _write_minimal_graph(out_dir):
    graph = {
        "nodes": [
            {
                "id": "endpoint:GET:/x",
                "type": "endpoint",
                "file": str(out_dir / "src.py"),
                "line": 1,
                "hash": "hash-a",
                "inferred": False,
                "props": {"method": "GET", "path": "/x", "handler": "mod.handler"},
            }
        ],
        "edges": [],
        "gaps": [],
        "exclusions": [],
    }
    (out_dir / "src.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    (out_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")


def test_annotate_reports_config_error_without_crashing(tmp_path, monkeypatch, capsys):
    _write_minimal_graph(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CC_LLM_PROVIDER", raising=False)
    monkeypatch.setattr("sys.argv", ["cc", "annotate", str(tmp_path)])

    main()

    captured = capsys.readouterr()
    assert "Config error" in captured.out
    assert "CC_LLM_PROVIDER" in captured.out


def test_annotate_reports_unimplemented_provider(tmp_path, monkeypatch, capsys):
    _write_minimal_graph(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CC_LLM_API_KEY", "k")
    monkeypatch.setattr("sys.argv", ["cc", "annotate", str(tmp_path)])

    main()

    captured = capsys.readouterr()
    assert "not implemented yet" in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_annotate.py tests/test_cli_annotate.py -v`
Expected: FAIL — `test_annotate.py` with `ModuleNotFoundError: No module named 'cc.annotate'`; `test_cli_annotate.py` with `SystemExit` / `argparse` error (`invalid choice: 'annotate'`), since the subcommand doesn't exist yet.

- [ ] **Step 3: Implement `src/cc/annotate.py`**

```python
# src/cc/annotate.py
import datetime
import json
import pathlib

from cc.llm.client import LLMClient, LLMGenerationError
from cc.llm.neighborhood import serialize_neighborhood
from cc.llm.notes import load_notes, needs_regeneration, save_notes
from cc.llm.prompt import PROMPT_VERSION, build_system_prompt, build_user_prompt
from cc.llm.scope import select_annotation_targets
from cc.llm.source_span import get_source_span


def run_annotate(
    out_dir: pathlib.Path,
    client: LLMClient,
    model_name: str,
    extra_instructions: str | None = None,
    node_id: str | None = None,
    all_nodes: bool = False,
    force: bool = False,
    threshold: int = 2,
) -> dict:
    out_dir = pathlib.Path(out_dir)
    graph = json.loads((out_dir / "graph.json").read_text(encoding="utf-8"))
    notes_path = out_dir / "notes.json"
    notes = load_notes(notes_path)
    by_id = {n["id"]: n for n in graph["nodes"]}

    if node_id is not None:
        target_ids = [node_id]
    elif all_nodes:
        target_ids = [n["id"] for n in graph["nodes"]]
    else:
        target_ids = select_annotation_targets(graph, threshold)

    report = {"generated": 0, "cached": 0, "failed": 0, "failed_ids": []}

    for tid in target_ids:
        node = by_id.get(tid)
        if node is None:
            report["failed"] += 1
            report["failed_ids"].append(tid)
            continue

        current_hash = node["hash"]
        existing = notes.get(tid)
        if not needs_regeneration(existing, current_hash, PROMPT_VERSION, force):
            report["cached"] += 1
            continue

        try:
            source_span = get_source_span(node["file"], node["line"])
            neighborhood_text = serialize_neighborhood(graph, tid)
            system = build_system_prompt(extra_instructions)
            user = build_user_prompt(source_span, neighborhood_text)
            text = client.generate(system, user)
        except (LLMGenerationError, ValueError):
            report["failed"] += 1
            report["failed_ids"].append(tid)
            continue

        notes[tid] = {
            "text": text,
            "hash": current_hash,
            "prompt_version": PROMPT_VERSION,
            "model": model_name,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        save_notes(notes_path, notes)
        report["generated"] += 1

    return report
```

- [ ] **Step 4: Wire the CLI — modify `src/cc/cli.py`**

Add a second subparser after the existing `comp` block (after line 45, before `args = parser.parse_args()` on line 47):

```python
    ann = sub.add_parser("annotate", help="Generate LLM why-notes overlay for a compiled graph")
    ann.add_argument(
        "out", type=pathlib.Path, help="Path to a compiled output directory (from `cc compile --out`)"
    )
    ann.add_argument(
        "--all", action="store_true", help="Annotate every node, not just the default role-based scope"
    )
    ann.add_argument("--node", metavar="NODE_ID", help="Annotate only this single node id (on-demand)")
    ann.add_argument(
        "--force", action="store_true", help="Regenerate even if hash and prompt_version already match"
    )
```

Add a new branch after the existing `if args.cmd == "compile":` block (the whole block currently spans lines 49-78; add this as a sibling `elif` right after it, still inside `main()`):

```python
    elif args.cmd == "annotate":
        from cc.llm.config import LLMConfigError, load_config

        try:
            config = load_config()
        except LLMConfigError as exc:
            print(f"Config error: {exc}")
            return

        if config.provider == "anthropic":
            from cc.llm.anthropic_adapter import AnthropicClient

            client = AnthropicClient(config)
        else:
            print(f"Provider {config.provider!r} is not implemented yet.")
            return

        report = run_annotate(
            args.out,
            client,
            model_name=config.model,
            extra_instructions=config.extra_instructions,
            node_id=args.node,
            all_nodes=args.all,
            force=args.force,
            threshold=config.orchestrator_threshold,
        )
        print(
            f"Generadas: {report['generated']}, Cacheadas: {report['cached']}, "
            f"Falladas: {report['failed']}"
        )
        if report["failed_ids"]:
            print("Nodos fallados:", ", ".join(report["failed_ids"]))
```

Add the import at the top of `src/cc/cli.py` (alongside `from cc.pipeline import run` on line 4):

```python
from cc.annotate import run_annotate
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_annotate.py tests/test_cli_annotate.py -v`
Expected: PASS (7 + 2 = 9 passed)

Run: `pytest -q`
Expected: full suite green, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/cc/annotate.py src/cc/cli.py tests/test_annotate.py tests/test_cli_annotate.py
git commit -m "feat: add cc annotate command (notes.json overlay batch generation)"
```

---

## Manual Verification (outside the automated suite)

After all 6 tasks are merged, the acceptance-criteria items that need a human ("Josem") and/or real API spend are **not** part of this plan's scope and must wait for explicit go-ahead, same as Step 1's smoke test:

- Running `cc annotate` for real against a compiled `agora` output (spends real API budget across potentially dozens of nodes).
- Spec acceptance criterion 7 (anti-paraphrase human eval over ~10 real notes) — this is explicitly Step 3's job ("iteración contra agora real"), not this plan's.

Do not run `cc annotate` against a real compiled repo without the user's explicit go-ahead, for the same reason Step 1's `llm_smoke_test.py` was never executed automatically.
