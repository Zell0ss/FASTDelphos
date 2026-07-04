from cc.graph.schema import Edge, Graph, Node


def build_graph(nodes: list[Node], edges: list[Edge]) -> Graph:
    seen: dict[str, Node] = {}
    for n in nodes:
        if n.id not in seen:
            seen[n.id] = n

    node_ids = set(seen)
    valid_edges: list[Edge] = []
    dropped: list[Edge] = []
    for e in edges:
        if e.from_ in node_ids and e.to in node_ids:
            valid_edges.append(e)
        else:
            dropped.append(e)

    if dropped:
        print(f"  graph build: {len(dropped)} edge(s) dropped — endpoint node missing:")
        for e in dropped:
            missing = [x for x in (e.from_, e.to) if x not in node_ids]
            print(f"    {e.type}: {e.from_} -> {e.to} (missing: {', '.join(missing)})")

    return Graph(nodes=list(seen.values()), edges=valid_edges, gaps=[])
