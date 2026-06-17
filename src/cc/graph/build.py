from cc.graph.schema import Edge, Graph, Node


def build_graph(nodes: list[Node], edges: list[Edge]) -> Graph:
    seen: dict[str, Node] = {}
    for n in nodes:
        if n.id not in seen:
            seen[n.id] = n
    return Graph(nodes=list(seen.values()), edges=list(edges), gaps=[])
