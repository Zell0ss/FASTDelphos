# AST Call Visitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace pyan3 with a self-contained `ast.NodeVisitor`-based call-graph extractor that resolves `calls` edges against the existing griffe symbol inventory, per `doc_proyecto/VISITOR.md` (spec + addendum, decided 2026-07-04/05).

**Architecture:** A new pure-logic module (`extract/_calls_resolver.py`) builds a repo-wide griffe symbol inventory (functions, methods, class hierarchies, top-level package names) and classifies any AST call site into exactly one of three buckets: `internal` (resolved via 3 documented cases — direct name, attribute-on-import, self/cls-in-hierarchy), `external` (positively matched to an import rooted outside the repo's own packages), or `dynamic` (everything else, the default). The orchestrator (`extract/calls.py`) walks every file with `ast`, folds nested/closure defs into their nearest named ancestor function, and for each resolved-internal call emits a `calls` Edge plus hydrated `function` Node stubs for both ends (caller from its own AST location, callee from the griffe inventory) — closing a gap where `build_graph` silently drops edges whose endpoints have no Node. `build_graph` is also updated to report dropped edges instead of discarding them silently.

**Tech Stack:** `ast` (stdlib), `griffe` (already a dependency, used the same way as `extract/models.py`). `pyan3` dependency is removed entirely — no coexistence flag.

## Global Constraints

- **Clean replacement.** No `--calls-engine` flag, no dead pyan3 code path left behind. `pyan3>=1.4` removed from `pyproject.toml`.
- **Exactly 3 resolution cases (Level 1).** Direct name (module-local or imported), attribute-on-import (any depth, any import style), self/cls method lookup via class hierarchy. If a 4th "cheap and obvious" case is discovered mid-implementation, STOP and ask — do not add it silently (per `VISITOR.md` §Notas).
- **3-bucket classification, not 2.** `resolved_internal` (edge), `resolved_external` (aggregate count only, never a gap, never in the comprehension-coverage denominator), `unresolved_dynamic` (default). A call site is only ever `external` when the import table positively proves its root package lives outside the repo's own top-level packages — never as a fallback for "couldn't figure it out."
- **Determinism.** Same code → identical output across runs. `collect_py_files` already returns a sorted list; per-file iteration order must not be re-randomized (no unsorted `set` iteration feeding into edges/nodes/coverage).
- **Anchors unchanged.** Node `id` = `function:{qualname}`, `hash` = `node_hash(file, lineno, end_lineno)` — same convention as every other extractor.
- **Real hydration, not stubs.** Every `function` Node the visitor emits carries a real file/line span — callee nodes from the griffe inventory (`SymbolInventory`), caller nodes from the AST node of the file currently being walked. Never `line=1` / `hash="0"*64` placeholders.
- **`tool_limitation` gap mechanism is kept**, now triggered by `ast.parse` `SyntaxError` (should be near-unreachable — `ast.parse` accepts any valid Python) instead of pyan3 crashes.
- **Node merge policy (by id, first-registered wins)** in `pipeline.py`: `ep_nodes + model_nodes + sql_nodes + call_nodes` — in that order, so a handler/DB-derived Node (which knows `is_handler`) always wins over the generic function stub the call visitor would otherwise contribute for the same id.
- **No silent drops anywhere in the pipeline.** `build_graph` must report (not silently discard) edges whose endpoints have no Node.

---

### Task 1: Griffe symbol inventory

**Files:**
- Create: `src/cc/extract/_calls_resolver.py`
- Test: `tests/test_calls_resolver.py`

**Interfaces:**
- Produces: `FuncInfo` (dataclass: `qualname: str, file: str, lineno: int, endlineno: int, kind: str`), `SymbolInventory` (dataclass: `functions: dict[str, FuncInfo]`, `class_bases: dict[str, list[str]]`, `class_methods: dict[str, dict[str, str]]`, `top_level_packages: set[str]`), `build_symbol_inventory(repo_path: str | pathlib.Path) -> SymbolInventory`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calls_resolver.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calls_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc.extract._calls_resolver'`

- [ ] **Step 3: Write the implementation**

Create `src/cc/extract/_calls_resolver.py`:

```python
"""Pure resolution logic for the AST call-graph visitor.

Given a repo-wide griffe symbol inventory and a per-file AST import table,
classify a call site into exactly one of three buckets: resolved_internal,
resolved_external, or unresolved_dynamic. No file walking here — see calls.py
for the orchestrator that drives this module across the repo.
"""
import ast
import pathlib
import sys
from dataclasses import dataclass, field

import griffe

_SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", ".tox", "dist", "build", "tests"}


@dataclass
class FuncInfo:
    qualname: str
    file: str
    lineno: int
    endlineno: int
    kind: str  # "function" | "method"


@dataclass
class SymbolInventory:
    functions: dict[str, FuncInfo] = field(default_factory=dict)
    class_bases: dict[str, list[str]] = field(default_factory=dict)
    class_methods: dict[str, dict[str, str]] = field(default_factory=dict)
    top_level_packages: set[str] = field(default_factory=set)


def _walk_griffe_functions(obj, inv: SymbolInventory, class_stack: list[str]) -> None:
    if isinstance(obj, griffe.Alias):
        return

    if isinstance(obj, griffe.Function):
        qname = obj.canonical_path
        kind = "method" if class_stack else "function"
        inv.functions[qname] = FuncInfo(
            qualname=qname,
            file=str(obj.filepath) if obj.filepath else "unknown",
            lineno=obj.lineno or 1,
            endlineno=obj.endlineno or (obj.lineno or 1),
            kind=kind,
        )
        if class_stack:
            inv.class_methods.setdefault(class_stack[-1], {})[obj.name] = qname
        return  # functions carry no nested defs worth walking into

    if isinstance(obj, griffe.Class):
        qname = obj.canonical_path
        bases = []
        for b in obj.bases or []:
            try:
                bases.append(b.canonical_path if hasattr(b, "canonical_path") else str(b))
            except Exception:
                bases.append(str(b))
        inv.class_bases[qname] = bases
        class_stack = class_stack + [qname]

    if hasattr(obj, "members"):
        for child in obj.members.values():
            _walk_griffe_functions(child, inv, class_stack)


def build_symbol_inventory(repo_path: str | pathlib.Path) -> SymbolInventory:
    """Load the repo's own top-level packages via griffe and collect every
    function/method qualname, class base-class relationship, and the set of
    top-level package names that belong to the repo (used later to tell
    "external" imports from "internal but unresolved" ones).
    """
    repo_path = pathlib.Path(repo_path)
    inv = SymbolInventory()

    def _try_load(pkg_name: str, search_paths: list[pathlib.Path]) -> None:
        sys.path.insert(0, str(search_paths[0]))
        try:
            pkg = griffe.load(pkg_name, search_paths=search_paths)
            _walk_griffe_functions(pkg, inv, [])
        except Exception:
            pass
        finally:
            try:
                sys.path.remove(str(search_paths[0]))
            except ValueError:
                pass

    loaded_any = False
    for init in repo_path.glob("*/__init__.py"):
        if init.parent.name in _SKIP_DIRS:
            continue
        # Recorded unconditionally — a package that exists but fails to parse
        # is still internal, never "external", even though its own symbols
        # won't make it into `functions`.
        inv.top_level_packages.add(init.parent.name)
        _try_load(init.parent.name, [repo_path])
        loaded_any = True

    if not loaded_any and (repo_path / "__init__.py").exists():
        inv.top_level_packages.add(repo_path.name)
        _try_load(repo_path.name, [repo_path.parent])

    return inv
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calls_resolver.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/_calls_resolver.py tests/test_calls_resolver.py
git commit -m "feat: griffe symbol inventory for the AST call visitor"
```

---

### Task 2: Import table + call-site classifier

**Files:**
- Modify: `src/cc/extract/_calls_resolver.py` (append)
- Test: `tests/test_calls_resolver.py` (append)

**Interfaces:**
- Consumes: `SymbolInventory` from Task 1.
- Produces: `build_import_table(tree: ast.Module, module_qname: str, is_package_init: bool) -> dict[str, str]`, `flatten_attribute(node: ast.expr) -> list[str] | None`, `resolve_method_in_hierarchy(inv: SymbolInventory, class_qname: str, method_name: str) -> str | None`, `Resolution` (dataclass: `kind: str` — `"internal" | "external" | "dynamic"`, `qualname: str | None`, `package: str | None`), `classify_call(call: ast.Call, *, import_table: dict[str, str], module_qname: str, class_qname: str | None, inventory: SymbolInventory) -> Resolution`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_calls_resolver.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calls_resolver.py -v`
Expected: FAIL — `ImportError: cannot import name 'Resolution'` (and friends) from `cc.extract._calls_resolver`

- [ ] **Step 3: Write the implementation**

Append to `src/cc/extract/_calls_resolver.py`:

```python
def _module_level_import_nodes(tree: ast.Module):
    """Yield ast.Import / ast.ImportFrom nodes reachable at module scope —
    including inside module-level `if`/`try` blocks, but NOT inside function
    or class bodies (a local import only rebinds a name within that function).
    """
    def _walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                yield child
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            else:
                yield from _walk(child)
    yield from _walk(tree)


def _relative_package(module_qname: str, is_package_init: bool, level: int) -> str | None:
    """Resolve `level` leading dots of a relative import to an absolute package prefix.

    `is_package_init` distinguishes a package's own `__init__.py` (whose
    containing package IS `module_qname`) from a regular module (whose
    containing package is `module_qname` minus its last component).
    """
    parts = module_qname.split(".") if module_qname else []
    base = parts if is_package_init else parts[:-1]
    trim = level - 1
    if trim:
        if trim > len(base):
            return None
        base = base[: len(base) - trim]
    return ".".join(base) if base else None


def build_import_table(tree: ast.Module, module_qname: str, is_package_init: bool) -> dict[str, str]:
    """Map each module-level imported local name to its absolute dotted qualname prefix."""
    table: dict[str, str] = {}
    for node in _module_level_import_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                qualname = alias.name if alias.asname else alias.name.split(".")[0]
                table[local] = qualname
        else:  # ast.ImportFrom
            if node.level:
                base = _relative_package(module_qname, is_package_init, node.level)
                if base is None:
                    continue
                module = f"{base}.{node.module}" if node.module else base
            elif node.module is not None:
                module = node.module
            else:
                continue
            for alias in node.names:
                local = alias.asname or alias.name
                table[local] = f"{module}.{alias.name}"
    return table


def flatten_attribute(node: ast.expr) -> list[str] | None:
    """Turn a Name/Attribute chain into its dotted parts (`a.b.c` -> ["a","b","c"]).

    Returns None if the chain includes anything other than Name/Attribute
    (a call result, a subscript, ...) — that signals a dynamic/chained base.
    """
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        base = flatten_attribute(node.value)
        if base is None:
            return None
        return base + [node.attr]
    return None


def resolve_method_in_hierarchy(
    inv: SymbolInventory, class_qname: str, method_name: str, _seen: set[str] | None = None,
) -> str | None:
    """Look up `method_name` on `class_qname`, then walk up its base classes."""
    if _seen is None:
        _seen = set()
    if class_qname in _seen:
        return None
    _seen.add(class_qname)

    methods = inv.class_methods.get(class_qname, {})
    if method_name in methods:
        return methods[method_name]
    for base in inv.class_bases.get(class_qname, []):
        found = resolve_method_in_hierarchy(inv, base, method_name, _seen)
        if found:
            return found
    return None


@dataclass
class Resolution:
    kind: str  # "internal" | "external" | "dynamic"
    qualname: str | None = None
    package: str | None = None


def _classify_qualname(qualname: str, inventory: SymbolInventory) -> Resolution:
    if qualname in inventory.functions:
        return Resolution(kind="internal", qualname=qualname)
    top = qualname.split(".")[0]
    if top not in inventory.top_level_packages:
        return Resolution(kind="external", package=top)
    return Resolution(kind="dynamic")


def classify_call(
    call: ast.Call,
    *,
    import_table: dict[str, str],
    module_qname: str,
    class_qname: str | None,
    inventory: SymbolInventory,
) -> Resolution:
    func = call.func

    if isinstance(func, ast.Name):
        name = func.id
        candidate = f"{module_qname}.{name}"
        if candidate in inventory.functions:
            return Resolution(kind="internal", qualname=candidate)
        prefix = import_table.get(name)
        if prefix is not None:
            return _classify_qualname(prefix, inventory)
        return Resolution(kind="dynamic")

    if isinstance(func, ast.Attribute):
        parts = flatten_attribute(func)
        if parts is None:
            return Resolution(kind="dynamic")

        base_name, attr = parts[0], parts[-1]

        if base_name in ("self", "cls") and class_qname is not None and len(parts) == 2:
            resolved = resolve_method_in_hierarchy(inventory, class_qname, attr)
            if resolved:
                return Resolution(kind="internal", qualname=resolved)
            return Resolution(kind="dynamic")

        prefix = import_table.get(base_name)
        if prefix is not None:
            rest = ".".join(parts[1:])
            full = f"{prefix}.{rest}" if rest else prefix
            return _classify_qualname(full, inventory)

        return Resolution(kind="dynamic")

    return Resolution(kind="dynamic")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calls_resolver.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/cc/extract/_calls_resolver.py tests/test_calls_resolver.py
git commit -m "feat: import table + 3-case call classifier with external/dynamic buckets"
```

---

### Task 3: Rewrite `calls.py` orchestrator, remove pyan3

**Files:**
- Modify: `src/cc/extract/calls.py` (full rewrite)
- Modify: `pyproject.toml` (remove `pyan3>=1.4`)
- Modify: `tests/conftest.py` (add `CALLS_REPO` path)
- Create: `tests/fixtures/calls_repo/__init__.py`
- Create: `tests/fixtures/calls_repo/services/__init__.py`
- Create: `tests/fixtures/calls_repo/services/base.py`
- Create: `tests/fixtures/calls_repo/services/child.py`
- Create: `tests/fixtures/calls_repo/services/helpers.py`
- Create: `tests/fixtures/calls_repo/services/synthesis.py`
- Create: `tests/fixtures/calls_repo/main.py`
- Create: `tests/fixtures/calls_repo/other.py`
- Modify: `tests/test_calls.py` (rewrite against the new fixture and new return signature)

**Interfaces:**
- Consumes: `build_symbol_inventory`, `build_import_table`, `classify_call`, `Resolution`, `SymbolInventory` from `_calls_resolver.py` (Tasks 1-2).
- Produces: `extract_calls(repo_path: str | pathlib.Path) -> tuple[list[Node], list[Edge], list[tuple[str, str]], dict]` where the last element is `{"per_file": {rel_path: counts}, "total": counts}` and `counts = {"functions": int, "call_sites": int, "resolved_internal": int, "resolved_external": int, "unresolved_dynamic": int}`.

- [ ] **Step 1: Write the fixture package**

Create `tests/fixtures/calls_repo/__init__.py` (empty):

```python
```

Create `tests/fixtures/calls_repo/services/__init__.py` (empty):

```python
```

Create `tests/fixtures/calls_repo/services/base.py`:

```python
class Greeter:
    def greet(self, name: str) -> str:
        return f"hello {name}"
```

Create `tests/fixtures/calls_repo/services/child.py`:

```python
from services.base import Greeter


class LoudGreeter(Greeter):
    def shout(self, name: str) -> str:
        return self.greet(name).upper()
```

Create `tests/fixtures/calls_repo/services/helpers.py`:

```python
def extra(text: str) -> str:
    return text + "!"
```

Create `tests/fixtures/calls_repo/services/synthesis.py`:

```python
import logging

import services.helpers as helpers_mod


def _compress(text: str) -> str:
    return text[:10]


def build_context(text: str) -> str:
    short = _compress(text)
    extra_text = helpers_mod.extra(short)
    logging.info("built context: %s", short)
    return short + extra_text


async def build_context_async(text: str) -> str:
    return await build_context(text)


def dynamic_dispatch(handlers: dict, key: str, text: str) -> str:
    return handlers[key](text)
```

Create `tests/fixtures/calls_repo/main.py`:

```python
from services.synthesis import build_context
from services import synthesis as syn


def handler(text: str) -> str:
    return build_context(text)


def handler_via_module(text: str) -> str:
    return syn.build_context(text)
```

Create `tests/fixtures/calls_repo/other.py`:

```python
import services.synthesis


def call_dotted(text: str) -> str:
    return services.synthesis.build_context(text)
```

- [ ] **Step 2: Register the fixture path**

Modify `tests/conftest.py`:

```python
import pathlib

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SIMPLE_API = FIXTURES / "simple_api"
CALLS_REPO = FIXTURES / "calls_repo"
```

- [ ] **Step 3: Write the failing tests**

Rewrite `tests/test_calls.py`:

```python
from cc.extract.calls import extract_calls
from tests.conftest import CALLS_REPO


def test_returns_four_tuple():
    nodes, edges, excluded, coverage = extract_calls(CALLS_REPO)
    assert isinstance(nodes, list)
    assert isinstance(edges, list)
    assert isinstance(excluded, list)
    assert isinstance(coverage, dict)


def test_calls_edges_have_correct_type():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    for e in edges:
        assert e.type == "calls"
        assert e.inferred is False
        assert e.from_.startswith("function:")
        assert e.to.startswith("function:")


def test_no_self_loops():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    for e in edges:
        assert e.from_ != e.to


def test_function_nodes_are_hydrated_not_placeholders():
    nodes, _, _, _ = extract_calls(CALLS_REPO)
    by_id = {n.id: n for n in nodes}
    callee = by_id["function:services.helpers.extra"]
    assert callee.line == 1
    assert callee.hash != "0" * 64
    assert callee.props["qualname"] == "services.helpers.extra"
    assert callee.props["is_handler"] is False


def test_case1_direct_name_same_module():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert ("function:services.synthesis.build_context",
            "function:services.synthesis._compress") in pairs


def test_case1_imported_name_called_directly():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert ("function:main.handler", "function:services.synthesis.build_context") in pairs


def test_case2_attribute_on_aliased_dotted_import():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert ("function:services.synthesis.build_context",
            "function:services.helpers.extra") in pairs


def test_case2_from_import_as_module_plus_attribute():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert ("function:main.handler_via_module", "function:services.synthesis.build_context") in pairs


def test_case2_plain_dotted_import_three_levels():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert ("function:other.call_dotted", "function:services.synthesis.build_context") in pairs


def test_case3_inherited_method_across_modules():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert ("function:services.child.LoudGreeter.shout",
            "function:services.base.Greeter.greet") in pairs


def test_async_await_unwrapped_without_special_casing():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert ("function:services.synthesis.build_context_async",
            "function:services.synthesis.build_context") in pairs


def test_dynamic_dispatch_produces_no_edge():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    froms = {e.from_ for e in edges}
    assert "function:services.synthesis.dynamic_dispatch" not in froms


def test_coverage_totals_match_fixture():
    _, _, _, coverage = extract_calls(CALLS_REPO)
    total = coverage["total"]
    assert total["functions"] == 10
    assert total["call_sites"] == 10
    assert total["resolved_internal"] == 7
    assert total["resolved_external"] == 1
    assert total["unresolved_dynamic"] == 2


def test_coverage_per_file_has_synthesis_entry():
    _, _, _, coverage = extract_calls(CALLS_REPO)
    synth = coverage["per_file"]["services/synthesis.py"]
    assert synth["resolved_external"] == 1  # logging.info


def test_excluded_is_list_of_tuples():
    _, _, excluded, _ = extract_calls(CALLS_REPO)
    for filepath, error in excluded:
        assert isinstance(filepath, str)
        assert isinstance(error, str)


def test_syntax_error_file_is_excluded_not_silently_dropped(tmp_path):
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    nodes, edges, excluded, coverage = extract_calls(tmp_path)
    assert len(excluded) == 1
    assert str(tmp_path / "broken.py") == excluded[0][0]
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_calls.py -v`
Expected: FAIL — `extract_calls` still returns a 2-tuple (pyan3-based implementation) and doesn't know `CALLS_REPO`.

- [ ] **Step 5: Write the implementation**

Rewrite `src/cc/extract/calls.py`:

```python
import ast
import pathlib

from cc.extract._calls_resolver import build_import_table, build_symbol_inventory, classify_call
from cc.extract._collect import collect_py_files
from cc.graph.hash_util import node_hash
from cc.graph.schema import Edge, Node


def _module_qualname(file: pathlib.Path, root: pathlib.Path) -> str:
    rel = file.relative_to(root).with_suffix("")
    if rel.name == "__init__":
        rel = rel.parent
    return str(rel).replace("/", ".").replace("\\", ".")


def _iter_named_defs(tree, class_stack=None):
    """Yield (fn_node, class_stack) for every named function/method.

    Nested (closure) defs are NOT yielded on their own — their call sites are
    folded into the nearest enclosing named function via ast.walk(fn_node) in
    extract_calls, since griffe doesn't track function-local defs as symbols.
    """
    class_stack = class_stack or []
    for child in ast.iter_child_nodes(tree):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield child, list(class_stack)
        elif isinstance(child, ast.ClassDef):
            yield from _iter_named_defs(child, class_stack + [child.name])
        else:
            yield from _iter_named_defs(child, class_stack)


def _zero_counts() -> dict:
    return {"functions": 0, "call_sites": 0, "resolved_internal": 0,
             "resolved_external": 0, "unresolved_dynamic": 0}


def extract_calls(
    repo_path: str | pathlib.Path,
) -> tuple[list[Node], list[Edge], list[tuple[str, str]], dict]:
    """Return (function nodes, call edges, [(excluded_file, error_msg)], coverage).

    coverage = {"per_file": {rel_path: counts}, "total": counts} where
    counts = {"functions", "call_sites", "resolved_internal",
              "resolved_external", "unresolved_dynamic"}.
    """
    repo_path = pathlib.Path(repo_path)
    files = collect_py_files(repo_path)
    if not files:
        return [], [], [], {"per_file": {}, "total": _zero_counts()}

    inventory = build_symbol_inventory(repo_path)

    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    seen_edges: set[tuple[str, str]] = set()
    excluded: list[tuple[str, str]] = []
    per_file: dict[str, dict] = {}

    for file in files:
        source = file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(file))
        except SyntaxError as exc:
            excluded.append((str(file), str(exc)))
            continue

        module_qname = _module_qualname(file, repo_path)
        is_package_init = file.name == "__init__.py"
        import_table = build_import_table(tree, module_qname, is_package_init)
        rel = str(file.relative_to(repo_path))
        counts = _zero_counts()

        for fn_node, class_stack in _iter_named_defs(tree):
            fn_qualname = ".".join([module_qname] + class_stack + [fn_node.name])
            class_qname = ".".join([module_qname] + class_stack) if class_stack else None
            counts["functions"] += 1

            caller_id = f"function:{fn_qualname}"
            end_lineno = fn_node.end_lineno or fn_node.lineno
            nodes.setdefault(caller_id, Node(
                id=caller_id, type="function", file=str(file),
                line=fn_node.lineno,
                hash=node_hash(file, fn_node.lineno, end_lineno),
                inferred=False,
                props={"qualname": fn_qualname, "kind": "method" if class_stack else "function",
                       "is_handler": False},
            ))

            for call in ast.walk(fn_node):
                if not isinstance(call, ast.Call):
                    continue
                counts["call_sites"] += 1
                resolution = classify_call(
                    call, import_table=import_table, module_qname=module_qname,
                    class_qname=class_qname, inventory=inventory,
                )
                if resolution.kind == "internal":
                    counts["resolved_internal"] += 1
                    callee_qname = resolution.qualname
                    if callee_qname == fn_qualname:
                        continue  # no self-loops
                    callee_info = inventory.functions[callee_qname]
                    callee_id = f"function:{callee_qname}"
                    nodes.setdefault(callee_id, Node(
                        id=callee_id, type="function", file=callee_info.file,
                        line=callee_info.lineno,
                        hash=node_hash(callee_info.file, callee_info.lineno, callee_info.endlineno),
                        inferred=False,
                        props={"qualname": callee_qname, "kind": callee_info.kind,
                               "is_handler": False},
                    ))
                    key = (caller_id, callee_id)
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append(Edge(from_=caller_id, to=callee_id,
                                           type="calls", inferred=False, props={}))
                elif resolution.kind == "external":
                    counts["resolved_external"] += 1
                else:
                    counts["unresolved_dynamic"] += 1

        per_file[rel] = counts

    total = _zero_counts()
    for counts in per_file.values():
        for k in total:
            total[k] += counts[k]

    return list(nodes.values()), edges, excluded, {"per_file": per_file, "total": total}
