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

    # Conditionally include exclusions code only if the graph has exclusions
    # Extract the code between comment markers or leave empty
    if graph.exclusions:
        exclusions_start = template.find("<!-- EXCLUSIONS_START -->")
        exclusions_end = template.find("<!-- EXCLUSIONS_END -->")
        if exclusions_start >= 0 and exclusions_end > exclusions_start:
            # Extract the code between markers (including newline after start marker)
            exclusions_code = template[exclusions_start + len("<!-- EXCLUSIONS_START -->"):exclusions_end]
        else:
            exclusions_code = ""
    else:
        # Remove the entire exclusions block if no exclusions
        exclusions_code = ""

    # Replace comment markers and code with final content
    template_with_exclusions = template
    if exclusions_code:
        # Keep the exclusions code as-is
        template_with_exclusions = template_with_exclusions.replace("<!-- EXCLUSIONS_START -->", "")
        template_with_exclusions = template_with_exclusions.replace("<!-- EXCLUSIONS_END -->", "")
    else:
        # Remove the entire block including markers
        exclusions_start = template_with_exclusions.find("<!-- EXCLUSIONS_START -->")
        exclusions_end = template_with_exclusions.find("<!-- EXCLUSIONS_END -->")
        if exclusions_start >= 0 and exclusions_end > exclusions_start:
            template_with_exclusions = (
                template_with_exclusions[:exclusions_start] +
                template_with_exclusions[exclusions_end + len("<!-- EXCLUSIONS_END -->"):]
            )

    html = (
        template_with_exclusions.replace("__CYTOSCAPE_JS__", cytoscape_js)
        .replace("__CYTOSCAPE_DAGRE_JS__", cytoscape_dagre_js)
        .replace("__GRAPH_JSON__", json.dumps(graph_dict))
    )
    (out_dir / "index.html").write_text(html, encoding="utf-8")
