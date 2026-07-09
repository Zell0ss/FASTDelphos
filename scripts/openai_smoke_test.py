"""Manual smoke test for the openai_compatible LLM adapter — NOT part of the
automated test suite. Costs real usage against whatever endpoint you point
it at. Run by hand:

    source .venv/bin/activate
    export CC_LLM_PROVIDER=openai_compatible
    export CC_LLM_BASE_URL=http://localhost:11434/v1  # full base, INCLUDING /v1
    export CC_LLM_API_KEY=                             # optional — many servers don't need one
    export CC_LLM_MODEL=qwen2.5-coder                  # whatever model name your server expects
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

Corporate-gateway gotchas, if the request fails on a real endpoint:
  - 401 despite a valid token: confirm the gateway actually expects
    `Authorization: Bearer <token>` — some enterprise gateways (notably
    Azure OpenAI-style ones) use a different header (e.g. `api-key: ...`)
    instead. This client only sends Bearer.
  - Connection/SSL error against an internal https:// gateway: the
    default client verifies TLS against the public `certifi` bundle,
    which won't include your corporate CA. Point `SSL_CERT_FILE` or
    `SSL_CERT_DIR` at the internal CA bundle (httpx honors both).
"""

from cc.llm.client import LLMGenerationError
from cc.llm.config import LLMConfigError, load_config
from cc.llm.openai_compatible_adapter import OpenAICompatibleClient

_SMOKE_SYSTEM_PROMPT = "You are a code comprehension assistant. Answer in one short sentence."
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
            "Set CC_LLM_PROVIDER=openai_compatible, CC_LLM_BASE_URL, and "
            "CC_LLM_MODEL (env vars or .env file) and retry."
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
