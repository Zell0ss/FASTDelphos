import os
from dataclasses import dataclass, field

from dotenv import dotenv_values

_VALID_PROVIDERS = {"anthropic", "openai_compatible"}
_DEFAULT_MODEL = "claude-haiku-4-5"
_DEFAULT_MAX_TOKENS = 500
_DEFAULT_ORCHESTRATOR_THRESHOLD = 2


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
    orchestrator_threshold: int


def _read_env(env: dict[str, str] | None) -> dict[str, str]:
    """Merge .env file values with real environment variables, real env wins.

    When `env` is provided (for testing), it is used as the complete source —
    this ensures test isolation and prevents unexpected values from .env.

    When `env` is None (production), `dotenv_values` parses the .env file
    into a dict. We start from the .env values, then let real os.environ
    vars override, matching "real vars win (pods/CI)" from the spec.
    """
    if env is not None:
        # For tests: use only the provided env dict (isolation)
        return env

    # For production: merge .env with os.environ
    file_values = dotenv_values(".env")
    live_values = dict(os.environ)
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

    raw_threshold = values.get("CC_LLM_ORCHESTRATOR_THRESHOLD", "").strip()
    if not raw_threshold:
        orchestrator_threshold = _DEFAULT_ORCHESTRATOR_THRESHOLD
    else:
        try:
            orchestrator_threshold = int(raw_threshold)
        except ValueError as exc:
            raise LLMConfigError("CC_LLM_ORCHESTRATOR_THRESHOLD must be an integer") from exc
        if orchestrator_threshold <= 0:
            raise LLMConfigError("CC_LLM_ORCHESTRATOR_THRESHOLD must be a positive integer")

    base_url = values.get("CC_LLM_BASE_URL", "").strip() or None
    if provider == "openai_compatible" and not base_url:
        raise LLMConfigError(
            "CC_LLM_BASE_URL is not set (required for provider=openai_compatible)"
        )
    if provider == "openai_compatible" and not values.get("CC_LLM_MODEL", "").strip():
        raise LLMConfigError(
            "CC_LLM_MODEL is not set (required for provider=openai_compatible — "
            "there's no cross-provider default model name)"
        )
    extra_instructions = values.get("CC_LLM_EXTRA_INSTRUCTIONS", "").strip() or None

    return LLMConfig(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        extra_instructions=extra_instructions,
        orchestrator_threshold=orchestrator_threshold,
    )
