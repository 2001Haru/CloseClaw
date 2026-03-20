"""LLM provider implementations.

Provides concrete LLM provider classes that implement the LLMProvider protocol
defined in core.py. Currently supports:
  - OpenAI-compatible API (works with OpenAI, OhMyGPT, DeepSeek, Azure, etc.)

All providers use httpx for async HTTP requests to stay lightweight
(no openai SDK dependency required).

Usage:
    provider = OpenAICompatibleProvider(
        api_key="sk-xxx",
        model="gpt-4",
        base_url="https://api.ohmygpt.com/v1",  # or any compatible endpoint
    )
    text, tool_calls = await provider.generate(messages, tools)
"""

import json
import logging
from typing import Any, Optional

import httpx

from ..types import ToolCall

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    """LLM provider for any OpenAI-compatible API.
    
    Works with:
    - OpenAI (api.openai.com)
    - OhMyGPT (api.ohmygpt.com)
    - DeepSeek (api.deepseek.com)
    - Azure OpenAI
    - Ollama (localhost)
    - Any other OpenAI-compatible endpoint
    
    Uses raw httpx instead of the openai SDK to keep dependencies minimal.
    """
    
    def __init__(self,
                 api_key: str,
                 model: str,
                 base_url: str = "https://api.openai.com/v1",
                 temperature: float = 0.0,
                 max_tokens: int = 2000,
                 timeout_seconds: int = 60):
        """Initialize OpenAI-compatible provider.
        
        Args:
            api_key: API key for authentication
            model: Model name (e.g., "gpt-4", "gpt-3.5-turbo", "deepseek-chat")
            base_url: API base URL (default: OpenAI, change for third-party)
            temperature: Sampling temperature
            max_tokens: Maximum response tokens
            timeout_seconds: Request timeout
        """
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        
        logger.info(f"LLM Provider initialized: model={model}, base_url={self.base_url}")
    
    async def generate(self,
                       messages: list[dict[str, str]],
                       tools: list[dict[str, Any]],
                       **kwargs: Any) -> tuple[str, Optional[list[ToolCall]]]:
        """Generate response from OpenAI-compatible API.
        
        Args:
            messages: Chat messages in OpenAI format
            tools: Tool definitions in OpenAI function calling format
            **kwargs: Additional parameters to pass to the API
        
        Returns:
            (response_text, tool_calls) 鈥?tool_calls is None if no tools called
        """
        # Build request body
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
        }
        
        # Add tools if provided (OpenAI function calling format)
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
            
        import json
        print("\n\n" + "="*80)
        print("DEBUG: EXACT LLM PROMPT PAYLOAD")
        print("TOTAL MESSAGES:", len(body["messages"]))
        for i, m in enumerate(body["messages"]):
            print(f"--- Message {i} ({m.get('role')}) ---")
            print(str(m)[:1000] + ("..." if len(str(m)) > 1000 else ""))
        print("="*80 + "\n\n")
        logger.info(f"[DEBUG] Sending {len(body['messages'])} messages to LLM")
        
        # Make API call
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
            
            return self._parse_response(data)
            
        except httpx.TimeoutException:
            logger.error(f"LLM request timed out after {self.timeout_seconds}s")
            raise TimeoutError(f"LLM request timed out after {self.timeout_seconds}s")
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else "No response"
            logger.error(f"LLM API error {e.response.status_code}: {error_body}")
            raise RuntimeError(f"LLM API error {e.response.status_code}: {error_body}")
        except Exception as e:
            logger.error(f"LLM request failed: {e}")
            raise
    
    def _parse_response(self, data: dict[str, Any]) -> tuple[str, Optional[list[ToolCall]]]:
        """Parse OpenAI API response into (text, tool_calls).
        
        Handles both regular text responses and function calling responses.
        """
        choices = data.get("choices", [])
        if not choices:
            logger.warning("LLM returned empty choices")
            return ("", None)
        
        message = choices[0].get("message", {})
        
        # Extract text content
        text = message.get("content", "") or ""
        
        # Extract tool calls (function calling)
        raw_tool_calls = message.get("tool_calls")
        tool_calls = None
        
        if raw_tool_calls:
            tool_calls = []
            for tc in raw_tool_calls:
                function = tc.get("function", {})
                
                # Parse arguments (may be a JSON string)
                args_raw = function.get("arguments", "{}")
                if isinstance(args_raw, str):
                    try:
                        arguments = json.loads(args_raw)
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse tool arguments: {args_raw[:100]}")
                        arguments = {"raw": args_raw}
                else:
                    arguments = args_raw
                
                tool_calls.append(ToolCall(
                    tool_id=tc.get("id", ""),
                    name=function.get("name", ""),
                    arguments=arguments,
                ))
            
            logger.info(f"LLM called {len(tool_calls)} tool(s): {[tc.name for tc in tool_calls]}")
        
        # Log usage info if available
        usage = data.get("usage", {})
        if usage:
            logger.debug(f"LLM usage: prompt={usage.get('prompt_tokens', '?')}, "
                        f"completion={usage.get('completion_tokens', '?')}, "
                        f"total={usage.get('total_tokens', '?')}")
        
        return (text, tool_calls)


def create_llm_provider(provider: str,
                        model: str,
                        api_key: str = "",
                        base_url: str = "",
                        temperature: float = 0.0,
                        max_tokens: int = 2000,
                        timeout_seconds: int = 60) -> OpenAICompatibleProvider:
    """Factory function to create LLM provider from config.
    
    Supports provider shortcuts:
    - "openai" -> base_url defaults to https://api.openai.com/v1
    - "ohmygpt" -> base_url defaults to https://api.ohmygpt.com/v1
    - "deepseek" -> base_url defaults to https://api.deepseek.com/v1
    - "ollama" -> base_url defaults to http://localhost:11434/v1
    - "openai-compatible" -> requires explicit base_url
    - Any other -> treated as openai-compatible with explicit base_url
    
    Args:
        provider: Provider name or shortcut
        model: Model name
        api_key: API key
        base_url: Custom base URL (overrides provider defaults)
        temperature: Sampling temperature
        max_tokens: Max response tokens
        timeout_seconds: Request timeout
    
    Returns:
        Configured OpenAICompatibleProvider
    """
    # Provider -> default base_url mapping
    default_urls = {
        "openai": "https://api.openai.com/v1",
        "ohmygpt": "https://api.ohmygpt.com/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "ollama": "http://localhost:11434/v1",
    }
    
    # Resolve base_url
    if base_url:
        resolved_url = base_url
    else:
        resolved_url = default_urls.get(provider.lower(), "https://api.openai.com/v1")
    
    if not api_key and provider.lower() != "ollama":
        logger.warning(f"No API key provided for provider '{provider}'. "
                      f"Set api_key in config or ${provider.upper()}_API_KEY env var.")
    
    return OpenAICompatibleProvider(
        api_key=api_key or "",
        model=model,
        base_url=resolved_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )

