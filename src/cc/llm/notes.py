import json
import pathlib


def load_notes(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_notes(path: pathlib.Path, notes: dict) -> None:
    path.write_text(json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8")


def needs_regeneration(
    existing: dict | None, current_hash: str, prompt_version: int, force: bool
) -> bool:
    """Spec §4: regenerate iff forced, missing, hash drift, or a prompt_version bump."""
    if force:
        return True
    if existing is None:
        return True
    if existing.get("hash") != current_hash:
        return True
    if existing.get("prompt_version") != prompt_version:
        return True
    return False
