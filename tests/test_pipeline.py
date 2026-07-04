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
