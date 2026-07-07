from cc.graph.hash_util import node_hash
from cc.graph.schema import Edge, Gap, Graph, Node
from tests.conftest import SIMPLE_API


def test_node_hash_is_stable():
    h1 = node_hash(SIMPLE_API / "models.py", 1, 6)
    h2 = node_hash(SIMPLE_API / "models.py", 1, 6)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_node_hash_differs_by_span():
    h1 = node_hash(SIMPLE_API / "models.py", 1, 6)
    h2 = node_hash(SIMPLE_API / "models.py", 1, 3)
    assert h1 != h2


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


def test_graph_exclusions_defaults_to_empty_list():
    graph = Graph(nodes=[], edges=[], gaps=[])
    assert graph.exclusions == []


def test_graph_exclusions_can_be_set():
    graph = Graph(nodes=[], edges=[], gaps=[], exclusions=[{"pattern": "tests/**", "count": 3}])
    assert graph.exclusions == [{"pattern": "tests/**", "count": 3}]
