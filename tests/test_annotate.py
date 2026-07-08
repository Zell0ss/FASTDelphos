import json

from cc.annotate import run_annotate
from cc.llm.client import LLMGenerationError
from cc.llm.prompt import PROMPT_VERSION


class FakeLLMClient:
    def __init__(self, responses=None, fail_on=()):
        self.responses = responses or {}
        self.fail_on = set(fail_on)
        self.calls = []

    def generate(self, system, user):
        self.calls.append((system, user))
        return "nota generada"


class FailingLLMClient:
    def __init__(self, fail_ids):
        self.fail_ids = set(fail_ids)
        self.call_count = 0

    def generate(self, system, user):
        self.call_count += 1
        raise LLMGenerationError("Anthropic generation failed: SimulatedError")


def _write_graph(out_dir):
    graph = {
        "nodes": [
            {
                "id": "endpoint:GET:/x",
                "type": "endpoint",
                "file": str(out_dir / "src.py"),
                "line": 1,
                "hash": "hash-a",
                "inferred": False,
                "props": {"method": "GET", "path": "/x", "handler": "mod.handler"},
            }
        ],
        "edges": [],
        "gaps": [],
        "exclusions": [],
    }
    (out_dir / "src.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    (out_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    return graph


def test_first_run_generates_and_second_run_is_fully_cached(tmp_path):
    _write_graph(tmp_path)
    client = FakeLLMClient()

    report1 = run_annotate(tmp_path, client, model_name="m")
    assert report1 == {"generated": 1, "cached": 0, "failed": 0, "failed_ids": []}
    assert len(client.calls) == 1

    report2 = run_annotate(tmp_path, client, model_name="m")
    assert report2 == {"generated": 0, "cached": 1, "failed": 0, "failed_ids": []}
    assert len(client.calls) == 1  # no new call


def test_hash_drift_regenerates_only_that_node(tmp_path):
    graph = _write_graph(tmp_path)
    client = FakeLLMClient()
    run_annotate(tmp_path, client, model_name="m")

    graph["nodes"][0]["hash"] = "hash-b"
    (tmp_path / "graph.json").write_text(json.dumps(graph), encoding="utf-8")

    report = run_annotate(tmp_path, client, model_name="m")
    assert report == {"generated": 1, "cached": 0, "failed": 0, "failed_ids": []}
    assert len(client.calls) == 2

    notes = json.loads((tmp_path / "notes.json").read_text(encoding="utf-8"))
    assert notes["endpoint:GET:/x"]["hash"] == "hash-b"


def test_prompt_version_bump_forces_full_regeneration(tmp_path):
    _write_graph(tmp_path)
    notes = {
        "endpoint:GET:/x": {
            "text": "vieja",
            "hash": "hash-a",
            "prompt_version": PROMPT_VERSION - 1,
            "model": "m",
            "generated_at": "t",
        }
    }
    (tmp_path / "notes.json").write_text(json.dumps(notes), encoding="utf-8")
    client = FakeLLMClient()

    report = run_annotate(tmp_path, client, model_name="m")
    assert report == {"generated": 1, "cached": 0, "failed": 0, "failed_ids": []}


def test_force_regenerates_even_when_everything_matches(tmp_path):
    _write_graph(tmp_path)
    client = FakeLLMClient()
    run_annotate(tmp_path, client, model_name="m")

    report = run_annotate(tmp_path, client, model_name="m", force=True)
    assert report == {"generated": 1, "cached": 0, "failed": 0, "failed_ids": []}
    assert len(client.calls) == 2


def test_node_id_targets_only_that_node(tmp_path):
    _write_graph(tmp_path)
    client = FakeLLMClient()
    report = run_annotate(tmp_path, client, model_name="m", node_id="endpoint:GET:/x")
    assert report == {"generated": 1, "cached": 0, "failed": 0, "failed_ids": []}


def test_failing_node_is_reported_and_does_not_raise(tmp_path):
    _write_graph(tmp_path)
    client = FailingLLMClient(fail_ids={"endpoint:GET:/x"})
    report = run_annotate(tmp_path, client, model_name="m")
    assert report == {"generated": 0, "cached": 0, "failed": 1, "failed_ids": ["endpoint:GET:/x"]}
    assert (tmp_path / "notes.json").exists() is False or json.loads(
        (tmp_path / "notes.json").read_text(encoding="utf-8")
    ) == {}


def test_notes_json_records_model_and_prompt_version(tmp_path):
    _write_graph(tmp_path)
    client = FakeLLMClient()
    run_annotate(tmp_path, client, model_name="claude-haiku-4-5")
    notes = json.loads((tmp_path / "notes.json").read_text(encoding="utf-8"))
    entry = notes["endpoint:GET:/x"]
    assert entry["model"] == "claude-haiku-4-5"
    assert entry["prompt_version"] == PROMPT_VERSION
    assert entry["text"] == "nota generada"
