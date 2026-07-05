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
