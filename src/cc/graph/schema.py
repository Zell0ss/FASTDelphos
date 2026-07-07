from dataclasses import dataclass, field


@dataclass
class Node:
    id: str
    type: str  # endpoint | function | model | table
    file: str
    line: int
    hash: str
    inferred: bool
    props: dict = field(default_factory=dict)


@dataclass
class Edge:
    from_: str
    to: str
    type: str  # handles | uses_model | calls | reads | writes
    inferred: bool
    props: dict = field(default_factory=dict)


@dataclass
class Gap:
    kind: str  # missing_artifact | unresolved_dynamic | tool_limitation
    where: str  # "file:line"
    node_id: str | None
    missing: str
    suggested: str
    severity: dict  # {"comprehension": "warning"|"error", "compliance": "warning"|"error"}


@dataclass
class Graph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    exclusions: list[dict] = field(default_factory=list)
