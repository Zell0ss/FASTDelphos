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
