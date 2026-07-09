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
        {
            "CC_LLM_PROVIDER": "openai_compatible",
            "CC_LLM_API_KEY": "sk-test-key",
            "CC_LLM_BASE_URL": "http://localhost:8000/v1",
        }
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


def test_production_path_env_vars_win_over_dotenv_file(monkeypatch, tmp_path):
    # This test exercises the actual production path (env=None) where
    # load_config() merges a real .env file with real os.environ,
    # with real env vars winning the precedence battle.
    # We use tmp_path + monkeypatch.chdir() to isolate to a temp directory
    # so we don't depend on or touch the real repo-root .env file.

    # Change to temp directory so load_config()'s dotenv_values(".env") reads our test file
    monkeypatch.chdir(tmp_path)

    # Create a .env file with test values (some will be overridden by env vars)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CC_LLM_PROVIDER=openai_compatible\n"
        "CC_LLM_MODEL=file-model\n"
        "CC_LLM_MAX_TOKENS=111\n"
        "CC_LLM_EXTRA_INSTRUCTIONS=from-file\n"
    )

    # Set real env vars for some keys (these will win over .env values)
    # and omit CC_LLM_MAX_TOKENS from env (so it falls back to .env)
    monkeypatch.setenv("CC_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("CC_LLM_API_KEY", "test-api-key")
    monkeypatch.setenv("CC_LLM_MODEL", "env-model")

    # Call load_config() with NO arguments — this triggers the production path
    # where _read_env(None) merges dotenv + os.environ
    config = load_config()

    # Assert: env vars win where both are set (provider)
    assert config.provider == "anthropic", "env var should override .env file for provider"

    # Assert: env vars win where both are set (model)
    assert config.model == "env-model", "env var should override .env file for model"

    # Assert: .env file is used as fallback where env var is not set
    assert config.max_tokens == 111, ".env file should provide value when env var absent"

    # Assert: env var from the real environment is used
    assert config.api_key == "test-api-key"

    # Assert: unused .env vars are still read (not shadowed by env vars)
    assert config.extra_instructions == "from-file"


def test_orchestrator_threshold_defaults_to_two():
    config = load_config({"CC_LLM_PROVIDER": "anthropic", "CC_LLM_API_KEY": "k"})
    assert config.orchestrator_threshold == 2


def test_orchestrator_threshold_reads_from_env():
    config = load_config(
        {
            "CC_LLM_PROVIDER": "anthropic",
            "CC_LLM_API_KEY": "k",
            "CC_LLM_ORCHESTRATOR_THRESHOLD": "3",
        }
    )
    assert config.orchestrator_threshold == 3


def test_orchestrator_threshold_must_be_a_positive_integer():
    with pytest.raises(LLMConfigError, match="CC_LLM_ORCHESTRATOR_THRESHOLD"):
        load_config(
            {
                "CC_LLM_PROVIDER": "anthropic",
                "CC_LLM_API_KEY": "k",
                "CC_LLM_ORCHESTRATOR_THRESHOLD": "0",
            }
        )


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
