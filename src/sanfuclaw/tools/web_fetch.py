"""Web fetch tool — HTTP requests via curl_cffi with HTML-to-text extraction.

Uses curl_cffi (libcurl + browser TLS fingerprint impersonation) instead of
plain httpx so basic Cloudflare/CDN fingerprint checks don't block us. We
still can't execute JavaScript, so JS-driven challenge pages are detected
and surfaced as a clear error rather than silently returning the stub HTML.
"""

from __future__ import annotations

import re
from typing import Any

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


_INTERSTITIAL_TITLE = re.compile(
    r"<title[^>]*>\s*(just a moment|please wait|checking your browser|attention required)",
    re.IGNORECASE,
)
_INTERSTITIAL_MARKERS = (
    "cf-browser-verification",
    "cf-chl-bypass",
    "cf-challenge-running",
    "challenge-platform",
    "_cf_chl_opt",
)
_INTERSTITIAL_PHRASES = re.compile(
    r"please wait|just a moment|checking your browser|enable javascript and cookies",
    re.IGNORECASE,
)
_TITLE_TAG = re.compile(r"<title[^>]*>\s*([^<]{0,200})", re.IGNORECASE)
_SOFT_404_TITLE = re.compile(
    r"\b404\b|page not found|not\s*found|"
    r"页面不存在|页面.{0,4}找不到|找不到了|页面.{0,4}已删除",
    re.IGNORECASE,
)


def _html_to_text(html: str) -> str:
    """Extract readable text from HTML — lightweight, no external dependency."""
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(br|hr|p|div|li|tr|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _detect_interstitial(html: str, text: str) -> str | None:
    """Return a brief reason if the response looks like a JS-challenge /
    bot-protection stub (Cloudflare, Akamai, etc.) rather than real content;
    otherwise None."""
    if _INTERSTITIAL_TITLE.search(html):
        return "page title indicates a bot-check interstitial"
    lower = html.lower()
    for marker in _INTERSTITIAL_MARKERS:
        if marker in lower:
            return f"page contains challenge marker {marker!r}"
    # Suspiciously short body that's almost entirely a "please wait" stub.
    if len(text) < 500 and _INTERSTITIAL_PHRASES.search(text):
        return "page body is a short 'please wait' stub"
    return None


def _detect_soft_404(html: str) -> str | None:
    """Return a brief reason if the page is a 'soft 404' — HTTP 200 served
    with a not-found page (common on news.qq.com, sina, weibo, etc.).
    We only match in <title> to avoid false positives where '404' or
    'not found' appears in legitimate article text.
    """
    m = _TITLE_TAG.search(html)
    if not m:
        return None
    title = m.group(1).strip()
    if _SOFT_404_TITLE.search(title):
        return f"page title indicates a not-found page: {title[:80]!r}"
    return None


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

    def __init__(self, timeout: int = 30, impersonate: str = "chrome120"):
        self._timeout = timeout
        self._impersonate = impersonate

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        url = params.get("url", "")
        method = params.get("method", "GET").upper()
        raw = params.get("raw", False)
        if not url:
            raise ToolError("No URL provided")

        try:
            async with AsyncSession() as client:
                response = await client.request(
                    method, url,
                    timeout=self._timeout,
                    impersonate=self._impersonate,
                    allow_redirects=True,
                    verify=False,  # Allow local proxy (e.g. clash) to handle SSL
                )
                status = response.status_code
                if not 200 <= status < 300:
                    raise ToolError(
                        f"HTTP {status} from {url}; the URL is unreachable, "
                        "wrong, or the resource no longer exists."
                    )

                body = response.text
                content_type = response.headers.get("content-type", "")

                if "html" in content_type.lower():
                    text = _html_to_text(body)
                    reason = _detect_interstitial(body, text)
                    if reason:
                        raise ToolError(
                            f"URL returned a JS-challenge / bot-protection "
                            f"interstitial ({reason}); the real page requires "
                            "a headless browser to render. Try a different "
                            "source or summary feed."
                        )
                    soft_404 = _detect_soft_404(body)
                    if soft_404:
                        raise ToolError(
                            f"URL returned a soft 404 ({soft_404}); the page "
                            "does not exist or has moved. Try a different URL."
                        )
                    # JS-rendered shell: substantial HTML payload but almost
                    # nothing readable after stripping <script> blocks. The
                    # real content lives in JS-built DOM that we can't reach.
                    if len(text) < 50 and len(body) > 2000 and "<script" in body.lower():
                        raise ToolError(
                            f"URL appears to be a JavaScript-rendered shell "
                            f"(only {len(text)} chars of text extracted from "
                            f"{len(body)} bytes of HTML). The real content is "
                            "built by client-side JS. Try the site's RSS feed, "
                            "API, or a different source."
                        )
                    if not raw:
                        body = text

                return body[:10000]
        except ToolError:
            raise
        except RequestsError as e:
            msg = str(e).lower()
            if "timed out" in msg or "timeout" in msg:
                raise ToolError(f"Request timed out after {self._timeout}s")
            raise ToolError(f"HTTP request failed: {e}")
        except Exception as e:
            raise ToolError(f"HTTP request failed: {e}")