```

Modify `pyproject.toml` — remove the pyan3 dependency line:

```toml
dependencies = [
    "griffe>=0.47",
    "sqlglot>=25.0",
]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_calls.py -v`
Expected: PASS (all 16 tests)

- [ ] **Step 7: Reinstall without pyan3 and run the full suite**

Run: `pip uninstall -y pyan3 && pip install -e ".[dev]" && pytest -v`
Expected: PASS — no import of `pyan` remains anywhere (`grep -rn "pyan" src/` returns nothing)

- [ ] **Step 8: Commit**

```bash
git add src/cc/extract/calls.py pyproject.toml tests/conftest.py tests/test_calls.py tests/fixtures/calls_repo
git commit -m "feat: replace pyan3 with ast-based call visitor"
```

---

### Task 4: Wire `pipeline.py` to the new `extract_calls` signature

**Files:**
- Modify: `src/cc/pipeline.py`
- Modify: `tests/test_pipeline.py` (append)

**Interfaces:**
- Consumes: `extract_calls(repo_path) -> (nodes, edges, excluded, coverage)` from Task 3.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline.py`:

```python
def test_pipeline_call_edges_have_nodes_on_both_ends():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        node_ids = {n["id"] for n in data["nodes"]}
        calls_edges = [e for e in data["edges"] if e["type"] == "calls"]
        for e in calls_edges:
            assert e["from_"] in node_ids
            assert e["to"] in node_ids
```

