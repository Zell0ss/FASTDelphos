from cc.llm.prompt import PROMPT_VERSION, build_system_prompt, build_user_prompt


def test_prompt_version_starts_at_one():
    assert PROMPT_VERSION == 1


def test_system_prompt_forbids_paraphrasing_and_line_by_line_description():
    system = build_system_prompt(None)
    assert "PROHIBIDO" in system
    assert "línea a línea" in system


def test_system_prompt_appends_extra_instructions_when_present():
    system = build_system_prompt("Sé aún más breve.")
    assert system.endswith("Sé aún más breve.")


def test_system_prompt_without_extra_instructions_has_no_trailing_junk():
    system = build_system_prompt(None)
    assert system.strip() == system


def test_user_prompt_includes_source_span_and_neighborhood_verbatim():
    user = build_user_prompt("def f():\n    pass", "Quién lo llama: nadie")
    assert "def f():\n    pass" in user
    assert "Quién lo llama: nadie" in user
