# Spec — Fase 2: Capa de comprensión LLM (why-notes)

> **Para:** Claude Code. **Estado:** decidido, listo para implementar.
> **Contrato del grafo:** `ESQUEMA-POC.md` — **NO se toca.** Las notas viven en un overlay separado; el JSON del grafo queda byte-idéntico al de Fase 1.
> **Principio rector (del diseño original, sigue vigente):** determinista primero; LLM racionado, cacheado, y siempre marcado `inferred`. Sin config LLM, el tool funciona exactamente como hoy — Fase 2 es opt-in por construcción.

---

## 1. Arquitectura de proveedor — interfaz propia con dos adapters

Interfaz mínima (una función, single-turn, sin streaming):

```python
class LLMClient(Protocol):
    def generate(self, system: str, user: str) -> str: ...
```

Dos adapters, seleccionables por config:

| Provider | Cliente | Notas |
|---|---|---|
| `anthropic` | SDK `anthropic` oficial | Casa (Haiku). Usar prompt caching sobre el system prompt (mismo system para todos los nodos de un run → cache hit en todo menos el primero) |
| `openai_compatible` | `httpx` contra `/v1/chat/completions` | BNP (Qwen Coder vía endpoint local) y cualquier proveedor futuro. NO usar el SDK de openai — una llamada REST no justifica la dependencia |

**Sin LiteLLM ni capas universales** — decisión tomada: dependencias mínimas es requisito de instalabilidad en BNP.

Errores: timeout y fallo de API por nodo → se registra, se salta el nodo, el run continúa (flag, no bloquees — una nota que falla no aborta el batch). Reporte final: generadas / cacheadas / falladas.

## 2. Configuración — `.env` con prefijo `CC_LLM_*`

```bash
CC_LLM_PROVIDER=anthropic          # anthropic | openai_compatible
CC_LLM_BASE_URL=                   # vacío para anthropic (default SDK); URL del endpoint para openai_compatible
CC_LLM_API_KEY=sk-...              # o el token del gateway BNP
CC_LLM_MODEL=claude-haiku-4-5
CC_LLM_MAX_TOKENS=500              # una why-note es un párrafo, no un ensayo
CC_LLM_EXTRA_INSTRUCTIONS=         # opcional: instrucciones extra por-proveedor (modelos pequeños necesitan prompts más rígidos)
```

- Carga: `python-dotenv`, con override por variables de entorno reales (pods/CI).
- **La API key jamás aparece en logs, en el output compilado, ni en mensajes de error.**
- Sin `CC_LLM_PROVIDER` configurado → `cc annotate` falla con mensaje claro; `cc compile` ni se entera de que existe Fase 2.

## 3. Overlay de notas — `notes.json`, separado del grafo

`cc annotate <output-dir>` (comando nuevo; `cc compile` intocado) lee `graph.json`, consulta el overlay y genera solo lo que toca. Estructura por nota:

```json
{
  "function:backend.services.synthesizer.run_synthesis": {
    "text": "…",
    "hash": "<hash del nodo cuando se generó>",
    "prompt_version": 3,
    "model": "claude-haiku-4-5",
    "generated_at": "2026-07-08T19:00:00Z"
  }
}
```

Razones de la separación (para que nadie la "simplifique" después): contrato del grafo congelado; separación determinista/inferido **física** (a compliance se le entrega el grafo sin overlay — literalmente otro fichero); regeneración independiente.

## 4. Gate de regeneración — hash + versión de prompt

Se regenera la nota de un nodo si y solo si:

- `hash` guardado ≠ `hash` actual del nodo (drift de código), **o**
- `prompt_version` guardada ≠ `PROMPT_VERSION` actual (constante en el código; se incrementa a mano al cambiar el prompt), **o**
- `--force` explícito.

El `model` se registra pero **NO gatea** — cambiar Haiku↔Qwen no regenera nada por sí solo.

## 5. Scope — racionar por rol, no anotar todo

Batch por defecto anota SOLO:

- nodos `endpoint` (todos), y
- nodos `function` que orquestan: ≥2 aristas `calls` salientes **o** tocan ≥2 tablas (reads+writes). Umbral en config, no hardcodeado.