- [ ] **Step 2: Run test to verify it currently fails to even collect**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL — `pipeline.py` still unpacks `extract_calls`'s old 2-tuple, raising `ValueError: not enough values to unpack`

- [ ] **Step 3: Update `pipeline.py`**

Modify `src/cc/pipeline.py`:

```python
import pathlib

from cc.extract._collect import collect_py_files
from cc.extract.endpoints import extract_endpoints
from cc.extract.models import extract_models
from cc.extract.calls import extract_calls
from cc.extract.sql import extract_sql
from cc.gaps import detect_gaps
from cc.graph.build import build_graph
from cc.graph.schema import Gap
from cc.render.emit import emit


def run(repo_path: str | pathlib.Path, out_dir: str | pathlib.Path) -> None:
    repo_path = pathlib.Path(repo_path)
    out_dir = pathlib.Path(out_dir)

    ep_nodes, ep_edges = extract_endpoints(repo_path)
    handler_nodes = [n for n in ep_nodes if n.type == "function"]

    model_nodes, model_edges = extract_models(repo_path, handler_nodes)
    sql_nodes, sql_edges = extract_sql(repo_path)
    call_nodes, call_edges, call_excluded, call_coverage = extract_calls(repo_path)

    # Order matters: build_graph keeps the FIRST node registered per id. Handler
    # nodes (ep_nodes) and DB-touching nodes (sql_nodes) carry more specific
    # props (is_handler=True, etc.) than the generic function stub the call
    # visitor emits for the same id, so they must come first.
    all_nodes = ep_nodes + model_nodes + sql_nodes + call_nodes
    all_edges = ep_edges + model_edges + sql_edges + call_edges

    graph = build_graph(all_nodes, all_edges)
    graph.gaps = detect_gaps(graph)

    for filepath, error in call_excluded:
        rel = pathlib.Path(filepath).relative_to(repo_path)
        graph.gaps.append(Gap(
            kind="tool_limitation",
            where=f"{filepath}:0",
            node_id=None,
            missing=f"Call graph unavailable for `{rel}` — SyntaxError: {error}",
            suggested="Fix the syntax error so `ast.parse` can process the file.",
            severity={"comprehension": "warning", "compliance": "error"},
        ))

    if call_excluded:
        total_files = len(collect_py_files(repo_path))
        excluded_count = len(call_excluded)
        print(
            f"  call graph: {total_files - excluded_count}/{total_files} files analyzed"
            f" ({excluded_count} excluded — see gaps in output)"
        )
        for filepath, error in call_excluded:
            rel = pathlib.Path(filepath).relative_to(repo_path)
            print(f"    excluded: {rel} — {error}")

    total = call_coverage["total"]
    print(
        f"  call graph coverage: {total['resolved_internal']} internal, "
        f"{total['resolved_external']} external, "
        f"{total['unresolved_dynamic']} unresolved_dynamic "
        f"(of {total['call_sites']} call sites across {total['functions']} functions)"
    )

    emit(graph, out_dir)
```

