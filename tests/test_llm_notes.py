import json

from cc.llm.notes import load_notes, needs_regeneration, save_notes


def test_load_notes_missing_file_returns_empty_dict(tmp_path):
    assert load_notes(tmp_path / "notes.json") == {}


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "notes.json"
    notes = {"function:a": {"text": "x", "hash": "h1", "prompt_version": 1, "model": "m", "generated_at": "t"}}
    save_notes(path, notes)
    assert load_notes(path) == notes
    assert json.loads(path.read_text(encoding="utf-8")) == notes


def test_needs_regeneration_when_no_existing_entry():
    assert needs_regeneration(None, "h1", 1, force=False) is True


def test_needs_regeneration_when_hash_differs():
    existing = {"hash": "old", "prompt_version": 1}
    assert needs_regeneration(existing, "new", 1, force=False) is True


def test_needs_regeneration_when_prompt_version_differs():
    existing = {"hash": "h1", "prompt_version": 1}
    assert needs_regeneration(existing, "h1", 2, force=False) is True


def test_needs_regeneration_false_when_everything_matches():
    existing = {"hash": "h1", "prompt_version": 1}
    assert needs_regeneration(existing, "h1", 1, force=False) is False


def test_needs_regeneration_true_when_forced_even_if_matching():
    existing = {"hash": "h1", "prompt_version": 1}
    assert needs_regeneration(existing, "h1", 1, force=True) is True
