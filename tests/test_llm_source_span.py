import pytest

from cc.llm.source_span import get_source_span


def test_extracts_a_simple_function_body(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "x = 1\n"
        "\n"
        "def greet(name):\n"
        "    return f'hello {name}'\n"
        "\n"
        "y = 2\n",
        encoding="utf-8",
    )
    span = get_source_span(str(f), 3)
    assert span == "def greet(name):\n    return f'hello {name}'"


def test_extracts_an_async_function_body(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "async def fetch(x):\n"
        "    return await x()\n",
        encoding="utf-8",
    )
    span = get_source_span(str(f), 1)
    assert span == "async def fetch(x):\n    return await x()"


def test_extracts_a_method_by_its_own_lineno(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "class Service:\n"
        "    def run(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    span = get_source_span(str(f), 2)
    assert span == "    def run(self):\n        return 1"


def test_no_definition_at_line_raises_value_error(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("x = 1\ny = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mod.py:1"):
        get_source_span(str(f), 1)
