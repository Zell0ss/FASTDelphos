import pathlib

from cc.extract._calls_resolver import build_symbol_inventory


def _write(root: pathlib.Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    _write(repo, "services/__init__.py", "")
    _write(repo, "services/base.py", (
        "class Greeter:\n"
        "    def greet(self, name: str) -> str:\n"
        "        return f'hello {name}'\n"
    ))
    _write(repo, "services/child.py", (
        "from services.base import Greeter\n\n\n"
        "class LoudGreeter(Greeter):\n"
        "    def shout(self, name: str) -> str:\n"
        "        return self.greet(name).upper()\n"
    ))
    _write(repo, "services/helpers.py", (
        "def extra(text: str) -> str:\n"
        "    return text + '!'\n"
    ))
    return repo


def test_finds_module_level_function(tmp_path):
    repo = _make_repo(tmp_path)
    inv = build_symbol_inventory(repo)
    assert "services.helpers.extra" in inv.functions
    info = inv.functions["services.helpers.extra"]
    assert info.kind == "function"
    assert info.lineno == 1
    assert info.endlineno == 2


def test_finds_methods_with_method_kind(tmp_path):
    repo = _make_repo(tmp_path)
    inv = build_symbol_inventory(repo)
    assert "services.base.Greeter.greet" in inv.functions
    assert inv.functions["services.base.Greeter.greet"].kind == "method"


def test_records_class_bases(tmp_path):
    repo = _make_repo(tmp_path)
    inv = build_symbol_inventory(repo)
    assert inv.class_bases["services.child.LoudGreeter"] == ["services.base.Greeter"]


def test_records_class_methods(tmp_path):
    repo = _make_repo(tmp_path)
    inv = build_symbol_inventory(repo)
    assert inv.class_methods["services.base.Greeter"] == {"greet": "services.base.Greeter.greet"}
    assert inv.class_methods["services.child.LoudGreeter"] == {"shout": "services.child.LoudGreeter.shout"}


def test_top_level_packages_recorded_even_if_load_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _write(repo, "broken/__init__.py", "")
    _write(repo, "broken/oops.py", "def f(:\n")  # SyntaxError — griffe.load will raise
    inv = build_symbol_inventory(repo)
    assert "broken" in inv.top_level_packages  # directory-based, not parse-success-based
    assert "services" in inv.top_level_packages
