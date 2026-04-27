"""Web search tool — DuckDuckGo HTML endpoint, no API key needed.

The point of this tool is to let the agent *discover* current URLs
instead of guessing them from training-time memory. Hallucinated URLs
(stale paths, renamed sections) are the #1 cause of failed web_fetch
calls; a search-then-fetch pattern eliminates almost all of them.

We hit ``html.duckduckgo.com/html/`` directly with a Chrome TLS
fingerprint via curl_cffi. The response wraps every result URL in
DDG's ``/l/?uddg=<encoded>`` redirect — we unwrap to give the agent
real, fetchable URLs.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


_RESULT_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?'
    r'<a[^>]*class="result__snippet"[^>]*?>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_NO_RESULTS_RE = re.compile(
    r"no results|no\s*matches|anomaly", re.IGNORECASE,
)


def _decode_ddg_url(href: str) -> str | None:
    """Unwrap DuckDuckGo's /l/?uddg=<encoded> redirect to the real URL.
    Returns None for sponsored/ad slots, which go through /y.js with
    ``ad_provider=bingv7aa`` and aren't useful organic results."""
    if "/y.js" in href or "ad_provider=" in href or "ad_type=txad" in href:
        return None
    if "duckduckgo.com/l/" in href:
        m = re.search(r"[?&]uddg=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    for ent, ch in [
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&#x27;", "'"),
        ("&nbsp;", " "), ("&hellip;", "…"),
    ]:
        s = s.replace(ent, ch)
    return re.sub(r"\s+", " ", s).strip()


class WebSearchTool:
    """Search the web via DuckDuckGo and return a ranked list of results."""

    name = "web_search"
    description = (
        "Search the web for current pages. Returns a ranked list of "
        "title / URL / snippet entries. Use this BEFORE web_fetch "
        "whenever you need time-sensitive content (today's news, recent "
        "releases, current docs) so you fetch real URLs instead of "
        "guessing them from memory."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query.",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results to return (default 10, capped at 20).",
            },
        },
        "required": ["query"],
    }

    def __init__(self, timeout: int = 15, impersonate: str = "chrome120"):
        self._timeout = timeout
        self._impersonate = impersonate

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        query = (params.get("query") or "").strip()
        if not query:
            raise ToolError("No query provided")
        try:
            max_results = int(params.get("max_results") or 10)
        except (TypeError, ValueError):
            max_results = 10
        max_results = max(1, min(max_results, 20))

        try:
            async with AsyncSession() as client:
                response = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    timeout=self._timeout,
                    impersonate=self._impersonate,
                    allow_redirects=True,
                    verify=False,
                )
        except RequestsError as e:
            msg = str(e).lower()
            if "timed out" in msg or "timeout" in msg:
                raise ToolError(f"Search timed out after {self._timeout}s")
            raise ToolError(f"Search request failed: {e}")
        except Exception as e:
            raise ToolError(f"Search request failed: {e}")

        if not 200 <= response.status_code < 300:
            raise ToolError(
                f"DuckDuckGo returned HTTP {response.status_code}; "
                "the search backend may be rate-limiting or blocking us."
            )

        html = response.text
        results = []
        for href, title_html, snippet_html in _RESULT_RE.findall(html):
            url = _decode_ddg_url(href)
            if not url:
                continue  # sponsored/ad slot
            title = _strip_html(title_html)
            snippet = _strip_html(snippet_html)
            if not title:
                continue
            results.append((title, url, snippet))
            if len(results) >= max_results:
                break

        if not results:
            # DDG sometimes returns a 200 with a no-results / anomaly page
            # (rate limit, captcha). Surface that instead of an empty body.
            if _NO_RESULTS_RE.search(html):
                raise ToolError(
                    f"No results from DuckDuckGo for {query!r} (possibly "
                    "rate-limited or blocked). Try again or rephrase."
                )
            raise ToolError(
                f"No parseable results for {query!r}. The search page "
                "format may have changed."
            )

        lines = [f"Search results for {query!r}:", ""]
        for i, (title, url, snippet) in enumerate(results, 1):
            lines.append(f"[{i}] {title}")
            lines.append(f"    {url}")
            if snippet:
                lines.append(f"    {snippet}")
            lines.append("")
        return "\n".join(lines).rstrip()
