from cc.gaps import detect_gaps
from cc.graph.schema import Graph, Node


def test_table_without_columns_is_a_gap():
    table_node = Node(
        id="table:messages",
        type="table",
        file="db.py",
        line=1,
        hash="x" * 64,
        inferred=False,
        props={"name": "messages", "columns": []},
    )
    graph = Graph(nodes=[table_node], edges=[], gaps=[])
    gaps = detect_gaps(graph)
    assert len(gaps) == 1
    assert gaps[0].kind == "missing_artifact"
    assert gaps[0].node_id == "table:messages"


def test_table_with_columns_has_no_gap():
    table_node = Node(
        id="table:messages",
        type="table",
        file="db.py",
        line=1,
        hash="x" * 64,
        inferred=False,
        props={"name": "messages", "columns": ["id", "content"]},
    )
    graph = Graph(nodes=[table_node], edges=[], gaps=[])
    gaps = detect_gaps(graph)
    assert len(gaps) == 0


def test_two_endpoints_same_method_and_path_emit_ambiguity_gap():
    nodes = [
        Node(
            id="endpoint:GET:/ecosystems/:pkg_a.routes.list_ecosystems",
            type="endpoint",
            file="pkg_a/routes.py",
            line=10,
            hash="a" * 64,
            inferred=False,
            props={
                "method": "GET",
                "path": "/ecosystems/",
                "handler": "pkg_a.routes.list_ecosystems",
            },
        ),
        Node(
            id="endpoint:GET:/ecosystems/:pkg_b.routes.list_ecosystems",
            type="endpoint",
            file="pkg_b/routes.py",
            line=20,
            hash="b" * 64,
            inferred=False,
            props={
                "method": "GET",
                "path": "/ecosystems/",
                "handler": "pkg_b.routes.list_ecosystems",
            },
        ),
    ]
    graph = Graph(nodes=nodes, edges=[], gaps=[])
    gaps = detect_gaps(graph)
    ambiguous = [g for g in gaps if g.kind == "unresolved_dynamic"]
    assert len(ambiguous) == 1
    gap = ambiguous[0]
    assert gap.missing == (
        "ruta ambigua: 2 handlers declaran GET /ecosystems/; "
        "la desambiguación vive en el registro de routers"
    )
    assert gap.where == "pkg_a/routes.py:10; pkg_b/routes.py:20"
    assert gap.severity == {"comprehension": "warning", "compliance": "error"}


def test_single_endpoint_for_a_route_has_no_ambiguity_gap():
    nodes = [
        Node(
            id="endpoint:GET:/x:pkg.routes.handler",
            type="endpoint",
            file="pkg/routes.py",
            line=1,
            hash="a" * 64,
            inferred=False,
            props={"method": "GET", "path": "/x", "handler": "pkg.routes.handler"},
        ),
    ]
    graph = Graph(nodes=nodes, edges=[], gaps=[])
    gaps = detect_gaps(graph)
    assert not [g for g in gaps if g.kind == "unresolved_dynamic"]
