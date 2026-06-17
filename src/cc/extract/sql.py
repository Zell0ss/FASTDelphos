import ast
import pathlib
from collections import defaultdict

import sqlglot
import sqlglot.expressions as exp

from cc.graph.schema import Edge, Node
from cc.graph.hash_util import node_hash

_DB_METHODS = {"execute", "executemany", "fetchone", "fetchall", "fetchmany"}


def _str_const(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _module_qualname(file: pathlib.Path, root: pathlib.Path) -> str:
    rel = file.relative_to(root).with_suffix("")
    return str(rel).replace("/", ".")


def _operation(stmt: exp.Expression) -> str:
    if isinstance(stmt, exp.Select):
        return "reads"
    return "writes"


def _table_names(stmt: exp.Expression) -> list[str]:
    return [t.name for t in stmt.find_all(exp.Table) if t.name]


def _insert_columns(stmt: exp.Expression) -> list[str]:
    """Extract column names from the INSERT column list (the schema, not the VALUES).

    sqlglot represents INSERT INTO t (col1, col2) VALUES (...) as:
      Insert.this = Schema(this=Table("t"), expressions=[Identifier("col1"), ...])
    Using find_all(exp.Column) is unreliable when VALUES contain placeholder
    expressions (e.g. %s parsed as Mod), so we read the Schema.expressions directly.
    """
    if not isinstance(stmt, exp.Insert):
        return []
    schema = stmt.args.get("this")
    if isinstance(schema, exp.Schema):
        return [
            ident.name
            for ident in schema.expressions
            if isinstance(ident, exp.Identifier) and ident.name
        ]
    return []


def _select_columns(stmt: exp.Expression) -> list[str]:
    if not isinstance(stmt, exp.Select):
        return []
    cols = []
    for sel in stmt.expressions:
        if isinstance(sel, exp.Star):
            return []  # SELECT * — don't infer
        if isinstance(sel, exp.Column) and sel.name:
            cols.append(sel.name)
        elif isinstance(sel, exp.Alias):
            inner = sel.this
            if isinstance(inner, exp.Column) and inner.name:
                cols.append(inner.name)
    return cols


def _find_enclosing_function(
    call_node: ast.Call,
    tree: ast.AST,
    module_qname: str,
) -> str:
    """Return module-qualified function name enclosing call_node, or module_qname fallback."""
    # Build a mapping: for each call lineno, find the innermost function whose
    # line range contains it. We collect all function defs, sort by start line
    # descending (innermost first for nested functions), and pick the first that
    # wraps the call.
    fn_defs: list[tuple[int, int, str]] = []  # (start, end, qname)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            fn_defs.append((node.lineno, end, node.name))

    call_line = call_node.lineno

    # Sort by start_line descending so innermost (latest start) is checked first
    fn_defs.sort(key=lambda x: x[0], reverse=True)
    for start, end, name in fn_defs:
        if start <= call_line <= end:
            return f"{module_qname}.{name}"

    return module_qname  # fallback: module level


def extract_sql(repo_path: str | pathlib.Path) -> tuple[list[Node], list[Edge]]:
    repo_path = pathlib.Path(repo_path)
    table_columns: dict[str, set[str]] = defaultdict(set)
    table_files: dict[str, tuple[str, int]] = {}  # table -> (file, line)
    raw_edges: list[tuple[str, str, str, str, str, int]] = []  # (fn_qname, table, op, via, file, lineno)

    for file in sorted(repo_path.rglob("*.py")):
        source = file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(file))
        except SyntaxError:
            continue

        module_qname = _module_qualname(file, repo_path)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in _DB_METHODS:
                continue
            if not node.args:
                continue
            sql = _str_const(node.args[0])
            if not sql:
                continue

            try:
                stmt = sqlglot.parse_one(sql, error_level=sqlglot.ErrorLevel.IGNORE)
            except Exception:
                continue
            if stmt is None:
                continue

            tables = _table_names(stmt)
            op = _operation(stmt)

            if op == "writes":
                cols = _insert_columns(stmt)
            else:
                cols = _select_columns(stmt)

            for tbl in tables:
                table_columns[tbl].update(cols)
                if tbl not in table_files:
                    table_files[tbl] = (str(file), node.lineno)

            fn_qname = _find_enclosing_function(node, tree, module_qname)
            via = f"{file}:{node.lineno}"
            for tbl in tables:
                raw_edges.append((fn_qname, tbl, op, via, str(file), node.lineno))

    # Build table nodes
    table_nodes: dict[str, Node] = {}
    for tbl, cols in table_columns.items():
        tbl_file, tbl_line = table_files.get(tbl, ("unknown", 1))
        if tbl_file != "unknown":
            t_hash = node_hash(tbl_file, tbl_line, tbl_line)
        else:
            t_hash = "0" * 64
        table_nodes[tbl] = Node(
            id=f"table:{tbl}",
            type="table",
            file=tbl_file,
            line=tbl_line,
            hash=t_hash,
            inferred=False,
            props={"name": tbl, "columns": sorted(cols)},
        )

    # Build function nodes for each unique enclosing function that touches the DB
    fn_nodes: dict[str, Node] = {}
    for fn_qname, tbl, op, via, edge_file, edge_lineno in raw_edges:
        if tbl not in table_nodes:
            continue
        fn_id = f"function:{fn_qname}"
        if fn_id not in fn_nodes:
            fn_nodes[fn_id] = Node(
                id=fn_id,
                type="function",
                file=edge_file,
                line=edge_lineno,
                hash=node_hash(edge_file, edge_lineno, edge_lineno),
                inferred=False,
                props={"qualname": fn_qname, "kind": "function", "is_handler": False},
            )

    # Build edges
    edges: list[Edge] = []
    for fn_qname, tbl, op, via, _edge_file, _edge_lineno in raw_edges:
        if tbl not in table_nodes:
            continue
        edges.append(Edge(
            from_=f"function:{fn_qname}",
            to=f"table:{tbl}",
            type=op,
            inferred=False,
            props={"via": via},
        ))

    return list(table_nodes.values()) + list(fn_nodes.values()), edges
