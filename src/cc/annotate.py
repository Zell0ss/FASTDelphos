import datetime
import json
import pathlib

from cc.llm.client import LLMClient, LLMGenerationError
from cc.llm.neighborhood import serialize_neighborhood
from cc.llm.notes import load_notes, needs_regeneration, save_notes
from cc.llm.prompt import PROMPT_VERSION, build_system_prompt, build_user_prompt
from cc.llm.scope import select_annotation_targets
from cc.llm.source_span import get_source_span


def run_annotate(
    out_dir: pathlib.Path,
    client: LLMClient,
    model_name: str,
    extra_instructions: str | None = None,
    node_id: str | None = None,
    all_nodes: bool = False,
    force: bool = False,
    threshold: int = 2,
) -> dict:
    out_dir = pathlib.Path(out_dir)
    graph = json.loads((out_dir / "graph.json").read_text(encoding="utf-8"))
    notes_path = out_dir / "notes.json"
    notes = load_notes(notes_path)
    by_id = {n["id"]: n for n in graph["nodes"]}

    if node_id is not None:
        target_ids = [node_id]
    elif all_nodes:
        target_ids = [n["id"] for n in graph["nodes"]]
    else:
        target_ids = select_annotation_targets(graph, threshold)

    report = {"generated": 0, "cached": 0, "failed": 0, "failed_ids": []}

    for tid in target_ids:
        node = by_id.get(tid)
        if node is None:
            report["failed"] += 1
            report["failed_ids"].append(tid)
            continue

        current_hash = node["hash"]
        existing = notes.get(tid)
        if not needs_regeneration(existing, current_hash, PROMPT_VERSION, force):
            report["cached"] += 1
            continue

        try:
            source_span = get_source_span(node["file"], node["line"])
            neighborhood_text = serialize_neighborhood(graph, tid)
            system = build_system_prompt(extra_instructions)
            user = build_user_prompt(source_span, neighborhood_text)
            text = client.generate(system, user)
        except (LLMGenerationError, ValueError, OSError, SyntaxError):
            report["failed"] += 1
            report["failed_ids"].append(tid)
            continue

        notes[tid] = {
            "text": text,
            "hash": current_hash,
            "prompt_version": PROMPT_VERSION,
            "model": model_name,
            "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        save_notes(notes_path, notes)
        report["generated"] += 1

    return report
