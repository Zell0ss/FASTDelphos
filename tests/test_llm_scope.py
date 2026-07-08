from cc.llm.scope import select_annotation_targets


def _node(id_, type_):
    return {
        "id": id_,
        "type": type_,
        "file": "f.py",
        "line": 1,
        "hash": "h",
        "inferred": False,
        "props": {},
    }


def _edge(from_, to, type_):
    return {"from_": from_, "to": to, "type": type_, "inferred": False, "props": {}}


def test_all_endpoints_are_always_selected():
    graph = {
        "nodes": [_node("endpoint:GET:/x", "endpoint"), _node("function:leaf", "function")],
        "edges": [],
    }
    targets = select_annotation_targets(graph, threshold=2)
    assert "endpoint:GET:/x" in targets
    assert "function:leaf" not in targets


def test_function_selected_when_calls_out_meets_threshold():
    graph = {
        "nodes": [
            _node("function:orchestrator", "function"),
            _node("function:a", "function"),
            _node("function:b", "function"),
        ],
        "edges": [
            _edge("function:orchestrator", "function:a", "calls"),
            _edge("function:orchestrator", "function:b", "calls"),
        ],
    }
    targets = select_annotation_targets(graph, threshold=2)
    assert "function:orchestrator" in targets


def test_function_selected_when_tables_touched_meets_threshold():
    graph = {
        "nodes": [
            _node("function:writer", "function"),
            _node("table:t1", "table"),
            _node("table:t2", "table"),
        ],
        "edges": [
            _edge("function:writer", "table:t1", "reads"),
            _edge("function:writer", "table:t2", "writes"),
        ],
    }
    targets = select_annotation_targets(graph, threshold=2)
    assert "function:writer" in targets


def test_function_below_threshold_is_excluded():
    graph = {
        "nodes": [_node("function:leaf", "function"), _node("function:only_one", "function")],
        "edges": [_edge("function:leaf", "function:only_one", "calls")],
    }
    targets = select_annotation_targets(graph, threshold=2)
    assert "function:leaf" not in targets


def test_threshold_is_configurable_not_hardcoded():
    graph = {
        "nodes": [_node("function:a", "function"), _node("function:b", "function")],
        "edges": [_edge("function:a", "function:b", "calls")],
    }
    assert "function:a" in select_annotation_targets(graph, threshold=1)
    assert "function:a" not in select_annotation_targets(graph, threshold=2)
