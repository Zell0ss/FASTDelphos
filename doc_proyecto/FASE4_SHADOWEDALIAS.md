# Spec — Carga griffe resistente a aliases que shadowean subpaquetes (caso illumiows)

> **Para:** Claude Code. **Contrato:** `esquema-grafo-poc.md` — no se toca. Esto es el loader del inventario, no el esquema.
> **Estado del diagnóstico:** cerrado con sondas en el repo real (illumiows). Todo lo de abajo es reproducible en casa con el fixture — no hace falta acceso al repo original.
> **Estado de la implementación: cerrado.** Ver §Resultado al final.

---

## Diagnóstico (completo, verificado)

**Patrón del repo:** raíz namespace package (`api/`, sin `__init__.py`) → subpaquete regular (`api/public/`, CON `__init__.py`) → subpaquetes namespace (`workload/`, `labels/`, `iplists/`... sin `__init__.py`). El `__init__.py` de `public` monta los routers así:

```python
from api.public.workload import views as workload
from api.public.labels import views as labels
# ... x12 namespaces
```

**El veneno:** cada alias se llama IGUAL que el subpaquete que re-exporta. Para griffe, el miembro `workload` de `api.public` es un Alias → `api.public.workload.views`. Cuando el loader recorre el directorio `workload/` e intenta crear/consultar el módulo padre de cualquier fichero interior (`_get_or_create_parent_module` → `parent_module.is_namespace_package`), encuentra el ALIAS donde esperaba un Module → resolverlo pasa por el miembro `workload` de `api.public` → el propio alias → **CyclicAliasError**, y griffe aborta la carga del paquete ENTERO. 255 funciones invisibles por un patrón de import idiomático y runtime-legal.

Verificado además:
- griffe 2.1.0 (última release, Artifactory al día) — el upgrade NO lo arregla.
- Cargar por path (`griffe.load("/…/api/public/workload")`) NO esquiva el veneno: el finder sube directorios hasta `public` (tiene `__init__.py`), lo carga, y muere igual (`AliasResolutionError: Could not resolve alias public.workload pointing at api.public.workload.views (in …/api/public/__init__.py:8)`).
- El gap `tool_limitation` de paquete-entero que añadimos la semana pasada declaró el fallo correctamente (por eso lo vimos) — pero paquete-entero es granularidad inaceptable: el objetivo de esta spec es que un shadowing cueste CERO (o, en el peor caso, un módulo).

## Fixture (obligatorio, primero)

Repo de juguete que reproduce EXACTAMENTE la topología (es el repro de dos ficheros + estructura):

```
fixture_shadowed/
  api/                      # namespace, SIN __init__.py
    public/
      __init__.py           # from api.public.workload import views as workload
                            # from api.public.labels import views as labels
      workload/             # namespace, SIN __init__.py
        views.py            # def get_workload(): ...
        crud.py             # def helper(): ...   (views.py llama a helper)
      labels/
        views.py
  asgi.py                   # módulo suelto en raíz
```

Verificar que el fixture reproduce los DOS crashes antes de arreglar nada: `griffe.load("api", search_paths=[fixture])` → CyclicAliasError; `griffe.load(f"{fixture}/api/public/workload")` → AliasResolutionError.

## El fix — scrub de aliases shadow durante la carga

**Idea central:** un Alias cuyo nombre coincide con un subdirectorio-subpaquete real del mismo módulo es puro ruido para NUESTRO caso de uso — la tabla de imports del visitor (AST) ya captura ese `from X import views as Y` por su lado; no perdemos información eliminándolo del árbol griffe EN MEMORIA. Nunca se toca el repo (read-only intacto): se poda la representación, no la fuente.

**Diseño primario — subclase de GriffeLoader con scrub proactivo:**

1. Subclase propia (p.ej. `ShadowTolerantLoader(GriffeLoader)`).
2. Override del punto donde se descienden los submódulos (`_load_submodules` o `_load_submodule`, según resulte más limpio contra el código real de griffe 2.1.0): ANTES de descender a los subdirectorios de un módulo ya cargado, escanear sus `members`: todo miembro que sea `Alias` Y cuyo nombre coincida con un subdirectorio real del módulo (el filesystem manda) → retirarlo de `members` y registrarlo en una lista `scrubbed: list[(parent_qualname, alias_name, target_path)]`.
3. Descender normal. El lookup del padre encuentra ahora el hueco y griffe crea el módulo namespace real.
4. **Red de seguridad además del scrub** (no en lugar de): el override envuelve cada carga de submódulo en try/except `GriffeError` → el submódulo que aun así falle se registra y la carga CONTINÚA. Granularidad de fallo: un módulo, jamás el paquete.

**Transparencia (doctrina, no opcional):**
- Los aliases scrubbed se reportan como contador agregado en el reporte de cobertura ("N re-exports shadow neutralizados durante la carga") — no son gaps (no falta nada: la info vive en la tabla de imports AST), pero no desaparecen en silencio.
- Los submódulos que fallen pese al scrub → gap `tool_limitation` POR MÓDULO con el error concreto. El gap de paquete-entero queda solo para el caso "ni siquiera se pudo empezar a cargar".

**Interacción con lo ya construido (no tocar):** `internal_top_levels = attempted` sigue tal cual — si un módulo cae pese a todo, las llamadas hacia él clasifican `unresolved_dynamic`, no external.

