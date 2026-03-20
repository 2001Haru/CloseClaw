"""Web tools - HTTP fetching and web search.

Uses httpx.AsyncClient for non-blocking HTTP requests.
"""

import logging
from typing import Any

import httpx

from .base import tool
from ..types import ToolType

logger = logging.getLogger(__name__)


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
    """Search the web.
    
    Note: This is a stub. For production, integrate with:
    - Google Custom Search API
    - Bing Search API
    - DuckDuckGo API
    
    Returns:
        List of search results with title, url, snippet
    """
    logger.info(f"Web search: {query} (max_results={max_results})")
    
    # Stub: return placeholder results
    # TODO: Integrate with real search API based on user config
    return [
        {
            "title": f"Search results for: {query}",
            "url": "https://example.com/search",
            "snippet": "Web search API not configured. Set up a search provider in config.yaml.",
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

