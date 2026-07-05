# Spec — Visitor AST para aristas `calls` (Nivel 1)

> **Para:** Claude Code. **Estado:** decisión tomada, listo para implementar.
> **Contrato del grafo:** `esquema-grafo-poc.md` (autoritativo). Esta spec solo cambia el *poblador* de aristas `calls`; el esquema no se toca.
> **Reemplaza a pyan3** en el pipeline (ver Contexto). Reemplazo limpio, no convivencia — no dejar flag `--calls-engine`.

---

## Contexto y decisión

pyan3 2.6.0 (revival de febrero 2026, última versión) crashea en los 3 ficheros de `backend/services/` de agora con `argument of type 'NoneType' is not iterable` — exactamente donde viven las respuestas a las preguntas 2 y 3 del eval. Verificado que la versión instalada ya era la última: no hay upgrade que esperar.

Motivos del reemplazo (por orden de peso):

1. **Cobertura**: 0 aristas `calls` en la capa de servicios = eval 2 y 3 irrespondibles.
2. **BNP**: licencia GPL-2.0 (probable veto administrativo) + bus factor 1.
3. **Filosofía**: un visitor propio falla explícitamente con nuestra taxonomía de gaps nativa; pyan3 falla en opaco.

En paralelo (no bloquea esta tarea): abrir issue upstream en `Technologicat/pyan` con repro mínimo — ver §Tarea paralela.

---

## Qué construir

Un visitor sobre `ast` (stdlib) que recorre los cuerpos de función del repo target y emite aristas `calls` (`function → function`, `inferred=false`) resolviendo contra el inventario de símbolos de griffe.

### Insumos

- **Inventario griffe** ya existente: tabla de símbolos con qualnames consultables. Es el resolutor — el visitor NO mantiene su propio inventario.
- **Tabla de imports por módulo**: construirla con el propio visitor (`ast.Import` / `ast.ImportFrom`), mapeando nombre local → qualname. Incluye alias (`import x as y`, `from a import b as c`).

### Casos de resolución (Nivel 1 — exactamente estos tres)

| # | Patrón | Ejemplo | Resolución |
|---|---|---|---|
| 1 | Llamada directa a nombre | `build_context(...)` | Nombre → tabla de imports del módulo o defs del propio módulo → qualname en inventario griffe |
| 2 | Atributo sobre import/alias | `synthesis.build(...)` tras `from services import synthesis` | Base del atributo → tabla de imports → qualname compuesto → inventario |
| 3 | Método same-module / same-class | `self._compress(...)`, `helper()` definido en el mismo módulo | Resolución ingenua por nombre dentro de la clase/módulo actual |

**Todo lo que no caiga en esos tres casos → `unresolved_dynamic`.** Sin heurísticas, sin adivinar. Incluye: atributos encadenados (`a.b.c()`), callables en variables, `getattr`, dispatch por dict, `Depends(...)`, decoradores que envuelven.

### Detalles de implementación que no son opcionales

- **Async**: tratar `ast.AsyncFunctionDef` igual que `ast.FunctionDef`. Desenvolver `ast.Await` para llegar al `ast.Call` interior — agora es async por todas partes; si esto falla, el visitor no aporta nada sobre pyan3.
- **Determinismo**: output ordenado y estable entre runs a código fijo (mismo criterio que el resto del pipeline).
- **Aristas**: `type=calls`, `from`/`to` = qualnames, `inferred=false`. `id`/`hash` de nodos: sin cambios, los anchors no se tocan.

### Registro de lo no resuelto

- Cada call site no resuelto → entrada `unresolved_dynamic` (según esquema: marcado, visible, **sin** `suggested` accionable — no se pide reescribir código que funciona).
- Si el visitor mismo no puede procesar un fichero (no debería ocurrir: `ast.parse` traga cualquier Python válido) → gap `tool_limitation`, mismo mecanismo ya construido para pyan3. **Prohibido excluir en silencio** — ningún fichero desaparece del output sin rastro.

### Reporte de cobertura