`collect_py_files` moves to the top-level imports (shown above) — remove the old inline `from cc.extract._collect import collect_py_files` that used to live inside the `if call_excluded:` block; the top-level import is simply cleaner now that `pipeline.py` is getting a second coverage-print block right below it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS (all tests, including the new one)

- [ ] **Step 5: Commit**

```bash
git add src/cc/pipeline.py tests/test_pipeline.py
git commit -m "feat: wire pipeline.py to 4-tuple extract_calls, print coverage summary"
```

---

### Task 5: `build_graph` reports dropped edges instead of discarding them silently

**Files:**
- Modify: `src/cc/graph/build.py`
- Modify: `tests/test_graph.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_graph.py`:

```python
def test_dangling_edge_is_reported_not_silently_dropped(capsys):
    nodes = [
        Node(id="function:a", type="function", file="f.py", line=1,
             hash="a" * 64, inferred=False, props={}),
    ]
    edge = Edge(from_="function:a", to="function:missing", type="calls",
                inferred=False, props={})
    graph = build_graph(nodes, [edge])
    assert graph.edges == []  # still dropped — build_graph can't invent a node
    out = capsys.readouterr().out
    assert "function:missing" in out
    assert "1 edge" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_graph.py -v`
Expected: FAIL — no output printed, `capsys.readouterr().out` is empty

