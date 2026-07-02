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


def _probe_file(file: str) -> str | None:
    """Return error message if this file alone crashes pyan3, else None."""
    try:
        v = CallGraphVisitor([file])
        v.process()
        v.postprocess()
        return None
    except Exception as exc:
        return str(exc)


def _run_pyan(
    files: list[str],
) -> tuple["CallGraphVisitor | None", list[tuple[str, str]]]:
    """Run pyan3 on files, auto-excluding any that cause crashes.

    Returns (visitor | None, [(excluded_file, error_message)]).
    """
    excluded: dict[str, str] = {}  # file -> error
    while True:
        working = [f for f in files if f not in excluded]
        if not working:
            return None, list(excluded.items())
        try:
            v = CallGraphVisitor(working)
            v.process()
            v.postprocess()
            return v, list(excluded.items())
        except Exception:
            # Probe individually to isolate one bad file per iteration
            bad = next(
                ((f, err) for f in working
                 if (err := _probe_file(f)) is not None and f not in excluded),
                None,
            )
            if bad is None:
                return None, list(excluded.items())
            excluded[bad[0]] = bad[1]


def extract_calls(
    repo_path: str | pathlib.Path,
) -> tuple[list[Edge], list[tuple[str, str]]]:
    """Return (call edges, [(excluded_file, error_msg)]).

    Files that crash pyan3 are excluded and reported rather than silently dropped.
    """
    repo_path = pathlib.Path(repo_path)
    files = [str(f) for f in collect_py_files(repo_path)]
    if not files:
        return [], []

    visitor, excluded = _run_pyan(files)
    if visitor is None:
        return [], excluded

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

    return edges, excluded