Por fichero: funciones analizadas, call sites totales, resueltos, `unresolved_dynamic`. Agregado global al final. Este reporte es producto, no debug — en BNP será la métrica de completitud de linaje.

---

## Fuera de scope (Nivel 2 — NO implementar salvo que el eval lo exija)

- Resolución de `Depends()` de FastAPI (semi-estático, candidato natural si la pregunta 2 queda coja)
- Atributos encadenados / inferencia de tipos de variables
- Cualquier resolutor externo (jedi, pyright)

---

## Criterios de aceptación

1. **Baseline a batir**: pyan3 dio 32 aristas `calls` y 3 gaps `tool_limitation` en `backend/services/`. El visitor debe producir aristas `calls` en esos 3 ficheros (donde pyan3 dio cero).
2. Las preguntas 2 (¿qué toca synthesize?) y 3 (¿dónde se arman los prompts de personajes?) del eval son recorribles en el grafo: `endpoint —handles→ —calls*→ —reads/writes→`.
3. Reporte de cobertura emitido con conteos resueltos/no-resueltos por fichero. Cero ficheros excluidos en silencio.
4. Validación manual de Josem (criterio 4 del diseño): muestreo de recorridos `calls` contra agora real. El visitor no se da por bueno hasta este OK.
5. Determinismo: dos runs consecutivos sin cambios en el código → output idéntico.

---

## Tarea paralela (no bloquea)

Issue en `Technologicat/pyan`:

1. Bisectar UNO de los 3 ficheros crasheantes hasta el snippet mínimo (5-15 líneas) que reproduce `argument of type 'NoneType' is not iterable`. El repro es la issue; sin él no vale la pena abrirla.
2. Adjuntar: traceback completo, pyan3 2.6.0, versión exacta de Python del venv.
3. El snippet resultante tiene valor propio: documenta qué patrón de agora mata parsers estáticos — dato de diseño para el propio visitor.

---

## Notas

- Si durante la implementación aparece un cuarto caso de resolución "barato y obvio", NO añadirlo sin consultar — el scope está cerrado a tres casos a propósito. La presión de scope se resuelve con datos del eval, no con intuición.
- El código de integración de pyan3 se elimina del pipeline, pero el mecanismo de gaps `tool_limitation` se conserva tal cual (lo hereda el visitor).

---

## Addendum — 3 aclaraciones (decididas 2026-07-04)

1. **Calls fuera del inventario (stdlib/terceros): 3 buckets, no 2.**
   - `resolved_internal` → arista `calls` (sin cambios respecto al cuerpo de la spec).
   - `resolved_external` → la tabla de imports identifica **positivamente** que la base de la llamada pertenece a un paquete top-level que vive fuera del repo target (p.ej. `logging`, `aiomysql`). Sin arista, sin gap, fuera del denominador de cobertura de comprensión. Se cuenta como **contador agregado por fichero** (no registros individuales) en el reporte de cobertura — coste cero, contabilidad completa.
   - `unresolved_dynamic` → default cuando la call site no se puede clasificar ni como interna ni como externa.
   - **Regla:** no saber qué es una llamada nunca la clasifica como externa. Externo es una conclusión positiva (evidencia en la tabla de imports), no una ausencia de resolución.

2. **Caso 2 (atributo sobre import/alias) cubre imports dotted planos.** `import services.synthesis` (sin alias) usado como `services.synthesis.build(...)` resuelve igual que `from services import synthesis` + `synthesis.build(...)`. Se resuelve recursivamente la base del `ast.Attribute` anidado hasta el `ast.Name` raíz contra la tabla de imports. No es un caso 4 — mismo mecanismo de resolución, solo cubre el nivel de anidamiento del propio import.

3. **Caso 3 (same-module/same-class) consulta la jerarquía de clases de griffe.** Un método heredado (p.ej. `self._compress(...)` definido en una clase padre en otro módulo) se resuelve vía `bases`/MRO de griffe, no solo contra el cuerpo AST de la clase actual. El inventario griffe ya modela esa relación — no es un resolutor nuevo, es leer un dato que ya existe en el insumo declarado en §Insumos.