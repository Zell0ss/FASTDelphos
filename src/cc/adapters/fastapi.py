import pathlib


class FastAPIAdapter:
    def collect_files(self, repo_path: pathlib.Path) -> list[pathlib.Path]:
        return sorted(repo_path.rglob("*.py"))
