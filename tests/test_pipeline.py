import json
import pathlib
import tempfile

from cc.graph.hash_util import node_hash
from cc.pipeline import run
from tests.conftest import SIMPLE_API


def test_pipeline_produces_json():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        assert "nodes" in data
        assert len(data["nodes"]) > 0


def test_pipeline_produces_html():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        assert (pathlib.Path(d) / "index.html").exists()


def test_pipeline_finds_endpoint_node():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        ep_nodes = [n for n in data["nodes"] if n["type"] == "endpoint"]
        assert any("POST" in n["id"] and "messages" in n["id"] for n in ep_nodes)


def test_pipeline_finds_table_node():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        table_nodes = [n for n in data["nodes"] if n["type"] == "table"]
        assert any(n["id"] == "table:messages" for n in table_nodes)


def test_pipeline_all_edge_sources_exist():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        node_ids = {n["id"] for n in data["nodes"]}
        for e in data["edges"]:
            assert e["from_"] in node_ids, f"Edge source {e['from_']} has no node"
            assert e["to"] in node_ids, f"Edge target {e['to']} has no node"


def test_pipeline_call_edges_have_nodes_on_both_ends():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        node_ids = {n["id"] for n in data["nodes"]}
        calls_edges = [e for e in data["edges"] if e["type"] == "calls"]
        for e in calls_edges:
            assert e["from_"] in node_ids
            assert e["to"] in node_ids


def test_pipeline_emits_unresolved_dynamic_gap_for_fully_dynamic_sql():
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "backend").mkdir(parents=True)
        (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "backend" / "db.py").write_text(
            "async def run_query(cur, query_var, values):\n"
            "    await cur.execute(query_var, values)\n",
            encoding="utf-8",
        )
        out = pathlib.Path(d) / "out"
        run(repo, out)
        data = json.loads((out / "graph.json").read_text())
        dyn_gaps = [g for g in data["gaps"] if g["kind"] == "unresolved_dynamic"]
        assert len(dyn_gaps) == 1
        assert dyn_gaps[0]["severity"] == {"comprehension": "warning", "compliance": "error"}


def test_run_without_exclude_arg_matches_default_empty_tuple():
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        run(SIMPLE_API, pathlib.Path(d1))
        run(SIMPLE_API, pathlib.Path(d2), exclude_patterns=())
        a = (pathlib.Path(d1) / "graph.json").read_text()
        b = (pathlib.Path(d2) / "graph.json").read_text()
        assert a == b


def test_pipeline_graph_json_has_empty_exclusions_by_default():
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        assert data["exclusions"] == []


def test_pipeline_reports_exclusions_when_patterns_given(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "backend" / "tests").mkdir()
    (repo / "backend" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "tests" / "test_app.py").write_text(
        "def drop():\n    return 2\n", encoding="utf-8"
    )
    out = tmp_path / "out"
    run(repo, out, exclude_patterns=("backend/tests/**",))
    data = json.loads((out / "graph.json").read_text())
    assert data["exclusions"] == [{"pattern": "backend/tests/**", "count": 2}]


def test_excluded_run_keeps_surviving_node_ids_and_hashes_stable(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "backend" / "tests").mkdir()
    (repo / "backend" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "tests" / "test_app.py").write_text(
        "def drop():\n    return 2\n", encoding="utf-8"
    )

    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"
    run(repo, out_a)
    run(repo, out_b, exclude_patterns=("backend/tests/**",))

    nodes_a = {n["id"]: n for n in json.loads((out_a / "graph.json").read_text())["nodes"]}
    nodes_b = {n["id"]: n for n in json.loads((out_b / "graph.json").read_text())["nodes"]}

    assert "function:backend.tests.test_app.drop" in nodes_a
    assert "function:backend.tests.test_app.drop" not in nodes_b

    common_ids = set(nodes_a) & set(nodes_b)
    assert "function:backend.app.keep" in common_ids
    for node_id in common_ids:
        assert nodes_a[node_id]["hash"] == nodes_b[node_id]["hash"]


