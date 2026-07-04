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