- [ ] **Step 3: Update `build_graph`**

Modify `src/cc/graph/build.py`:

```python
from cc.graph.schema import Edge, Graph, Node


def build_graph(nodes: list[Node], edges: list[Edge]) -> Graph:
    seen: dict[str, Node] = {}
    for n in nodes:
        if n.id not in seen:
            seen[n.id] = n

    node_ids = set(seen)
    valid_edges: list[Edge] = []
    dropped: list[Edge] = []
    for e in edges:
        if e.from_ in node_ids and e.to in node_ids:
            valid_edges.append(e)
        else:
            dropped.append(e)

    if dropped:
        print(f"  graph build: {len(dropped)} edge(s) dropped — endpoint node missing:")
        for e in dropped:
            missing = [x for x in (e.from_, e.to) if x not in node_ids]
            print(f"    {e.type}: {e.from_} -> {e.to} (missing: {', '.join(missing)})")

    return Graph(nodes=list(seen.values()), edges=valid_edges, gaps=[])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_graph.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc/graph/build.py tests/test_graph.py
git commit -m "fix: build_graph reports dropped dangling edges instead of discarding silently"
```

---

### Task 6: Update `CLAUDE.md` extractor conventions

**Files:**
- Modify: `/data/FASTDelphos/CLAUDE.md`

