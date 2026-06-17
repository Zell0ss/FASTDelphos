import hashlib
import pathlib


def node_hash(file: str | pathlib.Path, lineno: int, end_lineno: int) -> str:
    """SHA-256 hex digest of lines[lineno-1:end_lineno] from file (1-based, inclusive)."""
    if lineno > end_lineno:
        raise ValueError(f"lineno ({lineno}) cannot be greater than end_lineno ({end_lineno})")

    try:
        lines = pathlib.Path(file).read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, IOError) as e:
        raise RuntimeError(f"Failed to read file {file}: {e}") from e

    span = "\n".join(lines[lineno - 1 : end_lineno])
    return hashlib.sha256(span.encode()).hexdigest()
