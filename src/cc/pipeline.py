import pathlib

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
    call_edges, call_excluded = extract_calls(repo_path)

    all_nodes = ep_nodes + model_nodes + sql_nodes
    all_edges = ep_edges + model_edges + sql_edges + call_edges

    graph = build_graph(all_nodes, all_edges)
    graph.gaps = detect_gaps(graph)

    for filepath, error in call_excluded:
        rel = pathlib.Path(filepath).relative_to(repo_path)
        graph.gaps.append(Gap(
            kind="tool_limitation",
            where=f"{filepath}:0",
            node_id=None,
            missing=f"Call graph unavailable for `{rel}` — pyan3 parser error: {error}",
            suggested=(
                "Wrap module-level runtime setup in `if __name__ == '__main__':` "
                "or move it inside a function so static parsers can process the file."
            ),
            severity={"comprehension": "warning", "compliance": "warning"},
        ))

    if call_excluded:
        from cc.extract._collect import collect_py_files
        total_files = len(collect_py_files(repo_path))
        excluded_count = len(call_excluded)
        print(
            f"  call graph: {total_files - excluded_count}/{total_files} files analyzed"
            f" ({excluded_count} excluded — see gaps in output)"
        )
        for filepath, error in call_excluded:
            rel = pathlib.Path(filepath).relative_to(repo_path)
            print(f"    excluded: {rel} — {error}")

    emit(graph, out_dir)
