"""Web search tool — DuckDuckGo + Bing backends with auto-fallback.

The point of this tool is to let the agent *discover* current URLs
instead of guessing them from training-time memory. Hallucinated URLs
(stale paths, renamed sections) are the #1 cause of failed web_fetch
calls; a search-then-fetch pattern eliminates almost all of them.

Why two backends: DuckDuckGo's HTML endpoint is the cleanest scrape
target but is unreachable from some networks (CN mainland in
particular — TLS handshake gets reset). Bing reaches more places and
its result page is parseable; the URLs come wrapped in a
``bing.com/ck/a?u=a1<base64>`` redirect we can unwrap locally without
an extra round trip.

We try backends in order and surface results from the first that
returns any. A backend's transport error or empty/blocked response
counts as "skip and try next" — only when all backends fail do we
raise.
"""

from __future__ import annotations

import base64
import html as html_lib
import re
import urllib.parse
from typing import Any, Callable

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


# --- DuckDuckGo HTML endpoint -------------------------------------------------

_DDG_RESULT_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?'
    r'<a[^>]*class="result__snippet"[^>]*?>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_DDG_NO_RESULTS_RE = re.compile(r"no results|no\s*matches|anomaly", re.IGNORECASE)


def _decode_ddg_url(href: str) -> str | None:
    """Unwrap DuckDuckGo's /l/?uddg=<encoded> redirect. Returns None for
    sponsored slots routed through /y.js."""
    if "/y.js" in href or "ad_provider=" in href or "ad_type=txad" in href:
        return None
    if "duckduckgo.com/l/" in href:
        m = re.search(r"[?&]uddg=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


async def _search_duckduckgo(
    client: AsyncSession, query: str, max_results: int, timeout: int, impersonate: str,
) -> list[tuple[str, str, str]]:
    response = await client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        timeout=timeout,
        impersonate=impersonate,
        allow_redirects=True,
        verify=False,
    )
    if not 200 <= response.status_code < 300:
        raise ToolError(f"HTTP {response.status_code} from DuckDuckGo")
    html = response.text
    results: list[tuple[str, str, str]] = []
    for href, title_html, snippet_html in _DDG_RESULT_RE.findall(html):
        url = _decode_ddg_url(href)
        if not url:
            continue
        title = _strip_html(title_html)
        if not title:
            continue
        results.append((title, url, _strip_html(snippet_html)))
        if len(results) >= max_results:
            break
    if not results and _DDG_NO_RESULTS_RE.search(html):
        raise ToolError("DuckDuckGo returned a no-results / anomaly page")
    return results


# --- Bing -------------------------------------------------------------------

_BING_ALGO_START = re.compile(r'<li class="b_algo[" ]')
_BING_TITLE_RE = re.compile(
    r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_BING_SNIPPET_RES = (
    re.compile(r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', re.DOTALL),
    re.compile(r'<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>', re.DOTALL),
)


def _decode_bing_url(href: str) -> str | None:
    """Unwrap Bing's bing.com/ck/a?...&u=a1<base64>... redirect."""
    href = href.replace("&amp;", "&")
    if "bing.com/ck/a" not in href:
        return href if href.startswith(("http://", "https://")) else None
    qs = urllib.parse.urlparse(href).query
    u = urllib.parse.parse_qs(qs).get("u", [""])[0]
    if not u.startswith("a1"):
        return None
    try:
        # Bing uses URL-safe base64 without padding
        decoded = base64.urlsafe_b64decode(u[2:] + "===").decode("utf-8", errors="replace")
    except Exception:
        return None
    if not decoded.startswith(("http://", "https://")):
        return None
    return decoded


async def _search_bing(
    client: AsyncSession, query: str, max_results: int, timeout: int, impersonate: str,
) -> list[tuple[str, str, str]]:
    response = await client.get(
        "https://www.bing.com/search",
        params={"q": query},
        timeout=timeout,
        impersonate=impersonate,
        allow_redirects=True,
        verify=False,
    )
    if not 200 <= response.status_code < 300:
        raise ToolError(f"HTTP {response.status_code} from Bing")
    html = response.text

    # Slice into per-result regions by b_algo start positions, since
    # blocks contain nested <li> and a single </li> match wouldn't bound them.
    starts = [m.start() for m in _BING_ALGO_START.finditer(html)]
    if not starts:
        return []

    results: list[tuple[str, str, str]] = []
    for s, e in zip(starts, starts[1:] + [len(html)]):
        block = html[s:e]
        tm = _BING_TITLE_RE.search(block)
        if not tm:
            continue
        url = _decode_bing_url(tm.group(1))
        if not url:
            continue
        title = _strip_html(tm.group(2))
        if not title:
            continue
        snippet = ""
        for sn_re in _BING_SNIPPET_RES:
            sm = sn_re.search(block)
            if sm:
                snippet = _strip_html(sm.group(1))
                break
        results.append((title, url, snippet))
        if len(results) >= max_results:
            break
    return results


# --- Shared helpers ---------------------------------------------------------

def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = html_lib.unescape(s)  # handles &amp; &nbsp; &#183; &#x27; etc.
    return re.sub(r"\s+", " ", s).strip()


_BACKENDS: dict[str, Callable] = {
    "duckduckgo": _search_duckduckgo,
    "bing": _search_bing,
}


class WebSearchTool:
    """Search the web with automatic backend fallback."""

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
            "query": {"type": "string", "description": "Search query."},
            "max_results": {
                "type": "integer",
                "description": "Max results to return (default 10, capped at 20).",
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        timeout: int = 15,
        impersonate: str = "chrome120",
        backends: tuple[str, ...] = ("duckduckgo", "bing"),
    ):
        unknown = [b for b in backends if b not in _BACKENDS]
        if unknown:
            raise ValueError(f"Unknown search backend(s): {unknown}")
        self._timeout = timeout
        self._impersonate = impersonate
        self._backends = backends

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        query = (params.get("query") or "").strip()
        if not query:
            raise ToolError("No query provided")
        try:
            max_results = int(params.get("max_results") or 10)
        except (TypeError, ValueError):
            max_results = 10
        max_results = max(1, min(max_results, 20))

        errors: list[str] = []
        async with AsyncSession() as client:
            for name in self._backends:
                fn = _BACKENDS[name]
                try:
                    results = await fn(
                        client, query, max_results, self._timeout, self._impersonate,
                    )
                except RequestsError as e:
                    errors.append(f"{name}: {e}")
                    continue
                except ToolError as e:
                    errors.append(f"{name}: {e}")
                    continue
                except Exception as e:
                    errors.append(f"{name}: {e}")
                    continue
                if results:
                    return _format_results(query, name, results)
                errors.append(f"{name}: no results")

        raise ToolError(
            "All search backends failed for query "
            f"{query!r}. Tried {len(errors)}: " + "; ".join(errors)
        )


def _format_results(
    query: str, backend: str, results: list[tuple[str, str, str]]
) -> str:
    lines = [f"Search results for {query!r} (via {backend}):", ""]
    for i, (title, url, snippet) in enumerate(results, 1):
        lines.append(f"[{i}] {title}")
        lines.append(f"    {url}")
        if snippet:
            lines.append(f"    {snippet}")
        lines.append("")
    return "\n".join(lines).rstrip()
