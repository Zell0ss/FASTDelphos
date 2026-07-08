from cc.extract.endpoints import extract_endpoints
from tests.conftest import SIMPLE_API


def test_finds_two_endpoints():
    nodes, edges = extract_endpoints(SIMPLE_API)
    ep_nodes = [n for n in nodes if n.type == "endpoint"]
    assert len(ep_nodes) == 2


def test_endpoint_methods_and_paths():
    nodes, edges = extract_endpoints(SIMPLE_API)
    ep_nodes = {n.props["method"] + " " + n.props["path"]: n for n in nodes if n.type == "endpoint"}
    assert "POST /messages/" in ep_nodes
    assert "GET /messages/{msg_id}" in ep_nodes


def test_endpoint_ids_are_stable():
    nodes, _ = extract_endpoints(SIMPLE_API)
    ep_ids = {n.id for n in nodes if n.type == "endpoint"}
    assert "endpoint:POST:/messages/" in ep_ids
    assert "endpoint:GET:/messages/{msg_id}" in ep_ids


def test_handles_edges_link_endpoint_to_handler():
    nodes, edges = extract_endpoints(SIMPLE_API)
    handles = [e for e in edges if e.type == "handles"]
    assert len(handles) == 2
    handler_qualnames = {e.to for e in handles}
    assert any("create_message" in q for q in handler_qualnames)
    assert any("get_message" in q for q in handler_qualnames)


def test_endpoint_nodes_have_hash():
    nodes, _ = extract_endpoints(SIMPLE_API)
    for n in nodes:
        assert len(n.hash) == 64
        assert n.inferred is False


def test_extract_endpoints_respects_exclude_patterns(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "backend" / "routes.py").write_text(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n\n"
        "@router.get('/kept')\n"
        "def kept():\n    return {}\n",
        encoding="utf-8",
    )
    (tmp_path / "backend" / "tests").mkdir()
    (tmp_path / "backend" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "backend" / "tests" / "fixtures.py").write_text(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n\n"
        "@router.get('/dropped')\n"
        "def dropped():\n    return {}\n",
        encoding="utf-8",
    )
    nodes, _ = extract_endpoints(tmp_path, exclude_patterns=("backend/tests/**",))
    paths = {n.props["path"] for n in nodes if n.type == "endpoint"}
    assert paths == {"/kept"}


def test_decorated_handler_gets_decorator_inclusive_hash(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "api.py").write_text(
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter()\n"
        "\n"
        "\n"
        "def audit(fn):\n"
        "    return fn\n"
        "\n"
        "\n"
        "@audit\n"
        '@router.get("/x")\n'
        "def handler():\n"
        "    return {}\n",
        encoding="utf-8",
    )
    nodes, edges = extract_endpoints(repo)
    fn_node = next(n for n in nodes if n.type == "function")
    ep_node = next(n for n in nodes if n.type == "endpoint")
    assert fn_node.line == 12  # the `def` line, not either decorator (10 or 11)
    from cc.graph.hash_util import node_hash

    expected_hash = node_hash(repo / "backend" / "api.py", 10, 13)  # both decorators through end
    assert fn_node.hash == expected_hash
    assert ep_node.line == fn_node.line  # endpoint and handler still share the same span
    assert ep_node.hash == fn_node.hash
