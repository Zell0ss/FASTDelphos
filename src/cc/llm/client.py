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
