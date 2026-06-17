import importlib
import importlib.util
import pathlib

from cc.graph.schema import Node


def _load_app(repo_path: pathlib.Path):
    """Import the FastAPI `app` object from the target repo. Returns None on failure.

    Tries each candidate module name in order.  We first attempt a package
    import (so relative imports inside the module work) and fall back to
    direct file-loading when no package is present.
    """
    for candidate in ["main", "app", "server"]:
        # --- package-aware import (handles `from .models import …`) ---
        pkg_name = repo_path.name  # e.g. "simple_api"
        qualified = f"{pkg_name}.{candidate}"
        try:
            mod = importlib.import_module(qualified)
            if hasattr(mod, "app"):
                return mod.app
        except Exception:
            pass

        # --- fallback: direct file load (no relative imports) ---
        try:
            spec = importlib.util.spec_from_file_location(
                candidate, repo_path / f"{candidate}.py"
            )
            if spec is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "app"):
                return mod.app
        except Exception:
            continue

    return None


def _oracle_routes(app) -> set[str]:
    """Extract METHOD:path strings from a FastAPI app.

    Uses `app.openapi()` which fully resolves all included routers and returns
    only user-defined routes (no framework internals like /docs or /openapi.json).
    Falls back to walking `app.routes` when the OpenAPI generation fails.
    """
    try:
        schema = app.openapi()
        routes: set[str] = set()
        for path, methods_dict in schema.get("paths", {}).items():
            for method in methods_dict:
                routes.add(f"{method.upper()}:{path}")
        return routes
    except Exception:
        pass

    # Fallback: walk app.routes (may miss sub-routers in newer FastAPI)
    routes = set()
    for route in getattr(app, "routes", []):
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if path and methods:
            for m in methods:
                routes.add(f"{m.upper()}:{path}")
    return routes


def compare_oracle(
    repo_path: str | pathlib.Path,
    ep_nodes: list[Node],
) -> dict:
    """Compare static-extracted endpoints against runtime-introspected routes.

    Args:
        repo_path: Path to the target repo directory.
        ep_nodes:  Nodes produced by extract_endpoints() for the same repo.

    Returns:
        dict with keys:
            static_count   – endpoints found by static analysis
            oracle_count   – routes found at runtime
            recovery_rate  – fraction of oracle routes found statically (0.0–1.0)
            missing        – sorted list of routes the static pass missed
    """
    repo_path = pathlib.Path(repo_path)
    app = _load_app(repo_path)
    if app is None:
        return {
            "static_count": len([n for n in ep_nodes if n.type == "endpoint"]),
            "oracle_count": 0,
            "recovery_rate": 0.0,
            "missing": [],
            "error": "Could not load app",
        }

    oracle_routes = _oracle_routes(app)

    static_routes: set[str] = set()
    for n in ep_nodes:
        if n.type == "endpoint":
            static_routes.add(f"{n.props['method']}:{n.props['path']}")

    oracle_count = len(oracle_routes)
    static_count = len(static_routes)
    matched = static_routes & oracle_routes
    recovery_rate = len(matched) / oracle_count if oracle_count > 0 else 0.0
    missing = sorted(oracle_routes - static_routes)

    return {
        "static_count": static_count,
        "oracle_count": oracle_count,
        "recovery_rate": recovery_rate,
        "missing": missing,
    }
