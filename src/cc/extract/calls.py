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
