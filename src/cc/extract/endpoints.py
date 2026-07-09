import ast
import pathlib

from cc.extract._calls_resolver import SymbolInventory, build_symbol_inventory
from cc.extract._collect import collect_py_files
from cc.extract._node_hydration import hydrate_function_node, node_from_ast_def, parse_module_cached
from cc.graph.schema import Edge, Node

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


def _module_qualname(file: pathlib.Path, root: pathlib.Path) -> str:
    rel = file.relative_to(root).with_suffix("")
    return str(rel).replace("/", ".")


def _str_const(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kw(call: ast.Call, name: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == name:
            return _str_const(kw.value)
    return None


def _collect_router_prefixes(tree: ast.Module) -> dict[str, str]:
    """var_name -> prefix string."""
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else (func.attr if isinstance(func, ast.Attribute) else None)
        )
        if name != "APIRouter":
            continue
        prefix = _kw(node.value, "prefix") or ""
        for target in node.targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix
    return prefixes


def _collect_include_prefixes(tree: ast.Module) -> dict[str, str]:
    """router_var_name -> extra prefix from include_router call."""
    extras: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "include_router":
            continue
        if not call.args:
            continue
        var = call.args[0]
        if not isinstance(var, ast.Name):
            continue
        prefix = _kw(call, "prefix") or ""
        extras[var.id] = prefix
    return extras


def extract_endpoints(
    repo_path: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    inventory: SymbolInventory | None = None,
    ast_cache: dict[str, ast.Module | None] | None = None,
    use_gitignore: bool = True,
) -> tuple[list[Node], list[Edge]]:
    repo_path = pathlib.Path(repo_path)
    if inventory is None:
        inventory = build_symbol_inventory(repo_path, exclude_patterns, use_gitignore)
    if ast_cache is None:
        ast_cache = {}
    nodes: list[Node] = []
    edges: list[Edge] = []

    for file in collect_py_files(repo_path, exclude_patterns, use_gitignore):
        try:
            tree = parse_module_cached(file, ast_cache)
        except SyntaxError:
            continue

        router_prefixes = _collect_router_prefixes(tree)
        include_extras = _collect_include_prefixes(tree)
        module_qname = _module_qualname(file, repo_path)

        for fn_node in ast.walk(tree):
            if not isinstance(fn_node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for dec in fn_node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                if not isinstance(dec.func, ast.Attribute):
                    continue
                method = dec.func.attr.lower()
                if method not in _HTTP_METHODS:
                    continue
                router_var = dec.func.value.id if isinstance(dec.func.value, ast.Name) else None
                if not dec.args:
                    continue
                path_suffix = _str_const(dec.args[0])
                if path_suffix is None:
                    continue

                r_prefix = router_prefixes.get(router_var, "") if router_var else ""
                i_prefix = include_extras.get(router_var, "") if router_var else ""
                full_path = i_prefix + r_prefix + path_suffix

                handler_qname = f"{module_qname}.{fn_node.name}"
                ep_id = f"endpoint:{method.upper()}:{full_path}:{handler_qname}"
                fn_id = f"function:{handler_qname}"

                fn_node_obj = hydrate_function_node(
                    handler_qname, inventory, ast_cache, is_handler=True
                )
                if fn_node_obj is None:
                    fn_node_obj = node_from_ast_def(
                        fn_node, str(file), handler_qname, "function", is_handler=True
                    )

                ep_node = Node(
                    id=ep_id,
                    type="endpoint",
                    file=str(file),
                    line=fn_node_obj.line,
                    hash=fn_node_obj.hash,
                    inferred=False,
                    props={"method": method.upper(), "path": full_path, "handler": handler_qname},
                )
                edge = Edge(from_=ep_id, to=fn_id, type="handles", inferred=False, props={})

                nodes.extend([ep_node, fn_node_obj])
                edges.append(edge)

    return nodes, edges
