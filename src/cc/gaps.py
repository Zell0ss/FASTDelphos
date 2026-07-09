from cc.graph.schema import Gap, Graph, Node


def _detect_ambiguous_endpoints(graph: Graph) -> list[Gap]:
    """Group endpoint nodes by (method, path) — the apparent route a runtime
    caller would use. A group with more than one member means the tool found
    multiple handlers that could apparently answer the same route; real
    disambiguation lives in the router-registration order, which is
    runtime-bound, not something a dev should be asked to change. Flag it,
    don't guess which one wins."""
    groups: dict[tuple[str, str], list[Node]] = {}
    for node in graph.nodes:
        if node.type != "endpoint":
            continue
        key = (node.props["method"], node.props["path"])
        groups.setdefault(key, []).append(node)

    gaps: list[Gap] = []
    for (method, path), nodes in sorted(groups.items()):
        if len(nodes) < 2:
            continue
        locations = sorted(f"{n.file}:{n.line}" for n in nodes)
        gaps.append(
            Gap(
                kind="unresolved_dynamic",
                where="; ".join(locations),
                node_id=None,
                missing=f"ruta ambigua: {len(nodes)} handlers declaran {method} {path}; "
                "la desambiguación vive en el registro de routers",
                suggested="",
                severity={"comprehension": "warning", "compliance": "error"},
            )
        )
    return gaps


def detect_gaps(graph: Graph) -> list[Gap]:
    gaps: list[Gap] = []
    for node in graph.nodes:
        if node.type != "table":
            continue
        if not node.props.get("columns"):
            gaps.append(
                Gap(
                    kind="missing_artifact",
                    where=f"{node.file}:{node.line}",
                    node_id=node.id,
                    missing=f"No columns inferred for table `{node.props['name']}`"
                    " — no CREATE TABLE, INSERT, or single-table SELECT found",
                    suggested=f"-- TODO: add DDL for `{node.props['name']}`, "
                    f"e.g. CREATE TABLE {node.props['name']} (id INT, ...)",
                    severity={"comprehension": "warning", "compliance": "error"},
                )
            )
    gaps.extend(_detect_ambiguous_endpoints(graph))
    return gaps
