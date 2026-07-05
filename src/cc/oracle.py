import importlib
import importlib.util
import pathlib

from cc.graph.schema import Node

_SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", "tests", "dist", "build"}
_CANDIDATES = ["main", "app", "server"]


def _pkg_names(repo_path: pathlib.Path) -> list[str]:
    """Top-level importable names to probe: repo itself, then sub-packages."""
    names = [repo_path.name]
    for init in repo_path.glob("*/__init__.py"):
        if init.parent.name not in _SKIP_DIRS:
            names.append(init.parent.name)
    return names


def _repo_site_packages(repo_path: pathlib.Path) -> list[str]:
    """Find the target repo's .venv site-packages paths, if present."""
    import sys as _sys

    ver = f"python{_sys.version_info.major}.{_sys.version_info.minor}"
    candidates = [
        repo_path / ".venv" / "lib" / ver / "site-packages",
        repo_path / "venv" / "lib" / ver / "site-packages",
    ]
    return [str(p) for p in candidates if p.is_dir()]


def _load_app(repo_path: pathlib.Path):
    """Import the FastAPI `app` object from the target repo. Returns None on failure."""
    import os as _os
    import sys as _sys

    extra = [str(repo_path)] + _repo_site_packages(repo_path)
    old_cwd = _os.getcwd()
    for p in reversed(extra):
        _sys.path.insert(0, p)
    _os.chdir(repo_path)  # pydantic-settings reads .env relative to CWD
    try:
        for pkg_name in _pkg_names(repo_path):
            for candidate in _CANDIDATES:
                try:
                    mod = importlib.import_module(f"{pkg_name}.{candidate}")
                    if hasattr(mod, "app"):
                        return mod.app
                except Exception:
                    pass

        # Fallback: direct file load for flat repos (no package)
        for candidate in _CANDIDATES:
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
    finally:
        _os.chdir(old_cwd)
        for p in extra:
            try:
                _sys.path.remove(p)
            except ValueError:
                pass

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
