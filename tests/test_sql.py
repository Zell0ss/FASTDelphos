import pathlib

from cc.extract.sql import extract_sql
from tests.conftest import SIMPLE_API


def _write(root: pathlib.Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_finds_messages_table():
    nodes, _, _ = extract_sql(SIMPLE_API)
    table_nodes = [n for n in nodes if n.type == "table"]
    names = {n.props["name"] for n in table_nodes}
    assert "messages" in names


def test_table_node_id():
    nodes, _, _ = extract_sql(SIMPLE_API)
    ids = {n.id for n in nodes}
    assert "table:messages" in ids


def test_extracts_write_edge():
    nodes, edges, _ = extract_sql(SIMPLE_API)
    write_edges = [e for e in edges if e.type == "writes"]
    assert len(write_edges) >= 1
    assert any("table:messages" == e.to for e in write_edges)


def test_extracts_read_edge():
    nodes, edges, _ = extract_sql(SIMPLE_API)
    read_edges = [e for e in edges if e.type == "reads"]
    assert len(read_edges) >= 1
    assert any("table:messages" == e.to for e in read_edges)


def test_write_columns_from_insert():
    nodes, _, _ = extract_sql(SIMPLE_API)
    msg = next(n for n in nodes if n.id == "table:messages")
    assert "content" in msg.props["columns"]
    assert "author" in msg.props["columns"]


def test_via_contains_file_and_line():
    _, edges, _ = extract_sql(SIMPLE_API)
    for e in edges:
        assert ":" in e.props["via"]


def test_fstring_update_with_static_table_emits_writes_edge(tmp_path):
    _write(
        tmp_path,
        "db.py",
        (
            "async def update_channel(cur, channel_id, fields):\n"
            "    set_clause = ', '.join(f'{k} = %s' for k in fields)\n"
            "    values = list(fields.values()) + [channel_id]\n"
            '    await cur.execute(f"UPDATE channels SET {set_clause} WHERE id = %s", values)\n'
        ),
    )
    nodes, edges, dynamic_gaps = extract_sql(tmp_path)
    write_edges = [e for e in edges if e.type == "writes" and e.to == "table:channels"]
    assert len(write_edges) == 1
    assert write_edges[0].props["via"] == f"{tmp_path / 'db.py'}:4"
    table_node = next(n for n in nodes if n.id == "table:channels")
    assert table_node.props["columns"] == []
    assert dynamic_gaps == []


def test_fstring_select_with_static_table_emits_reads_edge(tmp_path):
    _write(
        tmp_path,
        "db.py",
        (
            "async def get_messages(cur, condition):\n"
            '    await cur.execute(f"SELECT * FROM messages WHERE {condition}")\n'
        ),
    )
    nodes, edges, dynamic_gaps = extract_sql(tmp_path)
    read_edges = [e for e in edges if e.type == "reads" and e.to == "table:messages"]
    assert len(read_edges) == 1
    assert dynamic_gaps == []


def test_fstring_dynamic_prefix_before_table_does_not_fabricate_edge(tmp_path):
    # The Frankenstein case: concatenating fragments would wrongly read "channels"
    # as the table name, when the real (unknowable) table is f"{prefix}channels".
    _write(
        tmp_path,
        "db.py",
        (
            "async def insert_dynamic(cur, prefix, values):\n"
            '    await cur.execute(f"INSERT INTO {prefix}channels (a, b) VALUES (%s, %s)", values)\n'
        ),
    )
    nodes, edges, dynamic_gaps = extract_sql(tmp_path)
    assert edges == []
    assert not any(n.type == "table" for n in nodes)
    assert len(dynamic_gaps) == 1
    file, lineno, fn_qname = dynamic_gaps[0]
    assert lineno == 2
    assert fn_qname == "db.insert_dynamic"


def test_fully_dynamic_sql_with_no_static_verb_or_table_is_a_gap(tmp_path):
    _write(
        tmp_path,
        "db.py",
        (
            "async def run_query(cur, query_var, values):\n"
            "    await cur.execute(query_var, values)\n"
        ),
    )
    nodes, edges, dynamic_gaps = extract_sql(tmp_path)
    assert edges == []
    assert len(dynamic_gaps) == 1
    file, lineno, fn_qname = dynamic_gaps[0]
    assert lineno == 2
    assert fn_qname == "db.run_query"
