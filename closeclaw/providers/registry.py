"""Provider registry and matching helpers for CloseClaw."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    model_keywords: tuple[str, ...]
    runtime: str  # "openai_compatible" | "litellm"


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(name="openai-compatible", model_keywords=("gpt", "openai", "deepseek"), runtime="openai_compatible"),
    ProviderSpec(name="openai", model_keywords=("gpt", "openai"), runtime="openai_compatible"),
    ProviderSpec(name="gemini", model_keywords=("gemini",), runtime="litellm"),
    ProviderSpec(name="anthropic", model_keywords=("claude", "anthropic"), runtime="litellm"),
)


def find_provider_spec(provider: str | None, model: str | None) -> ProviderSpec:
    """Resolve provider spec using explicit provider first, then model keyword fallback."""
    provider_norm = (provider or "").strip().lower()
    model_norm = (model or "").strip().lower()

    if provider_norm:
        for spec in PROVIDER_SPECS:
            if provider_norm == spec.name:
                return spec

    if model_norm:
        for spec in PROVIDER_SPECS:
            if any(keyword in model_norm for keyword in spec.model_keywords):
                return spec

    # Safe fallback keeps current behavior unchanged.
    return ProviderSpec(
        name=provider_norm or "openai-compatible",
        model_keywords=("gpt",),
        runtime="openai_compatible",
    )
