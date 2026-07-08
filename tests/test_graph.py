import pytest

from cc.graph.build import build_graph
from cc.graph.schema import Edge, Graph, Node


def _make_nodes():
    return [
        Node(
            id="endpoint:POST:/x",
            type="endpoint",
            file="f.py",
            line=1,
            hash="a" * 64,
            inferred=False,
            props={"method": "POST", "path": "/x"},
        ),
        Node(
            id="function:app.handler",
            type="function",
            file="f.py",
            line=1,
            hash="a" * 64,
            inferred=False,
            props={},
        ),
        # Duplicate — should be deduplicated
        Node(
            id="function:app.handler",
            type="function",
            file="f.py",
            line=1,
            hash="a" * 64,
            inferred=False,
            props={},
        ),
    ]


def test_build_deduplicates_nodes():
    graph = build_graph(_make_nodes(), [])
    ids = [n.id for n in graph.nodes]
    assert ids.count("function:app.handler") == 1


def test_build_returns_graph():
    graph = build_graph(_make_nodes(), [])
    assert isinstance(graph, Graph)


def test_build_includes_all_edges():
    e = Edge(
        from_="endpoint:POST:/x",
        to="function:app.handler",
        type="handles",
        inferred=False,
        props={},
    )
    graph = build_graph(_make_nodes(), [e])
    assert len(graph.edges) == 1


def test_dangling_edge_is_reported_not_silently_dropped(capsys):
    nodes = [
        Node(
            id="function:a",
            type="function",
            file="f.py",
            line=1,
            hash="a" * 64,
            inferred=False,
            props={},
        ),
    ]
    edge = Edge(from_="function:a", to="function:missing", type="calls", inferred=False, props={})
    graph = build_graph(nodes, [edge])
    assert graph.edges == []  # still dropped — build_graph can't invent a node
    out = capsys.readouterr().out
    assert "function:missing" in out
    assert "1 edge" in out


def test_build_raises_on_conflicting_node_identity():
    nodes = [
        Node(
            id="function:app.handler",
            type="function",
            file="f.py",
            line=1,
            hash="a" * 64,
            inferred=False,
            props={},
        ),
        Node(
            id="function:app.handler",
            type="function",
            file="f.py",
            line=5,  # conflicting line for the same id
            hash="b" * 64,  # conflicting hash for the same id
            inferred=False,
            props={},
        ),
    ]
    with pytest.raises(ValueError, match="function:app.handler"):
        build_graph(nodes, [])


def test_build_allows_duplicate_with_matching_identity_but_different_props():
    # Different props (e.g. is_handler) for the same id/file/line/hash is fine —
    # only file/line/hash conflicts are a real defect.
    nodes = [
        Node(
            id="function:app.handler",
            type="function",
            file="f.py",
            line=1,
            hash="a" * 64,
            inferred=False,
            props={"is_handler": True},
        ),
        Node(
            id="function:app.handler",
            type="function",
            file="f.py",
            line=1,
            hash="a" * 64,
            inferred=False,
            props={"is_handler": False},
        ),
    ]
    graph = build_graph(nodes, [])
    assert len(graph.nodes) == 1
    assert graph.nodes[0].props == {"is_handler": True}  # first registration wins props
