import pathlib

import griffe
import pytest

from cc.extract._griffe_loader import load_tolerant


def _write(root: pathlib.Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_shadowed_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Mirrors illumiows: a namespace root (`api/`, no __init__.py) containing a
    regular subpackage (`public/`, WITH __init__.py) that re-exports two of its
    own namespace sub-subpackages (`workload/`, `labels/`, no __init__.py) under
    aliases sharing the subdirectory's own name — the shadowing pattern that
    poisons griffe's loader (see doc_proyecto spec)."""
    repo = tmp_path / "repo"
    _write(
        repo,
        "api/public/__init__.py",
        (
            "from api.public.workload import views as workload\n"
            "from api.public.labels import views as labels\n"
        ),
    )
    _write(
        repo,
        "api/public/workload/views.py",
        (
            "from api.public.workload.crud import helper\n\n\n"
            "def get_workload():\n"
            "    return helper()\n"
        ),
    )
    _write(repo, "api/public/workload/crud.py", "def helper():\n    return 42\n")
    _write(repo, "api/public/labels/views.py", "def get_labels():\n    return []\n")
    _write(repo, "asgi.py", "x = 1\n")
    return repo


def _walk_names(obj) -> set[str]:
    names = {obj.path}
    if hasattr(obj, "members"):
        for child in obj.members.values():
            if isinstance(child, griffe.Alias):
                continue
            names |= _walk_names(child)
    return names


def test_plain_griffe_load_crashes_on_shadowed_reexport(tmp_path):
    # RED baseline: locks in the crash this whole module exists to work around.
    # If a future griffe upgrade stops crashing here, this test starts failing
    # loudly instead of our workaround silently becoming dead code.
    repo = _make_shadowed_repo(tmp_path)
    with pytest.raises(griffe.GriffeError):
        griffe.load("api", search_paths=[repo])


def test_shadow_tolerant_loader_loads_full_tree(tmp_path):
    repo = _make_shadowed_repo(tmp_path)
    obj, scrubbed, failures = load_tolerant("api", [repo])
    names = _walk_names(obj)
    assert "api.public.workload.views" in names
    assert "api.public.workload.crud" in names
    assert "api.public.labels.views" in names
    assert not failures


def test_shadow_tolerant_loader_resolves_call_across_shadowed_subpackage(tmp_path):
    repo = _make_shadowed_repo(tmp_path)
    obj, _, _ = load_tolerant("api", [repo])
    views = obj.members["public"].members["workload"].members["views"]
    assert "get_workload" in views.members
    crud = obj.members["public"].members["workload"].members["crud"]
    assert "helper" in crud.members


def test_shadow_tolerant_loader_records_scrubbed_aliases(tmp_path):
    repo = _make_shadowed_repo(tmp_path)
    _, scrubbed, _ = load_tolerant("api", [repo])
    scrubbed_names = {(parent, name) for parent, name, _target in scrubbed}
    assert ("api.public", "workload") in scrubbed_names
    assert ("api.public", "labels") in scrubbed_names


def test_shadow_tolerant_loader_no_scrub_on_normal_repo(tmp_path):
    # Regression guard: a repo with no shadowing must scrub nothing.
    repo = tmp_path / "repo"
    _write(repo, "services/__init__.py", "")
    _write(repo, "services/helpers.py", "def extra(text):\n    return text + '!'\n")
    obj, scrubbed, failures = load_tolerant("services", [repo])
    assert scrubbed == []
    assert failures == []
    assert "extra" in obj.members["helpers"].members


def test_shadow_tolerant_loader_isolates_genuinely_broken_module(tmp_path):
    # A nested submodule with a real syntax error must be reported as its own
    # module-level failure, not silently dropped (plain griffe already
    # tolerates this at the package level, but says nothing about which
    # module failed) and must not take down its sibling modules.
    repo = tmp_path / "repo"
    _write(repo, "pkg/__init__.py", "")
    _write(repo, "pkg/broken.py", "def f(:\n")
    _write(repo, "pkg/fine.py", "def g():\n    return 1\n")
    obj, scrubbed, failures = load_tolerant("pkg", [repo])
    assert scrubbed == []
    failure_names = {qualname for qualname, _location, _error in failures}
    assert "pkg.broken" in failure_names
    assert "g" in obj.members["fine"].members
