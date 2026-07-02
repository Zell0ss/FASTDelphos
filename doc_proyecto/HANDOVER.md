# Comprehension Compiler — Diseño / Handover a Claude Code

> **Para:** Claude Code. **Estado:** diseño cerrado, listo para implementar.
> **Contrato del grafo:** `esquema-grafo-poc.md` (autoritativo — los tipos de nodo/arista/gap viven ahí).
> **Decisiones marcadas `[CCode confirma]` son defaults razonables, ajustables.**

---

## 1. Qué es (y qué no)

Un **compilador de comprensión**: lee un repo (FastAPI en fase 1), extrae su estructura de forma determinista y la compila en un grafo navegable. **No es un generador de documentación** — la documentación describe; esto traduce la estructura muerta del código en navegación del razonamiento (qué toca qué, qué fluye a dónde).

## 2. Principios (no negociables)

- **Source-only, cero infra.** Lee ficheros de fuente. Nunca conecta a BD, secrets ni servicios. Nunca importa la app del target en producción (ver §6 introspección).
- **Read-only sobre el target.** El tool nunca escribe en el repo analizado. Output va a su propio dir.
- **Determinista primero; LLM racionado y marcado** (`inferred`). Fase 1 = cero LLM.
- **Comportamiento de compilador.** Lo que no sale de la fuente no se adivina: se declara como *gap* y se pide la señal que falta (litmus y estructura en `esquema-grafo-poc.md` §Huecos).

## 3. Topología

Repo propio, reutilizable. agora es solo input.

```
compile <ruta-al-repo> --out <dir>
  → lee fuente (read-only)
  → grafo JSON + reporte de gaps
  → render HTML/Cytoscape (single file, lee el JSON)
```

## 4. Stack del tool  `[CCode confirma versiones]`

- **Python 3.11**, `.venv`, `pyproject.toml`.
- **griffe** — inventario de símbolos + firmas (nodos `function`/`model`, serializa a JSON).
- **`ast` (stdlib)** — extracción de decoradores `@router.*`/`@app.*`, anotaciones del handler, y localización de call sites de BD.
- **pyan3** — call graph best-effort (aristas `calls`, `inferred=false` con agujeros).
- **sqlglot** (o sqlparse — `[CCode elige]`) — parsear queries: tabla(s), columnas, operación (SELECT=read, INSERT/UPDATE/DELETE=write).
- **hashlib (stdlib)** — `hash` por nodo (tramo de fuente).
- **Cytoscape.js (CDN)** — render. Sin build.

## 5. Pipeline

```
adapter (fastapi)  →  extractores  →  build grafo  →  gaps  →  render
```

- **adapter**: sabe qué es "punto de entrada" para el tipo de repo. Fase 1: `fastapi` (ruta = endpoint).
- **extractores**: endpoints, models, calls, sql → nodos y aristas (esquema autoritativo).
- **build grafo**: ensambla nodos+aristas, asigna `id` estable (qualname+path) y `hash` (contenido).
- **gaps**: lo no inferible → reporte de legibilidad (no se inventa).
- **render**: HTML+Cytoscape; clic en nodo → panel (source span, props, aristas) = drill-down Nivel 1.

### Layout sugerido `[CCode ajusta]`

```
comprehension-compiler/
  pyproject.toml
  src/cc/
    cli.py
    pipeline.py
    adapters/{base.py, fastapi.py, generic.py(stub)}
    extract/{_collect.py, endpoints.py, models.py, calls.py, sql.py}
    graph/{schema.py, build.py}
    gaps.py
    render/{template.html, emit.py}
  tests/
```

`_collect.py` — helper compartido por todos los extractores. Devuelve `.py` del repo excluyendo `.venv`, `__pycache__`, `.git`, `node_modules`, `tests`, `dist`, `build`. Todos los extractores DEBEN usarlo; no usar `rglob("*.py")` directamente o se scanea el `.venv` del target.

## 6. Introspección runtime — solo como ORÁCULO en fase 1

La vía de producción para endpoints/models es **puro estático** (parsear decoradores + anotaciones). NO importar la app.

Excepción controlada: agora boota limpio en dev, así que CCode puede importar la app **una sola vez** (`app.routes`/`app.openapi()`) para producir un *ground truth* y medir cuánto recupera el extractor estático (diff = tasa de recuperación de rutas). Es un checker del POC, **no** la vía de producción. En BNP no existe.