- [ ] **Step 1: Replace the pyan3 paragraph**

In the `## Extractor Conventions` section, replace:

```
**pyan3 resilience** (`extract/calls.py`) — `extract_calls()` returns `(list[Edge], list[tuple[str, str]])` where the second element is `[(excluded_file, error_message)]`. When pyan3 crashes, the pipeline probes files individually to find the bad one, excludes it, and retries. Each excluded file becomes a `tool_limitation` gap visible in the output. Never silently return empty.
```

with:

```
**AST call visitor** (`extract/calls.py` + `extract/_calls_resolver.py`) — replaced pyan3 (GPL-2.0, 0 edges recovered in agora's `backend/services/`). `extract_calls()` returns `(nodes, edges, excluded, coverage)`. Resolution is griffe-inventory-backed and covers exactly 3 cases: direct name (module-local or imported), attribute-on-import (any depth), self/cls method via class hierarchy (MRO-aware, cross-module). Every call site lands in one of 3 buckets — `resolved_internal` (edge + hydrated `function` Nodes on both ends), `resolved_external` (aggregate count only — import positively rooted outside the repo's own top-level packages, never a gap), `unresolved_dynamic` (the default — "not knowing what a call is never classifies it as external"). `tool_limitation` gaps now come from `ast.parse` `SyntaxError`, not parser crashes — near-unreachable in practice. Do not add a 4th resolution case without checking `doc_proyecto/VISITOR.md` — the 3-case scope is deliberate.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: describe the ast call visitor in Extractor Conventions"
```

