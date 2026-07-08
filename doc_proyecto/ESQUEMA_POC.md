# Comprehension Compiler — Esquema del grafo (v0 / POC)

> Artefacto de diseño. Property graph que produce el extractor. Contra esto se construye el POC.
> Scope POC: adapter `fastapi` sobre **agora**, extracción determinista, sin LLM, sin tocar BD.

---

## Principios

- **Source-only, cero infra.** El tool lee código y nada más. Nunca conecta a BD, secrets ni servicios. Esto es lo que lo hace instalable en Corporate sin preparativos.
- **Determinista primero; LLM racionado y siempre marcado** (`inferred`). El LLM queda fuera del POC.
- **El grafo se regenera desde el código en cada run** (build artifact, no una BD persistida). Añadir tipos de nodo/arista después es barato. Lo único caro de cambiar son los anclajes de caché (`id`, `hash`, `inferred`).
- **Comportamiento de compilador.** Un hueco que no sale de la fuente no se adivina: se declara y se pide la señal que falta (ver Huecos). Trinquete de legibilidad — cada run deja el repo más auto-descriptivo, y los huecos solo bajan.

---

## Nodos

Campos comunes a todo nodo: `id`, `type`, `file`, `line`, `hash`.

- `id` — identidad **estable**, derivada de qualname + path (p.ej. `function:agora.services.synthesis.build_context`). No cambia al editar el cuerpo.
- `hash` — huella de **contenido** del tramo de fuente. Cambia al editar. Gate de la Capa 3 futura.
- `line` (nodos `function`/`endpoint`) — línea del propio `def`/`async def`, sin decoradores (para que "ir al nodo" aterrice en la definición). `hash` cubre el tramo **con decoradores incluidos** — un decorador es parte del significado de la pieza (`@router.post(...)`, un decorador de caché/auth), así que editarlo cuenta como edición del nodo. Un único punto de hidratación (`src/cc/extract/_node_hydration.py`) aplica esta convención para los cuatro emisores de nodos `function` (endpoints, calls-caller, calls-callee, sql) — ningún extractor calcula su propio `line`/`hash` de forma independiente.

| type | Props específicas |
|---|---|
| `endpoint` | `method`, `path`, `handler` (qualname del handler) |
| `function` | `qualname`, `kind` (function\|method), `is_handler` |
| `model` | `name`, `kind` (request\|response), `fields[]` (inferidos del cuerpo Pydantic) |
| `table` | `name`, `columns[]` (inferidos — ver poblado) |

`model.fields[]` y `table.columns[]` siguen el mismo patrón: descriptivos del nodo, no nodos propios (no hacemos granularidad a nivel campo/columna en v0).

---

## Aristas

Campos comunes: `from`, `to`, `type`, `inferred` (bool).

| type | from → to | Props |
|---|---|---|
| `handles` | endpoint → function | — |
| `uses_model` | endpoint → model | `direction` (in\|out) |
| `calls` | function → function | (best-effort) |
| `reads` | function → table | `via` (file:line del call site SQL) |
| `writes` | function → table | `via` (file:line del call site SQL) |

`via` permite drill-down a la query exacta (ahí se ve la columna concreta; p.ej. dónde se escribe `cost_usd`).

---

## Anclajes de caché — lo único caro de cambiar después

- **`id`** = quién es (identidad estable). Si fuera por contenido, cada edición crearía un nodo nuevo y se perdería el enganche con anotaciones cacheadas.
- **`hash`** = en qué estado está (contenido).
- **`inferred`** = qué puede variar en una reinterpretación aunque el código no cambie. Determinista (`false`) es estable a código fijo; LLM (`true`) bailaría en cada run.

Mecanismo Capa 3 (futuro): la anotación guarda el `hash` contra el que se generó. **Drift = hash guardado ≠ hash actual.** El gate de hash no es solo ahorro: *fija una interpretación por estado de código y se niega a re-tirar el dado hasta que el hash cambie.*

---

## Estrategia de poblado

**Endpoints + models**
- Por defecto: **puro estático** — parsear decoradores `@router.*`/`@app.*` + anotaciones de tipo del handler. Portable, no importa nada.
- Acelerador opcional: introspección runtime (`app.routes`, `app.openapi()`) **solo donde la app arranca limpia sin tocar infra** (agora sí; repos Corporate, asumir que no).

