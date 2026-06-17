import pathlib

from cc.adapters.fastapi import FastAPIAdapter
from cc.extract.endpoints import extract_endpoints
from cc.extract.models import extract_models
from cc.extract.calls import extract_calls
from cc.extract.sql import extract_sql
from cc.gaps import detect_gaps
from cc.graph.build import build_graph
from cc.render.emit import emit


def run(repo_path: str | pathlib.Path, out_dir: str | pathlib.Path) -> None:
    repo_path = pathlib.Path(repo_path)
    out_dir = pathlib.Path(out_dir)

    _adapter = FastAPIAdapter()

    ep_nodes, ep_edges = extract_endpoints(repo_path)
    handler_nodes = [n for n in ep_nodes if n.type == "function"]

    model_nodes, model_edges = extract_models(repo_path, handler_nodes)
    sql_nodes, sql_edges = extract_sql(repo_path)
    call_edges = extract_calls(repo_path)

    all_nodes = ep_nodes + model_nodes + sql_nodes
    all_edges = ep_edges + model_edges + sql_edges + call_edges

    graph = build_graph(all_nodes, all_edges)
    graph.gaps = detect_gaps(graph)

    emit(graph, out_dir)
