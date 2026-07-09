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
