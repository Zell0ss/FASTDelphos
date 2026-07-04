import logging

import services.helpers as helpers_mod


def _compress(text: str) -> str:
    return text[:10]


def build_context(text: str) -> str:
    short = _compress(text)
    extra_text = helpers_mod.extra(short)
    logging.info("built context: %s", short)
    return short + extra_text


async def build_context_async(text: str) -> str:
    return await build_context(text)


def dynamic_dispatch(handlers: dict, key: str, text: str) -> str:
    return handlers[key](text)
