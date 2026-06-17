import dataclasses
import json
import pathlib

from cc.graph.schema import Graph

_TEMPLATE = pathlib.Path(__file__).parent / "template.html"


def emit(graph: Graph, out_dir: str | pathlib.Path) -> None:
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    graph_dict = dataclasses.asdict(graph)
    json_path = out_dir / "graph.json"
    json_path.write_text(json.dumps(graph_dict, indent=2), encoding="utf-8")

    template = _TEMPLATE.read_text(encoding="utf-8")
    html = template.replace("__GRAPH_JSON__", json.dumps(graph_dict))
    (out_dir / "index.html").write_text(html, encoding="utf-8")
