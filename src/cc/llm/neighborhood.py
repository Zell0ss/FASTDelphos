import math

_HUB_MIN_PERCENT = 0.15
_HUB_MIN_ABSOLUTE = 5


def _hub_ids(nodes: list[dict], edges: list[dict]) -> set[str]:
    """Mirror the render's hub detection (template_src.html) so the prompt's
    reachability description matches what a human sees in the UI panel."""
    function_count = sum(1 for n in nodes if n["type"] == "function")
    threshold = max(_HUB_MIN_ABSOLUTE, math.ceil(_HUB_MIN_PERCENT * function_count))
    in_degree: dict[str, int] = {}
    for e in edges:
        in_degree[e["to"]] = in_degree.get(e["to"], 0) + 1
    return {n["id"] for n in nodes if in_degree.get(n["id"], 0) >= threshold}


def _reachable_endpoints(nodes: list[dict], edges: list[dict], target_id: str) -> list[str]:
    by_id = {n["id"]: n for n in nodes}
    edges_to: dict[str, list[dict]] = {}
    for e in edges:
        edges_to.setdefault(e["to"], []).append(e)
    hub_ids = _hub_ids(nodes, edges)

    visited = {target_id}
    queue = [target_id]
    endpoint_ids: list[str] = []
    while queue:
        curr = queue.pop(0)
        for e in edges_to.get(curr, []):
            prev = e["from_"]
            if prev in visited:
                continue
            visited.add(prev)
            prev_node = by_id.get(prev)
            if prev_node and prev_node["type"] == "endpoint":
                endpoint_ids.append(prev)
            if prev in hub_ids:
                continue
            queue.append(prev)
    return endpoint_ids


def _table_line(label: str, table_ids: list[str], by_id: dict[str, dict]) -> str:
    if not table_ids:
        return f"{label}: ninguna"
    parts = []
    for tid in table_ids:
        t = by_id.get(tid)
        props = t["props"] if t else {}
        name = props.get("name", tid)
        cols = props.get("columns", [])
        parts.append(f"{name}({', '.join(cols)})" if cols else name)
    return f"{label}: " + ", ".join(parts)


def serialize_neighborhood(graph: dict, node_id: str) -> str:
    """Plain-text serialization of a node's graph neighborhood, for the LLM
    user prompt (spec §6 point 2). Deliberately mirrors the same adjacency
    the render's node panel already shows a human."""
    nodes = graph["nodes"]
    edges = graph["edges"]
    by_id = {n["id"]: n for n in nodes}

    callers = [e["from_"] for e in edges if e["type"] == "calls" and e["to"] == node_id]
    callees = [e["to"] for e in edges if e["type"] == "calls" and e["from_"] == node_id]
    reads = [e["to"] for e in edges if e["type"] == "reads" and e["from_"] == node_id]
    writes = [e["to"] for e in edges if e["type"] == "writes" and e["from_"] == node_id]
    endpoint_ids = _reachable_endpoints(nodes, edges, node_id)

    lines = [
        "Quién lo llama: " + (", ".join(callers) if callers else "nadie (dentro del grafo)"),
        "A qué llama: " + (", ".join(callees) if callees else "nada (dentro del grafo)"),
        _table_line("Tablas que lee", reads, by_id),
        _table_line("Tablas que escribe", writes, by_id),
        "Alcanzable desde estos endpoints: "
        + (", ".join(endpoint_ids) if endpoint_ids else "ninguno directamente"),
    ]
    return "\n".join(lines)
