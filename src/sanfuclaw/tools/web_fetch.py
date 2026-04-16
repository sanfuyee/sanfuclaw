"""Web fetch tool — HTTP requests via httpx with HTML-to-text extraction."""

from __future__ import annotations

import re
from typing import Any

import httpx

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


def _html_to_text(html: str) -> str:
    """Extract readable text from HTML — lightweight, no external dependency."""
    # Remove script and style blocks
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Convert block tags to newlines
    text = re.sub(r"<(br|hr|p|div|li|tr|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class WebFetchTool:
    """Fetch content from a URL."""

    name = "web_fetch"
    description = "Fetch the content of a URL. Returns extracted text (HTML is auto-converted)."
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
            "raw": {
                "type": "boolean",
                "description": "Return raw response without HTML extraction (default: false)",
            },
        },
        "required": ["url"],
    }

    def __init__(self, timeout: int = 30):
        self._timeout = timeout

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        url = params.get("url", "")
        method = params.get("method", "GET").upper()
        raw = params.get("raw", False)
        if not url:
            raise ToolError("No URL provided")

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                verify=False,  # Allow local proxy (e.g. clash) to handle SSL
                headers={"User-Agent": "Mozilla/5.0 (compatible; Sanfuclaw/0.1)"},
            ) as client:
                response = await client.request(method, url)
                body = response.text

                content_type = response.headers.get("content-type", "")
                if not raw and "html" in content_type:
                    body = _html_to_text(body)

                return body[:10000]  # Limit response size
        except httpx.TimeoutException:
            raise ToolError(f"Request timed out after {self._timeout}s")
        except Exception as e:
            raise ToolError(f"HTTP request failed: {e}")
