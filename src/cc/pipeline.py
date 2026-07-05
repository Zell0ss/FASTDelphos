import pathlib

from cc.extract._collect import collect_py_files
from cc.extract.calls import extract_calls
from cc.extract.endpoints import extract_endpoints
from cc.extract.models import extract_models
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
    sql_nodes, sql_edges, sql_dynamic_gaps = extract_sql(repo_path)
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
        graph.gaps.append(
            Gap(
                kind="tool_limitation",
                where=f"{filepath}:0",
                node_id=None,
                missing=f"Call graph unavailable for `{rel}` — SyntaxError: {error}",
                suggested="Fix the syntax error so `ast.parse` can process the file.",
                severity={"comprehension": "warning", "compliance": "error"},
            )
        )

    for filepath, lineno, fn_qname in sql_dynamic_gaps:
        graph.gaps.append(
            Gap(
                kind="unresolved_dynamic",
                where=f"{filepath}:{lineno}",
                node_id=f"function:{fn_qname}",
                missing=f"SQL built dynamically (f-string) in `{fn_qname}` — "
                "table/operation could not be statically determined",
                suggested="Consider keeping the table name as literal text even if "
                "the rest of the query is dynamic, so lineage stays traceable.",
                severity={"comprehension": "warning", "compliance": "error"},
            )
        )

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
