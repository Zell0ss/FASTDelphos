from cc.llm.client import LLMClient, LLMGenerationError


class _FakeClient:
    """A minimal object satisfying the LLMClient Protocol structurally."""

    def generate(self, system: str, user: str) -> str:
        return f"system={system} user={user}"


def test_llm_client_protocol_is_satisfied_structurally():
    client: LLMClient = _FakeClient()
    assert client.generate("sys", "usr") == "system=sys user=usr"


def test_llm_generation_error_is_a_plain_exception():
    err = LLMGenerationError("boom")
    assert isinstance(err, Exception)
    assert str(err) == "boom"
