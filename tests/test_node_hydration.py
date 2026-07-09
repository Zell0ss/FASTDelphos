import ast
import warnings

from cc.extract._calls_resolver import FuncInfo, SymbolInventory
from cc.extract._node_hydration import hydrate_function_node, node_from_ast_def, parse_module_cached
from cc.graph.hash_util import node_hash


def _parse_def(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(source)
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))


def test_node_from_ast_def_line_excludes_decorators(tmp_path):
    f = tmp_path / "mod.py"
    source = "@audit\nasync def get_active_roster(cur):\n    return 1\n"
    f.write_text(source, encoding="utf-8")
    def_node = _parse_def(source)
    node = node_from_ast_def(def_node, str(f), "mod.get_active_roster", "function")
    assert node.line == 2  # the `async def` line, not the decorator's line 1


def test_node_from_ast_def_hash_includes_decorators(tmp_path):
    f = tmp_path / "mod.py"
    source = "@audit\nasync def get_active_roster(cur):\n    return 1\n"
    f.write_text(source, encoding="utf-8")
    def_node = _parse_def(source)
    node = node_from_ast_def(def_node, str(f), "mod.get_active_roster", "function")
    assert node.hash == node_hash(f, 1, 3)  # decorator (line 1) through end (line 3)


def test_node_from_ast_def_undecorated_span_starts_at_def_line(tmp_path):
    f = tmp_path / "mod.py"
    source = "def plain():\n    return 1\n"
    f.write_text(source, encoding="utf-8")
    def_node = _parse_def(source)
    node = node_from_ast_def(def_node, str(f), "mod.plain", "function")
    assert node.line == 1
    assert node.hash == node_hash(f, 1, 2)


def test_node_from_ast_def_sets_id_and_props(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("def f():\n    pass\n", encoding="utf-8")
    def_node = _parse_def("def f():\n    pass\n")
    node = node_from_ast_def(def_node, str(f), "mod.f", "method", is_handler=True)
    assert node.id == "function:mod.f"
    assert node.type == "function"
    assert node.inferred is False
    assert node.props == {"qualname": "mod.f", "kind": "method", "is_handler": True}


def test_hydrate_function_node_uses_griffe_location_and_ast_span(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "@audit\nasync def get_active_roster(cur):\n    return 1\n",
        encoding="utf-8",
    )
    inventory = SymbolInventory(
        functions={
            "mod.get_active_roster": FuncInfo(
                qualname="mod.get_active_roster",
                file=str(f),
                # griffe's own (decorator-inclusive) lineno — irrelevant, re-derived from AST
                lineno=1,
                endlineno=3,
                kind="function",
            )
        }
    )
    node = hydrate_function_node("mod.get_active_roster", inventory, {})
    assert node is not None
    assert node.line == 2
    assert node.hash == node_hash(f, 1, 3)


def test_hydrate_function_node_returns_none_when_not_in_inventory():
    node = hydrate_function_node("mod.missing", SymbolInventory(functions={}), {})
    assert node is None


def test_hydrate_function_node_returns_none_when_file_is_unknown():
    inventory = SymbolInventory(
        functions={
            "mod.f": FuncInfo(
                qualname="mod.f", file="unknown", lineno=1, endlineno=1, kind="function"
            )
        }
    )
    node = hydrate_function_node("mod.f", inventory, {})
    assert node is None


def test_hydrate_function_node_caches_the_parsed_ast_per_file(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "def a():\n    return 1\n\n\ndef b():\n    return 2\n",
        encoding="utf-8",
    )
    inventory = SymbolInventory(
        functions={
            "mod.a": FuncInfo(
                qualname="mod.a", file=str(f), lineno=1, endlineno=2, kind="function"
            ),
            "mod.b": FuncInfo(
                qualname="mod.b", file=str(f), lineno=5, endlineno=6, kind="function"
            ),
        }
    )
    cache: dict = {}
    node_a = hydrate_function_node("mod.a", inventory, cache)
    node_b = hydrate_function_node("mod.b", inventory, cache)
    assert node_a is not None and node_a.line == 1
    assert node_b is not None and node_b.line == 5
    assert len(cache) == 1  # one file, parsed once, reused for both lookups


def test_hydrate_function_node_returns_none_on_invalid_utf8(tmp_path):
    f = tmp_path / "broken.py"
    # Write file with invalid UTF-8 bytes (latin-1 encoded é character)
    f.write_bytes(b"def broken():\n    x = '\xe9'\n")
    inventory = SymbolInventory(
        functions={
            "broken.broken": FuncInfo(
                qualname="broken.broken",
                file=str(f),
                lineno=1,
                endlineno=2,
                kind="function",
            )
        }
    )
    node = hydrate_function_node("broken.broken", inventory, {})
    assert node is None  # gracefully returns None instead of raising UnicodeDecodeError


def test_parse_module_cached_reuses_prior_successful_parse(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def a():\n    pass\n", encoding="utf-8")
    ast_cache: dict = {}
    first = parse_module_cached(f, ast_cache)
    second = parse_module_cached(f, ast_cache)
    assert first is second  # identical object -> genuinely reused, not re-parsed


def test_parse_module_cached_raises_syntax_error_uncached(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("def f(:\n", encoding="utf-8")
    ast_cache: dict = {}
    import pytest

    with pytest.raises(SyntaxError):
        parse_module_cached(f, ast_cache)
    assert f not in ast_cache and str(f) not in ast_cache


def test_parse_module_cached_suppresses_parse_warnings(tmp_path):
    # An unescaped regex string (real illumiows case) triggers a warning at
    # parse time — SyntaxWarning on Python 3.12+, DeprecationWarning on
    # 3.11 (verified empirically: same underlying issue, different category
    # depending on interpreter version). Suppress broadly rather than pin
    # to one category, so this doesn't silently stop working on whichever
    # Python actually runs the tool.
    f = tmp_path / "regex.py"
    f.write_text(r'import re' + "\n" + r"re.compile('\d+')" + "\n", encoding="utf-8")
    ast_cache: dict = {}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        parse_module_cached(f, ast_cache)  # must not raise despite the invalid escape
