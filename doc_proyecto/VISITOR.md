# Spec — Visitor AST para aristas `calls` (Nivel 1)

> **Para:** Claude Code. **Contrato del grafo:** `esquema-grafo-poc.md` (autoritativo). Esta spec solo cambia el *poblador* de aristas `calls` (y, por extensión de este mismo análisis, una corrección en el poblador de `reads`/`writes`); el esquema no se toca.
> **Reemplazó a pyan3** en el pipeline. Reemplazo limpio, no convivencia — no hay flag `--calls-engine`.

---

## Estado actual (2026-07-05)

- ✅ **Nivel 1 implementado, mergeado a `main`, validado contra agora real.** Ver §Criterios de aceptación para el detalle de cada uno.
- ✅ **Nivel 2 cerrado al backlog con datos** — ver §Nivel 2.
- ✅ **Fix `extract_sql.py` / SQL dinámico vía f-string implementado** — ver §Fix f-string.
- ⏳ **Caso 2b (alias local a import externo) decidido, pendiente de implementar** — ver §Caso 2b.
- ⏳ **Import local a función — pendiente de decisión de scope** (no implementado) — ver §Hallazgos pendientes.
- ⏳ **Tarea paralela (issue upstream pyan3)** — no bloqueante, estado independiente de este documento, ver §Tarea paralela.

Lo que sigue debajo es el histórico completo de decisiones, en orden cronológico, que sustenta el estado de arriba.

---

## Contexto y decisión

pyan3 2.6.0 (revival de febrero 2026, última versión) crasheaba en los 3 ficheros de `backend/services/` de agora con `argument of type 'NoneType' is not iterable` — exactamente donde viven las respuestas a las preguntas 2 y 3 del eval. Verificado que la versión instalada ya era la última: no había upgrade que esperar.

Motivos del reemplazo (por orden de peso):

1. **Cobertura**: 0 aristas `calls` en la capa de servicios = eval 2 y 3 irrespondibles.
2. **BNP**: licencia GPL-2.0 (probable veto administrativo) + bus factor 1.
3. **Filosofía**: un visitor propio falla explícitamente con nuestra taxonomía de gaps nativa; pyan3 fallaba en opaco.

---

## Qué construir

Un visitor sobre `ast` (stdlib) que recorre los cuerpos de función del repo target y emite aristas `calls` (`function → function`, `inferred=false`) resolviendo contra el inventario de símbolos de griffe.

### Insumos

- **Inventario griffe** ya existente: tabla de símbolos con qualnames consultables. Es el resolutor — el visitor NO mantiene su propio inventario.
- **Tabla de imports por módulo**: construida por el propio visitor (`ast.Import` / `ast.ImportFrom`), mapeando nombre local → qualname. Incluye alias (`import x as y`, `from a import b as c`).

### Casos de resolución (Nivel 1 — exactamente estos tres)

| # | Patrón | Ejemplo | Resolución |
|---|---|---|---|
| 1 | Llamada directa a nombre | `build_context(...)` | Nombre → tabla de imports del módulo o defs del propio módulo → qualname en inventario griffe |
| 2 | Atributo sobre import/alias | `synthesis.build(...)` tras `from services import synthesis` | Base del atributo → tabla de imports → qualname compuesto → inventario |
| 3 | Método same-module / same-class | `self._compress(...)`, `helper()` definido en el mismo módulo | Resolución ingenua por nombre dentro de la clase/módulo actual |

**Todo lo que no caiga en esos tres casos → `unresolved_dynamic`.** Sin heurísticas, sin adivinar. Incluye: atributos encadenados (`a.b.c()`), callables en variables, `getattr`, dispatch por dict, `Depends(...)`, decoradores que envuelven.

### Addendum — 3 aclaraciones a los casos de arriba (decididas 2026-07-04, antes de implementar)