---

### Task 7: Fix root-level-module misclassification and `"unknown"`-filepath crash risk

**Added after the final whole-branch review** (Tasks 1-6 all individually approved; this task addresses two Important findings that only surfaced at whole-branch scale — both confirmed agora-safe, but real defects for any repo with a flatter layout).

**Files:**
- Modify: `src/cc/extract/_calls_resolver.py`
- Modify: `src/cc/extract/calls.py`
- Test: `tests/test_calls_resolver.py` (append)
- Test: `tests/test_calls.py` (append)

**Bug 1 — root-level modules misclassified as `external`.** `build_symbol_inventory` only discovers packages via `repo_path.glob("*/__init__.py")`. A standalone module sitting directly at the repo root (e.g. `main.py` in the `calls_repo` fixture) is never added to `top_level_packages` and never griffe-loaded — so a call into it resolves `external` instead of `dynamic`/`internal`, violating "external is a positive conclusion of being outside the repo, never an absence of resolution."

**Bug 2 — `FuncInfo.file == "unknown"` can crash `node_hash`.** If griffe's `obj.filepath` is `None` (namespace packages, compiled stubs) for a symbol later classified `internal`, hydrating its callee Node calls `node_hash("unknown", ...)`, which raises `RuntimeError` (file not found) and aborts the whole pipeline run — directly contradicting the project's "flag, don't block" philosophy.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_calls_resolver.py`:

```python
def test_root_level_module_recorded_as_top_level_package(tmp_path):
    repo = _make_repo(tmp_path)
    _write(repo, "loose.py", "def stray(x):\n    return x\n")
    inv = build_symbol_inventory(repo)
    assert "loose" in inv.top_level_packages


def test_root_level_module_functions_are_indexed(tmp_path):
    repo = _make_repo(tmp_path)
    _write(repo, "loose.py", "def stray(x):\n    return x\n")
    inv = build_symbol_inventory(repo)
    assert "loose.stray" in inv.functions


def test_flat_repo_with_no_package_markers_still_indexes_root_modules(tmp_path):
    repo = tmp_path / "flat_repo"
    _write(repo, "script.py", "def run(x):\n    return x\n")
    inv = build_symbol_inventory(repo)
    assert "script" in inv.top_level_packages
    assert "script.run" in inv.functions
```

Append to `tests/test_calls.py`:

```python
def test_root_level_module_call_is_internal_not_external(tmp_path):
    (tmp_path / "loose.py").write_text("def stray(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "user.py").write_text(
        "from loose import stray\n\n\ndef use_it(x):\n    return stray(x)\n",
        encoding="utf-8",
    )
    _, edges, _, coverage = extract_calls(tmp_path)
    pairs = {(e.from_, e.to) for e in edges}
    assert ("function:user.use_it", "function:loose.stray") in pairs
    assert coverage["total"]["resolved_external"] == 0


