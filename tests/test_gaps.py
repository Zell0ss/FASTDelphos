from cc.graph.schema import Graph, Node
from cc.gaps import detect_gaps


def test_table_without_columns_is_a_gap():
    table_node = Node(id="table:messages", type="table", file="db.py", line=1,
                      hash="x" * 64, inferred=False,
                      props={"name": "messages", "columns": []})
    graph = Graph(nodes=[table_node], edges=[], gaps=[])
    gaps = detect_gaps(graph)
    assert len(gaps) == 1
    assert gaps[0].kind == "missing_artifact"
    assert gaps[0].node_id == "table:messages"


def test_table_with_columns_has_no_gap():
    table_node = Node(id="table:messages", type="table", file="db.py", line=1,
                      hash="x" * 64, inferred=False,
                      props={"name": "messages", "columns": ["id", "content"]})
    graph = Graph(nodes=[table_node], edges=[], gaps=[])
    gaps = detect_gaps(graph)
    assert len(gaps) == 0
