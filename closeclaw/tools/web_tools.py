"""Web tools - HTTP fetching and web search.

Uses httpx.AsyncClient for non-blocking HTTP requests.
"""

import asyncio
import inspect
import logging
import time
from typing import Any, Optional

import httpx
import socket
import ipaddress
import urllib.parse

try:
    from ddgs import DDGS
except Exception:  # pragma: no cover - dependency availability is environment-specific
    try:
        # Backward compatibility for environments that still have old package name.
        from duckduckgo_search import DDGS
    except Exception:  # pragma: no cover - dependency availability is environment-specific
        DDGS = None

from .base import tool
from ..types import ToolType

logger = logging.getLogger(__name__)


def _validate_safe_url(url: str) -> None:
    """Validate URL to prevent SSRF and local file access."""
    parsed = urllib.parse.urlparse(url)
    
    if parsed.scheme.lower() not in ("http", "https"):
        raise PermissionError(f"Unsupported URL scheme: {parsed.scheme}. Only http/https are allowed.")
        
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Invalid URL: {url}")
        
    try:
        # Resolve hostname to IP to catch domain-based SSRF
        addr_info = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise PermissionError(
                    f"SSRF blocked: Host {hostname} resolves to restricted IP {ip_str}"
                )
    except socket.gaierror:
        # If we can't resolve it, httpx will fail anyway, but we allow it through the sandbox check
        pass


_web_search_enabled = False
_web_search_provider = "brave"
_brave_api_key: Optional[str] = None
_web_search_timeout_seconds = 30
_duckduckgo_min_interval_seconds = 2.0
_duckduckgo_rate_lock = asyncio.Lock()
_duckduckgo_last_request_started_at = 0.0


def configure_web_search(
    *,
    enabled: bool = False,
    provider: str = "brave",
    brave_api_key: Optional[str] = None,
    timeout_seconds: int = 30,
    duckduckgo_min_interval_seconds: float = 2.0,
) -> None:
    """Configure runtime web search provider settings."""
    global _web_search_enabled, _web_search_provider, _brave_api_key, _web_search_timeout_seconds
    global _duckduckgo_min_interval_seconds
    _web_search_enabled = bool(enabled)
    _web_search_provider = (provider or "brave").strip().lower()
    _brave_api_key = (brave_api_key or "").strip() or None
    _web_search_timeout_seconds = max(1, int(timeout_seconds))
    _duckduckgo_min_interval_seconds = max(0.0, float(duckduckgo_min_interval_seconds))


def _normalize_search_results(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "title": item.get("title") or "(no title)",
                "url": item.get("url") or "",
                "snippet": item.get("snippet") or "",
            }
        )
    return normalized


async def _search_with_brave(query: str, count: int) -> list[dict[str, Any]]:
    if not _brave_api_key:
        raise ValueError("Brave Search API key not configured")

    async with httpx.AsyncClient(
        timeout=float(_web_search_timeout_seconds),
        follow_redirects=True,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": _brave_api_key,
            "User-Agent": "CloseClaw/0.1",
        },
    ) as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": count},
        )
        response.raise_for_status()
        payload = response.json() if response.content else {}

    results = payload.get("web", {}).get("results", [])
    mapped = [
        {
            "title": item.get("title") or "(no title)",
            "url": item.get("url") or "",
            "snippet": item.get("description") or "",
        }
        for item in results
        if isinstance(item, dict)
    ]
    return _normalize_search_results(mapped)


async def _enforce_duckduckgo_rate_limit() -> None:
    """Throttle DDG requests to reduce chance of provider-side limiting."""
    global _duckduckgo_last_request_started_at

    async with _duckduckgo_rate_lock:
        now = time.monotonic()
        elapsed = now - _duckduckgo_last_request_started_at
        wait_seconds = _duckduckgo_min_interval_seconds - elapsed
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        _duckduckgo_last_request_started_at = time.monotonic()


