import pathlib

from cc.extract._calls_resolver import build_symbol_inventory


def _write(root: pathlib.Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    _write(repo, "services/__init__.py", "")
    _write(repo, "services/base.py", (
        "class Greeter:\n"
        "    def greet(self, name: str) -> str:\n"
        "        return f'hello {name}'\n"
    ))
    _write(repo, "services/child.py", (
        "from services.base import Greeter\n\n\n"
        "class LoudGreeter(Greeter):\n"
        "    def shout(self, name: str) -> str:\n"
        "        return self.greet(name).upper()\n"
    ))
    _write(repo, "services/helpers.py", (
        "def extra(text: str) -> str:\n"
        "    return text + '!'\n"
    ))
    return repo


def test_finds_module_level_function(tmp_path):
    repo = _make_repo(tmp_path)
    inv = build_symbol_inventory(repo)
    assert "services.helpers.extra" in inv.functions
    info = inv.functions["services.helpers.extra"]
    assert info.kind == "function"
    assert info.lineno == 1
    assert info.endlineno == 2


def test_finds_methods_with_method_kind(tmp_path):
    repo = _make_repo(tmp_path)
    inv = build_symbol_inventory(repo)
    assert "services.base.Greeter.greet" in inv.functions
    assert inv.functions["services.base.Greeter.greet"].kind == "method"


def test_records_class_bases(tmp_path):
    repo = _make_repo(tmp_path)
    inv = build_symbol_inventory(repo)
    assert inv.class_bases["services.child.LoudGreeter"] == ["services.base.Greeter"]


def test_records_class_methods(tmp_path):
    repo = _make_repo(tmp_path)
    inv = build_symbol_inventory(repo)
    assert inv.class_methods["services.base.Greeter"] == {"greet": "services.base.Greeter.greet"}
    assert inv.class_methods["services.child.LoudGreeter"] == {"shout": "services.child.LoudGreeter.shout"}


def test_top_level_packages_recorded_even_if_load_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _write(repo, "broken/__init__.py", "")
    _write(repo, "broken/oops.py", "def f(:\n")  # SyntaxError — griffe.load will raise
    inv = build_symbol_inventory(repo)
    assert "broken" in inv.top_level_packages  # directory-based, not parse-success-based
    assert "services" in inv.top_level_packages


import ast

from cc.extract._calls_resolver import (
    Resolution,
    SymbolInventory,
    FuncInfo,
    build_import_table,
    classify_call,
    flatten_attribute,
    resolve_method_in_hierarchy,
)


def _parse_import_table(source: str, module_qname: str = "pkg.mod", is_package_init: bool = False):
    tree = ast.parse(source)
    return build_import_table(tree, module_qname, is_package_init)


def test_plain_import_binds_top_level_name():
    table = _parse_import_table("import services.synthesis\n")
    assert table["services"] == "services"


def test_plain_import_with_alias_binds_full_dotted_path():
    table = _parse_import_table("import services.synthesis as syn\n")
    assert table["syn"] == "services.synthesis"


def test_from_import_binds_local_name():
    table = _parse_import_table("from services import synthesis\n")
    assert table["synthesis"] == "services.synthesis"


def test_from_import_with_alias():
    table = _parse_import_table("from services import synthesis as syn\n")
    assert table["syn"] == "services.synthesis"


def test_relative_import_resolved_against_module_package():
    # module "pkg.mod" (a regular module, not __init__.py) -> its own package is "pkg"
    table = _parse_import_table("from .sibling import helper\n", module_qname="pkg.mod", is_package_init=False)
    assert table["helper"] == "pkg.sibling.helper"


def test_relative_import_from_package_init():
    # module "pkg" IS a package (__init__.py) -> "." means "pkg" itself
    table = _parse_import_table("from .sibling import helper\n", module_qname="pkg", is_package_init=True)
    assert table["helper"] == "pkg.sibling.helper"


def test_relative_import_dot_only():
    table = _parse_import_table("from . import sibling\n", module_qname="pkg.mod", is_package_init=False)
    assert table["sibling"] == "pkg.sibling"


def test_imports_inside_function_body_are_not_tracked():
    table = _parse_import_table("def f():\n    import os\n")
    assert "os" not in table


def test_imports_inside_module_level_if_are_tracked():
    table = _parse_import_table("if True:\n    import os\n")
    assert table["os"] == "os"


def test_flatten_attribute_simple_name():
    node = ast.parse("x", mode="eval").body
    assert flatten_attribute(node) == ["x"]


def test_flatten_attribute_dotted_chain():
    node = ast.parse("a.b.c", mode="eval").body
    assert flatten_attribute(node) == ["a", "b", "c"]


def test_flatten_attribute_none_on_call_base():
    node = ast.parse("f().attr", mode="eval").body
    assert flatten_attribute(node) is None


def _inventory_with(functions=None, class_bases=None, class_methods=None, top_level=None):
    return SymbolInventory(
        functions=functions or {},
        class_bases=class_bases or {},
        class_methods=class_methods or {},
        top_level_packages=top_level or set(),
    )


def test_resolve_method_in_hierarchy_direct():
    inv = _inventory_with(class_methods={"pkg.Foo": {"bar": "pkg.Foo.bar"}})
    assert resolve_method_in_hierarchy(inv, "pkg.Foo", "bar") == "pkg.Foo.bar"


def test_resolve_method_in_hierarchy_inherited():
    inv = _inventory_with(
        class_bases={"pkg.Child": ["pkg.Base"]},
        class_methods={"pkg.Base": {"greet": "pkg.Base.greet"}},
    )
    assert resolve_method_in_hierarchy(inv, "pkg.Child", "greet") == "pkg.Base.greet"


def test_resolve_method_in_hierarchy_not_found():
    inv = _inventory_with(class_bases={"pkg.Child": ["pkg.Base"]})
    assert resolve_method_in_hierarchy(inv, "pkg.Child", "missing") is None


def test_resolve_method_in_hierarchy_cycle_safe():
    inv = _inventory_with(class_bases={"pkg.A": ["pkg.B"], "pkg.B": ["pkg.A"]})
    assert resolve_method_in_hierarchy(inv, "pkg.A", "whatever") is None


def _call(source: str) -> ast.Call:
    return ast.parse(source, mode="eval").body


def test_classify_case1_module_local_function():
    inv = _inventory_with(functions={"pkg.mod.helper": FuncInfo("pkg.mod.helper", "f.py", 1, 1, "function")})
    res = classify_call(_call("helper(1)"), import_table={}, module_qname="pkg.mod",
                         class_qname=None, inventory=inv)
    assert res == Resolution(kind="internal", qualname="pkg.mod.helper")


def test_classify_case1_imported_name():
    inv = _inventory_with(functions={"services.synthesis.build_context": FuncInfo(
        "services.synthesis.build_context", "f.py", 1, 1, "function")})
    table = {"build_context": "services.synthesis.build_context"}
    res = classify_call(_call("build_context(1)"), import_table=table, module_qname="main",
                         class_qname=None, inventory=inv)
    assert res == Resolution(kind="internal", qualname="services.synthesis.build_context")


def test_classify_case2_attribute_on_aliased_dotted_import():
    inv = _inventory_with(functions={"services.helpers.extra": FuncInfo(
        "services.helpers.extra", "f.py", 1, 1, "function")})
    table = {"helpers_mod": "services.helpers"}
    res = classify_call(_call("helpers_mod.extra(1)"), import_table=table, module_qname="services.synthesis",
                         class_qname=None, inventory=inv)
    assert res == Resolution(kind="internal", qualname="services.helpers.extra")


def test_classify_case2_plain_dotted_import_three_levels():
    inv = _inventory_with(functions={"services.synthesis.build_context": FuncInfo(
        "services.synthesis.build_context", "f.py", 1, 1, "function")})
    table = {"services": "services"}
    res = classify_call(_call("services.synthesis.build_context(1)"), import_table=table,
                         module_qname="other", class_qname=None, inventory=inv)
    assert res == Resolution(kind="internal", qualname="services.synthesis.build_context")


def test_classify_case3_self_inherited_method():
    inv = _inventory_with(
        class_bases={"services.child.LoudGreeter": ["services.base.Greeter"]},
        class_methods={"services.base.Greeter": {"greet": "services.base.Greeter.greet"}},
    )
    res = classify_call(_call("self.greet(name)"), import_table={}, module_qname="services.child",
                         class_qname="services.child.LoudGreeter", inventory=inv)
    assert res == Resolution(kind="internal", qualname="services.base.Greeter.greet")


def test_classify_external_import_outside_repo():
    inv = _inventory_with(top_level=set())  # "logging" is not a repo package
    table = {"logging": "logging"}
    res = classify_call(_call("logging.info('x')"), import_table=table, module_qname="services.synthesis",
                         class_qname=None, inventory=inv)
    assert res == Resolution(kind="external", package="logging")


def test_classify_dynamic_default_for_unknown_name():
    inv = _inventory_with()
    res = classify_call(_call("mystery(1)"), import_table={}, module_qname="pkg.mod",
                         class_qname=None, inventory=inv)
    assert res == Resolution(kind="dynamic")


def test_classify_dynamic_for_chained_attribute():
    inv = _inventory_with()
    res = classify_call(_call("get_obj().method(1)"), import_table={}, module_qname="pkg.mod",
                         class_qname=None, inventory=inv)
    assert res == Resolution(kind="dynamic")


def test_classify_dynamic_for_subscript_dispatch():
    inv = _inventory_with()
    res = classify_call(_call("handlers[key](1)"), import_table={}, module_qname="pkg.mod",
                         class_qname=None, inventory=inv)
    assert res == Resolution(kind="dynamic")


def test_classify_dynamic_for_builtin_with_no_import_evidence():
    # `getattr` is never imported — no positive evidence it's external, so it's
    # dynamic, not external. See VISITOR.md addendum point 1.
    inv = _inventory_with()
    res = classify_call(_call("getattr(obj, name)"), import_table={}, module_qname="pkg.mod",
                         class_qname=None, inventory=inv)
    assert res == Resolution(kind="dynamic")
