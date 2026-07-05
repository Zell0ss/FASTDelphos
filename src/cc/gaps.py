from cc.graph.schema import Gap, Graph


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
    return gaps
