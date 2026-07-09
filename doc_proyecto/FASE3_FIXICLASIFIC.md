# Spec — Fix clasificación interno/externo (caso illumiows)

> **Para:** Claude Code. **Contrato:** `esquema-grafo-poc.md` — no se toca (esto es el clasificador de calls, no el esquema).
> **Síntoma:** repo real (illumiows) compila con `0 internal, 1066 external, 1390 unresolved_dynamic` sobre 2456 call sites / 255 funciones. Imposible: ningún repo real tiene cero llamadas internas.
> **Causa raíz confirmada:** el descubridor de top-level packages del clasificador exige `__init__.py` — `api/` es un namespace package (PEP 420, sin `__init__.py`) y quedó fuera. Detectó `{asgi, asgi_debug, conftest, test_mount}` (módulos sueltos de la raíz) en vez de `api`. Mientras, griffe SÍ cargó `api.*` (el inventario está bien — los nodos existen e hidratan). Dos lógicas de descubrimiento = dos opiniones sobre qué es el repo. Consecuencia: las llamadas a `api.*` importadas explícitamente se clasificaron como `resolved_external` (¡con "evidencia positiva"!) — las internas del repo están escondidas dentro de esos 1066 external.

---

## Cambios (en orden)

### 1. Los top-level internos se DERIVAN del inventario griffe — eliminar el descubrimiento paralelo

```python
internal_top_levels = {qualname.split(".")[0] for qualname in inventory}
```

El inventario ya es la lista autorizada de "código de este repo". Si griffe cargó `api`, entonces `api.*` es interno **por construcción** — sin segunda lógica que pueda divergir. El descubridor de paquetes del clasificador no se arregla: **se elimina como concepto**. (Si algo queda de él para decidir qué cargarle a griffe, esa es otra pieza — pero la clasificación interno/externo bebe SOLO del inventario.)

### 2. Orden de clasificación: inventario PRIMERO

Orden robusto por call site resuelto por la tabla de imports:

1. ¿El qualname está en el inventario griffe? → `resolved_internal`. Fin.
2. Si no está: ¿la base resuelve positivamente a un top-level NO interno? → `resolved_external`.
3. Si no → `unresolved_dynamic`.

El bug actual clasificaba por top-level ANTES del lookup — por eso las `api.*` acabaron en external. El inventario manda; la lista de top-levels solo arbitra el external/dynamic de lo que no esté en él.

### 3. `--toppackages` como override opcional (válvula, no fix)

- `--toppackages api,otro` fuerza el conjunto de internos para repos genuinamente retorcidos.
- Si el override diverge de lo derivado del inventario → **warning visible** con ambos conjuntos.
- Sin el flag, todo se deriva solo (caso normal — el usuario CORPORATE no sabe los top-levels de un repo ajeno, no se le puede exigir).

### 4. Sanity-check de primera línea (obligatorio, no cosmético)

Si `resolved_internal == 0` con inventario no vacío, el reporte abre con:

```
⚠ 0 llamadas internas resueltas con 255 funciones inventariadas.
  Internos derivados del inventario: {api}
  Posible mismatch de descubrimiento/topología — revisar antes de fiarse del grafo.
```

Esta línea convierte una hora de diagnóstico en diez segundos de lectura. En CORPORATE el usuario no tendrá este chat al lado.

### 5. Higiene de parseo del target (menor, misma tanda)

- Suprimir/capturar `SyntaxWarning` del código target al parsearlo (p.ej. regex sin raw string en illumiows) — el output del tool no muestra la ropa interior del repo analizado.
- Nota observada: cada warning salía 3 veces = 3 pasadas de parseo por fichero (endpoints/calls/sql). No bloqueante, pero el cache de AST por fichero de la ventanilla ya existe — candidato natural a servir a las tres pasadas.

## Fixture nuevo (obligatorio)

Repo de juguete con la topología illumiows: paquete raíz **namespace (SIN `__init__.py`)** con subpaquetes normales dentro + módulos sueltos en la raíz (`asgi.py`, `conftest.py`). Test: compila con internal > 0 y las llamadas cross-módulo del paquete namespace resuelven como internas.

## Criterios de aceptación

1. illumiows recompilado sin flags: `resolved_internal` pasa de 0 a cientos; el salto `views.deleteIPList → crud.delete_iplist_allregions` aparece como arista `calls` y es navegable en el subgrafo del endpoint.
2. Los external restantes son solo librerías reales (fastapi, sqlmodel, typing...) — muestreo manual de Josem.
3. agora recompila idéntico (regresión cero — diff de ids/hashes/aristas vacío).
4. Fixture namespace-package en verde.
5. El sanity-check dispara en un repo fabricado para dar 0 internas, y NO dispara en agora/illumiows post-fix.