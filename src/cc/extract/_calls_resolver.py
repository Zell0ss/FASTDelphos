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


def build_import_table(
    tree: ast.Module, module_qname: str, is_package_init: bool
) -> dict[str, str]:
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
    inv: SymbolInventory,
    class_qname: str,
    method_name: str,
    _seen: set[str] | None = None,
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


def _own_scope_assign_nodes(fn_node: ast.AST):
    """Yield ast.Assign nodes reachable within fn_node's own function scope —
    including inside nested if/for/while/with/try blocks, but NOT inside any
    function or class definition nested within fn_node (those have their own,
    separate scope and binding of the same name means something different).
    """

    def _walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Assign):
                yield child
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            else:
                yield from _walk(child)

    yield from _walk(fn_node)


def build_local_alias_table(
    fn_node: ast.AST,
    import_table: dict[str, str],
    module_qname: str,
    class_qname: str | None,
    inventory: SymbolInventory,
) -> dict[str, str]:
    """Track simple local aliases to external imports within one function scope.

    Only `name = base.attr(...)` assignments where `base` resolves — via the
    same classify_call used for every other call site — to an EXTERNAL
    package are trusted. If a name has more than one qualifying assignment
    in this scope and they disagree (different external package, or mixed
    with a non-qualifying assignment), the name is dropped entirely rather
    than guessing which one wins — no last-wins.
    """
    seen: dict[str, set[str]] = {}
    for node in _own_scope_assign_nodes(fn_node):
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id

        value = "\x00other"  # sentinel distinct from any real package name
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            resolution = classify_call(
                node.value, import_table=import_table, module_qname=module_qname,
                class_qname=class_qname, inventory=inventory,
            )
            if resolution.kind == "external":
                value = resolution.package

        seen.setdefault(name, set()).add(value)

    return {
        name: next(iter(values))
        for name, values in seen.items()
        if len(values) == 1 and next(iter(values)) != "\x00other"
    }


def _collect_target_names(target: ast.expr, names: set[str]) -> None:
    """Recursively add every bound name in a (possibly nested) assignment
    target, handling plain names as well as tuple/list/starred unpacking
    (`a, b = ...`, `*rest, x = ...`, `(a, (b, c)) = ...`).
    """
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, ast.Starred):
        _collect_target_names(target.value, names)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _collect_target_names(elt, names)
    # Any other target form (ast.Attribute, ast.Subscript, ...) rebinds an
    # attribute/item of an existing object, not a new local name — skip it.


def local_assignment_targets(fn_node: ast.AST) -> set[str]:
    """Names locally bound anywhere within fn_node's own function scope,
    regardless of whether the binding qualifies as an external alias.

    Recognizes every real Python local-binding form:
    - function parameters (positional, keyword-only, `*args`, `**kwargs`) —
      only when fn_node is itself a FunctionDef/AsyncFunctionDef
    - `ast.Assign` targets, including tuple/list/starred unpacking
    - `ast.AnnAssign` and `ast.AugAssign` targets
    - `for` / `async for` loop targets (unpacking-aware)
    - `with` / `async with ... as` targets (unpacking-aware)
    - `except ... as name` bindings
    - walrus (`ast.NamedExpr`) targets

    Used so a function's own (possibly non-qualifying) rebinding of a name
    shadows any module-level alias for that same name — matching real Python
    scoping, where a local binding always shadows an outer/global one. Does
    NOT descend into nested function/class definitions — those are a
    separate scope with their own, independent bindings.
    """
    names: set[str] = set()

    if isinstance(fn_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        fn_args = fn_node.args
        for arg in (*fn_args.posonlyargs, *fn_args.args, *fn_args.kwonlyargs):
            names.add(arg.arg)
        if fn_args.vararg is not None:
            names.add(fn_args.vararg.arg)
        if fn_args.kwarg is not None:
            names.add(fn_args.kwarg.arg)

    def _walk(node) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    _collect_target_names(target, names)
            elif isinstance(child, (ast.AnnAssign, ast.AugAssign)):
                _collect_target_names(child.target, names)
            elif isinstance(child, (ast.For, ast.AsyncFor)):
                _collect_target_names(child.target, names)
            elif isinstance(child, (ast.With, ast.AsyncWith)):
                for item in child.items:
                    if item.optional_vars is not None:
                        _collect_target_names(item.optional_vars, names)
            elif isinstance(child, ast.ExceptHandler):
                if child.name is not None:
                    names.add(child.name)
            elif isinstance(child, ast.NamedExpr):
                _collect_target_names(child.target, names)
            # Recurse into every non-def/class child, including expression
            # subtrees, so walrus targets nested inside expressions (e.g.
            # `if (x := f()):`) are still found.
            _walk(child)

    _walk(fn_node)
    return names
