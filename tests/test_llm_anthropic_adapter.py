import pytest

from cc.llm.anthropic_adapter import AnthropicClient
from cc.llm.client import LLMGenerationError
from cc.llm.config import LLMConfig


def _config(**overrides) -> LLMConfig:
    defaults = dict(
        provider="anthropic",
        base_url=None,
        api_key="sk-test-key",
        model="claude-haiku-4-5",
        max_tokens=500,
        extra_instructions=None,
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


class _FakeResponseContentBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeResponseContentBlock(text)]


class _FakeMessages:
    """Records the last call's kwargs; returns a canned response or raises."""

    def __init__(self, response_text: str = "generated note", raise_exc: Exception | None = None):
        self._response_text = response_text
        self._raise_exc = raise_exc
        self.last_call_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_call_kwargs = kwargs
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeResponse(self._response_text)


class _FakeAnthropicClient:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


def test_generate_returns_response_text():
    fake_messages = _FakeMessages(response_text="hello from claude")
    fake_client = _FakeAnthropicClient(fake_messages)
    adapter = AnthropicClient(_config(), client=fake_client)

    result = adapter.generate(system="you are helpful", user="say hi")

    assert result == "hello from claude"


def test_generate_sends_correct_model_and_max_tokens():
    fake_messages = _FakeMessages()
    fake_client = _FakeAnthropicClient(fake_messages)
    adapter = AnthropicClient(_config(model="claude-haiku-4-5", max_tokens=321), client=fake_client)

    adapter.generate(system="sys", user="usr")

    assert fake_messages.last_call_kwargs["model"] == "claude-haiku-4-5"
    assert fake_messages.last_call_kwargs["max_tokens"] == 321


def test_generate_sends_system_prompt_with_cache_control():
    fake_messages = _FakeMessages()
    fake_client = _FakeAnthropicClient(fake_messages)
    adapter = AnthropicClient(_config(), client=fake_client)

    adapter.generate(system="you are a careful reviewer", user="usr")

    system_blocks = fake_messages.last_call_kwargs["system"]
    assert system_blocks == [
        {
            "type": "text",
            "text": "you are a careful reviewer",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_generate_sends_user_message_as_single_user_turn():
    fake_messages = _FakeMessages()
    fake_client = _FakeAnthropicClient(fake_messages)
    adapter = AnthropicClient(_config(), client=fake_client)

    adapter.generate(system="sys", user="explain this function")

    assert fake_messages.last_call_kwargs["messages"] == [
        {"role": "user", "content": "explain this function"}
    ]


def test_generate_wraps_any_failure_as_llm_generation_error():
    fake_messages = _FakeMessages(raise_exc=RuntimeError("connection reset"))
    fake_client = _FakeAnthropicClient(fake_messages)
    adapter = AnthropicClient(_config(), client=fake_client)

    with pytest.raises(LLMGenerationError):
        adapter.generate(system="sys", user="usr")


def test_generation_error_message_never_contains_api_key():
    fake_messages = _FakeMessages(raise_exc=RuntimeError("boom, key=sk-test-key leaked"))
    fake_client = _FakeAnthropicClient(fake_messages)
    adapter = AnthropicClient(_config(api_key="sk-test-key"), client=fake_client)

    try:
        adapter.generate(system="sys", user="usr")
        pytest.fail("expected LLMGenerationError")
    except LLMGenerationError as exc:
        assert "sk-test-key" not in str(exc)


def test_generation_error_message_does_not_echo_raw_exception_text():
    # The wrapped message must not include str(original_exc) verbatim,
    # since we can't verify that's always safe across SDK versions/paths.
    fake_messages = _FakeMessages(raise_exc=RuntimeError("some raw internal detail"))
    fake_client = _FakeAnthropicClient(fake_messages)
    adapter = AnthropicClient(_config(), client=fake_client)

    try:
        adapter.generate(system="sys", user="usr")
        pytest.fail("expected LLMGenerationError")
    except LLMGenerationError as exc:
        assert "some raw internal detail" not in str(exc)
        assert "RuntimeError" in str(exc)  # type name IS allowed


def test_constructing_without_injected_client_builds_a_real_anthropic_client():
    # Doesn't call the network — just confirms the constructor path that
    # production code uses (no `client=` kwarg) builds a real SDK client
    # rather than crashing or requiring the kwarg.
    import anthropic

    adapter = AnthropicClient(_config())
    assert isinstance(adapter._client, anthropic.Anthropic)


def test_generate_wraps_response_parsing_failure_as_llm_generation_error():
    # Verify that failures during response-parsing (e.g. empty .content list)
    # are caught and wrapped as LLMGenerationError, not leaked as raw IndexError.
    class _FakeResponseWithEmptyContent:
        content = []

    class _FakeMessagesReturningEmptyContent:
        def create(self, **kwargs):
            return _FakeResponseWithEmptyContent()

    class _FakeClientWithBadMessages:
        def __init__(self):
            self.messages = _FakeMessagesReturningEmptyContent()

    adapter = AnthropicClient(_config(), client=_FakeClientWithBadMessages())

    with pytest.raises(LLMGenerationError) as exc_info:
        adapter.generate(system="sys", user="usr")

    # Verify the wrapped message doesn't leak the underlying IndexError details
    error_msg = str(exc_info.value)
    assert "IndexError" in error_msg
    assert "Anthropic generation failed:" in error_msg
