from cc.llm.neighborhood import serialize_neighborhood


def _node(id_, type_, props=None):
    return {"id": id_, "type": type_, "file": "f.py", "line": 1, "hash": "h", "inferred": False, "props": props or {}}


def _edge(from_, to, type_):
    return {"from_": from_, "to": to, "type": type_, "inferred": False, "props": {}}


def test_lists_callers_and_callees():
    graph = {
        "nodes": [
            _node("function:a", "function"),
            _node("function:b", "function"),
            _node("function:c", "function"),
        ],
        "edges": [
            _edge("function:a", "function:b", "calls"),
            _edge("function:b", "function:c", "calls"),
        ],
    }
    text = serialize_neighborhood(graph, "function:b")
    assert "Quién lo llama: function:a" in text
    assert "A qué llama: function:c" in text


def test_no_callers_or_callees_says_so():
    graph = {"nodes": [_node("function:a", "function")], "edges": []}
    text = serialize_neighborhood(graph, "function:a")
    assert "Quién lo llama: nadie" in text
    assert "A qué llama: nada" in text


def test_tables_include_columns():
    graph = {
        "nodes": [
            _node("function:a", "function"),
            _node("table:t", "table", props={"name": "t", "columns": ["id", "name"]}),
        ],
        "edges": [_edge("function:a", "table:t", "reads")],
    }
    text = serialize_neighborhood(graph, "function:a")
    assert "Tablas que lee: t(id, name)" in text
    assert "Tablas que escribe: ninguna" in text


def test_reachable_endpoints_via_backward_bfs():
    graph = {
        "nodes": [
            _node("endpoint:GET:/x", "endpoint"),
            _node("function:handler", "function"),
            _node("function:target", "function"),
        ],
        "edges": [
            _edge("function:handler", "function:target", "calls"),
        ],
    }
    # endpoint "handles" its handler via a `handles` edge, per ESQUEMA_POC.md
    graph["edges"].append(_edge("endpoint:GET:/x", "function:handler", "handles"))
    text = serialize_neighborhood(graph, "function:target")
    assert "Alcanzable desde estos endpoints: endpoint:GET:/x" in text


def test_reachability_stops_at_hub_nodes():
    # 5 distinct callers into "function:hub" makes it a hub (HUB_MIN_ABSOLUTE=5),
    # so the walk must not continue past it even though an endpoint calls it.
    nodes = [_node("function:hub", "function"), _node("function:target", "function")]
    edges = [_edge("function:hub", "function:target", "calls")]
    for i in range(5):
        caller_id = f"function:caller{i}"
        nodes.append(_node(caller_id, "function"))
        edges.append(_edge(caller_id, "function:hub", "calls"))
    nodes.append(_node("endpoint:GET:/x", "endpoint"))
    edges.append(_edge("endpoint:GET:/x", "function:caller0", "calls"))
    graph = {"nodes": nodes, "edges": edges}

    text = serialize_neighborhood(graph, "function:target")
    assert "Alcanzable desde estos endpoints: ninguno directamente" in text
