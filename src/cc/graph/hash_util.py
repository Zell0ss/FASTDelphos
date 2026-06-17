import hashlib
import pathlib


def node_hash(file: str | pathlib.Path, lineno: int, end_lineno: int) -> str:
    lines = pathlib.Path(file).read_text(encoding="utf-8").splitlines()
    span = "\n".join(lines[lineno - 1 : end_lineno])
    return hashlib.sha256(span.encode()).hexdigest()