def test_pipeline_builds_inventory_once_and_shares_it(tmp_path, monkeypatch):
    # A DB-touching function that's ALSO called by another function — this is
    # exactly the scenario where sql.py and calls.py must agree on identity.
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "db.py").write_text(
        "async def get_active_roster(cur, channel_id):\n"
        "    await cur.execute('SELECT * FROM channels WHERE id = %s', (channel_id,))\n",
        encoding="utf-8",
    )
    (repo / "backend" / "service.py").write_text(
        "from .db import get_active_roster\n"
        "\n"
        "async def run_turn(cur, channel_id):\n"
        "    return await get_active_roster(cur, channel_id)\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    run(repo, out)
    data = json.loads((out / "graph.json").read_text())
    fn_node = next(n for n in data["nodes"] if n["id"] == "function:backend.db.get_active_roster")
    assert fn_node["line"] == 1  # the `async def` line, not the execute() call's line 2


def test_db_function_node_uses_def_line_not_call_site_line():
    # Original bug report: tests/fixtures/simple_api/db.py's create_message
    # is defined at line 1, but before this fix the compiled graph reported
    # line 2 (the `await conn.execute(...)` call site inside it).
    with tempfile.TemporaryDirectory() as d:
        run(SIMPLE_API, pathlib.Path(d))
        data = json.loads((pathlib.Path(d) / "graph.json").read_text())
        fn_node = next(n for n in data["nodes"] if n["id"] == "function:db.create_message")
        assert fn_node["line"] == 1
        expected_hash = node_hash(SIMPLE_API / "db.py", 1, 5)
        assert fn_node["hash"] == expected_hash


def test_pipeline_shares_ast_cache_across_all_three_extractors(tmp_path):
    # Not a behavior test — a wiring smoke test: the pipeline must not crash
    # when endpoints.py also needs inventory/ast_cache now.
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "api.py").write_text(
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter()\n"
        "\n"
        "\n"
        '@router.get("/x")\n'
        "def handler():\n"
        "    return {}\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    run(repo, out)
    data = json.loads((out / "graph.json").read_text())
    assert any(n["type"] == "endpoint" for n in data["nodes"])


def test_decorated_function_that_is_caller_callee_and_table_toucher(tmp_path):
    # The exact case that surfaced this whole plan: a decorated function that
    # is simultaneously (a) a caller, (b) a callee, and (c) a DB-toucher —
    # exercised by all four function-node emitters at once. Before this
    # plan, endpoints.py/calls.py's caller path (AST, decorator-excluded
    # line) and sql.py/calls.py's callee path (griffe, decorator-inclusive
    # line) disagreed on this function's identity, and graph/build.py's
    # identity assertion turned that disagreement into a hard crash.
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "db.py").write_text(
        "def audit(fn):\n"
        "    return fn\n"
        "\n"
        "\n"
        "@audit\n"
        "async def get_active_roster(cur, channel_id):\n"
        "    rows = await cur.execute(\n"
        "        'SELECT * FROM channels WHERE id = %s', (channel_id,)\n"
        "    )\n"
        "    return format_roster(rows)\n"
        "\n"
        "\n"
        "def format_roster(rows):\n"
        "    return list(rows)\n"
        "\n"
        "\n"
        "async def run_turn(cur, channel_id):\n"
        "    return await get_active_roster(cur, channel_id)\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    run(repo, out)  # must not raise — this is the crash this plan fixes

    data = json.loads((out / "graph.json").read_text())
    matches = [n for n in data["nodes"] if n["id"] == "function:backend.db.get_active_roster"]
    assert len(matches) == 1  # exactly one node — no silent duplicate/conflict either
    fn_node = matches[0]
    assert fn_node["line"] == 6  # the `async def` line, not the decorator (5)

    assert fn_node["hash"] == node_hash(
        repo / "backend" / "db.py", 5, 10
    )  # decorator (5) through end (10, the `return format_roster(rows)` line)

    edge_types = {(e["from_"], e["to"], e["type"]) for e in data["edges"]}
    assert (
        "function:backend.db.run_turn",
        "function:backend.db.get_active_roster",
        "calls",
    ) in edge_types
    assert (
        "function:backend.db.get_active_roster",
        "function:backend.db.format_roster",
        "calls",
    ) in edge_types
    assert (
        "function:backend.db.get_active_roster",
        "table:channels",
        "reads",
    ) in edge_types


def test_two_routers_same_path_different_namespace_compiles_with_ambiguity_gap():
    # Regression fixture for the real-world crash this plan fixes: two
    # routers registered from different namespaces both declare
    # `GET /ecosystems/`. Before the endpoint-id fix (Task 1) this crashed
    # the whole compile via graph/build.py's identity assertion — same id,
    # different file/line/hash. Synthetic fixture — never real Corporate code.
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "team_a").mkdir(parents=True)
        (repo / "team_a" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "team_a" / "routes.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n\n"
            '@router.get("/ecosystems/")\n'
            "def list_ecosystems():\n    return []\n",
            encoding="utf-8",
        )
        (repo / "team_b").mkdir(parents=True)
        (repo / "team_b" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "team_b" / "routes.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n\n"
            '@router.get("/ecosystems/")\n'
            "def list_ecosystems():\n    return []\n",
            encoding="utf-8",
        )
        out = pathlib.Path(d) / "out"
        run(repo, out)  # must not raise — this is the crash this plan fixes

        data = json.loads((out / "graph.json").read_text())
        ep_nodes = [n for n in data["nodes"] if n["type"] == "endpoint"]
        assert len(ep_nodes) == 2
        assert len({n["id"] for n in ep_nodes}) == 2  # distinct ids despite identical method+path

        ambiguous = [
            g
            for g in data["gaps"]
            if g["kind"] == "unresolved_dynamic" and "ruta ambigua" in g["missing"]
        ]
        assert len(ambiguous) == 1
        assert ambiguous[0]["severity"] == {"comprehension": "warning", "compliance": "error"}


