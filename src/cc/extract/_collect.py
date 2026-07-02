import pathlib

_SKIP_PARTS = {".venv", "__pycache__", ".git", "node_modules", ".tox", "dist", "build"}


def collect_py_files(repo_path: pathlib.Path) -> list[pathlib.Path]:
    """Return all .py files under repo_path, excluding non-source directories."""
    return sorted(
        f for f in repo_path.rglob("*.py")
        if not _SKIP_PARTS.intersection(f.parts)
    )