**calls**
- pyan3 best-effort, `inferred=false` con agujeros asumidos (indirección, `Depends`, dispatch dinámico).
- Josem valida los recorridos recuperados contra agora real (= la medición de recuperación del call graph).
- Los agujeros dinámicos son `unresolved_dynamic`: se marcan `inferred`, **no** generan gap (la señal está en el repo, es el estático el que no llega).

**reads / writes + table.columns**
- Parsear el SQL de los call sites de BD (aiomysql, etc.). DB-agnóstico: se lee la intención del código, no la BD.
- Columnas: preferir `CREATE TABLE` si está en el repo. Si no:
  - **INSERT / UPDATE** → fuente principal (`columna ↔ tabla` explícito y limpio).
  - **SELECT sobre una sola tabla** → complemento best-effort.
  - **`SELECT *` y joins** → se omiten / no se resuelven.
- `table.columns` resultante = unión de columnas tocadas por el código (las no tocadas son irrelevantes para comprensión/linaje).
- Si no hay `CREATE TABLE` **ni** se pueden inferir (p.ej. solo `SELECT *`) → no se inventan: se emite un **gap** `missing_artifact` (ver Huecos).

---

## Huecos / Reporte de legibilidad

El tool no rellena en silencio ni adivina hechos estructurales: cuando algo no sale de la fuente, lo **declara como hueco**. Un hueco es un diagnóstico, no un fallo del tool.

**Litmus — ¿es "falta de señal" (se pregunta) o ruido del parser (no se pregunta)?**
*¿Rellenarlo ayuda también a un humano que lee el repo, o solo al parser?*

- **`missing_artifact`** — la info no está en la fuente (p.ej. `messages` referenciada pero sin `CREATE TABLE`). Ayuda al humano → **se pregunta**: el dev añade el artefacto al repo, aunque sea a mano.
- **`unresolved_dynamic`** — la info sí está, pero es runtime-bound (`Depends`, `getattr`, dispatch por dict). Pedir reescribir código que funciona solo contenta al parser → **no se pregunta**: se marca `inferred`.
- **`tool_limitation`** — la info está en la fuente, pero la herramienta actual no puede parsearla (p.ej. pyan3 crashea en código con inicialización a nivel de módulo). No es un gap del repo: es transparencia sobre la cobertura del tool. `comprehension: warning` (la comprensión es parcial pero útil), `compliance: error` (un auditor no puede trazar flujos a través de un fichero sin call graph).
- Zona gris: si rellenar ayuda al humano (anotación de tipo en un `Depends`), entonces cuenta como `missing_artifact`.

**Estructura de un gap:**

| Campo | Contenido |
|---|---|
| `kind` | `missing_artifact` \| `unresolved_dynamic` \| `tool_limitation` |
| `where` | `file:line` y/o `id` del nodo afectado |
| `missing` | qué falta, en humano |
| `suggested` | artefacto concreto a añadir; idealmente un **stub rellenable** (`-- TODO: DDL de messages, ref. synthesis.py:42`) |
| `severity` | por audiencia: `comprehension` y `compliance` ∈ {warning, error} |

**Flag, no bloquees.** El tool produce lo que puede y marca el hueco visible; no se niega a generar (bloquear al primer agujero = inútil en repos reales). El **mismo** hueco es `warning` en comprensión y `error` en compliance — un linaje con agujeros no vale para un auditor; una comprensión con dos tablas a medias sí sirve.

**En el POC:** versión barata — cuando una `table` no tiene columnas inferibles, su nodo se marca "columnas desconocidas — falta DDL" en vez de quedar vacío. El reporte completo (panel de huecos, stubs, severidades) es posterior.

---

## Fuera del POC

Capa 3 / LLM / hashing en uso · vista de linaje profundo (taint) · adapter `generic` · multi-repo · stores no-SQL (Mongo = otro parser de poblado, mismo esquema).

---

## Test de éxito (eval sobre agora)

El POC vale si navegar el grafo responde más rápido que `grep` + leer:

1. ¿Dónde se escribe `cost_usd`? (dato — vía `writes → messages` + `via`)
2. ¿Qué toca el endpoint de synthesize? (recorrido — `endpoint —handles→ —calls*→ —reads/writes→`)
3. ¿Dónde se arman los prompts de los personajes? (recorrido/lógica — el más exigente; mide cuánto duele el call graph incompleto)