1. **Calls fuera del inventario (stdlib/terceros): 3 buckets, no 2.**
   - `resolved_internal` → arista `calls` (sin cambios respecto al cuerpo de la spec).
   - `resolved_external` → la tabla de imports identifica **positivamente** que la base de la llamada pertenece a un paquete top-level que vive fuera del repo target (p.ej. `logging`, `aiomysql`). Sin arista, sin gap, fuera del denominador de cobertura de comprensión. Se cuenta como **contador agregado por fichero** (no registros individuales) en el reporte de cobertura — coste cero, contabilidad completa.
   - `unresolved_dynamic` → default cuando la call site no se puede clasificar ni como interna ni como externa.
   - **Regla:** no saber qué es una llamada nunca la clasifica como externa. Externo es una conclusión positiva (evidencia en la tabla de imports), no una ausencia de resolución.

2. **Caso 2 (atributo sobre import/alias) cubre imports dotted planos.** `import services.synthesis` (sin alias) usado como `services.synthesis.build(...)` resuelve igual que `from services import synthesis` + `synthesis.build(...)`. Se resuelve recursivamente la base del `ast.Attribute` anidado hasta el `ast.Name` raíz contra la tabla de imports. No es un caso 4 — mismo mecanismo de resolución, solo cubre el nivel de anidamiento del propio import.

3. **Caso 3 (same-module/same-class) consulta la jerarquía de clases de griffe.** Un método heredado (p.ej. `self._compress(...)` definido en una clase padre en otro módulo) se resuelve vía `bases`/MRO de griffe, no solo contra el cuerpo AST de la clase actual. El inventario griffe ya modela esa relación — no es un resolutor nuevo, es leer un dato que ya existe en el insumo declarado en §Insumos.

### Detalles de implementación que no son opcionales

- **Async**: `ast.AsyncFunctionDef` se trata igual que `ast.FunctionDef`. `ast.Await` no necesita desenvolverse explícitamente — `ast.walk` visita el `ast.Call` interior igual que cualquier otro nodo descendiente.
- **Determinismo**: output ordenado y estable entre runs a código fijo (mismo criterio que el resto del pipeline).
- **Aristas**: `type=calls`, `from`/`to` = qualnames, `inferred=false`. `id`/`hash` de nodos: sin cambios, los anchors no se tocan.

### Registro de lo no resuelto

- Cada call site no resuelto → entrada `unresolved_dynamic` (según esquema: marcado, visible, **sin** `suggested` accionable — no se pide reescribir código que funciona).
- Si el visitor mismo no puede procesar un fichero (no debería ocurrir: `ast.parse` traga cualquier Python válido) → gap `tool_limitation`, mismo mecanismo ya construido para pyan3. **Prohibido excluir en silencio** — ningún fichero desaparece del output sin rastro.

### Reporte de cobertura

Por fichero: funciones analizadas, call sites totales, resueltos, `unresolved_dynamic`. Agregado global al final. Este reporte es producto, no debug — en BNP será la métrica de completitud de linaje.

---

## Nivel 2 — cerrado al backlog con datos (2026-07-05)

**Fuera de scope original:** resolución de `Depends()` de FastAPI, atributos encadenados / inferencia de tipos de variables, cualquier resolutor externo (jedi, pyright) — "NO implementar salvo que el eval lo exija".

**Cierre con datos:** tras implementar el visitor Nivel 1 y compilar agora en real, se desglosaron los 243 `unresolved_dynamic` por patrón sintáctico. Residuo genuino que Nivel 2 (jedi/pyright, inferencia de tipos) resolvería — variables locales de tipo verdaderamente desconocido, descontando lo cubierto por correlación (`reads`/`writes`/`handles` reales) y builtins: **~42 sitios, ≈6% de los 677 call sites totales de agora.** Las 3 preguntas del eval (`ESQUEMA_POC.md §Test`) ya se responden sin Nivel 2.

**Decisión: Nivel 2 muere en el backlog.** Quien quiera resucitarlo argumenta contra este número — no contra la intuición de que "seguro hace falta". Si agora crece o aparece un repo BNP donde el residuo sea proporcionalmente mayor, se re-mide antes de reabrir la conversación.

---

## Criterios de aceptación — resultado

