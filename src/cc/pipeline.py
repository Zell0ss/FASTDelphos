import ast
import pathlib

from cc.extract._calls_resolver import build_symbol_inventory
from cc.extract._collect import collect_py_files, exclusion_report
from cc.extract.calls import extract_calls
from cc.extract.endpoints import extract_endpoints
from cc.extract.models import extract_models
from cc.extract.sql import extract_sql
from cc.gaps import detect_gaps
from cc.graph.build import build_graph
from cc.graph.schema import Gap
from cc.render.emit import emit


def run(
    repo_path: str | pathlib.Path,
    out_dir: str | pathlib.Path,
    exclude_patterns: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> None:
    repo_path = pathlib.Path(repo_path)
    out_dir = pathlib.Path(out_dir)

    inventory = build_symbol_inventory(repo_path, exclude_patterns, use_gitignore)
    ast_cache: dict[str, ast.Module | None] = {}

    ep_nodes, ep_edges = extract_endpoints(
        repo_path, exclude_patterns, inventory=inventory, ast_cache=ast_cache,
        use_gitignore=use_gitignore,
    )
    handler_nodes = [n for n in ep_nodes if n.type == "function"]

    model_nodes, model_edges = extract_models(
        repo_path, handler_nodes, exclude_patterns, use_gitignore
    )
    sql_nodes, sql_edges, sql_dynamic_gaps = extract_sql(
        repo_path, exclude_patterns, inventory=inventory, ast_cache=ast_cache,
        use_gitignore=use_gitignore,
    )
    call_nodes, call_edges, call_excluded, call_coverage = extract_calls(
        repo_path, exclude_patterns, inventory=inventory, ast_cache=ast_cache,
        use_gitignore=use_gitignore,
    )

    # Order still matters for which extractor's `props` win a given id (e.g.
    # an endpoint handler's is_handler=True vs. the call visitor's generic
    # stub) — but file/line/hash correctness no longer depends on it: sql.py
    # and calls.py both hydrate from the same shared `inventory` now, and
    # graph/build.py raises if two sources ever disagree on identity again.
    all_nodes = ep_nodes + model_nodes + sql_nodes + call_nodes
    all_edges = ep_edges + model_edges + sql_edges + call_edges

    graph = build_graph(all_nodes, all_edges)
    graph.gaps = detect_gaps(graph)
    graph.exclusions = exclusion_report(repo_path, exclude_patterns, use_gitignore)

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
        total_files = len(collect_py_files(repo_path, exclude_patterns, use_gitignore))
        excluded_count = len(call_excluded)
        print(
            f"  call graph: {total_files - excluded_count}/{total_files} files analyzed"
            f" ({excluded_count} excluded — see gaps in output)"
        )
        for filepath, error in call_excluded:
            rel = pathlib.Path(filepath).relative_to(repo_path)
            print(f"    excluded: {rel} — {error}")

    print(
        "  top-level packages detected: "
        f"{', '.join(sorted(inventory.top_level_packages)) or '(none)'}"
    )

    total = call_coverage["total"]
    print(
        f"  call graph coverage: {total['resolved_internal']} internal, "
        f"{total['resolved_external']} external, "
        f"{total['unresolved_dynamic']} unresolved_dynamic "
        f"(of {total['call_sites']} call sites across {total['functions']} functions)"
    )

    emit(graph, out_dir)
