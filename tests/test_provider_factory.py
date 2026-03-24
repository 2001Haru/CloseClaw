import pytest

from closeclaw.providers.factory import create_llm_provider
from closeclaw.providers.ollama import OllamaProvider
from closeclaw.providers.openai_compatible import OpenAICompatibleProvider
from closeclaw.providers.registry import find_provider_spec


def test_registry_prefers_explicit_provider_over_model_hint():
    spec = find_provider_spec("openai-compatible", "claude-3-7-sonnet")
    assert spec.name == "openai-compatible"
    assert spec.runtime == "openai_compatible"


def test_registry_uses_model_keyword_when_provider_missing():
    spec = find_provider_spec("", "gemini-2.5-flash")
    assert spec.name == "gemini"
    assert spec.runtime == "litellm"


def test_registry_recognizes_explicit_ollama_provider():
    spec = find_provider_spec("ollama", "llama3.1")
    assert spec.name == "ollama"
    assert spec.runtime == "ollama"


def test_factory_returns_dedicated_ollama_provider():
    provider = create_llm_provider(
        provider="ollama",
        model="llama3.1",
        api_key="",
        base_url="",
    )

    assert isinstance(provider, OllamaProvider)


def test_factory_returns_openai_compatible_provider_for_openai_compatible():
    provider = create_llm_provider(
        provider="openai-compatible",
        model="gpt-4o-mini",
        api_key="sk-test",
        base_url="https://example.com/v1",
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://example.com/v1"


@pytest.mark.parametrize("provider_name", ["gemini", "anthropic"])
def test_factory_uses_litellm_runtime_for_gemini_and_anthropic(monkeypatch, provider_name):
    class DummyLiteProvider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("closeclaw.providers.factory.LiteLLMProvider", DummyLiteProvider)

    provider = create_llm_provider(
        provider=provider_name,
        model="test-model",
        api_key="test-key",
        base_url="",
    )

    assert isinstance(provider, DummyLiteProvider)
    assert provider.kwargs["provider"] == provider_name


def test_factory_uses_model_fallback_to_litellm(monkeypatch):
    class DummyLiteProvider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("closeclaw.providers.factory.LiteLLMProvider", DummyLiteProvider)

    provider = create_llm_provider(
        provider="",
        model="claude-3-7-sonnet",
        api_key="test-key",
    )

    assert isinstance(provider, DummyLiteProvider)
    assert provider.kwargs["provider"] == "anthropic"
