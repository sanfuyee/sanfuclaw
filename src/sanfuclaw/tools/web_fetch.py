"""Web fetch tool — HTTP requests via httpx."""

from __future__ import annotations

from typing import Any

import httpx

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


class WebFetchTool:
    """Fetch content from a URL."""

    name = "web_fetch"
    description = "Fetch the content of a URL. Returns the text response body."
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST"],
                "description": "HTTP method (default: GET)",
            },
        },
        "required": ["url"],
    }

    def __init__(self, timeout: int = 30):
        self._timeout = timeout

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        url = params.get("url", "")
        method = params.get("method", "GET").upper()
        if not url:
            raise ToolError("No URL provided")

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                verify=False,  # Allow local proxy (e.g. clash) to handle SSL
            ) as client:
                response = await client.request(method, url)
                return response.text[:10000]  # Limit response size
        except httpx.TimeoutException:
            raise ToolError(f"Request timed out after {self._timeout}s")
        except Exception as e:
            raise ToolError(f"HTTP request failed: {e}")
