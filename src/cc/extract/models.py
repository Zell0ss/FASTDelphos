import ast
import pathlib
import sys

import griffe

from cc.graph.schema import Edge, Node
from cc.graph.hash_util import node_hash


_SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", ".tox", "dist", "build", "tests"}


def _load_models(repo_path: pathlib.Path) -> dict[str, "griffe.Class"]:
    """Return short_name -> griffe.Class for all BaseModel subclasses under repo_path."""
    found: dict[str, griffe.Class] = {}

    def _try_load(pkg_name: str, search_paths: list[pathlib.Path]) -> None:
        sys.path.insert(0, str(search_paths[0]))
        try:
            pkg = griffe.load(pkg_name, search_paths=search_paths)
            _walk_griffe(pkg, found)
        except Exception:
            pass
        finally:
            try:
                sys.path.remove(str(search_paths[0]))
            except ValueError:
                pass

    # Top-level sub-packages inside the repo (e.g. agora/backend/).
    # griffe.load on each recurses into sub-packages automatically.
    loaded_any = False
    for init in repo_path.glob("*/__init__.py"):
        if init.parent.name in _SKIP_DIRS:
            continue
        _try_load(init.parent.name, [repo_path])
        loaded_any = True

    # Fallback: the repo itself is the package (e.g. tests/fixtures/simple_api/).
    if not loaded_any and (repo_path / "__init__.py").exists():
        _try_load(repo_path.name, [repo_path.parent])

    return found


def _walk_griffe(obj: "griffe.Object", found: dict[str, "griffe.Class"]) -> None:
    """Recursively walk griffe object tree, skipping unresolvable aliases."""
    if isinstance(obj, griffe.Alias):
        # Skip aliases to external packages (e.g. fastapi.APIRouter)
        return
    if isinstance(obj, griffe.Class):
        bases = []
        for b in obj.bases or []:
            try:
                bases.append(b.canonical_path if hasattr(b, "canonical_path") else str(b))
            except Exception:
                bases.append(str(b))
        if any("BaseModel" in b for b in bases):
            found[obj.name] = obj
    if hasattr(obj, "members"):
        for child in obj.members.values():
            _walk_griffe(child, found)


def _griffe_fields(cls: "griffe.Class") -> list[dict]:
    """Extract field dicts from a griffe Class (Attributes with annotations)."""
    fields = []
    for name, member in cls.members.items():
        if isinstance(member, griffe.Attribute) and member.annotation is not None:
            fields.append({"name": name, "type": str(member.annotation)})
    return fields


def _annotation_names(ann: ast.expr | None) -> list[str]:
    """Extract bare type names from an annotation AST node."""
    if ann is None:
        return []
    if isinstance(ann, ast.Name):
        return [ann.id]
    if isinstance(ann, ast.Attribute):
        return [ann.attr]
    if isinstance(ann, ast.Subscript):
        return _annotation_names(ann.slice)
    if isinstance(ann, ast.Tuple):
        names = []
        for elt in ann.elts:
            names.extend(_annotation_names(elt))
        return names
    return []


def extract_models(
    repo_path: str | pathlib.Path,
    handler_nodes: list[Node],
) -> tuple[list[Node], list[Edge]]:
    repo_path = pathlib.Path(repo_path)
    griffe_models = _load_models(repo_path)

    model_nodes: dict[str, Node] = {}
    for short_name, cls in griffe_models.items():
        qname = cls.canonical_path
        m_id = f"model:{qname}"
        file_path = cls.filepath or "unknown"
        lineno = cls.lineno or 1
        end_lineno = cls.endlineno or lineno
        m_hash = node_hash(file_path, lineno, end_lineno)
        fields = _griffe_fields(cls)
        model_nodes[short_name] = Node(
            id=m_id, type="model", file=str(file_path),
            line=lineno, hash=m_hash, inferred=False,
            props={"name": short_name, "kind": "request", "fields": fields},
        )

    edges: list[Edge] = []
    for fn_node in handler_nodes:
        if not fn_node.props.get("is_handler"):
            continue
        try:
            source = pathlib.Path(fn_node.file).read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            # File not found or unreadable; skip this handler
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        func_name = fn_node.props["qualname"].split(".")[-1]
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if node.name != func_name:
                continue
            # Parameters → direction=in
            for arg in node.args.args + node.args.kwonlyargs:
                for type_name in _annotation_names(arg.annotation):
                    if type_name in model_nodes:
                        edges.append(Edge(
                            from_=fn_node.id, to=model_nodes[type_name].id,
                            type="uses_model", inferred=False,
                            props={"direction": "in"},
                        ))
            # Return annotation → direction=out
            for type_name in _annotation_names(node.returns):
                if type_name in model_nodes:
                    edges.append(Edge(
                        from_=fn_node.id, to=model_nodes[type_name].id,
                        type="uses_model", inferred=False,
                        props={"direction": "out"},
                    ))

    return list(model_nodes.values()), edges
