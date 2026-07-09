import pathlib

from cc.extract._collect import (
    collect_py_files,
    excluded_files,
    exclusion_report,
    gitignore_excluded_files,
)


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


def test_gitignore_excluded_files_root_level_pattern(tmp_path):
    _write(tmp_path, ".gitignore", "artifacts/\n")
    _write(tmp_path, "artifacts/generated.py", "x = 1\n")
    _write(tmp_path, "app.py", "x = 1\n")
    excluded = gitignore_excluded_files(tmp_path)
    assert excluded == {tmp_path / "artifacts" / "generated.py"}


def test_gitignore_excluded_files_nested_pattern_is_scoped_to_its_own_subtree(tmp_path):
    _write(tmp_path, "backend/.gitignore", "generated.py\n")
    _write(tmp_path, "backend/generated.py", "x = 1\n")
    _write(tmp_path, "frontend/generated.py", "x = 1\n")  # same basename, different subtree
    excluded = gitignore_excluded_files(tmp_path)
    assert excluded == {tmp_path / "backend" / "generated.py"}


def test_gitignore_excluded_files_matches_at_any_depth_within_its_own_directory(tmp_path):
    _write(tmp_path, "backend/.gitignore", "generated.py\n")
    _write(tmp_path, "backend/sub/deep/generated.py", "x = 1\n")
    excluded = gitignore_excluded_files(tmp_path)
    assert excluded == {tmp_path / "backend" / "sub" / "deep" / "generated.py"}


def test_gitignore_excluded_files_supports_negation(tmp_path):
    _write(tmp_path, ".gitignore", "generated/*.py\n!generated/keep.py\n")
    _write(tmp_path, "generated/drop.py", "x = 1\n")
    _write(tmp_path, "generated/keep.py", "x = 1\n")
    excluded = gitignore_excluded_files(tmp_path)
    assert excluded == {tmp_path / "generated" / "drop.py"}


def test_gitignore_excluded_files_disabled_returns_empty(tmp_path):
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "build/generated.py", "x = 1\n")
    assert gitignore_excluded_files(tmp_path, use_gitignore=False) == set()


def test_gitignore_excluded_files_no_gitignore_present_returns_empty(tmp_path):
    _write(tmp_path, "app.py", "x = 1\n")
    assert gitignore_excluded_files(tmp_path) == set()


def test_gitignore_excluded_files_never_reads_gitignore_inside_dot_git(tmp_path):
    _write(tmp_path, ".git/.gitignore", "*.py\n")  # decoy — must never be treated as a source
    _write(tmp_path, "app.py", "x = 1\n")
    assert gitignore_excluded_files(tmp_path) == set()


def test_gitignore_excluded_files_is_deterministic_across_calls(tmp_path):
    _write(tmp_path, ".gitignore", "artifacts/\n")
    _write(tmp_path, "backend/.gitignore", "generated.py\n")
    _write(tmp_path, "artifacts/generated.py", "x = 1\n")
    _write(tmp_path, "backend/generated.py", "x = 1\n")
    first = gitignore_excluded_files(tmp_path)
    second = gitignore_excluded_files(tmp_path)
    assert first == second


def test_collect_py_files_excludes_gitignored_files_by_default(tmp_path):
    _write(tmp_path, "app.py", "x = 1\n")
    _write(tmp_path, ".gitignore", "generated.py\n")
    _write(tmp_path, "generated.py", "x = 1\n")
    files = collect_py_files(tmp_path)
    assert files == [tmp_path / "app.py"]


def test_collect_py_files_no_gitignore_flag_disables_gitignore_filtering(tmp_path):
    _write(tmp_path, "app.py", "x = 1\n")
    _write(tmp_path, ".gitignore", "generated.py\n")
    _write(tmp_path, "generated.py", "x = 1\n")
    files = collect_py_files(tmp_path, use_gitignore=False)
    assert files == [tmp_path / "app.py", tmp_path / "generated.py"]


def test_exclusion_report_adds_gitignore_origin_entry(tmp_path):
    _write(tmp_path, ".gitignore", "generated.py\n")
    _write(tmp_path, "generated.py", "x = 1\n")
    _write(tmp_path, "backend/tests/a.py", "")
    report = exclusion_report(tmp_path, ("backend/tests/**",))
    assert {"pattern": "backend/tests/**", "count": 1} in report
    assert {"pattern": "(.gitignore)", "count": 1} in report


def test_exclusion_report_no_gitignore_entry_when_repo_has_no_gitignore(tmp_path):
    _write(tmp_path, "backend/tests/a.py", "")
    report = exclusion_report(tmp_path, ("backend/tests/**",))
    assert report == [{"pattern": "backend/tests/**", "count": 1}]


def test_excluded_files_unions_exclude_patterns_and_gitignore(tmp_path):
    _write(tmp_path, ".gitignore", "generated.py\n")
    _write(tmp_path, "generated.py", "x = 1\n")
    _write(tmp_path, "backend/tests/a.py", "")
    excluded = excluded_files(tmp_path, ("backend/tests/**",))
    assert excluded == {tmp_path / "generated.py", tmp_path / "backend" / "tests" / "a.py"}
