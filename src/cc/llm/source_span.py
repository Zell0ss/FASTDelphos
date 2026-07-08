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