1. **Baseline a batir**: pyan3 daba 32 aristas `calls` y 3 gaps `tool_limitation` en `backend/services/`. ✅ **Cumplido** — el visitor produce aristas `calls` en esos 3 ficheros (36 aristas tocando `services/` en la compilación real de agora, 0 gaps `tool_limitation`).
2. Las preguntas 2 (¿qué toca synthesize?) y 3 (¿dónde se arman los prompts de personajes?) del eval recorribles en el grafo (`endpoint —handles→ —calls*→ —reads/writes→`). ✅ **Cumplido** — verificado por recorrido real: `synthesize —handles→ synthesize() —calls→ run_synthesis() —calls→ {...} —reads/writes→ {channels, messages, channel_syntheses, profiles}`; `orchestrator.run_turn —calls→ andamio.build_context` (línea 44).
3. Reporte de cobertura emitido con conteos resueltos/no-resueltos por fichero. Cero ficheros excluidos en silencio. ✅ **Cumplido**.
4. Validación manual de Josem: muestreo de recorridos `calls` contra agora real. ✅ **Cumplido** — compilación de agora revisada, los 3 eval questions confirmados por recorrido de grafo, no por grep.
5. Determinismo: dos runs consecutivos sin cambios en el código → output idéntico. ✅ **Cumplido** — `collect_py_files` ordena, sin iteración de sets/dicts que afecte el orden de salida.

---

## Caso 2b — alias local a import externo (decidido 2026-07-05, pendiente de implementar)

**Motivación:** `logger = logging.getLogger(__name__)` seguido de `logger.info(...)` cae a `unresolved_dynamic` porque `logger` es una variable local, no un import — el caso 2 solo resuelve bases que están literalmente en la tabla de imports. En agora esto son solo 8 sitios, pero el patrón es común.

**Regla (extiende el caso 2, no es un caso 4):** una asignación inmediata `nombre = base_resuelta.attr(...)` donde `base_resuelta` resuelve — vía la misma tabla de imports de siempre — a un paquete **externo** al repo, es evidencia positiva de que `nombre` es un alias a ese paquete. No es inferencia de tipos: es la misma regla de "conclusión positiva" ya aplicada un nivel de indirección más allá.

**Dos vallas obligatorias:**
1. **Solo si la base resuelve a externo.** Si `base_resuelta` resolviera a un paquete interno, NO se crea el alias ni se extiende la resolución interna a través de él — eso es otro perfil de riesgo (expandir cuánto del grafo interno se resuelve por asignación, no solo reclasificar externo/dinámico) y otra conversación.
2. **Sin last-wins.** Si el nombre tiene más de una asignación con bases distintas en el mismo scope, cae a `unresolved_dynamic` — no se adivina cuál asignación "gana".

Con estas dos vallas es mecánico y auditable — caso 2b, no un cuarto caso de resolución en el sentido que la nota de §Notas prohíbe.

---

## Hallazgos pendientes de decisión (2026-07-05, del análisis contra agora real)

1. **Import local a función no se resuelve (`from X import Y` dentro de un `def`).** 15 sitios en `backend/tests/`, todos resolubles en principio (la función importada ya está indexada), pero caen a `dynamic` porque la tabla de imports es deliberadamente solo-nivel-módulo. Es un límite explícito, no un bug — pero el patrón de import perezoso dentro de función también aparece fuera de tests (p.ej. para evitar imports circulares), así que no es solo ruido de test. **Pendiente:** ¿extender el rastreo de imports a nivel de función (con las mismas vallas de scope que el caso 2b), o dejarlo cerrado y que se resuelva solo cuando `tests/` tenga exclusión configurable?

---

## Fix — `extract_sql.py` y SQL dinámico vía f-string (decidido e implementado 2026-07-05)

**Hueco confirmado en agora:** `channels.py:33`, `channels.py:154`, `profiles.py:44` — tres `UPDATE` vía `f"UPDATE tabla SET {set_clause} WHERE ..."`. `_str_const` solo reconocía `ast.Constant`; el f-string es `ast.JoinedStr` y se descartaba en silencio (`if not sql: continue`). Eran escrituras a `channels`, `channel_profiles`, `profiles` invisibles en el grafo — no `extract_calls`, este es un hueco de `extract_sql.py`, documentado aquí porque salió del mismo análisis.

