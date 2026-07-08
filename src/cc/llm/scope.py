def select_annotation_targets(graph: dict, threshold: int) -> list[str]:
    """Spec §5: default batch scope. Every endpoint, plus every function that
    orchestrates — >=threshold outgoing calls, or >=threshold distinct tables
    touched (reads+writes deduped)."""
    nodes = graph["nodes"]
    edges = graph["edges"]

    calls_out: dict[str, set[str]] = {}
    tables_touched: dict[str, set[str]] = {}
    for e in edges:
        if e["type"] == "calls":
            calls_out.setdefault(e["from_"], set()).add(e["to"])
        elif e["type"] in ("reads", "writes"):
            tables_touched.setdefault(e["from_"], set()).add(e["to"])

    targets = []
    for n in nodes:
        if n["type"] == "endpoint":
            targets.append(n["id"])
        elif n["type"] == "function":
            out_count = len(calls_out.get(n["id"], ()))
            table_count = len(tables_touched.get(n["id"], ()))
            if out_count >= threshold or table_count >= threshold:
                targets.append(n["id"])
    return targets
