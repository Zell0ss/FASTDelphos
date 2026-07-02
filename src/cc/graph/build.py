from cc.graph.schema import Edge, Graph, Node


def build_graph(nodes: list[Node], edges: list[Edge]) -> Graph:
    seen: dict[str, Node] = {}
    for n in nodes:
        if n.id not in seen:
            seen[n.id] = n

    node_ids = set(seen)
    valid_edges = [e for e in edges if e.from_ in node_ids and e.to in node_ids]

    return Graph(nodes=list(seen.values()), edges=valid_edges, gaps=[])
