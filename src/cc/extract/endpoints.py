import ast
import pathlib

from cc.extract._collect import collect_py_files
from cc.graph.hash_util import node_hash
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
    repo_path: str | pathlib.Path, exclude_patterns: tuple[str, ...] = ()
) -> tuple[list[Node], list[Edge]]:
    repo_path = pathlib.Path(repo_path)
    nodes: list[Node] = []
    edges: list[Edge] = []

    for file in collect_py_files(repo_path, exclude_patterns):
        source = file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(file))
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
                ep_id = f"endpoint:{method.upper()}:{full_path}"
                fn_id = f"function:{handler_qname}"

                ep_hash = node_hash(file, fn_node.lineno, fn_node.end_lineno)
                fn_hash = ep_hash  # same source span

                ep_node = Node(
                    id=ep_id,
                    type="endpoint",
                    file=str(file),
                    line=fn_node.lineno,
                    hash=ep_hash,
                    inferred=False,
                    props={"method": method.upper(), "path": full_path, "handler": handler_qname},
                )
                fn_node_obj = Node(
                    id=fn_id,
                    type="function",
                    file=str(file),
                    line=fn_node.lineno,
                    hash=fn_hash,
                    inferred=False,
                    props={"qualname": handler_qname, "kind": "function", "is_handler": True},
                )
                edge = Edge(from_=ep_id, to=fn_id, type="handles", inferred=False, props={})

                nodes.extend([ep_node, fn_node_obj])
                edges.append(edge)

    return nodes, edges
