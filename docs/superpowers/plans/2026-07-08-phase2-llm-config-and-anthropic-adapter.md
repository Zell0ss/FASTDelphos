# Phase 2 Step 1 — LLM Config, Client Interface, Anthropic Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first of 5 incremental steps of Phase 2 (LLM why-notes) per `doc_proyecto/FASE2_WHYNOTES.md`'s own suggested build order: `.env`-based config, a minimal provider-agnostic `LLMClient` interface, and a working `anthropic` adapter with prompt caching — verified by hand against one real API call. This is infrastructure only; nothing in this plan touches `graph.json`, the render, or `cc compile`.

**Architecture:** Three small, single-responsibility modules under a new `src/cc/llm/` subpackage (mirroring the existing `src/cc/{extract,graph,render}/` convention): `config.py` (env loading, one dataclass, one loader function), `client.py` (the `LLMClient` Protocol + the one exception type adapters raise on failure), `anthropic_adapter.py` (the concrete `anthropic` SDK-backed implementation). A manual smoke-test script at the repo root (`scripts/llm_smoke_test.py`) is the human verification gate before Phase 2 Step 2 (the batch/overlay system) gets built on top of this.

**Tech Stack:** `anthropic` (official SDK), `python-dotenv` (`.env` loading) — both new dependencies, added to `pyproject.toml`'s `dependencies` list (not `dev`, since this is a real runtime feature).

## Global Constraints

