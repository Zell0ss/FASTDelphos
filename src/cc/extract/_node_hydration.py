"""Single shared hydration point for function-type graph Nodes.

Four different extractors (endpoints, calls-caller, calls-callee, sql) each
need to emit a `function`-type `Node` for a given def. Before this module
existed they each computed `line`/`hash` independently and disagreed —
notably on whether decorators counted. That disagreement is what causes the
strict identity check in `graph/build.py` to crash on real repos: decorated
functions (e.g. every FastAPI route handler) get discovered by two disagreeing
paths at once.

Convention (see doc_proyecto/ESQUEMA_POC.md, `## Nodos`):
- `line` = the bare `def`/`async def` line. Decorators excluded, so "go to
  node" lands a human on the definition itself, not on `@router.post(...)`.
- `hash` = the decorator-inclusive span. A decorator is part of the unit's
  meaning (auth, caching, route registration) — editing one must count as
  editing the node, or Phase 2's hash-gated re-generation would miss it.

All four call sites should route through here (`node_from_ast_def` when they
already hold a parsed AST def node, `hydrate_function_node` when they only
have a qualname and need griffe to locate the file) so they can never drift
apart again.
"""

import ast
import pathlib
import warnings

from cc.extract._calls_resolver import SymbolInventory
from cc.graph.hash_util import node_hash
from cc.graph.schema import Node

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def node_from_ast_def(
    def_node: ast.FunctionDef | ast.AsyncFunctionDef,
    file: str,
    qualname: str,
    kind: str,
    is_handler: bool = False,
) -> Node:
    """Canonical function-Node construction from an already-parsed AST def node.

    Convention (see doc_proyecto/ESQUEMA_POC.md): `line` is the bare
    `def`/`async def` line — decorators excluded, for human navigation.
    `hash` covers the decorator-inclusive span — decorators are part of the
    unit's meaning, so editing one must count as an edit to the node.
    """
    def_line = def_node.lineno
    span_start = def_node.decorator_list[0].lineno if def_node.decorator_list else def_node.lineno
    end_line = def_node.end_lineno or def_node.lineno

    return Node(
        id=f"function:{qualname}",
        type="function",
        file=file,
        line=def_line,
        hash=node_hash(file, span_start, end_line),
        inferred=False,
        props={"qualname": qualname, "kind": kind, "is_handler": is_handler},
    )


def parse_module_cached(
    file: pathlib.Path, ast_cache: dict[str, ast.Module | None]
) -> ast.Module:
    """Parse `file`, reusing a prior successful parse from `ast_cache` if present.

    Used by the three main per-file driving loops (endpoints.py, calls.py,
    sql.py), which previously each called `ast.parse` independently —
    3 passes per file. They share this cache (and `_parse_cached`'s own
    on-demand hydration lookups share it too, transparently, since both
    key on the same `str(file)`).

    Raises SyntaxError exactly like ast.parse — callers keep their own
    try/except, since some (calls.py) need the actual exception message
    for their own gap reporting. Only successful parses are cached; a file
    that fails to parse is simply reparsed (and re-raises) on each pass —
    rare in practice, and safer than caching a failure without its message.

    Suppresses warnings raised while parsing (e.g. an unescaped regex
    string — SyntaxWarning on Python 3.12+, DeprecationWarning on 3.11,
    same underlying issue) — the target repo's own code quality is not
    this tool's output to show off. Suppressed broadly, not pinned to one
    category, so this doesn't depend on which Python version runs the tool.
    """
    key = str(file)
    cached = ast_cache.get(key)
    if cached is not None:
        return cached
    source = file.read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tree = ast.parse(source, filename=key)
    ast_cache[key] = tree
    return tree


def _parse_cached(file: str, ast_cache: dict[str, ast.Module | None]) -> ast.Module | None:
    if file in ast_cache:
        return ast_cache[file]
    try:
        source = pathlib.Path(file).read_text(encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source, filename=file)
    except (OSError, SyntaxError, UnicodeDecodeError):
        ast_cache[file] = None
        return None
    ast_cache[file] = tree
    return tree


def hydrate_function_node(
    qualname: str,
    inventory: SymbolInventory,
    ast_cache: dict[str, ast.Module | None],
    is_handler: bool = False,
) -> Node | None:
    """Single source of truth for a function-type Node's file/line/hash.

    griffe (via `inventory`) locates which file `qualname` lives in; that
    file's own AST (parsed once, cached in `ast_cache` for the lifetime of
    a pipeline run) supplies the exact def/decorator lines, delegated to
    `node_from_ast_def` for the actual span computation — so this and any
    caller-side AST fallback always agree on the same math.

    Returns None if griffe has no entry for `qualname`, the file can't be
    (re-)parsed, or no matching def is found in it — callers own their own
    fallback for that case; this function never guesses.
    """
    info = inventory.functions.get(qualname)
    if info is None or info.file == "unknown":
        return None

    tree = _parse_cached(info.file, ast_cache)
    if tree is None:
        return None

    fn_name = qualname.rsplit(".", 1)[-1]
    match = None
    for node in ast.walk(tree):
        if (
            isinstance(node, _DEF_TYPES)
            and node.name == fn_name
            and (node.end_lineno or node.lineno) == info.endlineno
        ):
            match = node
            break
    if match is None:
        return None

    return node_from_ast_def(match, info.file, qualname, info.kind, is_handler=is_handler)
