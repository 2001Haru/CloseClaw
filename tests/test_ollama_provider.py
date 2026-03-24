import pytest

from closeclaw.providers.ollama import OllamaProvider


@pytest.mark.asyncio
async def test_ollama_provider_generate_parses_text_and_tool_calls(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "content": "hello from ollama",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "read_file",
                                "arguments": {"path": "README.md"},
                            }
                        }
                    ],
                }
            }

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            assert url.endswith("/api/chat")
            assert json["model"] == "llama3.1"
            assert json["stream"] is False
            return _Resp()

    monkeypatch.setattr("closeclaw.providers.ollama.httpx.AsyncClient", _Client)

    provider = OllamaProvider(model="llama3.1")
    text, tool_calls = await provider.generate(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
    )

    assert text == "hello from ollama"
    assert tool_calls is not None
    assert tool_calls[0].name == "read_file"
    assert tool_calls[0].arguments["path"] == "README.md"
