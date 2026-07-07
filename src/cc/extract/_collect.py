import pathlib

_SKIP_PARTS = {".venv", "__pycache__", ".git", "node_modules", ".tox", "dist", "build"}


def _glob_py_files(repo_path: pathlib.Path, pattern: str) -> list[pathlib.Path]:
    """Expand a single glob pattern (relative to repo_path) and return the .py
    files it matches.

    On Python <3.13, pathlib.Path.glob("dir/**") matches directories only —
    not the files beneath them (this changed in 3.13, where a trailing "**"
    also matches files recursively). Since this project targets 3.11+, a
    pattern ending in "**" is normalized to "**/*" so "backend/tests/**"
    reaches nested files like "backend/tests/unit/test_deep.py" on every
    supported Python version.
    """
    if pattern.endswith("**"):
        pattern = pattern + "/*"
    return [p for p in repo_path.glob(pattern) if p.suffix == ".py"]


def excluded_files(
    repo_path: pathlib.Path, exclude_patterns: tuple[str, ...] = ()
) -> set[pathlib.Path]:
    """Expand each glob pattern (relative to repo_path) and return the union of
    .py files any pattern matches (absolute paths).

    Shared by collect_py_files (subtracts this set from the file list) and the
    griffe-backed extractors in models.py / _calls_resolver.py (prune the same
    files out of their symbol inventories), so every stage of the pipeline
    agrees on what "doesn't exist" means — no asymmetric resolution toward
    excluded code.
    """
    excluded: set[pathlib.Path] = set()
    for pattern in sorted(exclude_patterns):
        excluded.update(_glob_py_files(repo_path, pattern))
    return excluded


def exclusion_report(repo_path: pathlib.Path, exclude_patterns: tuple[str, ...] = ()) -> list[dict]:
    """[{"pattern": str, "count": int}, ...] sorted by pattern — how many .py
    files each individual --exclude pattern matched, for the coverage report
    and the compiled graph's metadata."""
    report = []
    for pattern in sorted(exclude_patterns):
        count = len(_glob_py_files(repo_path, pattern))
        report.append({"pattern": pattern, "count": count})
    return report


def collect_py_files(
    repo_path: pathlib.Path, exclude_patterns: tuple[str, ...] = ()
) -> list[pathlib.Path]:
    """Return all .py files under repo_path, excluding non-source directories
    and anything matched by exclude_patterns (glob, relative to repo_path)."""
    excluded = excluded_files(repo_path, exclude_patterns)
    return sorted(
        f
        for f in repo_path.rglob("*.py")
        if not _SKIP_PARTS.intersection(f.parts) and f not in excluded
    )
