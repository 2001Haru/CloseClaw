import importlib
from types import SimpleNamespace

import pytest

from closeclaw.providers.litellm_provider import LiteLLMProvider


def test_litellm_provider_raises_clear_error_when_dependency_missing(monkeypatch):
    def _raise_import_error(name):
        raise ImportError("litellm missing")

    monkeypatch.setattr(importlib, "import_module", _raise_import_error)

    with pytest.raises(RuntimeError, match="pip install litellm"):
        LiteLLMProvider(
            api_key="key",
            model="gemini-2.5-flash",
            provider="gemini",
        )


def test_litellm_provider_resolve_model_prefixes():
    assert LiteLLMProvider._resolve_model("gemini", "gemini-2.5-flash") == "gemini/gemini-2.5-flash"
    assert LiteLLMProvider._resolve_model("anthropic", "claude-3-7-sonnet") == "anthropic/claude-3-7-sonnet"
    assert LiteLLMProvider._resolve_model("openai-compatible", "gpt-4o") == "gpt-4o"


@pytest.mark.asyncio
async def test_litellm_provider_generate_parses_tool_calls(monkeypatch):
    class _FakeToolFunction:
        def __init__(self):
            self.name = "fetch_url"
            self.arguments = '{"url":"https://example.com"}'

    class _FakeToolCall:
        def __init__(self):
            self.id = "tc_1"
            self.function = _FakeToolFunction()

    class _FakeMessage:
        def __init__(self):
            self.content = "I will call tool"
            self.tool_calls = [_FakeToolCall()]

    class _FakeChoice:
        def __init__(self):
            self.message = _FakeMessage()

    class _FakeResponse:
        def __init__(self):
            self.choices = [_FakeChoice()]

    async def _fake_acompletion(**kwargs):
        return _FakeResponse()

    class _FakeLiteLLMModule:
        acompletion = _fake_acompletion

    monkeypatch.setattr(importlib, "import_module", lambda name: _FakeLiteLLMModule)

    provider = LiteLLMProvider(
        api_key="key",
        model="gemini-2.5-flash",
        provider="gemini",
    )

    text, tool_calls = await provider.generate(
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "function": {"name": "fetch_url", "parameters": {"type": "object"}}}],
    )

    assert text == "I will call tool"
    assert tool_calls is not None
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "fetch_url"
    assert tool_calls[0].arguments["url"] == "https://example.com"
