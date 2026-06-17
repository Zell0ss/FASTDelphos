from cc.graph.schema import Edge, Gap, Graph, Node


def test_node_fields():
    n = Node(
        id="function:myapp.foo",
        type="function",
        file="myapp.py",
        line=1,
        hash="abc",
        inferred=False,
        props={},
    )
    assert n.id == "function:myapp.foo"
    assert n.inferred is False


def test_edge_fields():
    e = Edge(
        from_="function:myapp.foo", to="function:myapp.bar", type="calls", inferred=False, props={}
    )
    assert e.type == "calls"


def test_gap_fields():
    g = Gap(
        kind="missing_artifact",
        where="myapp.py:10",
        node_id="table:users",
        missing="No CREATE TABLE for users",
        suggested="-- TODO: DDL for users",
        severity={"comprehension": "warning", "compliance": "error"},
    )
    assert g.kind == "missing_artifact"


def test_graph_collects_all():
    n = Node(
        id="table:messages",
        type="table",
        file="db.py",
        line=1,
        hash="x",
        inferred=False,
        props={"name": "messages", "columns": []},
    )
    graph = Graph(nodes=[n], edges=[], gaps=[])
    assert len(graph.nodes) == 1
