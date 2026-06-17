from typing import Protocol
import pathlib


class Adapter(Protocol):
    def collect_files(self, repo_path: pathlib.Path) -> list[pathlib.Path]: ...
