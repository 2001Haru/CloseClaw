"""Planning service that encapsulates LLM planning/synthesis invocations."""

from __future__ import annotations

from typing import Any, Optional, Protocol

from ..types import ToolCall


class PlannerLLMProvider(Protocol):
    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> tuple[str, Optional[list[ToolCall]]]:
        ...


class PlanningService:
    """Thin service wrapper around LLM calls for plan/synthesis stages."""

    def __init__(self, llm_provider: PlannerLLMProvider) -> None:
        self._llm_provider = llm_provider

    async def generate_plan_or_answer(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
    ) -> tuple[str, Optional[list[ToolCall]]]:
        return await self._llm_provider.generate(
            messages=messages,
            tools=tools,
            temperature=temperature,
        )

    async def synthesize_answer(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
    ) -> str:
        text, _ = await self._llm_provider.generate(
            messages=messages,
            tools=[],
            temperature=temperature,
        )
        return text
