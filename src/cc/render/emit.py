import dataclasses
import json
import pathlib

from cc.graph.schema import Graph

_RENDER_DIR = pathlib.Path(__file__).parent
_TEMPLATE_SRC = _RENDER_DIR / "template_src.html"
_CYTOSCAPE = _RENDER_DIR / "cytoscape.min.js"
_CYTOSCAPE_DAGRE = _RENDER_DIR / "cytoscape-dagre.min.js"


def emit(graph: Graph, out_dir: str | pathlib.Path) -> None:
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    graph_dict = dataclasses.asdict(graph)
    json_path = out_dir / "graph.json"
    json_path.write_text(json.dumps(graph_dict, indent=2), encoding="utf-8")

    cytoscape_js = _CYTOSCAPE.read_text(encoding="utf-8")
    cytoscape_dagre_js = _CYTOSCAPE_DAGRE.read_text(encoding="utf-8")
    template = _TEMPLATE_SRC.read_text(encoding="utf-8")

    # Conditionally include or remove the exclusions block based on graph.exclusions
    start = template.find("<!-- EXCLUSIONS_START -->")
    end = template.find("<!-- EXCLUSIONS_END -->")
    has_markers = start >= 0 and end > start

    template_with_exclusions = template
    if graph.exclusions and has_markers:
        # Strip the markers, keep the code between them
        template_with_exclusions = template_with_exclusions.replace("<!-- EXCLUSIONS_START -->", "")
        template_with_exclusions = template_with_exclusions.replace("<!-- EXCLUSIONS_END -->", "")
    elif has_markers:
        # Remove the entire block including markers
        template_with_exclusions = (
            template_with_exclusions[:start]
            + template_with_exclusions[end + len("<!-- EXCLUSIONS_END -->") :]
        )

    html = (
        template_with_exclusions.replace("__CYTOSCAPE_JS__", cytoscape_js)
        .replace("__CYTOSCAPE_DAGRE_JS__", cytoscape_dagre_js)
        .replace("__GRAPH_JSON__", json.dumps(graph_dict))
    )
    (out_dir / "index.html").write_text(html, encoding="utf-8")
