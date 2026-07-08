import ast
import pathlib

from cc.extract._calls_resolver import FuncInfo, SymbolInventory
from cc.extract._node_hydration import (
    node_from_ast_def,  # noqa: F401 (re-export sanity, used implicitly)
)
from cc.extract.sql import _find_enclosing_function, extract_sql
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
            "    await cur.execute(\n"
            '        f"INSERT INTO {prefix}channels (a, b) VALUES (%s, %s)",\n'
            "        values,\n"
            "    )\n"
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


def test_extract_sql_respects_exclude_patterns(tmp_path):
    _write(
        tmp_path,
        "backend/db.py",
        ("async def get_kept(cur):\n    await cur.execute('SELECT * FROM kept_table')\n"),
    )
    _write(
        tmp_path,
        "backend/tests/db.py",
        ("async def get_dropped(cur):\n    await cur.execute('SELECT * FROM dropped_table')\n"),
    )
    nodes, _, _ = extract_sql(tmp_path, exclude_patterns=("backend/tests/**",))
    table_names = {n.props["name"] for n in nodes if n.type == "table"}
    assert "kept_table" in table_names
    assert "dropped_table" not in table_names


def test_find_enclosing_function_returns_def_span():
    source = (
        "async def get_message(conn, msg_id):\n"
        "    return await conn.fetchone('SELECT 1', (msg_id,))\n"
    )
    tree = ast.parse(source)
    call_node = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    qname, def_node = _find_enclosing_function(call_node, tree, "db")
    assert qname == "db.get_message"
    assert def_node is not None
    assert def_node.lineno == 1
    assert def_node.end_lineno == 2


def test_find_enclosing_function_module_level_returns_none_span():
    source = "CUR.execute('SELECT 1')\n"
    tree = ast.parse(source)
    call_node = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    qname, def_node = _find_enclosing_function(call_node, tree, "db")
    assert qname == "db"
    assert def_node is None


def test_function_node_uses_def_line_from_inventory_not_call_site(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "db.py").write_text(
        "async def create_message(conn, content):\n"
        "    await conn.execute(\n"
        "        'INSERT INTO messages (content) VALUES (%s)', (content,)\n"
        "    )\n",
        encoding="utf-8",
    )
    inventory = SymbolInventory(
        functions={
            "db.create_message": FuncInfo(
                qualname="db.create_message",
                file=str(repo / "db.py"),
                lineno=1,
                endlineno=4,
                kind="function",
            )
        }
    )
    nodes, _, _ = extract_sql(repo, inventory=inventory)
    fn_node = next(n for n in nodes if n.id == "function:db.create_message")
    assert fn_node.line == 1  # the `async def` line, not line 2's execute() call
    assert fn_node.file == str(repo / "db.py")


def test_function_node_falls_back_to_ast_span_when_not_in_inventory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "db.py").write_text(
        "async def create_message(conn, content):\n"
        "    await conn.execute(\n"
        "        'INSERT INTO messages (content) VALUES (%s)', (content,)\n"
        "    )\n",
        encoding="utf-8",
    )
    empty_inventory = SymbolInventory(functions={})
    nodes, _, _ = extract_sql(repo, inventory=empty_inventory)
    fn_node = next(n for n in nodes if n.id == "function:db.create_message")
    assert fn_node.line == 1  # AST fallback still finds the real def line
    assert fn_node.file == str(repo / "db.py")


def test_extract_sql_without_inventory_arg_still_works():
    # Backward compatibility: existing 2-positional-arg call sites (no inventory).
    nodes, edges, gaps = extract_sql(SIMPLE_API)
    assert any(n.type == "table" for n in nodes)


def test_decorated_db_function_gets_decorator_inclusive_hash(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "db.py").write_text(
        "def audit(fn):\n"
        "    return fn\n"
        "\n"
        "\n"
        "@audit\n"
        "async def create_message(conn, content):\n"
        "    await conn.execute(\n"
        "        'INSERT INTO messages (content) VALUES (%s)', (content,)\n"
        "    )\n",
        encoding="utf-8",
    )
    nodes, _, _ = extract_sql(repo)
    fn_node = next(n for n in nodes if n.id == "function:db.create_message")
    # the `async def` line, not the decorator (5) or the execute() call (7)
    assert fn_node.line == 6
    from cc.graph.hash_util import node_hash

    assert fn_node.hash == node_hash(repo / "db.py", 5, 9)  # decorator (5) through end (9)


def test_sql_still_works_when_griffe_cannot_resolve_the_function(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "db.py").write_text(
        "async def create_message(conn, content):\n"
        "    await conn.execute(\n"
        "        'INSERT INTO messages (content) VALUES (%s)', (content,)\n"
        "    )\n",
        encoding="utf-8",
    )
    from cc.extract._calls_resolver import SymbolInventory

    empty_inventory = SymbolInventory(functions={})
    nodes, _, _ = extract_sql(repo, inventory=empty_inventory)
    fn_node = next(n for n in nodes if n.id == "function:db.create_message")
    assert fn_node.line == 1  # local AST fallback still finds the real def line
