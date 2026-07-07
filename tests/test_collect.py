import pathlib

from cc.extract._collect import collect_py_files, excluded_files, exclusion_report


def _write(root: pathlib.Path, rel: str, content: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_collect_py_files_no_patterns_matches_current_behavior(tmp_path):
    _write(tmp_path, "app.py", "x = 1\n")
    _write(tmp_path, ".venv/lib/pkg.py", "x = 1\n")
    files = collect_py_files(tmp_path)
    assert files == [tmp_path / "app.py"]


def test_collect_py_files_excludes_matching_pattern(tmp_path):
    _write(tmp_path, "backend/app.py", "x = 1\n")
    _write(tmp_path, "backend/tests/test_app.py", "x = 1\n")
    files = collect_py_files(tmp_path, exclude_patterns=("backend/tests/**",))
    assert files == [tmp_path / "backend" / "app.py"]


def test_collect_py_files_pattern_matches_nested_files(tmp_path):
    _write(tmp_path, "backend/tests/unit/test_deep.py", "x = 1\n")
    files = collect_py_files(tmp_path, exclude_patterns=("backend/tests/**",))
    assert files == []


def test_collect_py_files_unmatched_pattern_excludes_nothing(tmp_path):
    _write(tmp_path, "backend/app.py", "x = 1\n")
    files = collect_py_files(tmp_path, exclude_patterns=("scripts/**",))
    assert files == [tmp_path / "backend" / "app.py"]


def test_excluded_files_returns_only_py_files(tmp_path):
    _write(tmp_path, "backend/tests/test_app.py", "x = 1\n")
    _write(tmp_path, "backend/tests/data.json", "{}")
    excluded = excluded_files(tmp_path, ("backend/tests/**",))
    assert excluded == {tmp_path / "backend" / "tests" / "test_app.py"}


def test_excluded_files_empty_when_no_patterns(tmp_path):
    _write(tmp_path, "backend/tests/test_app.py", "x = 1\n")
    assert excluded_files(tmp_path, ()) == set()


def test_exclusion_report_counts_per_pattern(tmp_path):
    _write(tmp_path, "backend/tests/a.py", "")
    _write(tmp_path, "backend/tests/b.py", "")
    _write(tmp_path, "scripts/one.py", "")
    report = exclusion_report(tmp_path, ("backend/tests/**", "scripts/**"))
    assert report == [
        {"pattern": "backend/tests/**", "count": 2},
        {"pattern": "scripts/**", "count": 1},
    ]


def test_exclusion_report_empty_when_no_patterns(tmp_path):
    assert exclusion_report(tmp_path, ()) == []


def test_exclusion_report_zero_count_for_unmatched_pattern(tmp_path):
    report = exclusion_report(tmp_path, ("nothing/here/**",))
    assert report == [{"pattern": "nothing/here/**", "count": 0}]