def test_unknown_filepath_callee_does_not_crash(tmp_path, monkeypatch):
    (tmp_path / "user.py").write_text(
        "def use_it(x):\n    return helper(x)\n", encoding="utf-8"
    )
    import cc.extract.calls as calls_mod
    from cc.extract._calls_resolver import FuncInfo, SymbolInventory

    fake_inventory = SymbolInventory(
        functions={"user.helper": FuncInfo("user.helper", "unknown", 1, 1, "function")},
        top_level_packages={"user"},
    )
    monkeypatch.setattr(calls_mod, "build_symbol_inventory", lambda repo_path: fake_inventory)
    # `user.py` at the repo root has module_qname "user", so case 1's module-local
    # check (`candidate = f"{module_qname}.{name}"`) resolves `helper(x)` to
    # "user.helper" directly — matching the fake inventory's (unhydratable) entry.
    nodes, edges, excluded, coverage = extract_calls(tmp_path)
    assert edges == []  # skipped, not crashed
    assert coverage["total"]["call_sites"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calls_resolver.py tests/test_calls.py -v -k "root_level or unknown_filepath or flat_repo"`
Expected: FAIL — root-level module tests fail because `"loose"`/`"script"` are absent from `top_level_packages`/`functions`; the crash test fails because `extract_calls` raises `RuntimeError` from `node_hash`.

- [ ] **Step 3: Fix `build_symbol_inventory`**

In `src/cc/extract/_calls_resolver.py`, replace the body of `build_symbol_inventory` from the `loaded_any = False` line through the final fallback with:

```python
    loaded_any = False
    for init in repo_path.glob("*/__init__.py"):
        if init.parent.name in _SKIP_DIRS:
            continue
        inv.top_level_packages.add(init.parent.name)
        _try_load(init.parent.name, [repo_path])
        loaded_any = True

    if not loaded_any and (repo_path / "__init__.py").exists():
        inv.top_level_packages.add(repo_path.name)
        _try_load(repo_path.name, [repo_path.parent])
    else:
        # Standalone modules living directly at the repo root (no package
        # wrapping them) are invisible to the subpackage/whole-repo-package
        # loading above. `griffe.load` can load a single module by name just
        # like a package — treat each root-level .py file the same way, so
        # calls into it resolve instead of falling through to "external"
        # for lack of any evidence either way.
        for py_file in repo_path.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            inv.top_level_packages.add(py_file.stem)
            _try_load(py_file.stem, [repo_path])

    return inv
```

(The rest of the function above `loaded_any = False` — the `repo_path = pathlib.Path(repo_path)`, `inv = SymbolInventory()`, and the nested `_try_load` definition — is unchanged.)

- [ ] **Step 4: Guard the `"unknown"`-filepath hydration path**

In `src/cc/extract/calls.py`, in the `if resolution.kind == "internal":` branch inside `extract_calls`, insert a guard right after computing `callee_info` and before constructing the callee `Node`:

```python
                if resolution.kind == "internal":
                    counts["resolved_internal"] += 1
                    callee_qname = resolution.qualname
                    if callee_qname == fn_qualname:
                        continue  # no self-loops
                    callee_info = inventory.functions[callee_qname]
                    if callee_info.file == "unknown":
                        # griffe couldn't locate this symbol's source (namespace
                        # package, compiled stub, ...) — we can't hydrate a real
                        # Node for it. Skip rather than crash node_hash; the call
                        # was still structurally resolved, so it stays counted
                        # above, it just can't be rendered as an edge.
                        continue
                    callee_id = f"function:{callee_qname}"
                    ...
```

(Keep everything else in that branch — the `nodes.setdefault(...)`, `seen_edges`/`edges.append(...)` — exactly as it is today, just below this new guard.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_calls_resolver.py tests/test_calls.py -v`
Expected: PASS — all tests in both files, including the 4 new ones.

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: PASS, same count as before plus 4.

- [ ] **Step 7: Ruff sweep**

Run: `ruff check --fix . && ruff format .`
Then re-run `pytest -q` to confirm the auto-fixes didn't change behavior.

- [ ] **Step 8: Commit**

```bash
git add src/cc/extract/_calls_resolver.py src/cc/extract/calls.py tests/test_calls_resolver.py tests/test_calls.py
git commit -m "fix: index root-level modules, guard unknown-filepath hydration crash"
```

If the ruff sweep in Step 7 touched other files too, stage those in the same commit (it's a mechanical, behavior-preserving formatting pass tied to this task's cleanup).

---

## Self-Review Notes (completed while writing this plan)

1. **Spec coverage:** All 3 VISITOR.md cases → Task 2. All 3 addendum points (external bucket, dotted imports, MRO inheritance) → Task 2 tests + implementation. Async unwrap → Task 3 (`test_async_await_unwrapped_without_special_casing`, works via plain `ast.walk`, no special code — commented in `_iter_named_defs` docstring). Determinism → `collect_py_files` sorted list + no unsorted set iteration feeding output. Coverage report → Task 3's `coverage` dict + Task 4's pipeline print. `tool_limitation` mechanism kept → Task 3/4. Node-merge policy → Task 4's explicit node ordering + comment. Dangling-edge silent drop → Task 5.
2. **Placeholder scan:** none found.
3. **Type consistency:** `extract_calls` signature `(nodes, edges, excluded, coverage)` used identically in Task 3 (definition), Task 4 (pipeline.py consumption), and test files. `Resolution.kind` values (`"internal"|"external"|"dynamic"`) consistent across `_calls_resolver.py` and `calls.py`. `FuncInfo`/`SymbolInventory` field names consistent between Task 1 definition and Task 2/3 usage.

**Known limitation (not fixed here, noted for awareness):** if griffe fails to parse an internal package entirely, that package's name IS still recorded in `top_level_packages` (Task 1's `test_top_level_packages_recorded_even_if_load_fails` locks this in), so calls into it correctly fall to `unresolved_dynamic` rather than being misclassified as `external` — but its own internal symbols won't be in `functions`, so calls *within* that broken package also won't resolve. This mirrors the existing best-effort griffe error handling in `extract/models.py` and is not a regression introduced by this plan.