def test_run_excludes_gitignored_file_by_default(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("backend/generated.py\n", encoding="utf-8")
    (repo / "backend" / "generated.py").write_text("def drop():\n    return 2\n", encoding="utf-8")
    out = tmp_path / "out"
    run(repo, out)
    data = json.loads((out / "graph.json").read_text())
    fn_ids = {n["id"] for n in data["nodes"] if n["type"] == "function"}
    assert "function:backend.app.keep" in fn_ids
    assert "function:backend.generated.drop" not in fn_ids


def test_run_no_gitignore_flag_reproduces_prior_behavior_byte_for_byte(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "backend" / "generated.py").write_text("def drop():\n    return 2\n", encoding="utf-8")

    out_before = tmp_path / "out_before"
    run(repo, out_before)  # no .gitignore exists yet — today's behavior

    (repo / ".gitignore").write_text("backend/generated.py\n", encoding="utf-8")
    out_after = tmp_path / "out_after"
    run(repo, out_after, use_gitignore=False)  # .gitignore now exists, but disabled

    before = (out_before / "graph.json").read_text()
    after = (out_after / "graph.json").read_text()
    assert before == after


def test_gitignore_excluded_run_keeps_surviving_node_ids_and_hashes_stable(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "backend" / "generated.py").write_text("def drop():\n    return 2\n", encoding="utf-8")

    out_a = tmp_path / "out_a"
    run(repo, out_a, use_gitignore=False)

    (repo / ".gitignore").write_text("backend/generated.py\n", encoding="utf-8")
    out_b = tmp_path / "out_b"
    run(repo, out_b)

    nodes_a = {n["id"]: n for n in json.loads((out_a / "graph.json").read_text())["nodes"]}
    nodes_b = {n["id"]: n for n in json.loads((out_b / "graph.json").read_text())["nodes"]}

    assert "function:backend.generated.drop" in nodes_a
    assert "function:backend.generated.drop" not in nodes_b

    common_ids = set(nodes_a) & set(nodes_b)
    assert "function:backend.app.keep" in common_ids
    for node_id in common_ids:
        assert nodes_a[node_id]["hash"] == nodes_b[node_id]["hash"]


def test_run_reports_gitignore_exclusion_in_graph_json(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("backend/generated.py\n", encoding="utf-8")
    (repo / "backend" / "generated.py").write_text("def drop():\n    return 2\n", encoding="utf-8")
    out = tmp_path / "out"
    run(repo, out)
    data = json.loads((out / "graph.json").read_text())
    assert {"pattern": "(.gitignore)", "count": 1} in data["exclusions"]


def test_namespace_package_calls_resolve_as_internal():
    # End-to-end regression for the illumiows classifier bug: a namespace
    # package (no __init__.py) at repo root must produce a real `calls`
    # edge between its own functions, not get miscategorized as external.
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "api" / "routes").mkdir(parents=True)
        (repo / "api" / "routes" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "api" / "routes" / "views.py").write_text(
            "from api.routes import crud\n\n\n"
            "def delete_iplist_allregions(list_id):\n"
            "    return crud.delete_iplist(list_id)\n",
            encoding="utf-8",
        )
        (repo / "api" / "routes" / "crud.py").write_text(
            "def delete_iplist(list_id):\n    return list_id\n", encoding="utf-8"
        )
        (repo / "asgi.py").write_text("from api.routes import views\n", encoding="utf-8")
        out = pathlib.Path(d) / "out"

        run(repo, out)

        data = json.loads((out / "graph.json").read_text())
        call_edges = {(e["from_"], e["to"]) for e in data["edges"] if e["type"] == "calls"}
        assert (
            "function:api.routes.views.delete_iplist_allregions",
            "function:api.routes.crud.delete_iplist",
        ) in call_edges


def test_run_is_deterministic_with_gitignore_active(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("backend/generated.py\n", encoding="utf-8")
    (repo / "backend" / "generated.py").write_text("def drop():\n    return 2\n", encoding="utf-8")

    out_1 = tmp_path / "out_1"
    out_2 = tmp_path / "out_2"
    run(repo, out_1)
    run(repo, out_2)
    assert (out_1 / "graph.json").read_text() == (out_2 / "graph.json").read_text()


def test_zero_internal_calls_prints_sanity_warning(capsys):
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "lonely").mkdir(parents=True)
        (repo / "lonely" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "lonely" / "mod.py").write_text(
            "import os\n\n\ndef f():\n    return os.getcwd()\n", encoding="utf-8"
        )
        out = pathlib.Path(d) / "out"

        run(repo, out)

        captured = capsys.readouterr()
        assert "0 llamadas internas resueltas" in captured.out
        assert "lonely" in captured.out


def test_nonzero_internal_calls_does_not_print_sanity_warning(capsys):
    # NOTE: deliberately not using the SIMPLE_API fixture here — its
    # handlers never actually call db.py's functions, so it genuinely has
    # 0 resolved_internal today (verified independently of this plan's
    # fix) and would make this test assert the wrong thing. This fixture
    # has a real cross-file call (main.compute -> helpers.double).
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "app").mkdir(parents=True)
        (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "app" / "helpers.py").write_text(
            "def double(x):\n    return x * 2\n", encoding="utf-8"
        )
        (repo / "app" / "main.py").write_text(
            "from app.helpers import double\n\n\n"
            "def compute(x):\n    return double(x)\n",
            encoding="utf-8",
        )
        out = pathlib.Path(d) / "out"

        run(repo, out)

        captured = capsys.readouterr()
        assert "llamadas internas resueltas" not in captured.out


