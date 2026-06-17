from cc.extract.calls import extract_calls
from tests.conftest import SIMPLE_API


def test_returns_edge_list():
    edges = extract_calls(SIMPLE_API)
    assert isinstance(edges, list)


def test_calls_edges_have_correct_type():
    edges = extract_calls(SIMPLE_API)
    for e in edges:
        assert e.type == "calls"
        assert e.inferred is False
        assert e.from_.startswith("function:")
        assert e.to.startswith("function:")


def test_no_self_loops():
    edges = extract_calls(SIMPLE_API)
    for e in edges:
        assert e.from_ != e.to
