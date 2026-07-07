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
            return response.content[0].text
        except Exception as exc:
            # Deliberately broad: we wrap ANY failure from the SDK call
            # (network, API, timeout, malformed request) into one type so
            # callers only handle LLMGenerationError. Never repeat str(exc)
            # verbatim — we can't verify every SDK exception's message is
            # guaranteed free of the API key across versions/paths, so we
            # only ever surface the exception's type name.
            raise LLMGenerationError(f"Anthropic generation failed: {type(exc).__name__}") from exc
