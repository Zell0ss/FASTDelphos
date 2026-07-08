import json

from cc.cli import main


def _write_minimal_graph(out_dir):
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


def test_annotate_reports_config_error_without_crashing(tmp_path, monkeypatch, capsys):
    _write_minimal_graph(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CC_LLM_PROVIDER", raising=False)
    monkeypatch.setattr("sys.argv", ["cc", "annotate", str(tmp_path)])

    main()

    captured = capsys.readouterr()
    assert "Config error" in captured.out
    assert "CC_LLM_PROVIDER" in captured.out


def test_annotate_reports_unimplemented_provider(tmp_path, monkeypatch, capsys):
    _write_minimal_graph(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CC_LLM_API_KEY", "k")
    monkeypatch.setattr("sys.argv", ["cc", "annotate", str(tmp_path)])

    main()

    captured = capsys.readouterr()
    assert "not implemented yet" in captured.out


def test_annotate_missing_graph_json_prints_clear_message(tmp_path, monkeypatch, capsys):
    # tmp_path deliberately has no graph.json — e.g. `cc annotate` pointed at
    # the wrong directory, or one where `cc compile` was never run.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("CC_LLM_API_KEY", "k")
    monkeypatch.setattr("sys.argv", ["cc", "annotate", str(tmp_path)])

    main()  # must not raise

    captured = capsys.readouterr()
    assert "graph.json" in captured.out
    assert "cc compile" in captured.out


def test_annotate_malformed_graph_json_prints_clear_message(tmp_path, monkeypatch, capsys):
    (tmp_path / "graph.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("CC_LLM_API_KEY", "k")
    monkeypatch.setattr("sys.argv", ["cc", "annotate", str(tmp_path)])

    main()  # must not raise

    captured = capsys.readouterr()
    assert "graph.json" in captured.out
    assert "no es JSON válido" in captured.out
