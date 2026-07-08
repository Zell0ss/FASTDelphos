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
