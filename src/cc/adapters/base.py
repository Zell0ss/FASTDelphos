import pathlib
from typing import Protocol


class Adapter(Protocol):
    def collect_files(self, repo_path: pathlib.Path) -> list[pathlib.Path]: ...