def test_package_load_failure_surfaces_as_tool_limitation_gap():
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "broken").mkdir(parents=True)
        # SyntaxError in __init__.py itself, not a nested submodule — see
        # the note on test_top_level_packages_recorded_even_if_load_fails
        # (Task 1) for why that distinction is what actually makes
        # griffe.load raise instead of silently tolerating it.
        (repo / "broken" / "__init__.py").write_text("def f(:\n", encoding="utf-8")
        out = pathlib.Path(d) / "out"

        run(repo, out)

        data = json.loads((out / "graph.json").read_text())
        load_gaps = [
            g
            for g in data["gaps"]
            if g["kind"] == "tool_limitation" and "griffe" in g["missing"]
        ]
        assert len(load_gaps) == 1
        assert "broken" in load_gaps[0]["missing"]
        assert load_gaps[0]["severity"] == {"comprehension": "warning", "compliance": "error"}


def test_shadowed_reexport_resolves_end_to_end_and_reports_scrub_count(capsys):
    # Mirrors illumiows: without the shadow-tolerant loader this whole
    # package would fail to load and the call below would be
    # unresolved_dynamic, not a real edge. The scrub must also never be
    # silent — it's reported as an aggregate count in the coverage report.
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "api" / "public" / "workload").mkdir(parents=True)
        (repo / "api" / "public" / "labels").mkdir(parents=True)
        (repo / "api" / "public" / "__init__.py").write_text(
            "from api.public.workload import views as workload\n"
            "from api.public.labels import views as labels\n",
            encoding="utf-8",
        )
        (repo / "api" / "public" / "workload" / "views.py").write_text(
            "from api.public.workload.crud import helper\n\n\n"
            "def get_workload():\n"
            "    return helper()\n",
            encoding="utf-8",
        )
        (repo / "api" / "public" / "workload" / "crud.py").write_text(
            "def helper():\n    return 42\n", encoding="utf-8"
        )
        (repo / "api" / "public" / "labels" / "views.py").write_text(
            "def get_labels():\n    return []\n", encoding="utf-8"
        )
        out = pathlib.Path(d) / "out"

        run(repo, out)

        data = json.loads((out / "graph.json").read_text())
        call_edges = {(e["from_"], e["to"]) for e in data["edges"] if e["type"] == "calls"}
        assert (
            "function:api.public.workload.views.get_workload",
            "function:api.public.workload.crud.helper",
        ) in call_edges

        captured = capsys.readouterr()
        assert "2 re-exports shadow neutralizados" in captured.out


def test_module_load_failure_surfaces_as_per_module_tool_limitation_gap():
    # A SyntaxError in a nested submodule (not the package's own __init__.py)
    # must be reported as its own module-level gap distinct from the
    # package-wide one — and must not block the rest of the package's
    # functions from being extracted.
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo"
        (repo / "pkg").mkdir(parents=True)
        (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "pkg" / "broken.py").write_text("def f(:\n", encoding="utf-8")
        (repo / "pkg" / "fine.py").write_text("def g():\n    return 1\n", encoding="utf-8")
        out = pathlib.Path(d) / "out"

        run(repo, out)

        data = json.loads((out / "graph.json").read_text())
        module_gaps = [
            g
            for g in data["gaps"]
            if g["kind"] == "tool_limitation" and "pkg.broken" in g["missing"]
        ]
        assert len(module_gaps) == 1
        assert module_gaps[0]["severity"] == {"comprehension": "warning", "compliance": "error"}
        assert any(n["id"] == "function:pkg.fine.g" for n in data["nodes"])