def _duckduckgo_text_search_sync(query: str, count: int) -> list[dict[str, Any]]:
    if DDGS is None:
        raise RuntimeError("DDG package is not installed. Install 'ddgs'.")

    with DDGS(timeout=_web_search_timeout_seconds) as ddgs:
        text_params = set(inspect.signature(ddgs.text).parameters.keys())
        attempts: list[dict[str, Any]] = [{}]

        # Some DDG backends can intermittently fail (for example, upstream returns None).
        # Try explicit backends when API supports a backend parameter.
        if "backend" in text_params:
            attempts.extend([{"backend": "html"}, {"backend": "lite"}])

        last_exc: Optional[Exception] = None
        raw_results = []
        for kwargs in attempts:
            call_kwargs: dict[str, Any] = {"max_results": count, **kwargs}
            try:
                raw_results = list(ddgs.text(query, **call_kwargs))
                break
            except Exception as exc:
                last_exc = exc
                continue

        if not raw_results and last_exc is not None:
            raise last_exc

    mapped: list[dict[str, Any]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        mapped.append(
            {
                "title": item.get("title") or "(no title)",
                "url": item.get("href") or "",
                "snippet": item.get("body") or "",
            }
        )
    return _normalize_search_results(mapped)


async def _search_with_duckduckgo(query: str, count: int) -> list[dict[str, Any]]:
    await _enforce_duckduckgo_rate_limit()
    return await asyncio.to_thread(_duckduckgo_text_search_sync, query, count)


@tool(
    name="web_search",
    description="Search the web for information (stub - configure search API for production)",
    need_auth=False,
    tool_type=ToolType.WEBSEARCH,
    parameters={
        "query": {
            "type": "string",
            "description": "Search query"
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum number of results (default: 5)"
        }
    }
)
async def web_search_impl(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search the web using configured provider with Brave->DDG fallback."""
    logger.info(f"Web search: {query} (max_results={max_results})")

    if not _web_search_enabled:
        return [
            {
                "title": f"Search disabled for: {query}",
                "url": "https://search.brave.com/",
                "snippet": (
                    "Web search is disabled. Set web_search.enabled=true in config.yaml."
                ),
            }
        ]

    provider = _web_search_provider
    if provider not in {"brave", "duckduckgo", "ddg"}:
        return [
            {
                "title": f"Unsupported search provider for: {query}",
                "url": "https://search.brave.com/",
                "snippet": (
                    f"Configured provider '{_web_search_provider}' is not supported yet. "
                    "Use provider='brave' or 'duckduckgo'."
                ),
            }
        ]

    count = max(1, min(int(max_results), 20))
    try:
        if provider == "brave" and _brave_api_key:
            normalized = await _search_with_brave(query, count)
            fallback_used = False
        elif provider == "brave":
            logger.warning("Brave provider selected but API key missing. Falling back to DuckDuckGo.")
            normalized = await _search_with_duckduckgo(query, count)
            fallback_used = True
        else:
            normalized = await _search_with_duckduckgo(query, count)
            fallback_used = False

        if not normalized:
            return [
                {
                    "title": f"No results for: {query}",
                    "url": "https://duckduckgo.com/",
                    "snippet": "Search provider returned no web results.",
                }
            ]

        if fallback_used:
            normalized[0]["snippet"] = (
                f"[Fallback: DuckDuckGo] {normalized[0].get('snippet', '')}"
            ).strip()

        return normalized
    except Exception as e:
        logger.error(f"Web search error: {e}")
        return [
            {
                "title": f"Web search error for: {query}",
                "url": "https://duckduckgo.com/",
                "snippet": f"Error: {str(e)}",
            }
        ]


@tool(
    name="fetch_url",
    description="Fetch content from a URL via HTTP GET",
    need_auth=False,
    tool_type=ToolType.WEBSEARCH,
    parameters={
        "url": {
            "type": "string",
            "description": "URL to fetch"
        },
        "max_length": {
            "type": "integer",
            "description": "Maximum content length to return (default: 10000)"
        }
    }
)
async def fetch_url_impl(url: str, max_length: int = 10000) -> dict[str, Any]:
    """Fetch content from a URL using httpx.
    
    Returns:
        {
            "url": str,
            "status_code": int,
            "content_type": str,
            "content": str (truncated to max_length),
            "content_length": int (original length),
        }
    """
    logger.info(f"Fetching URL: {url}")
    
    try:
        _validate_safe_url(url)
        
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "CloseClaw/0.1"},
        ) as client:
            response = await client.get(url)
            
            content = response.text
            original_length = len(content)
            
            # Truncate if too long
            if len(content) > max_length:
                content = content[:max_length] + f"\n... [truncated, {original_length} total chars]"
            
            result = {
                "url": str(response.url),
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", "unknown"),
                "content": content,
                "content_length": original_length,
            }
            
            logger.info(f"Fetched URL: {url} (status={response.status_code}, {original_length} chars)")
            return result
            
    except httpx.TimeoutException:
        logger.error(f"URL fetch timed out: {url}")
        return {
            "url": url,
            "status_code": -1,
            "content_type": "",
            "content": "Request timed out",
            "content_length": 0,
        }
    except Exception as e:
        logger.error(f"URL fetch error: {e}")
        return {
            "url": url,
            "status_code": -1,
            "content_type": "",
            "content": f"Error: {str(e)}",
            "content_length": 0,
        }

