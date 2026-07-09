import json

import httpx
import pytest

from cc.llm.client import LLMGenerationError
from cc.llm.config import LLMConfig
from cc.llm.openai_compatible_adapter import OpenAICompatibleClient


def _config(**overrides) -> LLMConfig:
    defaults = dict(
        provider="openai_compatible",
        base_url="http://localhost:11434/v1",
        api_key="test-token",
        model="qwen-coder",
        max_tokens=500,
        extra_instructions=None,
        orchestrator_threshold=2,
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


def _client_with_handler(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_generate_returns_response_text():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello from qwen"}}]})

    adapter = OpenAICompatibleClient(_config(), client=_client_with_handler(handler))
    result = adapter.generate(system="you are helpful", user="say hi")
    assert result == "hello from qwen"


def test_generate_posts_to_chat_completions_under_base_url():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    adapter = OpenAICompatibleClient(
        _config(base_url="http://localhost:11434/v1"), client=_client_with_handler(handler)
    )
    adapter.generate(system="sys", user="usr")
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"


def test_generate_strips_trailing_slash_from_base_url():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    adapter = OpenAICompatibleClient(
        _config(base_url="http://localhost:11434/v1/"), client=_client_with_handler(handler)
    )
    adapter.generate(system="sys", user="usr")
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"


def test_generate_sends_bearer_token_when_api_key_set():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    adapter = OpenAICompatibleClient(
        _config(api_key="secret-token"), client=_client_with_handler(handler)
    )
    adapter.generate(system="sys", user="usr")
    assert captured["auth"] == "Bearer secret-token"


def test_generate_omits_auth_header_when_api_key_empty():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    adapter = OpenAICompatibleClient(_config(api_key=""), client=_client_with_handler(handler))
    adapter.generate(system="sys", user="usr")
    assert captured["auth"] is None


def test_generate_sends_correct_model_max_tokens_and_messages():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    adapter = OpenAICompatibleClient(
        _config(model="qwen-coder", max_tokens=321), client=_client_with_handler(handler)
    )
    adapter.generate(system="you are a reviewer", user="explain this")

    assert captured["body"]["model"] == "qwen-coder"
    assert captured["body"]["max_tokens"] == 321
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "you are a reviewer"},
        {"role": "user", "content": "explain this"},
    ]


def test_generate_wraps_http_status_error_as_llm_generation_error():
    def handler(request):
        return httpx.Response(401, json={"error": "unauthorized, token=secret-token"})

    adapter = OpenAICompatibleClient(
        _config(api_key="secret-token"), client=_client_with_handler(handler)
    )

    with pytest.raises(LLMGenerationError) as exc_info:
        adapter.generate(system="sys", user="usr")

    assert "secret-token" not in str(exc_info.value)
    assert "HTTPStatusError" in str(exc_info.value)


def test_generate_wraps_malformed_response_as_llm_generation_error():
    def handler(request):
        return httpx.Response(200, json={"choices": []})  # empty choices -> IndexError

    adapter = OpenAICompatibleClient(_config(), client=_client_with_handler(handler))

    with pytest.raises(LLMGenerationError) as exc_info:
        adapter.generate(system="sys", user="usr")

    assert "IndexError" in str(exc_info.value)


def test_generate_wraps_connection_error_as_llm_generation_error():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    adapter = OpenAICompatibleClient(_config(), client=_client_with_handler(handler))

    with pytest.raises(LLMGenerationError) as exc_info:
        adapter.generate(system="sys", user="usr")

    assert "ConnectError" in str(exc_info.value)


def test_constructing_without_injected_client_builds_a_real_httpx_client():
    # Doesn't call the network — just confirms the constructor path that
    # production code uses (no `client=` kwarg) builds a real httpx.Client
    # rather than crashing or requiring the kwarg.
    adapter = OpenAICompatibleClient(_config())
    assert isinstance(adapter._client, httpx.Client)
