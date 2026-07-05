from cc.extract.calls import extract_calls
from tests.conftest import CALLS_REPO


def test_returns_four_tuple():
    nodes, edges, excluded, coverage = extract_calls(CALLS_REPO)
    assert isinstance(nodes, list)
    assert isinstance(edges, list)
    assert isinstance(excluded, list)
    assert isinstance(coverage, dict)


def test_calls_edges_have_correct_type():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    for e in edges:
        assert e.type == "calls"
        assert e.inferred is False
        assert e.from_.startswith("function:")
        assert e.to.startswith("function:")


def test_no_self_loops():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    for e in edges:
        assert e.from_ != e.to


def test_function_nodes_are_hydrated_not_placeholders():
    nodes, _, _, _ = extract_calls(CALLS_REPO)
    by_id = {n.id: n for n in nodes}
    callee = by_id["function:services.helpers.extra"]
    assert callee.line == 1
    assert callee.hash != "0" * 64
    assert callee.props["qualname"] == "services.helpers.extra"
    assert callee.props["is_handler"] is False


def test_case1_direct_name_same_module():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert (
        "function:services.synthesis.build_context",
        "function:services.synthesis._compress",
    ) in pairs


def test_case1_imported_name_called_directly():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert ("function:main.handler", "function:services.synthesis.build_context") in pairs


def test_case2_attribute_on_aliased_dotted_import():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert ("function:services.synthesis.build_context", "function:services.helpers.extra") in pairs


def test_case2_from_import_as_module_plus_attribute():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert (
        "function:main.handler_via_module",
        "function:services.synthesis.build_context",
    ) in pairs


def test_case2_plain_dotted_import_three_levels():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert ("function:other.call_dotted", "function:services.synthesis.build_context") in pairs


def test_case3_inherited_method_across_modules():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert (
        "function:services.child.LoudGreeter.shout",
        "function:services.base.Greeter.greet",
    ) in pairs


def test_async_await_unwrapped_without_special_casing():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    pairs = {(e.from_, e.to) for e in edges}
    assert (
        "function:services.synthesis.build_context_async",
        "function:services.synthesis.build_context",
    ) in pairs


def test_dynamic_dispatch_produces_no_edge():
    _, edges, _, _ = extract_calls(CALLS_REPO)
    froms = {e.from_ for e in edges}
    assert "function:services.synthesis.dynamic_dispatch" not in froms


def test_coverage_totals_match_fixture():
    _, _, _, coverage = extract_calls(CALLS_REPO)
    total = coverage["total"]
    assert total["functions"] == 10
    assert total["call_sites"] == 10
    assert total["resolved_internal"] == 7
    assert total["resolved_external"] == 1
    assert total["unresolved_dynamic"] == 2


def test_coverage_per_file_has_synthesis_entry():
    _, _, _, coverage = extract_calls(CALLS_REPO)
    synth = coverage["per_file"]["services/synthesis.py"]
    assert synth["resolved_external"] == 1  # logging.info


def test_excluded_is_list_of_tuples():
    _, _, excluded, _ = extract_calls(CALLS_REPO)
    for filepath, error in excluded:
        assert isinstance(filepath, str)
        assert isinstance(error, str)


def test_syntax_error_file_is_excluded_not_silently_dropped(tmp_path):
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    nodes, edges, excluded, coverage = extract_calls(tmp_path)
    assert len(excluded) == 1
    assert str(tmp_path / "broken.py") == excluded[0][0]


def test_root_level_module_call_is_internal_not_external(tmp_path):
    (tmp_path / "loose.py").write_text("def stray(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "user.py").write_text(
        "from loose import stray\n\n\ndef use_it(x):\n    return stray(x)\n",
        encoding="utf-8",
    )
    _, edges, _, coverage = extract_calls(tmp_path)
    pairs = {(e.from_, e.to) for e in edges}
    assert ("function:user.use_it", "function:loose.stray") in pairs
    assert coverage["total"]["resolved_external"] == 0


def test_unknown_filepath_callee_does_not_crash(tmp_path, monkeypatch):
    (tmp_path / "user.py").write_text("def use_it(x):\n    return helper(x)\n", encoding="utf-8")
    import cc.extract.calls as calls_mod
    from cc.extract._calls_resolver import FuncInfo, SymbolInventory

    fake_inventory = SymbolInventory(
        functions={"user.helper": FuncInfo("user.helper", "unknown", 1, 1, "function")},
        top_level_packages={"user"},
    )
    monkeypatch.setattr(calls_mod, "build_symbol_inventory", lambda repo_path: fake_inventory)
    # `user.py` at the repo root has module_qname "user", so case 1's module-local
    # check (`candidate = f"{module_qname}.{name}"`) resolves `helper(x)` to
    # "user.helper" directly — matching the fake inventory's (unhydratable) entry.
    nodes, edges, excluded, coverage = extract_calls(tmp_path)
    assert edges == []  # skipped, not crashed
    assert coverage["total"]["call_sites"] >= 1


def test_case_2b_function_scope_alias_resolves_as_external(tmp_path):
    (tmp_path / "mod.py").write_text(
        "import re\n\n\n"
        "def f(text):\n"
        "    match = re.search('x', text)\n"
        "    return match.group(1)\n",
        encoding="utf-8",
    )
    _, edges, _, coverage = extract_calls(tmp_path)
    froms = {e.from_ for e in edges}
    assert "function:mod.f" not in froms  # no internal edge — both calls are external
    per_file = coverage["per_file"]["mod.py"]
    assert per_file["resolved_external"] == 2  # re.search(...) and match.group(...)
    assert per_file["unresolved_dynamic"] == 0


def test_case_2b_module_scope_alias_resolves_as_external(tmp_path):
    (tmp_path / "mod.py").write_text(
        "import anthropic\n\n"
        "client = anthropic.AsyncAnthropic()\n\n\n"
        "def call_a(prompt):\n"
        "    return client.messages.stream(prompt)\n\n\n"
        "def call_b(prompt):\n"
        "    return client.messages.create(prompt)\n",
        encoding="utf-8",
    )
    _, edges, _, coverage = extract_calls(tmp_path)
    per_file = coverage["per_file"]["mod.py"]
    # anthropic.AsyncAnthropic() itself (module-level, not inside any function,
    # so not counted in any function's call_sites) + 2 function bodies each with
    # one external call through the module-level alias.
    assert per_file["resolved_external"] == 2
    assert per_file["unresolved_dynamic"] == 0


def test_case_2b_local_reassignment_shadows_module_alias(tmp_path):
    (tmp_path / "mod.py").write_text(
        "import anthropic\n\n"
        "client = anthropic.AsyncAnthropic()\n\n\n"
        "def uses_module_client(prompt):\n"
        "    return client.messages.stream(prompt)\n\n\n"
        "def uses_local_client(prompt, local_client):\n"
        "    client = local_client\n"
        "    return client.messages.stream(prompt)\n",
        encoding="utf-8",
    )
    _, edges, _, coverage = extract_calls(tmp_path)
    per_file = coverage["per_file"]["mod.py"]
    # uses_module_client's call resolves external via the module alias.
    # uses_local_client's call must NOT inherit the module alias — its own
    # `client = local_client` (non-qualifying: local_client isn't an import)
    # shadows it, so that call falls to dynamic instead of being wrongly
    # classified external.
    assert per_file["resolved_external"] == 1
    assert per_file["unresolved_dynamic"] == 1