**Mecanismo (extiende el patrón "SELECT * → tabla conocida, columnas vacías" que ya existía, no inventa uno nuevo):**

Cuando el argumento SQL no es un `ast.Constant` pero SÍ es un `ast.JoinedStr`, se busca verbo+tabla (`UPDATE`, `INSERT INTO`, `DELETE FROM` → `writes`; `FROM` → `reads`) por regex — **sobre cada fragmento `ast.Constant` del f-string por separado, nunca sobre la concatenación de fragmentos**. Concatenar antes de buscar permitiría un caso Frankenstein: `f"INSERT INTO {prefix}channels ..."` concatenado da "INSERT INTO channels", y el regex capturaría "channels" cuando la tabla real es `{prefix}channels` — una arista falsa, que por la propia doctrina del proyecto es peor que un gap (el gap se declara; la arista falsa se cree). Buscando por fragmento individual, ese caso no matchea en ningún segmento y cae correctamente a "no resuelto" en vez de fabricar una tabla.

- **Si se encuentra verbo+tabla dentro de un único fragmento:** se emite la arista `reads`/`writes` de siempre, columnas vacías (mismo trato que `SELECT *`). El dato "esto escribe en `channels`" es legible literalmente en el fragmento estático — no es inferencia.
- **Si no se encuentra ni el verbo** (SQL 100% dinámico, ni la tabla es texto literal): **no es `tool_limitation`** — nada ha fallado; ni `sqlglot` ni ningún fix futuro del extractor podrán parsear un texto que estáticamente no existe. Es la definición de libro de `unresolved_dynamic` (info runtime-bound). Pero a diferencia del `unresolved_dynamic` genérico de `extract_calls` (que no genera gap — "no se pregunta"), **aquí sí se emite un Gap** — `kind="unresolved_dynamic"`, `severity={"comprehension": "warning", "compliance": "error"}` — porque una escritura a BD con destino desconocido es el agujero de linaje más grave del catálogo; la categoría no le quita gravedad.

**Alcance:** solo `ast.JoinedStr` (f-strings) — único patrón confirmado en agora (los 3 sitios reales). No se toca `.format()` ni concatenación con `+` — sin evidencia de que existan.

**Firma:** `extract_sql()` pasó de `(nodes, edges)` a `(nodes, edges, dynamic_gaps)`, donde `dynamic_gaps: list[tuple[str, int, str]]` = `(file, lineno, fn_qname)` por cada sitio no resuelto. `pipeline.py` los convierte en `Gap` exactamente como ya hacía con `call_excluded` de `extract_calls` — mismo mecanismo, ninguno nuevo.

**Estado:** implementado y verificado contra agora (los 3 sitios ahora aparecen como aristas `writes`, 0 gaps `unresolved_dynamic` en agora — ninguno de sus f-strings reales es el caso 100%-dinámico).

---

## Notas

- Si aparece un cuarto caso de resolución "barato y obvio" (más allá de los ya extendidos como 2b), NO añadirlo sin consultar — el scope está cerrado a propósito. La presión de scope se resuelve con datos del eval, no con intuición.
- El código de integración de pyan3 fue eliminado del pipeline; el mecanismo de gaps `tool_limitation` se conservó tal cual (lo heredó el visitor).

---

## Tarea paralela (no bloquea, estado independiente de este documento)

Issue en `Technologicat/pyan`:

1. Bisectar UNO de los 3 ficheros crasheantes hasta el snippet mínimo (5-15 líneas) que reproduce `argument of type 'NoneType' is not iterable`. El repro es la issue; sin él no vale la pena abrirla.
2. Adjuntar: traceback completo, pyan3 2.6.0, versión exacta de Python del venv.
3. El snippet resultante tiene valor propio: documenta qué patrón de agora mata parsers estáticos — dato de diseño para el propio visitor.
