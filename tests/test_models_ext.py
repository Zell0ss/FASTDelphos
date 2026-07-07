from cc.extract.endpoints import extract_endpoints
from cc.extract.models import extract_models
from tests.conftest import SIMPLE_API


def _setup():
    handler_nodes, _ = extract_endpoints(SIMPLE_API)
    fn_nodes = [n for n in handler_nodes if n.type == "function"]
    return extract_models(SIMPLE_API, fn_nodes)


def test_finds_two_models():
    nodes, _ = _setup()
    model_nodes = [n for n in nodes if n.type == "model"]
    names = {n.props["name"] for n in model_nodes}
    assert "MessageIn" in names
    assert "MessageOut" in names


def test_model_ids():
    nodes, _ = _setup()
    ids = {n.id for n in nodes if n.type == "model"}
    assert any("MessageIn" in i for i in ids)


def test_model_fields():
    nodes, _ = _setup()
    msg_in = next(n for n in nodes if n.type == "model" and n.props["name"] == "MessageIn")
    field_names = {f["name"] for f in msg_in.props["fields"]}
    assert "content" in field_names
    assert "author" in field_names


def test_uses_model_edges():
    nodes, edges = _setup()
    um_edges = [e for e in edges if e.type == "uses_model"]
    assert len(um_edges) >= 2  # at least one in, one out
    directions = {e.props["direction"] for e in um_edges}
    assert "in" in directions
    assert "out" in directions


def test_extract_models_excludes_matching_files(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "schemas.py").write_text(
        "from pydantic import BaseModel\n\n\nclass Kept(BaseModel):\n    x: int\n",
        encoding="utf-8",
    )
    (repo / "backend" / "tests").mkdir()
    (repo / "backend" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "tests" / "schemas.py").write_text(
        "from pydantic import BaseModel\n\n\nclass Dropped(BaseModel):\n    y: int\n",
        encoding="utf-8",
    )
    nodes, _ = extract_models(repo, [], exclude_patterns=("backend/tests/**",))
    names = {n.props["name"] for n in nodes}
    assert "Kept" in names
    assert "Dropped" not in names