- **Source of truth:** `doc_proyecto/FASE2_WHYNOTES.md` — read it before starting; this plan implements exactly its §1 (provider architecture) and §2 (config), scoped to the `anthropic` adapter only. Do not implement §3 (notes.json overlay), §4 (regeneration gate), §5 (batch scope), §6 (the real anti-paraphrase prompt), §7 (render), or the `openai_compatible` adapter — those are later plans (YAGNI, per the doc's own "Orden de construcción sugerido").
- **`LLMClient` Protocol is exactly:** `class LLMClient(Protocol): def generate(self, system: str, user: str) -> str: ...` — single-turn, synchronous, no streaming. Do not add methods beyond this.
- **Env vars, prefix `CC_LLM_*`:** `CC_LLM_PROVIDER`, `CC_LLM_BASE_URL`, `CC_LLM_API_KEY`, `CC_LLM_MODEL`, `CC_LLM_MAX_TOKENS`, `CC_LLM_EXTRA_INSTRUCTIONS` — loaded via `python-dotenv`, with real process environment variables taking priority over `.env` file values (`load_dotenv(override=False)` — real env vars set before the process starts, e.g. in a pod/CI, must win; `python-dotenv`'s default behavior already does this correctly: it never overwrites a variable that's already present in `os.environ`).
- **`CC_LLM_PROVIDER` is required, no default** — unset or empty raises `LLMConfigError` with a clear message. Must be exactly `"anthropic"` or `"openai_compatible"` — anything else also raises `LLMConfigError` (typo protection; `openai_compatible` is a valid *value* to accept even though its adapter doesn't exist until a later plan — selecting it in Step 1 should fail later, when something tries to build that adapter, not when config alone is loaded).
- **`CC_LLM_API_KEY` is required when `CC_LLM_PROVIDER=anthropic`** — empty/unset raises `LLMConfigError` (message must not need the key's value to explain the problem — "not set" is enough).
- **`CC_LLM_MODEL` defaults to `"claude-haiku-4-5"`** when unset (matches the doc's own `.env` example).
- **`CC_LLM_MAX_TOKENS` defaults to `500`** when unset; must parse as a positive int — a non-numeric value raises `LLMConfigError` (never crash with a bare `ValueError` from `int(...)` — catch it and re-raise with a clear message).
- **`CC_LLM_BASE_URL` and `CC_LLM_EXTRA_INSTRUCTIONS` default to `None`** when unset or empty — both are optional per the doc (`CC_LLM_BASE_URL` is unused by the `anthropic` adapter in this plan; it exists in the config shape now because `openai_compatible` will need it in a later plan — do not build any `openai_compatible`-specific branching now).
- **Security — the API key must never appear in a log line, an exception message, or any object's printed/`repr()`'d form.** Concretely: `LLMConfig` is a `@dataclass` where the `api_key` field is declared `field(repr=False)` (so `repr(config)`/`print(config)` never shows it), and no code path anywhere in this plan ever interpolates `config.api_key` into an f-string, exception message, or print statement.
- **Adapter failure contract:** any failure inside `AnthropicClient.generate()` (network error, API error, timeout, malformed response — anything the SDK call can raise) is caught and re-raised as `LLMGenerationError`, a plain `Exception` subclass defined in `client.py`. The wrapping message includes only the exception's *type name*, never `str(exc)` verbatim and never any request/response body — this is a deliberately conservative choice: since we cannot verify from this plan alone that every possible underlying SDK exception's message is guaranteed key-free in every SDK version, we never repeat it. This also means the adapter does not need to know the exact `anthropic` SDK exception hierarchy (which this plan's author cannot verify without the package installed) — it catches broad `Exception` at that one call site and always wraps, which is simpler and more version-robust than enumerating specific SDK exception types.
- **No logging framework.** This project has none (confirmed: `print()` is the only mechanism used anywhere in the existing codebase) — do not add one. Nothing in this plan needs to print anything except the manual smoke-test script (Task 4), which is explicitly a human-facing tool, not library code.
- **Prompt caching is mandatory, not optional**, on the `anthropic` adapter's system prompt, via the SDK's block-form `system` parameter with `cache_control`: `system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]` — see Task 3 for the exact call shape.
- **No mocking of the whole SDK.** Tests inject a fake object satisfying the one method the adapter actually calls (`.messages.create(...)`) via constructor dependency injection — this exercises the adapter's real logic (request shape, error wrapping) without hitting the network or mocking library internals.
- **The real API call happens exactly once, by hand, in Task 4** — never in the automated `pytest` suite (costs money, needs a real key, not reproducible in CI).

---

### Task 1: `.env` config loading (`src/cc/llm/config.py`)

**Files:**
- Create: `src/cc/llm/__init__.py` (empty)
- Create: `src/cc/llm/config.py`
- Modify: `pyproject.toml` (add `python-dotenv` to `dependencies`)
- Test: `tests/test_llm_config.py` (new)

**Interfaces:**
- Produces:
  - `class LLMConfigError(Exception)` — raised by `load_config()` on any config problem.
  - `@dataclass class LLMConfig` with fields: `provider: str`, `base_url: str | None`, `api_key: str = field(repr=False)`, `model: str`, `max_tokens: int`, `extra_instructions: str | None`.
  - `def load_config(env: dict[str, str] | None = None) -> LLMConfig` — reads `.env` (via `python-dotenv`) then `os.environ` (or the injected `env` mapping, for testing — see Step 1), validates, returns a populated `LLMConfig`, or raises `LLMConfigError`.

- [ ] **Step 1: Add `python-dotenv` dependency**

In `pyproject.toml`, change:

```toml
dependencies = [
    "griffe>=0.47",
    "sqlglot>=25.0",
]
```

to:

```toml
dependencies = [
    "griffe>=0.47",
    "sqlglot>=25.0",
    "python-dotenv>=1.0",
]
```

Run: `pip install -e ".[dev]"` (from the repo root, with `.venv` activated)
Expected: install succeeds, `python -c "import dotenv"` succeeds with no error.

- [ ] **Step 2: Write the failing tests**

Create `src/cc/llm/__init__.py` (empty file — makes `src/cc/llm` a package).

Create `tests/test_llm_config.py`:

```python
import pytest

from cc.llm.config import LLMConfig, LLMConfigError, load_config


def test_loads_full_config_from_env_mapping():
    env = {
        "CC_LLM_PROVIDER": "anthropic",
        "CC_LLM_API_KEY": "sk-test-key",
        "CC_LLM_MODEL": "claude-haiku-4-5",
        "CC_LLM_MAX_TOKENS": "300",
        "CC_LLM_BASE_URL": "",
        "CC_LLM_EXTRA_INSTRUCTIONS": "",
    }
    config = load_config(env)
    assert config.provider == "anthropic"
    assert config.api_key == "sk-test-key"
    assert config.model == "claude-haiku-4-5"
    assert config.max_tokens == 300
    assert config.base_url is None
    assert config.extra_instructions is None


def test_missing_provider_raises_clear_error():
    with pytest.raises(LLMConfigError, match="CC_LLM_PROVIDER"):
        load_config({"CC_LLM_API_KEY": "sk-test-key"})


def test_empty_provider_raises_clear_error():
    with pytest.raises(LLMConfigError, match="CC_LLM_PROVIDER"):
        load_config({"CC_LLM_PROVIDER": "", "CC_LLM_API_KEY": "sk-test-key"})


def test_invalid_provider_value_raises_clear_error():
    with pytest.raises(LLMConfigError, match="CC_LLM_PROVIDER"):
        load_config({"CC_LLM_PROVIDER": "not-a-real-provider", "CC_LLM_API_KEY": "sk-test-key"})


def test_openai_compatible_provider_value_is_accepted_at_config_time():
    # The adapter for this provider doesn't exist until a later plan — but
    # config loading itself must not reject the value, per the plan's scope.
    config = load_config(
        {"CC_LLM_PROVIDER": "openai_compatible", "CC_LLM_API_KEY": "sk-test-key"}
    )
    assert config.provider == "openai_compatible"


def test_missing_api_key_raises_clear_error_for_anthropic_provider():
    with pytest.raises(LLMConfigError, match="CC_LLM_API_KEY"):
        load_config({"CC_LLM_PROVIDER": "anthropic"})


def test_model_defaults_when_unset():
    config = load_config({"CC_LLM_PROVIDER": "anthropic", "CC_LLM_API_KEY": "sk-test-key"})
    assert config.model == "claude-haiku-4-5"


def test_max_tokens_defaults_when_unset():
    config = load_config({"CC_LLM_PROVIDER": "anthropic", "CC_LLM_API_KEY": "sk-test-key"})
    assert config.max_tokens == 500


def test_max_tokens_non_numeric_raises_clear_error():
    with pytest.raises(LLMConfigError, match="CC_LLM_MAX_TOKENS"):
        load_config(
            {
                "CC_LLM_PROVIDER": "anthropic",
                "CC_LLM_API_KEY": "sk-test-key",
                "CC_LLM_MAX_TOKENS": "not-a-number",
            }
        )


def test_base_url_and_extra_instructions_default_to_none():
    config = load_config({"CC_LLM_PROVIDER": "anthropic", "CC_LLM_API_KEY": "sk-test-key"})
    assert config.base_url is None
    assert config.extra_instructions is None


def test_base_url_set_when_provided():
    config = load_config(
        {
            "CC_LLM_PROVIDER": "anthropic",
            "CC_LLM_API_KEY": "sk-test-key",
            "CC_LLM_BASE_URL": "https://example.internal/v1",
        }
    )
    assert config.base_url == "https://example.internal/v1"


def test_api_key_never_appears_in_repr():
    config = load_config(
        {"CC_LLM_PROVIDER": "anthropic", "CC_LLM_API_KEY": "sk-super-secret-value"}
    )
    assert "sk-super-secret-value" not in repr(config)
    assert "sk-super-secret-value" not in str(config)


def test_config_error_message_never_contains_key_value():
    # Even when the key IS set but something else is wrong, the error text
    # must not echo back any config value that could itself be sensitive.
    try:
        load_config(
            {
                "CC_LLM_PROVIDER": "anthropic",
                "CC_LLM_API_KEY": "sk-super-secret-value",
                "CC_LLM_MAX_TOKENS": "garbage",
            }
        )
        pytest.fail("expected LLMConfigError")
    except LLMConfigError as exc:
        assert "sk-super-secret-value" not in str(exc)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_llm_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc.llm'` (the package doesn't exist yet).

- [ ] **Step 4: Implement**

Create `src/cc/llm/config.py`:

```python
import os
from dataclasses import dataclass, field

from dotenv import dotenv_values

_VALID_PROVIDERS = {"anthropic", "openai_compatible"}
_DEFAULT_MODEL = "claude-haiku-4-5"
_DEFAULT_MAX_TOKENS = 500


class LLMConfigError(Exception):
    """Raised when CC_LLM_* configuration is missing or invalid."""


@dataclass
class LLMConfig:
    provider: str
    base_url: str | None
    api_key: str = field(repr=False)
    model: str
    max_tokens: int
    extra_instructions: str | None


def _read_env(env: dict[str, str] | None) -> dict[str, str]:
    """Merge .env file values with real environment variables, real env wins.

    `dotenv_values` never touches os.environ — it just parses the .env file
    into a dict. We start from the .env values, then let injected/real env
    vars override, matching "real vars win (pods/CI)" from the spec.
    """
    file_values = dotenv_values(".env")
    live_values = env if env is not None else dict(os.environ)
    merged = dict(file_values)
    merged.update({k: v for k, v in live_values.items() if k.startswith("CC_LLM_")})
    return merged


def load_config(env: dict[str, str] | None = None) -> LLMConfig:
    """Load and validate CC_LLM_* configuration.

    `env`, if given, replaces the real process environment for the "live
    values" half of the merge (used by tests to avoid depending on/mutating
    real os.environ). Production callers pass nothing and get real env vars.
    """
    values = _read_env(env)

    provider = values.get("CC_LLM_PROVIDER", "").strip()
    if not provider:
        raise LLMConfigError("CC_LLM_PROVIDER is not set (required: anthropic or openai_compatible)")
    if provider not in _VALID_PROVIDERS:
        raise LLMConfigError(
            f"CC_LLM_PROVIDER must be one of {sorted(_VALID_PROVIDERS)}, got an unrecognized value"
        )

    api_key = values.get("CC_LLM_API_KEY", "").strip()
    if provider == "anthropic" and not api_key:
        raise LLMConfigError("CC_LLM_API_KEY is not set (required for provider=anthropic)")

    model = values.get("CC_LLM_MODEL", "").strip() or _DEFAULT_MODEL

    raw_max_tokens = values.get("CC_LLM_MAX_TOKENS", "").strip()
    if not raw_max_tokens:
        max_tokens = _DEFAULT_MAX_TOKENS
    else:
        try:
            max_tokens = int(raw_max_tokens)
        except ValueError as exc:
            raise LLMConfigError("CC_LLM_MAX_TOKENS must be an integer") from exc
        if max_tokens <= 0:
            raise LLMConfigError("CC_LLM_MAX_TOKENS must be a positive integer")

    base_url = values.get("CC_LLM_BASE_URL", "").strip() or None
    extra_instructions = values.get("CC_LLM_EXTRA_INSTRUCTIONS", "").strip() or None

    return LLMConfig(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        extra_instructions=extra_instructions,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_llm_config.py -v`
Expected: PASS — all 13 tests.

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: PASS, 151 (current baseline) + 13 = 164 passing.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/cc/llm/__init__.py src/cc/llm/config.py tests/test_llm_config.py
git commit -m "feat: add CC_LLM_* config loading (src/cc/llm/config.py)"
```

---

### Task 2: `LLMClient` Protocol and error type (`src/cc/llm/client.py`)

**Files:**
- Create: `src/cc/llm/client.py`
- Test: `tests/test_llm_client.py` (new)

**Interfaces:**
- Produces:
  - `class LLMGenerationError(Exception)` — raised by any `LLMClient` implementation's `generate()` on failure.
  - `class LLMClient(Protocol): def generate(self, system: str, user: str) -> str: ...`

- [ ] **Step 1: Write the failing test**

Create `tests/test_llm_client.py`:

```python
from cc.llm.client import LLMClient, LLMGenerationError


class _FakeClient:
    """A minimal object satisfying the LLMClient Protocol structurally."""

    def generate(self, system: str, user: str) -> str:
        return f"system={system} user={user}"


def test_llm_client_protocol_is_satisfied_structurally():
    client: LLMClient = _FakeClient()
    assert client.generate("sys", "usr") == "system=sys user=usr"


def test_llm_generation_error_is_a_plain_exception():
    err = LLMGenerationError("boom")
    assert isinstance(err, Exception)
    assert str(err) == "boom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc.llm.client'`.

- [ ] **Step 3: Implement**

Create `src/cc/llm/client.py`:

```python
from typing import Protocol


class LLMGenerationError(Exception):
    """Raised by an LLMClient implementation when generate() fails.

    Never includes API keys, request bodies, or raw underlying exception
    text — only a short, safe description (see AnthropicClient for the
    canonical example of what an implementation is allowed to put here).
    """


class LLMClient(Protocol):
    def generate(self, system: str, user: str) -> str:
        """Single-turn, synchronous, non-streaming generation.

        Raises LLMGenerationError on any failure (network, API, timeout,
        malformed response). Never raises the underlying SDK's own
        exception type directly — callers only need to catch one type.
        """
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm_client.py -v`
Expected: PASS — both tests.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS, 164 (Task 1's end count) + 2 = 166 passing.

- [ ] **Step 6: Commit**

```bash
git add src/cc/llm/client.py tests/test_llm_client.py
git commit -m "feat: add LLMClient protocol and LLMGenerationError"
```

---

### Task 3: `anthropic` adapter with prompt caching (`src/cc/llm/anthropic_adapter.py`)

**Files:**
- Create: `src/cc/llm/anthropic_adapter.py`
- Modify: `pyproject.toml` (add `anthropic` to `dependencies`)
- Test: `tests/test_llm_anthropic_adapter.py` (new)

**Interfaces:**
- Consumes: `LLMConfig` (Task 1), `LLMGenerationError` (Task 2).
- Produces: `class AnthropicClient` — implements `LLMClient` (Task 2's Protocol; satisfied structurally, no explicit inheritance needed). Constructor: `AnthropicClient(config: LLMConfig, client: "anthropic.Anthropic | None" = None)` — the optional `client` param is dependency injection for tests (production callers omit it and a real `anthropic.Anthropic` is constructed from `config.api_key`).

- [ ] **Step 1: Add `anthropic` dependency**

In `pyproject.toml`, change:

```toml
dependencies = [
    "griffe>=0.47",
    "sqlglot>=25.0",
    "python-dotenv>=1.0",
]
```

to:

```toml
dependencies = [
    "griffe>=0.47",
    "sqlglot>=25.0",
    "python-dotenv>=1.0",
    "anthropic>=0.40",
]
```

Run: `pip install -e ".[dev]"`
Expected: install succeeds, `python -c "import anthropic"` succeeds with no error.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_llm_anthropic_adapter.py`:

```python
import pytest

from cc.llm.client import LLMGenerationError
from cc.llm.config import LLMConfig
from cc.llm.anthropic_adapter import AnthropicClient


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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_llm_anthropic_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc.llm.anthropic_adapter'`.

- [ ] **Step 4: Implement**

Create `src/cc/llm/anthropic_adapter.py`:

```python
import anthropic

from cc.llm.client import LLMGenerationError
from cc.llm.config import LLMConfig


class AnthropicClient:
    """LLMClient implementation backed by the official anthropic SDK.

    Uses block-form `system` with `cache_control: ephemeral` so a run that
    generates many notes with the same system prompt gets a cache hit on
    every call after the first (see doc_proyecto/FASE2_WHYNOTES.md §1).
    """

    def __init__(self, config: LLMConfig, client: "anthropic.Anthropic | None" = None) -> None:
        self._config = config
        self._client = client if client is not None else anthropic.Anthropic(api_key=config.api_key)

    def generate(self, system: str, user: str) -> str:
        try:
            response = self._client.messages.create(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            # Deliberately broad: we wrap ANY failure from the SDK call
            # (network, API, timeout, malformed request) into one type so
            # callers only handle LLMGenerationError. Never repeat str(exc)
            # verbatim — we can't verify every SDK exception's message is
            # guaranteed free of the API key across versions/paths, so we
            # only ever surface the exception's type name.
            raise LLMGenerationError(
                f"Anthropic generation failed: {type(exc).__name__}"
            ) from exc

        return response.content[0].text
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_llm_anthropic_adapter.py -v`
Expected: PASS — all 8 tests.

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: PASS, 166 (Task 2's end count) + 8 = 174 passing.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/cc/llm/anthropic_adapter.py tests/test_llm_anthropic_adapter.py
git commit -m "feat: add AnthropicClient adapter with prompt caching"
```

---

### Task 4: Manual smoke test against the real Anthropic API

**Files:**
- Create: `scripts/llm_smoke_test.py`

**Interfaces:**
- Consumes: `load_config` (Task 1), `AnthropicClient` (Task 3).

This task has no automated test — it is the "probar con 1 nodo a mano" (test by hand against 1 node) step the spec explicitly calls for, and it costs real money against a real API key, so it must never run in CI or the pytest suite.

- [ ] **Step 1: Write the script**

Create `scripts/llm_smoke_test.py`:

```python
"""Manual smoke test for the Anthropic LLM adapter — NOT part of the
automated test suite. Costs real API usage. Run by hand:

    source .venv/bin/activate
    export CC_LLM_PROVIDER=anthropic
    export CC_LLM_API_KEY=sk-...      # a real key
    python scripts/llm_smoke_test.py

Or put those CC_LLM_* values in a .env file at the repo root and just run
the script — load_config() reads .env automatically.

This is the human verification gate before Phase 2 Step 2 (the notes.json
overlay + batch system) gets built on top of this adapter.
"""

from cc.llm.anthropic_adapter import AnthropicClient
from cc.llm.client import LLMGenerationError
from cc.llm.config import LLMConfigError, load_config

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
        print("Set CC_LLM_PROVIDER and CC_LLM_API_KEY (env vars or .env file) and retry.")
        return

    print(f"Provider: {config.provider}, model: {config.model}, max_tokens: {config.max_tokens}")

    client = AnthropicClient(config)
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
        "If yes, Phase 2 Step 1 is verified — proceed to Step 2."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it by hand**

This step is performed by the human (Josem), not automated. Documented command:

```bash
source .venv/bin/activate
export CC_LLM_PROVIDER=anthropic
export CC_LLM_API_KEY=<a real key>
python scripts/llm_smoke_test.py
```

Expected: prints the provider/model/max_tokens line, then a real, coherent one-sentence response from Claude Haiku about the example function, with no traceback and no error message.

**Do not mark this step's checkbox complete until a human has actually run it and confirmed real output** — an implementer subagent without a real API key cannot complete this step and should report it as the one remaining manual gate in their final report, not attempt to fake or skip it.

- [ ] **Step 3: Run the full suite once more (regression check only — no new tests in this task)**

Run: `pytest -q`
Expected: PASS, 174 (Task 3's end count), unchanged — this task adds no automated tests.

- [ ] **Step 4: Commit**

```bash
git add scripts/llm_smoke_test.py
git commit -m "feat: add manual smoke-test script for the Anthropic adapter"
```

---

## Self-Review Notes

1. **Spec coverage:** `.env` config with the exact `CC_LLM_*` names, real-env-wins precedence → Task 1. `LLMClient` Protocol exactly as specified → Task 2. `anthropic` adapter with mandatory prompt caching → Task 3. API key never in logs/output/errors → `field(repr=False)` (Task 1) + never-echo-raw-exception-text policy (Task 3), tested explicitly in both tasks. Adapter failure contract (catchable, doesn't need to know SDK-specific exception types) → Task 2's `LLMGenerationError` + Task 3's broad-catch wrapping. Manual "probar con 1 nodo a mano" → Task 4. Everything else in the source doc (§3-§7, `openai_compatible`) explicitly out of scope, not stubbed anywhere in this plan.
2. **Placeholder scan:** none found — every step has complete code or an exact command with expected output. Task 4 Step 2 is intentionally a manual (non-automatable) step, clearly marked as such rather than faked as a pytest step.
3. **Type consistency:** `LLMConfig` fields/types match between Task 1's definition and Task 3's `_config()` test helper and `AnthropicClient.__init__` signature. `LLMGenerationError`/`LLMClient` match between Task 2's definition and Task 3's imports/usage. `AnthropicClient(config, client=None)` constructor signature used identically in Task 3's tests and Task 4's script (Task 4 omits `client=`, matching the "production callers omit it" contract established in Task 3's Interfaces block).
