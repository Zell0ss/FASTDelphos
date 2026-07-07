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
