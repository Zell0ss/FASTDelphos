# openai_compatible LLM Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the second `LLMClient` implementation — `OpenAICompatibleClient`, talking to any `/v1/chat/completions`-shaped REST endpoint via `httpx` — completing Phase 2's two-provider design (`anthropic` for home, `openai_compatible` for BNP's internal Qwen Coder gateway).

**Architecture:** `OpenAICompatibleClient` mirrors `AnthropicClient`'s exact shape (same `LLMClient` Protocol, same constructor-injection testability, same "wrap everything into `LLMGenerationError`, leak only the exception type name" security contract) but uses a plain `httpx.Client.post(...)` call instead of an SDK. `config.base_url` becomes required for this provider (there's no sane default endpoint to fall back to, unlike `anthropic`). Wired into `cli.py`'s existing provider dispatch as a second `elif` branch.

**Tech Stack:** `httpx` (already an installed transitive dependency of `anthropic`, made explicit here), the existing `cc.llm.config`/`cc.llm.client` modules from Phase 2 Step 1.

## Global Constraints

- `httpx`, never the `openai` SDK — spec: "NO usar el SDK de openai — una llamada REST no justifica la dependencia."
- `config.base_url` is the **full base URL including `/v1`** (e.g. `http://localhost:11434/v1` for a local Ollama server) — the client appends `/chat/completions` to it, never assumes a fixed suffix beyond that.
- `config.api_key`, if set, is sent as `Authorization: Bearer <key>`. It is **optional** for this provider (unlike `anthropic`, where it's required) — many local OpenAI-compatible dev servers (Ollama, vLLM) don't require auth, and the spec explicitly wants this "testeable en casa contra cualquier servidor local OpenAI-compatible antes de tocar BNP."
- `config.base_url` becomes **required** for this provider — `load_config()` must raise `LLMConfigError` if `CC_LLM_PROVIDER=openai_compatible` and `CC_LLM_BASE_URL` is empty, mirroring the existing `CC_LLM_API_KEY`-required-for-`anthropic` pattern.
- Same security contract as `AnthropicClient` (`src/cc/llm/anthropic_adapter.py`): every failure (network, HTTP status, malformed JSON, missing response keys) is caught by one broad `except Exception` and re-raised as `LLMGenerationError(f"OpenAI-compatible generation failed: {type(exc).__name__}")` — **never** `str(exc)` verbatim (an `httpx.HTTPStatusError`'s default message can echo response body text that hasn't been vetted for secrets). Response-parsing (`response.json()["choices"][0]["message"]["content"]`) must be **inside** the `try` block — a prior task found and fixed exactly this bug for `AnthropicClient` (a malformed response leaking a raw `IndexError` instead of `LLMGenerationError`); don't repeat it here.
- Tests use `httpx.MockTransport` (a real `httpx.Client` with a custom transport function returning canned `httpx.Response` objects) — never a hand-rolled fake for the HTTP layer, since `MockTransport` exercises real request/response serialization and matches this project's stated preference against mocking internal functions.
- `scripts/openai_smoke_test.py` must **never** be executed by an agent/automated process in this environment — it is manual-only, for Josem to run against a real endpoint (locally first, then BNP's internal gateway) himself.
- Reuse the existing `LLMClient` Protocol (`src/cc/llm/client.py`) and `LLMGenerationError` — do not redefine either.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/cc/llm/config.py` | Modified: `CC_LLM_BASE_URL` becomes required when `provider == "openai_compatible"`. |
| `pyproject.toml` | Modified: `httpx` added as an explicit dependency. |
| `src/cc/llm/openai_compatible_adapter.py` | New. `OpenAICompatibleClient` implementing `LLMClient`. |
| `src/cc/cli.py` | Modified: `annotate` command's provider dispatch gains an `openai_compatible` branch. |
| `scripts/openai_smoke_test.py` | New. Manual-only verification script, never run automatically. |
| `tests/test_llm_config.py` | Extended: `CC_LLM_BASE_URL`-required-for-`openai_compatible` tests. |
| `tests/test_llm_openai_compatible_adapter.py` | New. Adapter tests via `httpx.MockTransport`. |
| `tests/test_cli_annotate.py` | Extended/modified: the old "openai_compatible is unimplemented" test is replaced (it's no longer true) with tests for the new config-error path and for confirming the provider is actually wired. |

---

### Task 1: `config.py` — require `CC_LLM_BASE_URL` for `openai_compatible`; add `httpx` dependency

**Files:**
- Modify: `src/cc/llm/config.py`
- Modify: `pyproject.toml`
- Test: `tests/test_llm_config.py` (extend)

**Interfaces:**
- Produces: `load_config()` now raises `LLMConfigError` for `provider="openai_compatible"` with an empty/missing `CC_LLM_BASE_URL`. No change to `LLMConfig`'s fields (`base_url` already exists).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_llm_config.py
def test_openai_compatible_requires_base_url():
    with pytest.raises(LLMConfigError, match="CC_LLM_BASE_URL"):
        load_config({"CC_LLM_PROVIDER": "openai_compatible", "CC_LLM_API_KEY": ""})


def test_openai_compatible_with_base_url_and_no_api_key_is_valid():
    # api_key is optional for this provider — many local dev servers don't need one.
    config = load_config(
        {"CC_LLM_PROVIDER": "openai_compatible", "CC_LLM_BASE_URL": "http://localhost:11434/v1"}
    )
    assert config.base_url == "http://localhost:11434/v1"
    assert config.api_key == ""


def test_anthropic_still_does_not_require_base_url():
    # Regression guard: this task must not accidentally make base_url
    # required for the anthropic provider too.
    config = load_config({"CC_LLM_PROVIDER": "anthropic", "CC_LLM_API_KEY": "sk-test"})
    assert config.base_url is None
```

(check the existing top of `tests/test_llm_config.py` for how `pytest`/`LLMConfigError`/`load_config` are already imported — reuse those imports)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_config.py -v`
Expected: `test_openai_compatible_requires_base_url` FAILS — no `LLMConfigError` is raised today (empty `base_url` is silently accepted for every provider). The other two tests should already PASS (they document existing/unaffected behavior) — run them to confirm before your change, so you know your implementation doesn't accidentally break them.

- [ ] **Step 3: Implement**

In `src/cc/llm/config.py`, right after the existing line `base_url = values.get("CC_LLM_BASE_URL", "").strip() or None` (currently line 94, immediately before `extra_instructions = ...`), insert:

```python
    if provider == "openai_compatible" and not base_url:
        raise LLMConfigError(
            "CC_LLM_BASE_URL is not set (required for provider=openai_compatible)"
        )
```

In `pyproject.toml`, add `"httpx>=0.27",` to the `dependencies` list (currently `griffe`, `sqlglot`, `python-dotenv`, `anthropic`) — `httpx` is already installed as a transitive dependency of `anthropic` (confirmed: `0.28.1` in this project's venv), but must be declared explicitly since this plan makes it a direct, load-bearing dependency of this project's own code, not just something that happens to ride along with another package.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_config.py -v`
Expected: PASS (all tests, including every pre-existing `test_llm_config.py` test — this change only adds a new failure case for one specific provider value, it doesn't touch any other validation path)

Run: `pytest -q`
Expected: full suite passes.

- [ ] **Step 5: Commit**

```bash
git add src/cc/llm/config.py pyproject.toml tests/test_llm_config.py
git commit -m "feat: require CC_LLM_BASE_URL for the openai_compatible provider; add httpx dependency"
```

---

### Task 2: `OpenAICompatibleClient` adapter + CLI wiring + manual smoke test

**Files:**
- Create: `src/cc/llm/openai_compatible_adapter.py`
- Modify: `src/cc/cli.py`
- Create: `scripts/openai_smoke_test.py`
- Test: `tests/test_llm_openai_compatible_adapter.py`
- Test: `tests/test_cli_annotate.py` (modify — see Step 1 for exactly what to remove/add)

**Interfaces:**
- Consumes: `LLMClient` Protocol, `LLMGenerationError` (`src/cc/llm/client.py`); `LLMConfig` (`src/cc/llm/config.py`, already has `.base_url`, `.api_key`, `.model`, `.max_tokens` from Phase 2 Step 1); Task 1's new `CC_LLM_BASE_URL`-required-for-`openai_compatible` validation.
- Produces: `OpenAICompatibleClient(config: LLMConfig, client: "httpx.Client | None" = None)` with `.generate(system: str, user: str) -> str`, satisfying the `LLMClient` Protocol — usable anywhere `AnthropicClient` is (e.g. `run_annotate`'s `client` parameter from Phase 2 Step 2, unchanged by this plan).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_openai_compatible_adapter.py
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

    adapter = OpenAICompatibleClient(_config(api_key="secret-token"), client=_client_with_handler(handler))
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

    adapter = OpenAICompatibleClient(_config(api_key="secret-token"), client=_client_with_handler(handler))

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
```

Now update `tests/test_cli_annotate.py`. First, **remove** `test_annotate_reports_unimplemented_provider` entirely — it tested `CC_LLM_PROVIDER=openai_compatible` printing "not implemented yet.", which is no longer true once this task lands (and even before this task's CLI change, that exact test scenario — `openai_compatible` with no `base_url` set — now hits Task 1's new config-error path first, never reaching the "not implemented" branch at all). Then **add**:

```python
# add to tests/test_cli_annotate.py — reuse the existing _write_minimal_graph helper already in this file
def test_annotate_reports_config_error_when_openai_compatible_missing_base_url(tmp_path, monkeypatch, capsys):
    _write_minimal_graph(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_LLM_PROVIDER", "openai_compatible")
    monkeypatch.delenv("CC_LLM_BASE_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["cc", "annotate", str(tmp_path)])

    main()

    captured = capsys.readouterr()
    assert "Config error" in captured.out
    assert "CC_LLM_BASE_URL" in captured.out


def test_annotate_wires_openai_compatible_provider(tmp_path, monkeypatch, capsys):
    # Points at a loopback port nothing is listening on — connection fails
    # fast (ECONNREFUSED), which run_annotate's per-node error handling
    # (Phase 2 Step 2) already catches and reports as a failed node, not a
    # crash. This proves the provider dispatch reaches the real annotate
    # flow (never printing "not implemented yet."), without needing a real
    # reachable endpoint.
    _write_minimal_graph(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CC_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CC_LLM_BASE_URL", "http://localhost:1/v1")
    monkeypatch.setattr("sys.argv", ["cc", "annotate", str(tmp_path)])

    main()

    captured = capsys.readouterr()
    assert "not implemented yet" not in captured.out
    assert "Generadas:" in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_openai_compatible_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc.llm.openai_compatible_adapter'`

Run: `pytest tests/test_cli_annotate.py -v`
Expected: `test_annotate_reports_config_error_when_openai_compatible_missing_base_url` PASSES already (Task 1's validation is already merged) — that's fine, it's here to document the CLI-level behavior, not to prove Task 1 again. `test_annotate_wires_openai_compatible_provider` FAILS — today it prints `Provider 'openai_compatible' is not implemented yet.`, so `"not implemented yet" not in captured.out` is False.

- [ ] **Step 3: Implement**

```python
# src/cc/llm/openai_compatible_adapter.py
import httpx

from cc.llm.client import LLMGenerationError
from cc.llm.config import LLMConfig


class OpenAICompatibleClient:
    """LLMClient implementation for any OpenAI-compatible /v1/chat/completions
    endpoint (BNP's internal Qwen Coder gateway, a local Ollama/vLLM server,
    or any future provider exposing the same REST shape).

    `config.base_url` is the full base INCLUDING /v1 (e.g.
    "http://localhost:11434/v1" for Ollama) — this client appends
    "/chat/completions" to it. `config.api_key`, if set, is sent as a
    Bearer token; many local dev servers don't require one, so it's
    optional here (unlike the `anthropic` provider).
    """

    def __init__(self, config: LLMConfig, client: "httpx.Client | None" = None) -> None:
        self._config = config
        self._client = client if client is not None else httpx.Client(timeout=60.0)

    def generate(self, system: str, user: str) -> str:
        try:
            headers = {}
            if self._config.api_key:
                headers["Authorization"] = f"Bearer {self._config.api_key}"

            url = f"{self._config.base_url.rstrip('/')}/chat/completions"
            response = self._client.post(
                url,
                headers=headers,
                json={
                    "model": self._config.model,
                    "max_tokens": self._config.max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            # Deliberately broad, mirroring AnthropicClient: wrap ANY failure
            # (network, HTTP status, malformed JSON, missing response keys)
            # into one type so callers only handle LLMGenerationError. Never
            # repeat str(exc) verbatim — an httpx.HTTPStatusError's message
            # can echo response body text we haven't vetted for secrets;
            # only the exception's type name is safe to surface.
            raise LLMGenerationError(
                f"OpenAI-compatible generation failed: {type(exc).__name__}"
            ) from exc
```

In `src/cc/cli.py`, change the existing provider dispatch (currently):

```python
        if config.provider == "anthropic":
            from cc.llm.anthropic_adapter import AnthropicClient

            client = AnthropicClient(config)
        else:
            print(f"Provider {config.provider!r} is not implemented yet.")
            return
```

to:

```python
        if config.provider == "anthropic":
            from cc.llm.anthropic_adapter import AnthropicClient

            client = AnthropicClient(config)
        elif config.provider == "openai_compatible":
            from cc.llm.openai_compatible_adapter import OpenAICompatibleClient

            client = OpenAICompatibleClient(config)
        else:
            print(f"Provider {config.provider!r} is not implemented yet.")
            return
```

Create `scripts/openai_smoke_test.py`:

```python
"""Manual smoke test for the openai_compatible LLM adapter — NOT part of the
automated test suite. Costs real usage against whatever endpoint you point
it at. Run by hand:

    source .venv/bin/activate
    export CC_LLM_PROVIDER=openai_compatible
    export CC_LLM_BASE_URL=http://localhost:11434/v1   # full base, INCLUDING /v1
    export CC_LLM_API_KEY=                              # optional — many local servers don't need one
    export CC_LLM_MODEL=qwen2.5-coder                   # whatever model name your server expects
    python scripts/openai_smoke_test.py

Or put those CC_LLM_* values in a .env file at the repo root and just run
the script — load_config() reads .env automatically.

CC_LLM_BASE_URL convention: the FULL base URL, including /v1 — this client
appends "/chat/completions" to whatever you set here. Examples:
  - Ollama (local, OpenAI-compat mode):  http://localhost:11434/v1
  - vLLM (local):                        http://localhost:8000/v1
  - A corporate gateway (e.g. an internal Qwen Coder endpoint): whatever
    URL your platform team gives you, ending in /v1 — put the bearer token
    (if required) in CC_LLM_API_KEY.

Test locally first (e.g. against Ollama) before pointing this at a real
corporate gateway — same "probar con 1 nodo a mano" gate as the anthropic
adapter's smoke test (Phase 2 Step 1), just for the second provider.
"""

from cc.llm.client import LLMGenerationError
from cc.llm.config import LLMConfigError, load_config
from cc.llm.openai_compatible_adapter import OpenAICompatibleClient

_SMOKE_SYSTEM_PROMPT = (
    "You are a code comprehension assistant. Answer in one short sentence."
)
_SMOKE_USER_PROMPT = (
    "A Python function named `run_synthesis` calls `build_context` and then "
    "writes its result to a `channel_syntheses` table. In one sentence, why "
    "might this be a separate function rather than inlined into its caller?"
)


def main() -> None:
    try:
        config = load_config()
    except LLMConfigError as exc:
        print(f"Config error: {exc}")
        print(
            "Set CC_LLM_PROVIDER=openai_compatible and CC_LLM_BASE_URL "
            "(env vars or .env file) and retry."
        )
        return

    print(
        f"Provider: {config.provider}, base_url: {config.base_url}, "
        f"model: {config.model}, max_tokens: {config.max_tokens}"
    )

    client = OpenAICompatibleClient(config)
    try:
        result = client.generate(system=_SMOKE_SYSTEM_PROMPT, user=_SMOKE_USER_PROMPT)
    except LLMGenerationError as exc:
        print(f"Generation failed: {exc}")
        return

    print("--- Response ---")
    print(result)
    print("--- End ---")
    print(
        "\nManual check: does the response above read as a real, sensible "
        "answer (not an error page, not empty, not obviously truncated)? "
        "If yes, the openai_compatible adapter is verified against this endpoint."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_openai_compatible_adapter.py tests/test_cli_annotate.py -v`
Expected: PASS (11 + however many `test_cli_annotate.py` tests now exist, all green)

Run: `pytest -q`
Expected: full suite passes.

Run: `ruff check . && ruff format --check .` on the new/changed files (`src/cc/llm/openai_compatible_adapter.py`, `src/cc/cli.py`, `scripts/openai_smoke_test.py`, both test files) — fix any violations before committing. **Do not** run `ruff format .` unscoped across the whole repo — this project has pre-existing, deliberately-untouched lint/format drift in unrelated files from earlier work; only fix what this task's own files introduce.

- [ ] **Step 5: Commit**

```bash
git add src/cc/llm/openai_compatible_adapter.py src/cc/cli.py scripts/openai_smoke_test.py tests/test_llm_openai_compatible_adapter.py tests/test_cli_annotate.py
git commit -m "feat: add openai_compatible LLM adapter (httpx-based, for BNP's Qwen Coder gateway)"
```

---

## Manual Verification (outside the automated suite)

Do **not** run `scripts/openai_smoke_test.py` in this environment under any circumstances — there is no reachable local OpenAI-compatible server here, and this environment has no route to BNP's internal network. This is explicitly Josem's own manual gate, same pattern as the `anthropic` adapter's Phase 2 Step 1 smoke test: he runs it himself, first against something local (e.g. Ollama) to sanity-check the adapter, then against BNP's real gateway once he's there.