**Requisitos de la introspección oráculo** (implementados en `oracle.py`):
- El módulo de la app puede estar en un sub-paquete del repo (p.ej. `backend/main.py`, no `main.py` en raíz). El oracle descubre los sub-paquetes top-level y prueba `{pkg}.main`, `{pkg}.app`, `{pkg}.server`.
- El `.venv` del target repo se añade a `sys.path` para que las dependencias de la app sean importables (p.ej. `aiomysql`).
- Se hace `os.chdir(repo_path)` antes de importar para que pydantic-settings encuentre el `.env` del repo target.
- `app.openapi()` (no `app.routes`) — resuelve todos los sub-routers y devuelve paths completos.

---

## FASE 1 — Mapa determinista sobre agora

`fastapi` adapter · puro estático · sin LLM.

### Construye

- CLI `compile <path> --out <dir>`.
- **Endpoints**: decorador (método, path), handler (qualname), router prefixes resueltos estáticamente. Aristas `handles`.
- **Models**: clases Pydantic referenciadas en firmas del handler → nodos `model` con `fields[]`. Aristas `uses_model` (`direction` in/out) desde las anotaciones.
- **Calls**: pyan3 best-effort. Agujeros dinámicos → `inferred`, no gap. Ficheros que crashean pyan3 (código a nivel de módulo que el parser no digiere) → gap `tool_limitation` visible, no bloqueo silencioso. `extract_calls()` devuelve `(edges, [(file, error)])` — el pipeline convierte el segundo elemento en gaps.
- **Tables/columns**: sqlglot sobre los call sites de aiomysql → nodos `table`, aristas `reads`/`writes` con `via`. Columnas: `CREATE TABLE` si está → si no, INSERT/UPDATE → SELECT de una tabla. No inferible → **gap** `missing_artifact`.
- **Grafo JSON** conforme a `esquema-grafo-poc.md` (incluye `id`/`hash`/`inferred` y `gaps[]`).
- **Render** HTML/Cytoscape: lista de endpoints como puerta, drill-down a nodo, gaps visibles.

### Criterios de aceptación

1. Las 3 preguntas eval (ver esquema §Test) se responden navegando el grafo más rápido que `grep`+leer.
2. Toda `table` sin columnas inferibles aparece como gap accionable, no como nodo vacío.
3. Se reporta la tasa de recuperación de rutas (estático vs introspección-oráculo).
4. Josem valida los recorridos `calls` recuperados contra agora real.

### Fuera de fase 1

LLM/Capa 3 · linaje profundo (taint) · adapter `generic` · multi-repo.

---

## FASE 2 — Capa de comprensión (LLM / Capa 3)

Enchufa sobre los anchors de fase 1. Haiku (casa) / Qwen (BNP) — intercambiables, mismo slot.

### Construye

- **Why-notes por nodo**: un párrafo de "por qué" donde la estructura no habla sola. Generado **una vez**, cacheado, keyed por `id`+`hash`. `inferred=true`.
- **Hash-gate**: regenera solo si `hash` actual ≠ `hash` con el que se generó la nota (drift). No re-tira el dado a código fijo → sin ruido ni coste por commit.
- **Gap-fill de calls** asistido por LLM: rellena agujeros dinámicos del call graph, `inferred=true`. **Solo modo comprensión; nunca alimenta compliance.**
- **Render**: notas inline, **visualmente distintas** de lo determinista (lo `inferred` se ve que es inferido — honestidad en la UI).

### Criterios de aceptación

1. Las why-notes NO se regeneran si el código no cambió (gate de hash demostrable).
2. Todo contenido `inferred` está marcado en la UI, distinguible de lo determinista.
3. Coste acotado: solo regeneran los nodos cuyo `hash` cambió.

### Fuera de fase 2

Linaje profundo · adapter `generic`.

> **Alternativa de fase 2** si se prioriza anchura sobre profundidad: adapter `generic` (puro estático para repos no-FastAPI: Sebastian, claude-redditor). Intercambiable con esta fase sin tocar fase 1.

---

## Notas de handover

- El grafo se **regenera desde el código cada run** (build artifact, no BD). Añadir tipos de nodo/arista luego es barato; lo caro de cambiar son los anchors (`id`/`hash`/`inferred`) → respétalos.
- Empezar por el **extractor estático de endpoints** (primera pieza con valor verificable) y el oráculo de introspección en paralelo para medir.
- agora: FastAPI + aiomysql + MariaDB (`tertulia_db`), SQL en queries puras (sin ORM). Tablas conocidas: `profiles, channels, channel_profiles, messages, summaries, channel_syntheses`.