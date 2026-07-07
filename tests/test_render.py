import json
import pathlib
import tempfile

from cc.graph.schema import Edge, Graph, Node
from cc.render.emit import emit


def _minimal_graph():
    ep = Node(
        id="endpoint:GET:/hello",
        type="endpoint",
        file="main.py",
        line=1,
        hash="a" * 64,
        inferred=False,
        props={"method": "GET", "path": "/hello", "handler": "main.hello"},
    )
    fn = Node(
        id="function:main.hello",
        type="function",
        file="main.py",
        line=1,
        hash="a" * 64,
        inferred=False,
        props={"qualname": "main.hello"},
    )
    e = Edge(
        from_="endpoint:GET:/hello",
        to="function:main.hello",
        type="handles",
        inferred=False,
        props={},
    )
    return Graph(nodes=[ep, fn], edges=[e], gaps=[])


def _graph_with_exclusions():
    graph = _minimal_graph()
    graph.exclusions = [{"pattern": "backend/tests/**", "count": 3}]
    return graph


def test_emit_creates_json_file():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        assert (pathlib.Path(d) / "graph.json").exists()


def test_emit_creates_html_file():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        assert (pathlib.Path(d) / "index.html").exists()


def test_json_is_valid_and_has_nodes():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 2


def test_html_references_cytoscape():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        html = (pathlib.Path(d) / "index.html").read_text()
        assert "cytoscape" in html.lower()


def test_html_embeds_graph_json():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        html = (pathlib.Path(d) / "index.html").read_text()
        assert "endpoint:GET:/hello" in html


def test_html_includes_search_ui():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        html = (pathlib.Path(d) / "index.html").read_text()
        assert 'id="search-input"' in html
        assert 'id="search-dropdown"' in html
        assert "function buildSearchIndex" in html
        assert "function searchGraph" in html
        assert "function goToSearchResult" in html


def test_html_includes_node_panel_features():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        html = (pathlib.Path(d) / "index.html").read_text()
        assert "function renderPanelBody" in html
        assert "function renderProps" in html
        assert "function renderNeighborhood" in html
        assert "function reachableFromEndpoints" in html
        assert "function toggleHideNode" in html
        assert "function togglePanelRaw" in html
        assert "HUB_MIN_PERCENT" in html


def test_html_shows_no_exclusion_summary_when_none():
    with tempfile.TemporaryDirectory() as d:
        emit(_minimal_graph(), pathlib.Path(d))
        html = (pathlib.Path(d) / "index.html").read_text()
        assert "exclusión" not in html.lower()


def test_html_shows_exclusion_summary_when_present():
    with tempfile.TemporaryDirectory() as d:
        emit(_graph_with_exclusions(), pathlib.Path(d))
        html = (pathlib.Path(d) / "index.html").read_text()
        assert 'id="exclusions-info"' in html
        assert "backend/tests/**" in html
