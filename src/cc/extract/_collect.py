import pathlib

import pathspec

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


def _anchor_gitignore_pattern(raw_line: str, prefix: str) -> str | None:
    """Rewrite one raw .gitignore line so it's anchored to the repo root
    instead of to the directory its .gitignore file lives in (`prefix`,
    posix-style, relative to repo root; "" for the root .gitignore itself).

    Mirrors git's own anchoring rules (gitignore(5)): a pattern containing a
    "/" anywhere but the end is directory-relative already; a bare name (no
    "/") matches at any depth beneath its own .gitignore's directory.
    Returns None for blank lines and comments.
    """
    line = raw_line.rstrip("\n")
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if not prefix:
        return line

    negate = line.startswith("!")
    body = line[1:] if negate else line

    if body.startswith("/"):
        anchored = f"{prefix}{body}"
    elif "/" in body.rstrip("/"):
        anchored = f"{prefix}/{body}"
    else:
        anchored = f"{prefix}/**/{body}"

    return f"!{anchored}" if negate else anchored


def _gitignore_files(repo_path: pathlib.Path) -> list[pathlib.Path]:
    """Root + nested .gitignore files under repo_path, in deterministic
    order, excluding any living inside a skipped directory (.git, .venv,
    ...) — those aren't part of the repo's own source tree."""
    return sorted(
        p
        for p in repo_path.rglob(".gitignore")
        if not _SKIP_PARTS.intersection(p.relative_to(repo_path).parts[:-1])
    )


def _load_gitignore_spec(repo_path: pathlib.Path) -> "pathspec.PathSpec | None":
    """Combine every .gitignore under repo_path (root + nested, each
    anchored to its own directory via _anchor_gitignore_pattern) into a
    single gitignore-pattern PathSpec, or None if the repo has no .gitignore
    files at all."""
    patterns: list[str] = []
    for gi_file in _gitignore_files(repo_path):
        rel_dir = gi_file.parent.relative_to(repo_path)
        prefix = "" if str(rel_dir) == "." else rel_dir.as_posix()
        for raw_line in gi_file.read_text(encoding="utf-8").splitlines():
            pattern = _anchor_gitignore_pattern(raw_line, prefix)
            if pattern is not None:
                patterns.append(pattern)
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def gitignore_excluded_files(
    repo_path: pathlib.Path, use_gitignore: bool = True
) -> set[pathlib.Path]:
    """.py files under repo_path matched by the repo's own (root + nested)
    .gitignore rules — never the user's global gitignore or
    .git/info/exclude, so output stays identical across machines. Empty set
    when disabled or when the repo has no .gitignore at all."""
    if not use_gitignore:
        return set()
    spec = _load_gitignore_spec(repo_path)
    if spec is None:
        return set()
    return {
        f
        for f in repo_path.rglob("*.py")
        if not _SKIP_PARTS.intersection(f.parts)
        and spec.match_file(f.relative_to(repo_path).as_posix())
    }


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