**Guardas por depender de API privada de griffe (`_internal`):**
- Pin de versión de griffe en `pyproject.toml` (rango acotado, p.ej. `>=2.1,<2.2`).
- El fixture ES el canario: si un upgrade de griffe rompe el override, el fixture falla en CI antes de llegar a un repo real. Comentario en la subclase apuntando a esta spec y al pin.

**Fallback si el scrub resulta inviable** contra el código real de griffe (documentar por qué antes de caer aquí): solo la red de seguridad del punto 4 — catch por submódulo, gap por módulo. Recupera menos (en illumiows dejaría los doce namespaces gapeados a nivel módulo) pero mantiene el resto del inventario vivo y es honesto. El scrub es el objetivo; el catch es el suelo.

## Tarea paralela (no bloquea)

Issue upstream en `mkdocstrings/griffe`: el loader llama `is_namespace_package` sobre un miembro que puede ser Alias sin protegerse (models.py:1973 vía loader.py:733 en 2.1.0) — un import shadow runtime-legal mata la carga entera. Adjuntar el repro del fixture (dos ficheros + estructura), traceback completo, ambas variantes (Cyclic y AliasResolution). Mantenedor activo. Nuestro fix no espera a la suya.

**No presentado** — decisión de Josem: no se abre issue en el repo de un tercero desde este proyecto. Queda el repro documentado arriba por si se retoma.

## Criterios de aceptación

1. Fixture: `griffe.load` vía nuestro loader carga el árbol completo; `views.py` y `crud.py` de ambos namespaces en el inventario; la arista `calls` views→crud del fixture resuelve `resolved_internal`.
2. illumiows recompilado: `resolved_internal` pasa de 0 a cientos; el salto `views.deleteIPList → crud.delete_iplist_allregions` navegable en el subgrafo; los 12 namespaces con nodos function.
3. Reporte declara los aliases scrubbed (contador) — cero silencio.
4. Módulo genuinamente irrecuperable (fixture adicional con un fallo real de carga) → gap `tool_limitation` de módulo, el resto del paquete intacto.
5. agora: regresión cero (diff de ids/hashes/aristas vacío — agora no tiene shadowing, el scrub no debe activarse: contador a 0).
6. Fixture en CI como canario del pin de griffe.

---

## Resultado

Implementado vía TDD, `src/cc/extract/_griffe_loader.py` (`ShadowTolerantLoader` + `load_tolerant()`).

**Desviaciones del diagnóstico original** (verificadas empíricamente contra el entorno real, no solo confiando en la spec):

- **Versión de griffe instalada: 2.0.2, no 2.1.0.** El pin en `pyproject.toml` es `>=2.0,<2.1` — el rango que está realmente probado, no el asumido en la spec.
- **El diseño exacto del scrub difiere del punto 2 de la spec.** No hace falta escanear `members` de forma proactiva antes de descender: basta con override de `_get_or_create_parent_module` para detectar, en el momento exacto en que el lookup del padre devuelve un Alias en vez de un Module, que se trata de shadowing — ahí se hace el scrub y se crea el módulo namespace real in situ. Es un punto de intercepción más preciso que analizar `finder.submodules()` por adelantado, y evita re-implementar la lógica de "qué subdirectorios son reales" por fuera de griffe.
- **Ítem del `criterio de aceptación 1` que la spec asumía incorrectamente:** cargar por path (`griffe.load(f"{fixture}/api/public/workload")`) sigue fallando incluso con nuestro loader — pero esa ruta de carga NUNCA se usa en producción (ambos call sites, `models.py` y `_calls_resolver.py`, cargan siempre por nombre de paquete top-level). No se intentó arreglar; documentado como fuera de alcance real.
- **Issue upstream:** no presentado — decisión explícita de no publicar en un repo de terceros desde este proyecto.

**Bug hermano encontrado y corregido de paso:** `models.py`'s `_walk_griffe` no protegía contra `filepath` de tipo `list` (el caso de un Module namespace, que `_calls_resolver.py`'s `_walk_griffe_functions` sí manejaba) — el `in excluded` sobre una lista lanza `TypeError: unhashable type: 'list'`, silenciado por el `except Exception: pass` de `_load_models`, perdiendo TODOS los modelos del paquete sin ningún indicio. Solo se manifestó porque nuestro fix crea namespace Modules donde antes `models.py` jamás llegaba a cargarlos.

**Gap pre-existente y no corregido (fuera de alcance), registrado en `BACKLOG.md`:** el descubrimiento de paquetes top-level de `models.py` (a diferencia del de `_calls_resolver.py`) solo mira un nivel de profundidad buscando `__init__.py` y no soporta paquetes namespace-root (sin `__init__.py` en la raíz) — un repo con esa forma exacta (como illumiows) nunca ve sus modelos Pydantic extraídos, con o sin este fix.

**Verificación:**
- `tests/test_griffe_loader.py` (6 tests) — reproduce los dos crashes nativos de griffe como baseline, luego valida el loader tolerante.
- `tests/test_calls_resolver.py`, `tests/test_models_ext.py`, `tests/test_pipeline.py` — casos de integración end-to-end (llamada cross-shadow resuelve `resolved_internal`, reporte de scrub, gap por módulo irrecuperable).
- agora: `graph.json` byte-idéntico antes/después, contador de scrubbed en 0 (criterio 5 cumplido).
- 308 tests, ruff sin hallazgos nuevos.
