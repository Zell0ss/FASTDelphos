import json
import pathlib
import tempfile

from cc.pipeline import run
from tests.conftest import SIMPLE_API


def test_pipeline_produces_json():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        assert "nodes" in data
        assert len(data["nodes"]) > 0


def test_pipeline_produces_html():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        assert (pathlib.Path(d) / "index.html").exists()


def test_pipeline_finds_endpoint_node():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        ep_nodes = [n for n in data["nodes"] if n["type"] == "endpoint"]
        assert any("POST" in n["id"] and "messages" in n["id"] for n in ep_nodes)


def test_pipeline_finds_table_node():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        table_nodes = [n for n in data["nodes"] if n["type"] == "table"]
        assert any(n["id"] == "table:messages" for n in table_nodes)


def test_pipeline_all_edge_sources_exist():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        node_ids = {n["id"] for n in data["nodes"]}
        for e in data["edges"]:
            assert e["from_"] in node_ids, f"Edge source {e['from_']} has no node"
            assert e["to"] in node_ids, f"Edge target {e['to']} has no node"


def test_pipeline_call_edges_have_nodes_on_both_ends():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        node_ids = {n["id"] for n in data["nodes"]}
        calls_edges = [e for e in data["edges"] if e["type"] == "calls"]
        for e in calls_edges:
            assert e["from_"] in node_ids
            assert e["to"] in node_ids


def test_pipeline_emits_unresolved_dynamic_gap_for_fully_dynamic_sql():
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "backend").mkdir(parents=True)
        (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "backend" / "db.py").write_text(
            "async def run_query(cur, query_var, values):\n"
            "    await cur.execute(query_var, values)\n",
            encoding="utf-8",
        )
        out = pathlib.Path(d) / "out"
        run(repo, out)
        data = json.loads((out / "graph.json").read_text())
        dyn_gaps = [g for g in data["gaps"] if g["kind"] == "unresolved_dynamic"]
        assert len(dyn_gaps) == 1
        assert dyn_gaps[0]["severity"] == {"comprehension": "warning", "compliance": "error"}


def test_run_without_exclude_arg_matches_default_empty_tuple():
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        run(SIMPLE_API, pathlib.Path(d1))
        run(SIMPLE_API, pathlib.Path(d2), exclude_patterns=())
        a = (pathlib.Path(d1) / "graph.json").read_text()
        b = (pathlib.Path(d2) / "graph.json").read_text()
        assert a == b


def test_pipeline_graph_json_has_empty_exclusions_by_default():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        assert data["exclusions"] == []


def test_pipeline_reports_exclusions_when_patterns_given(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "backend" / "tests").mkdir()
    (repo / "backend" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "tests" / "test_app.py").write_text(
        "def drop():\n    return 2\n", encoding="utf-8"
    )
    out = tmp_path / "out"
    run(repo, out, exclude_patterns=("backend/tests/**",))
    data = json.loads((out / "graph.json").read_text())
    assert data["exclusions"] == [{"pattern": "backend/tests/**", "count": 2}]


def test_excluded_run_keeps_surviving_node_ids_and_hashes_stable(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "backend" / "tests").mkdir()
    (repo / "backend" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "tests" / "test_app.py").write_text(
        "def drop():\n    return 2\n", encoding="utf-8"
    )

    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"
    run(repo, out_a)
    run(repo, out_b, exclude_patterns=("backend/tests/**",))

    nodes_a = {n["id"]: n for n in json.loads((out_a / "graph.json").read_text())["nodes"]}
    nodes_b = {n["id"]: n for n in json.loads((out_b / "graph.json").read_text())["nodes"]}

    assert "function:backend.tests.test_app.drop" in nodes_a
    assert "function:backend.tests.test_app.drop" not in nodes_b

    common_ids = set(nodes_a) & set(nodes_b)
    assert "function:backend.app.keep" in common_ids
    for node_id in common_ids:
        assert nodes_a[node_id]["hash"] == nodes_b[node_id]["hash"]
