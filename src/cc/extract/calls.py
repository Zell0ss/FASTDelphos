import pathlib
from pyan.analyzer import CallGraphVisitor
from cc.graph.schema import Edge
from cc.extract._collect import collect_py_files


def _qualname_from_pyan_node(node) -> str | None:
    s = str(node)
    # Format: <Node function:module.name> or <Node module:name>
    if "function:" in s:
        return s.split("function:")[-1].rstrip(">").strip()
    return None


def _run_pyan(files: list[str]):
    """Run pyan3 on files, auto-excluding any that cause TypeError crashes."""
    skip: set[str] = set()
    while True:
        working = [f for f in files if f not in skip]
        if not working:
            return None
        try:
            v = CallGraphVisitor(working)
            v.process()
            v.postprocess()
            return v
        except Exception:
            # Probe files individually to isolate the problematic one
            bad = next(
                (f for f in working if _crashes_pyan(f) and f not in skip),
                None,
            )
            if bad is None:
                return None  # crash not isolatable; give up
            skip.add(bad)


def _crashes_pyan(file: str) -> bool:
    try:
        v = CallGraphVisitor([file])
        v.process()
        v.postprocess()
        return False
    except Exception:
        return True


def extract_calls(repo_path: str | pathlib.Path) -> list[Edge]:
    repo_path = pathlib.Path(repo_path)
    files = [str(f) for f in collect_py_files(repo_path)]
    if not files:
        return []

    visitor = _run_pyan(files)
    if visitor is None:
        return []

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()

    for caller_node, callee_set in visitor.uses_edges.items():
        caller_qname = _qualname_from_pyan_node(caller_node)
        if not caller_qname:
            continue
        for callee_node in callee_set:
            callee_qname = _qualname_from_pyan_node(callee_node)
            if not callee_qname:
                continue
            if caller_qname == callee_qname:
                continue
            key = (caller_qname, callee_qname)
            if key in seen:
                continue
            seen.add(key)
            edges.append(Edge(
                from_=f"function:{caller_qname}",
                to=f"function:{callee_qname}",
                type="calls", inferred=False, props={},
            ))

    return edges