El resto de nodos: **on-demand** — botón "generar nota" en el panel del nodo del render. (El render sirve estático; el botón puede, en esta fase, limitarse a mostrar el comando a ejecutar `cc annotate --node <id>` — la integración viva es mejora posterior. `[CCode propone]` si ve una vía barata de hacerlo vivo en local.)

`cc annotate --all` existe para quien quiera pagar la cola larga completa.

## 6. El prompt — anti-paráfrasis por diseño

**El modo de fallo a prevenir:** notas que re-narran el código ("esta función obtiene el canal y construye el contexto"). Una nota que parafrasea es un bug, no una nota.

**La defensa es el contexto estructural.** El user prompt de cada nodo incluye:

1. El source span del nodo (código real).
2. Su vecindario en el grafo, en texto plano: quién lo llama, a qué llama, qué tablas lee/escribe (con columnas si las hay), desde qué endpoints es alcanzable. Todo esto YA está en el JSON — es serializar la adyacencia que el panel del render ya muestra.

El system prompt exige responder SOLO lo que el código no dice por sí mismo:

- por qué existe esta pieza como unidad separada,
- qué papel juega en los flujos que la atraviesan,
- qué se rompería o cambiaría si no existiera,
- (si aplica) qué decisión de diseño revela (caché, transaccionalidad, ordenación, idempotencia…).

Y prohíbe explícitamente: re-describir lo que hace línea a línea, repetir el nombre de la función como explicación, listar sus llamadas (el grafo ya las muestra al lado).

Formato de salida: un párrafo, máx ~80 palabras, español. `CC_LLM_EXTRA_INSTRUCTIONS` se concatena al system si está definido.

## 7. Render — lo inferido se VE que es inferido

- La nota aparece en el panel del nodo, en bloque visualmente distinto (fondo/borde/icono diferenciado + etiqueta `inferred · <model>` visible).
- Si el `hash` actual del nodo ≠ `hash` de la nota → la nota se muestra **atenuada con aviso "desactualizada — el código cambió"**, no se oculta ni se muestra como fresca. (El render compara los dos hashes que ya tiene: el del grafo y el del overlay.)
- Sin `notes.json` presente, el render funciona idéntico a hoy.

## Fuera de scope

- Gap-fill de calls asistido por LLM (era la otra mitad de la Fase 2 original — se pospone: el visitor + caso 2b dejaron el residuo en ~6%, no hay presión).
- Notas multi-idioma, notas por arista, resúmenes de subgrafo.
- Integración viva del botón on-demand si no hay vía barata (ver §5).

## Criterios de aceptación

1. **Gate demostrable:** dos runs seguidos de `cc annotate` sin cambios en el código → el segundo hace CERO llamadas LLM (verificable en el reporte: 0 generadas, N cacheadas).
2. Editar UNA función → solo su nota se regenera.
3. Incrementar `PROMPT_VERSION` → todas se regeneran (y `--force` equivale).
4. Todo contenido de nota es visualmente distinguible de lo determinista en el render, con etiqueta de modelo.
5. Nota desactualizada (hash drift sin re-annotate) se muestra como desactualizada.
6. `cc compile` sin `.env` LLM: output byte-idéntico a Fase 1. `graph.json` byte-idéntico con o sin Fase 2.
7. **Anti-paráfrasis validado por Josem:** muestreo de ~10 notas del batch de agora; una nota que re-narra el código cuenta como fallo. Mismo protocolo que el eval de Fase 1: la firma es humana.
8. El mismo `notes.json` es regenerable contra el otro proveedor cambiando solo el `.env` (test manual casa→BNP cuando haya acceso).

## Orden de construcción sugerido

1. Config + interfaz + adapter `anthropic` (con caching) — probar con 1 nodo a mano.
2. Overlay + gate + `cc annotate` batch con scope por rol.
3. Prompt anti-paráfrasis + iteración contra agora real (aquí se quema el `PROMPT_VERSION` 1→2→3, es lo esperado).
4. Render (bloque inferred + aviso de drift).
5. Adapter `openai_compatible` — testeable en casa contra cualquier servidor local OpenAI-compatible antes de tocar BNP.