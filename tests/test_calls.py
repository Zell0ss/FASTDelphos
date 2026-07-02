from cc.extract.calls import extract_calls
from tests.conftest import SIMPLE_API


def test_returns_edge_list():
    edges, excluded = extract_calls(SIMPLE_API)
    assert isinstance(edges, list)
    assert isinstance(excluded, list)


def test_calls_edges_have_correct_type():
    edges, _ = extract_calls(SIMPLE_API)
    for e in edges:
        assert e.type == "calls"
        assert e.inferred is False
        assert e.from_.startswith("function:")
        assert e.to.startswith("function:")


def test_no_self_loops():
    edges, _ = extract_calls(SIMPLE_API)
    for e in edges:
        assert e.from_ != e.to


def test_excluded_is_list_of_tuples():
    _, excluded = extract_calls(SIMPLE_API)
    for filepath, error in excluded:
        assert isinstance(filepath, str)
        assert isinstance(error, str)
