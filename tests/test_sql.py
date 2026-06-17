from cc.extract.sql import extract_sql
from tests.conftest import SIMPLE_API


def test_finds_messages_table():
    nodes, _ = extract_sql(SIMPLE_API)
    table_nodes = [n for n in nodes if n.type == "table"]
    names = {n.props["name"] for n in table_nodes}
    assert "messages" in names


def test_table_node_id():
    nodes, _ = extract_sql(SIMPLE_API)
    ids = {n.id for n in nodes}
    assert "table:messages" in ids


def test_extracts_write_edge():
    nodes, edges = extract_sql(SIMPLE_API)
    write_edges = [e for e in edges if e.type == "writes"]
    assert len(write_edges) >= 1
    assert any("table:messages" == e.to for e in write_edges)


def test_extracts_read_edge():
    nodes, edges = extract_sql(SIMPLE_API)
    read_edges = [e for e in edges if e.type == "reads"]
    assert len(read_edges) >= 1
    assert any("table:messages" == e.to for e in read_edges)


def test_write_columns_from_insert():
    nodes, _ = extract_sql(SIMPLE_API)
    msg = next(n for n in nodes if n.id == "table:messages")
    assert "content" in msg.props["columns"]
    assert "author" in msg.props["columns"]


def test_via_contains_file_and_line():
    _, edges = extract_sql(SIMPLE_API)
    for e in edges:
        assert ":" in e.props["via"]
