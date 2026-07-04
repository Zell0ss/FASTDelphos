import services.synthesis


def call_dotted(text: str) -> str:
    return services.synthesis.build_context(text)
