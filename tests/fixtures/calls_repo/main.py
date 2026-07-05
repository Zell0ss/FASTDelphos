from services import synthesis as syn
from services.synthesis import build_context


def handler(text: str) -> str:
    return build_context(text)


def handler_via_module(text: str) -> str:
    return syn.build_context(text)
