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
    exclusions_code = ""
    if graph.exclusions and len(graph.exclusions) > 0:
        exclusions_code = """    // ── Exclusions ────────────────────────────────────────────────────────────
    if (GRAPH.exclusions && GRAPH.exclusions.length) {
      const totalExcluded = GRAPH.exclusions.reduce((sum, x) => sum + x.count, 0);
      const info = document.getElementById('exclusions-info');
      info.textContent =
        `compilado con ${GRAPH.exclusions.length} exclusión(es) — ${totalExcluded} ficheros fuera`;
      info.title = GRAPH.exclusions.map(x => `${x.pattern}: ${x.count}`).join('\n');
    }

"""

    html = (
        template.replace("__CYTOSCAPE_JS__", cytoscape_js)
        .replace("__CYTOSCAPE_DAGRE_JS__", cytoscape_dagre_js)
        .replace("__GRAPH_JSON__", json.dumps(graph_dict))
        .replace("__EXCLUSIONS_CODE__", exclusions_code)
    )
    (out_dir / "index.html").write_text(html, encoding="utf-8")
